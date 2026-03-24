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

        # When transitioning to executing, start the first step
        if new_status == "executing" and old_status != "executing":
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
