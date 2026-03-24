"""Service layer for plan runtime with tenant isolation."""

from datetime import datetime
from typing import List, Optional
import uuid

from sqlalchemy.orm import Session

from app.models.plan import Plan, PlanStep, PlanAssumption, PlanEvent
from app.schemas.plan import (
    PlanCreate,
    PlanUpdate,
    PlanStepCreate,
    PlanAssumptionCreate,
    PlanStatus,
)


def _validate_goal_ref(db: Session, tenant_id: uuid.UUID, goal_id: Optional[uuid.UUID]) -> None:
    if not goal_id:
        return
    from app.models.goal_record import GoalRecord
    exists = db.query(GoalRecord).filter(
        GoalRecord.id == goal_id, GoalRecord.tenant_id == tenant_id,
    ).first()
    if not exists:
        raise ValueError(f"Goal {goal_id} not found in this tenant")


def _validate_assertion_ref(db: Session, tenant_id: uuid.UUID, assertion_id: Optional[uuid.UUID]) -> None:
    if not assertion_id:
        return
    from app.models.world_state import WorldStateAssertion
    exists = db.query(WorldStateAssertion).filter(
        WorldStateAssertion.id == assertion_id, WorldStateAssertion.tenant_id == tenant_id,
    ).first()
    if not exists:
        raise ValueError(f"Assertion {assertion_id} not found in this tenant")


def _log_event(
    db: Session,
    plan_id: uuid.UUID,
    event_type: str,
    previous_status: Optional[str] = None,
    new_status: Optional[str] = None,
    reason: Optional[str] = None,
    step_id: Optional[uuid.UUID] = None,
    agent_slug: Optional[str] = None,
    metadata_json: Optional[dict] = None,
) -> PlanEvent:
    event = PlanEvent(
        plan_id=plan_id,
        step_id=step_id,
        event_type=event_type,
        previous_status=previous_status,
        new_status=new_status,
        reason=reason,
        agent_slug=agent_slug,
        metadata_json=metadata_json or {},
    )
    db.add(event)
    return event


def create_plan(
    db: Session,
    tenant_id: uuid.UUID,
    plan_in: PlanCreate,
) -> Plan:
    _validate_goal_ref(db, tenant_id, plan_in.goal_id)

    plan = Plan(
        tenant_id=tenant_id,
        goal_id=plan_in.goal_id,
        owner_agent_slug=plan_in.owner_agent_slug,
        title=plan_in.title,
        description=plan_in.description,
        budget_max_actions=plan_in.budget_max_actions,
        budget_max_cost_usd=plan_in.budget_max_cost_usd,
        budget_max_runtime_hours=plan_in.budget_max_runtime_hours,
        status="draft",
    )
    db.add(plan)
    db.flush()

    for i, step_in in enumerate(plan_in.steps):
        step = PlanStep(
            plan_id=plan.id,
            step_index=i,
            title=step_in.title,
            description=step_in.description,
            owner_agent_slug=step_in.owner_agent_slug,
            step_type=step_in.step_type,
            expected_inputs=step_in.expected_inputs,
            expected_outputs=step_in.expected_outputs,
            required_tools=step_in.required_tools,
            side_effect_level=step_in.side_effect_level,
            retry_policy=step_in.retry_policy,
            fallback_step_index=step_in.fallback_step_index,
        )
        db.add(step)

    for assumption_in in plan_in.assumptions:
        _validate_assertion_ref(db, tenant_id, assumption_in.assertion_id)
        assumption = PlanAssumption(
            plan_id=plan.id,
            description=assumption_in.description,
            assertion_id=assumption_in.assertion_id,
        )
        db.add(assumption)

    _log_event(db, plan.id, "created", new_status="draft", agent_slug=plan_in.owner_agent_slug)
    db.commit()
    db.refresh(plan)
    return plan


def get_plan(db: Session, tenant_id: uuid.UUID, plan_id: uuid.UUID) -> Optional[Plan]:
    return db.query(Plan).filter(Plan.id == plan_id, Plan.tenant_id == tenant_id).first()


def get_plan_detail(db: Session, tenant_id: uuid.UUID, plan_id: uuid.UUID) -> Optional[dict]:
    plan = get_plan(db, tenant_id, plan_id)
    if not plan:
        return None
    steps = (
        db.query(PlanStep).filter(PlanStep.plan_id == plan_id)
        .order_by(PlanStep.step_index).all()
    )
    assumptions = db.query(PlanAssumption).filter(PlanAssumption.plan_id == plan_id).all()
    events = (
        db.query(PlanEvent).filter(PlanEvent.plan_id == plan_id)
        .order_by(PlanEvent.created_at.desc()).limit(20).all()
    )
    return {
        "plan": plan,
        "steps": steps,
        "assumptions": assumptions,
        "recent_events": events,
    }


def list_plans(
    db: Session,
    tenant_id: uuid.UUID,
    owner_agent_slug: Optional[str] = None,
    status: Optional[str] = None,
    goal_id: Optional[uuid.UUID] = None,
    limit: int = 50,
) -> List[Plan]:
    q = db.query(Plan).filter(Plan.tenant_id == tenant_id)
    if owner_agent_slug:
        q = q.filter(Plan.owner_agent_slug == owner_agent_slug)
    if status:
        q = q.filter(Plan.status == status)
    if goal_id:
        q = q.filter(Plan.goal_id == goal_id)
    return q.order_by(Plan.updated_at.desc()).limit(limit).all()


def update_plan(
    db: Session,
    tenant_id: uuid.UUID,
    plan_id: uuid.UUID,
    plan_in: PlanUpdate,
) -> Optional[Plan]:
    plan = get_plan(db, tenant_id, plan_id)
    if not plan:
        return None

    data = plan_in.model_dump(exclude_unset=True)
    old_status = plan.status

    if "status" in data:
        new_status = data["status"]
        if isinstance(new_status, PlanStatus):
            new_status = new_status.value
        data["status"] = new_status
        _log_event(
            db, plan_id, "status_change",
            previous_status=old_status, new_status=new_status,
            agent_slug=plan.owner_agent_slug,
        )

        # When transitioning to executing, check budget then start the first step
        if new_status == "executing" and old_status != "executing":
            # Apply status first so budget check sees "executing"
            plan.status = new_status
            budget_violation = _enforce_budget_before_step(db, plan, plan_id)
            if budget_violation:
                # Budget already exceeded — don't start execution
                plan.updated_at = datetime.utcnow()
                db.commit()
                db.refresh(plan)
                return plan

            first_step = (
                db.query(PlanStep).filter(
                    PlanStep.plan_id == plan_id,
                    PlanStep.step_index == plan.current_step_index,
                ).first()
            )
            if first_step and first_step.status == "pending":
                first_step.status = "running"
                first_step.started_at = datetime.utcnow()
                _log_event(
                    db, plan_id, "step_started",
                    new_status="running", step_id=first_step.id,
                    metadata_json={"step_index": plan.current_step_index},
                )

    for key, value in data.items():
        if hasattr(plan, key):
            setattr(plan, key, value)

    plan.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(plan)
    return plan


def advance_step(
    db: Session,
    tenant_id: uuid.UUID,
    plan_id: uuid.UUID,
    step_output: Optional[dict] = None,
) -> Optional[PlanStep]:
    """Mark current step as completed and advance to the next."""
    plan = get_plan(db, tenant_id, plan_id)
    if not plan or plan.status != "executing":
        return None

    current_step = (
        db.query(PlanStep).filter(
            PlanStep.plan_id == plan_id,
            PlanStep.step_index == plan.current_step_index,
        ).first()
    )
    if not current_step:
        return None

    current_step.status = "completed"
    current_step.completed_at = datetime.utcnow()
    current_step.output = step_output
    plan.budget_actions_used += 1

    _log_event(
        db, plan_id, "step_completed",
        previous_status="running", new_status="completed",
        step_id=current_step.id,
        metadata_json={"step_index": plan.current_step_index},
    )

    # Check if there are more steps
    next_step = (
        db.query(PlanStep).filter(
            PlanStep.plan_id == plan_id,
            PlanStep.step_index == plan.current_step_index + 1,
        ).first()
    )

    # Check budget after every step completion (including the last one)
    budget_violation = _enforce_budget_before_step(db, plan, plan_id)
    if budget_violation:
        plan.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(current_step)
        return current_step

    if next_step:
        plan.current_step_index += 1
        next_step.status = "running"
        next_step.started_at = datetime.utcnow()
        _log_event(
            db, plan_id, "step_started",
            new_status="running", step_id=next_step.id,
            metadata_json={"step_index": plan.current_step_index},
        )
    else:
        plan.status = "completed"
        _log_event(db, plan_id, "completed", previous_status="executing", new_status="completed")

    plan.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(current_step)
    return current_step


def fail_step(
    db: Session,
    tenant_id: uuid.UUID,
    plan_id: uuid.UUID,
    error: str,
) -> Optional[PlanStep]:
    """Mark current step as failed."""
    plan = get_plan(db, tenant_id, plan_id)
    if not plan or plan.status != "executing":
        return None

    current_step = (
        db.query(PlanStep).filter(
            PlanStep.plan_id == plan_id,
            PlanStep.step_index == plan.current_step_index,
        ).first()
    )
    if not current_step:
        return None

    current_step.status = "failed"
    current_step.error = error
    current_step.completed_at = datetime.utcnow()
    plan.status = "failed"

    _log_event(
        db, plan_id, "step_failed",
        previous_status="running", new_status="failed",
        step_id=current_step.id, reason=error,
    )
    _log_event(db, plan_id, "failed", previous_status="executing", new_status="failed", reason=error)

    plan.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(current_step)
    return current_step


# ---------------------------------------------------------------------------
# Phase 3: Budget-aware execution
# ---------------------------------------------------------------------------

BUDGET_WARNING_THRESHOLD = 0.8  # Warn at 80% of budget


def check_budget(
    db: Session,
    tenant_id: uuid.UUID,
    plan_id: uuid.UUID,
) -> Optional[dict]:
    """Check budget status for a plan. Returns violations, warnings, and usage."""
    plan = get_plan(db, tenant_id, plan_id)
    if not plan:
        return None

    now = datetime.utcnow()
    violations = []
    warnings = []

    # Actions budget
    if plan.budget_max_actions is not None:
        pct = plan.budget_actions_used / max(plan.budget_max_actions, 1)
        if plan.budget_actions_used >= plan.budget_max_actions:
            violations.append({
                "budget": "actions",
                "limit": plan.budget_max_actions,
                "used": plan.budget_actions_used,
                "message": f"Action budget exhausted ({plan.budget_actions_used}/{plan.budget_max_actions})",
            })
        elif pct >= BUDGET_WARNING_THRESHOLD:
            warnings.append({
                "budget": "actions",
                "limit": plan.budget_max_actions,
                "used": plan.budget_actions_used,
                "pct": round(pct, 2),
                "message": f"Action budget at {round(pct * 100)}% ({plan.budget_actions_used}/{plan.budget_max_actions})",
            })

    # Cost budget
    if plan.budget_max_cost_usd is not None:
        pct = plan.budget_cost_used / max(plan.budget_max_cost_usd, 0.01)
        if plan.budget_cost_used >= plan.budget_max_cost_usd:
            violations.append({
                "budget": "cost",
                "limit": plan.budget_max_cost_usd,
                "used": round(plan.budget_cost_used, 4),
                "message": f"Cost budget exhausted (${plan.budget_cost_used:.4f}/${plan.budget_max_cost_usd:.2f})",
            })
        elif pct >= BUDGET_WARNING_THRESHOLD:
            warnings.append({
                "budget": "cost",
                "limit": plan.budget_max_cost_usd,
                "used": round(plan.budget_cost_used, 4),
                "pct": round(pct, 2),
                "message": f"Cost budget at {round(pct * 100)}% (${plan.budget_cost_used:.4f}/${plan.budget_max_cost_usd:.2f})",
            })

    # Runtime budget
    if plan.budget_max_runtime_hours is not None:
        runtime_hours = (now - plan.created_at).total_seconds() / 3600
        pct = runtime_hours / max(plan.budget_max_runtime_hours, 0.01)
        if runtime_hours >= plan.budget_max_runtime_hours:
            violations.append({
                "budget": "runtime",
                "limit": plan.budget_max_runtime_hours,
                "used": round(runtime_hours, 2),
                "message": f"Runtime budget exhausted ({runtime_hours:.1f}h/{plan.budget_max_runtime_hours}h)",
            })
        elif pct >= BUDGET_WARNING_THRESHOLD:
            warnings.append({
                "budget": "runtime",
                "limit": plan.budget_max_runtime_hours,
                "used": round(runtime_hours, 2),
                "pct": round(pct, 2),
                "message": f"Runtime budget at {round(pct * 100)}% ({runtime_hours:.1f}h/{plan.budget_max_runtime_hours}h)",
            })

    return {
        "plan_id": str(plan.id),
        "budget_ok": len(violations) == 0,
        "violations": violations,
        "warnings": warnings,
        "usage": {
            "actions": {"used": plan.budget_actions_used, "limit": plan.budget_max_actions},
            "cost_usd": {"used": round(plan.budget_cost_used, 4), "limit": plan.budget_max_cost_usd},
            "runtime_hours": {
                "used": round((now - plan.created_at).total_seconds() / 3600, 2),
                "limit": plan.budget_max_runtime_hours,
            },
        },
    }


def _enforce_budget_before_step(
    db: Session,
    plan: Plan,
    plan_id: uuid.UUID,
) -> Optional[dict]:
    """Check budget before starting a step. If violated, pause the plan.

    Returns None if budget is OK, or the violation dict if paused.
    """
    budget = check_budget(db, plan.tenant_id, plan_id)
    if not budget:
        return None

    # Log warnings
    for w in budget.get("warnings", []):
        _log_event(
            db, plan_id, "budget_warning",
            reason=w["message"],
            metadata_json={"budget": w["budget"], "pct": w.get("pct")},
        )

    # Enforce violations — pause the plan
    if budget["violations"]:
        plan.status = "paused"
        plan.updated_at = datetime.utcnow()
        violation_msg = "; ".join(v["message"] for v in budget["violations"])
        _log_event(
            db, plan_id, "budget_warning",
            previous_status="executing", new_status="paused",
            reason=f"Budget exceeded — paused: {violation_msg}",
            metadata_json={"violations": budget["violations"]},
        )
        return budget

    return None


def record_step_cost(
    db: Session,
    tenant_id: uuid.UUID,
    plan_id: uuid.UUID,
    cost_usd: float,
) -> Optional[Plan]:
    """Record cost incurred by the current step."""
    plan = get_plan(db, tenant_id, plan_id)
    if not plan:
        return None
    plan.budget_cost_used = round(plan.budget_cost_used + cost_usd, 6)
    plan.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(plan)
    return plan


def list_plan_events(
    db: Session,
    tenant_id: uuid.UUID,
    plan_id: uuid.UUID,
    limit: int = 50,
) -> List[PlanEvent]:
    plan = get_plan(db, tenant_id, plan_id)
    if not plan:
        return []
    return (
        db.query(PlanEvent).filter(PlanEvent.plan_id == plan_id)
        .order_by(PlanEvent.created_at.desc()).limit(limit).all()
    )


# ---------------------------------------------------------------------------
# Phase 2: Failure classification, repair policies, and resume
# ---------------------------------------------------------------------------

FAILURE_CLASSES = {
    "transient": "Temporary execution error — safe to retry",
    "missing_info": "Step needs data that is not yet available",
    "invalid_assumption": "A plan assumption has been invalidated",
    "blocked_approval": "Human approval required before continuing",
    "world_state_change": "External state changed, plan may need revision",
}

REPAIR_POLICIES = {
    "transient": "retry",
    "missing_info": "gather_info",
    "invalid_assumption": "replan",
    "blocked_approval": "escalate",
    "world_state_change": "replan",
}

TRANSIENT_PATTERNS = (
    "timeout", "timed out", "connection refused", "503", "502",
    "rate limit", "retry", "temporary", "EAGAIN", "ECONNRESET",
)


def classify_failure(error: str) -> str:
    """Classify a step failure into a failure class."""
    lower = (error or "").lower()
    if any(p in lower for p in TRANSIENT_PATTERNS):
        return "transient"
    if any(p in lower for p in ("not found", "missing", "no data", "unavailable")):
        return "missing_info"
    if any(p in lower for p in ("assumption", "invalidated", "no longer true")):
        return "invalid_assumption"
    if any(p in lower for p in ("approval", "permission", "unauthorized", "require_review")):
        return "blocked_approval"
    if any(p in lower for p in ("changed", "stale", "outdated", "conflict")):
        return "world_state_change"
    return "transient"


def get_repair_action(failure_class: str) -> str:
    """Get the recommended repair action for a failure class."""
    return REPAIR_POLICIES.get(failure_class, "retry")


def handle_step_failure(
    db: Session,
    tenant_id: uuid.UUID,
    plan_id: uuid.UUID,
    error: str,
) -> Optional[dict]:
    """Classify failure, apply repair policy, and return the action taken.

    Returns dict with failure_class, repair_action, and what happened.
    """
    plan = get_plan(db, tenant_id, plan_id)
    if not plan or plan.status != "executing":
        return None

    current_step = (
        db.query(PlanStep).filter(
            PlanStep.plan_id == plan_id,
            PlanStep.step_index == plan.current_step_index,
        ).first()
    )
    if not current_step:
        return None

    failure_class = classify_failure(error)
    repair_action = get_repair_action(failure_class)

    now = datetime.utcnow()
    result = {
        "failure_class": failure_class,
        "failure_description": FAILURE_CLASSES.get(failure_class, "Unknown"),
        "repair_action": repair_action,
        "step_index": current_step.step_index,
        "step_title": current_step.title,
    }

    if repair_action == "retry":
        # Check retry policy
        retry_max = (current_step.retry_policy or {}).get("max_attempts", 3)
        retry_count = (current_step.retry_policy or {}).get("_attempts", 0)
        if retry_count < retry_max:
            # Log the transient failure before retrying
            _log_event(
                db, plan_id, "step_failed",
                previous_status="running", new_status="failed",
                step_id=current_step.id, reason=f"Transient error (will retry): {error}",
                metadata_json={"failure_class": failure_class, "retry_attempt": retry_count},
            )
            current_step.retry_policy = {
                **(current_step.retry_policy or {}),
                "_attempts": retry_count + 1,
            }
            current_step.error = None
            current_step.started_at = now
            # Step goes back to running for the retry
            _log_event(
                db, plan_id, "step_started",
                previous_status="failed", new_status="running",
                step_id=current_step.id, reason=f"Retry {retry_count + 1}/{retry_max}",
                metadata_json={"failure_class": failure_class, "retry_attempt": retry_count + 1},
            )
            result["action_taken"] = f"retry ({retry_count + 1}/{retry_max})"
        else:
            # Max retries exhausted — fail the step
            current_step.status = "failed"
            current_step.error = error
            current_step.completed_at = now

            # Try fallback if available
            if current_step.fallback_step_index is not None:
                result = _apply_fallback(db, plan, current_step, error, failure_class)
            else:
                plan.status = "failed"
                _log_event(db, plan_id, "step_failed", step_id=current_step.id, reason=error)
                _log_event(db, plan_id, "failed", previous_status="executing", new_status="failed",
                           reason=f"Max retries exhausted: {error}")
                result["action_taken"] = "failed (max retries exhausted, no fallback)"

    elif repair_action == "gather_info":
        plan.status = "paused"
        current_step.status = "failed"
        current_step.error = error
        current_step.completed_at = now
        _log_event(db, plan_id, "step_failed", step_id=current_step.id, reason=error)
        _log_event(db, plan_id, "paused", previous_status="executing", new_status="paused",
                   reason=f"Missing information: {error}",
                   metadata_json={"failure_class": failure_class})
        result["action_taken"] = "paused (awaiting missing information)"

    elif repair_action == "escalate":
        plan.status = "paused"
        current_step.status = "failed"
        current_step.error = error
        current_step.completed_at = now
        _log_event(db, plan_id, "step_failed", step_id=current_step.id, reason=error)
        _log_event(db, plan_id, "paused", previous_status="executing", new_status="paused",
                   reason=f"Approval required: {error}",
                   metadata_json={"failure_class": failure_class})
        result["action_taken"] = "paused (escalated for approval)"

    elif repair_action == "replan":
        current_step.status = "failed"
        current_step.error = error
        current_step.completed_at = now
        plan.status = "paused"
        plan.replan_count += 1
        _log_event(db, plan_id, "step_failed", step_id=current_step.id, reason=error)
        _log_event(db, plan_id, "replanned", previous_status="executing", new_status="paused",
                   reason=f"Replan needed ({failure_class}): {error}",
                   metadata_json={"failure_class": failure_class, "replan_count": plan.replan_count})
        result["action_taken"] = f"paused for replanning (replan #{plan.replan_count})"

    plan.updated_at = now
    db.commit()
    return result


def _apply_fallback(
    db: Session,
    plan: Plan,
    failed_step: PlanStep,
    error: str,
    failure_class: str,
) -> dict:
    """Jump to the fallback step when a step fails."""
    fallback = (
        db.query(PlanStep).filter(
            PlanStep.plan_id == plan.id,
            PlanStep.step_index == failed_step.fallback_step_index,
        ).first()
    )
    if not fallback:
        plan.status = "failed"
        _log_event(db, plan.id, "failed", previous_status="executing", new_status="failed",
                   reason=f"Fallback step {failed_step.fallback_step_index} not found")
        return {
            "failure_class": failure_class,
            "repair_action": "fallback",
            "action_taken": f"failed (fallback step {failed_step.fallback_step_index} not found)",
        }

    now = datetime.utcnow()
    plan.current_step_index = fallback.step_index
    fallback.status = "running"
    fallback.started_at = now
    fallback.completed_at = None
    fallback.error = None
    fallback.output = None
    if fallback.retry_policy and "_attempts" in (fallback.retry_policy or {}):
        fallback.retry_policy = {k: v for k, v in fallback.retry_policy.items() if k != "_attempts"}

    _log_event(db, plan.id, "step_failed", step_id=failed_step.id, reason=error)
    _log_event(db, plan.id, "step_started", new_status="running", step_id=fallback.id,
               reason=f"Fallback from step {failed_step.step_index}",
               metadata_json={"failure_class": failure_class, "fallback_from": failed_step.step_index})

    return {
        "failure_class": failure_class,
        "repair_action": "fallback",
        "action_taken": f"jumped to fallback step {fallback.step_index}: {fallback.title}",
        "fallback_step_index": fallback.step_index,
    }


def resume_plan(
    db: Session,
    tenant_id: uuid.UUID,
    plan_id: uuid.UUID,
    from_step_index: Optional[int] = None,
) -> Optional[dict]:
    """Resume a paused or failed plan from the last confirmed step or a specific step.

    If from_step_index is None, resumes from the first non-completed step.
    """
    plan = get_plan(db, tenant_id, plan_id)
    if not plan or plan.status not in ("paused", "failed"):
        return None

    now = datetime.utcnow()

    if from_step_index is not None:
        resume_step = (
            db.query(PlanStep).filter(
                PlanStep.plan_id == plan_id,
                PlanStep.step_index == from_step_index,
            ).first()
        )
    else:
        # Resume from current_step_index (the plan's execution pointer).
        # This is correct after fallback jumps — current_step_index tracks
        # where execution actually was, not just step ordering.
        resume_step = (
            db.query(PlanStep).filter(
                PlanStep.plan_id == plan_id,
                PlanStep.step_index == plan.current_step_index,
            ).first()
        )
        # If current step is already completed, find the next non-completed one
        if resume_step and resume_step.status == "completed":
            resume_step = (
                db.query(PlanStep).filter(
                    PlanStep.plan_id == plan_id,
                    PlanStep.step_index > plan.current_step_index,
                    PlanStep.status.in_(["pending", "failed"]),
                )
                .order_by(PlanStep.step_index.asc())
                .first()
            )

    if not resume_step:
        return None

    # Check budget before resuming — don't restart an over-budget plan
    budget = check_budget(db, tenant_id, plan_id)
    if budget and budget["violations"]:
        violation_msg = "; ".join(v["message"] for v in budget["violations"])
        return {
            "error": "budget_exceeded",
            "message": f"Cannot resume: {violation_msg}",
            "violations": budget["violations"],
        }

    # Reset the step for re-execution (full reset including retry budget)
    plan_old_status = plan.status
    resume_step.status = "running"
    resume_step.started_at = now
    resume_step.error = None
    resume_step.output = None
    if resume_step.retry_policy and "_attempts" in (resume_step.retry_policy or {}):
        resume_step.retry_policy = {k: v for k, v in resume_step.retry_policy.items() if k != "_attempts"}
    resume_step.completed_at = None

    plan.status = "executing"
    plan.current_step_index = resume_step.step_index
    plan.updated_at = now

    _log_event(
        db, plan_id, "resumed",
        previous_status=plan_old_status, new_status="executing",
        step_id=resume_step.id,
        reason=f"Resumed from step {resume_step.step_index}",
        metadata_json={"from_step_index": resume_step.step_index},
    )
    _log_event(
        db, plan_id, "step_started",
        new_status="running", step_id=resume_step.id,
        metadata_json={"step_index": resume_step.step_index, "resumed": True},
    )

    db.commit()
    return {
        "resumed_from_step": resume_step.step_index,
        "step_title": resume_step.title,
        "plan_status": "executing",
    }
