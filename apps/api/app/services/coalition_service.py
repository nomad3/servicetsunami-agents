"""Service layer for coalition routing — learned team shape selection."""

from datetime import datetime
from typing import Dict, List, Optional
import uuid

from sqlalchemy.orm import Session

from app.models.coalition import CoalitionTemplate, CoalitionOutcome
from app.schemas.coalition import CoalitionTemplateCreate, CoalitionOutcomeCreate


def _validate_pattern_and_roles(pattern: str, role_agent_map: Dict) -> None:
    """Validate the pattern exists and all required roles are assigned."""
    from app.schemas.collaboration import PATTERN_PHASES, PHASE_REQUIRED_ROLES

    phases = PATTERN_PHASES.get(pattern)
    if not phases:
        valid = ", ".join(PATTERN_PHASES.keys())
        raise ValueError(f"Unknown pattern '{pattern}'. Valid patterns: {valid}")

    required_roles = set()
    for phase in phases:
        required_roles.update(PHASE_REQUIRED_ROLES.get(phase, []))

    assigned_roles = set(role_agent_map.keys())
    missing = required_roles - assigned_roles
    if missing:
        raise ValueError(
            f"Pattern '{pattern}' requires roles: {sorted(missing)}. "
            f"Provide them in role_agent_map."
        )


def create_template(
    db: Session,
    tenant_id: uuid.UUID,
    template_in: CoalitionTemplateCreate,
) -> CoalitionTemplate:
    _validate_pattern_and_roles(template_in.pattern, template_in.role_agent_map)

    template = CoalitionTemplate(
        tenant_id=tenant_id,
        name=template_in.name,
        description=template_in.description,
        pattern=template_in.pattern,
        role_agent_map=template_in.role_agent_map,
        task_types=template_in.task_types,
    )
    db.add(template)
    db.commit()
    db.refresh(template)
    return template


def get_template(
    db: Session,
    tenant_id: uuid.UUID,
    template_id: uuid.UUID,
) -> Optional[CoalitionTemplate]:
    return (
        db.query(CoalitionTemplate)
        .filter(CoalitionTemplate.id == template_id, CoalitionTemplate.tenant_id == tenant_id)
        .first()
    )


def list_templates(
    db: Session,
    tenant_id: uuid.UUID,
    task_type: Optional[str] = None,
    limit: int = 50,
) -> List[CoalitionTemplate]:
    q = (
        db.query(CoalitionTemplate)
        .filter(CoalitionTemplate.tenant_id == tenant_id, CoalitionTemplate.status == "active")
    )
    if task_type:
        # Include templates that match the task type OR have no type restriction (wildcard)
        from sqlalchemy import or_
        q = q.filter(or_(
            CoalitionTemplate.task_types.contains([task_type]),
            CoalitionTemplate.task_types == [],
        ))
    return q.order_by(CoalitionTemplate.avg_quality_score.desc()).limit(limit).all()


def record_outcome(
    db: Session,
    tenant_id: uuid.UUID,
    outcome_in: CoalitionOutcomeCreate,
) -> CoalitionOutcome:
    """Record a coalition outcome and update the template's aggregate stats.

    When linked to a template, the outcome's pattern and role_agent_map must
    match the template to prevent stat pollution.
    """
    # Validate template ref and enforce consistency
    if outcome_in.template_id:
        template = get_template(db, tenant_id, outcome_in.template_id)
        if not template:
            raise ValueError(f"Coalition template {outcome_in.template_id} not found in this tenant")
        if outcome_in.pattern != template.pattern:
            raise ValueError(
                f"Outcome pattern '{outcome_in.pattern}' does not match "
                f"template pattern '{template.pattern}'"
            )
        if outcome_in.role_agent_map and outcome_in.role_agent_map != template.role_agent_map:
            raise ValueError(
                f"Outcome role_agent_map does not match template. "
                f"Template: {template.role_agent_map}"
            )

    # Validate collaboration ref if provided
    if outcome_in.collaboration_id:
        from app.models.collaboration import CollaborationSession
        collab = (
            db.query(CollaborationSession)
            .filter(
                CollaborationSession.id == outcome_in.collaboration_id,
                CollaborationSession.tenant_id == tenant_id,
            ).first()
        )
        if not collab:
            raise ValueError(f"Collaboration {outcome_in.collaboration_id} not found in this tenant")
        if outcome_in.pattern != collab.pattern:
            raise ValueError(
                f"Outcome pattern '{outcome_in.pattern}' does not match "
                f"collaboration pattern '{collab.pattern}'"
            )
        if outcome_in.role_agent_map and collab.role_assignments:
            if outcome_in.role_agent_map != collab.role_assignments:
                raise ValueError(
                    f"Outcome role_agent_map does not match collaboration's "
                    f"role_assignments: {collab.role_assignments}"
                )

    outcome = CoalitionOutcome(
        tenant_id=tenant_id,
        template_id=outcome_in.template_id,
        collaboration_id=outcome_in.collaboration_id,
        task_type=outcome_in.task_type,
        pattern=outcome_in.pattern,
        role_agent_map=outcome_in.role_agent_map,
        success=outcome_in.success,
        quality_score=outcome_in.quality_score,
        rounds_completed=outcome_in.rounds_completed,
        consensus_reached=outcome_in.consensus_reached,
        cost_usd=outcome_in.cost_usd,
        duration_seconds=outcome_in.duration_seconds,
    )
    db.add(outcome)

    # Update template stats if linked
    if outcome_in.template_id:
        template = get_template(db, tenant_id, outcome_in.template_id)
        if template:
            _update_template_stats(db, template, outcome_in)

    db.commit()
    db.refresh(outcome)
    return outcome


def _update_template_stats(
    db: Session,
    template: CoalitionTemplate,
    outcome: CoalitionOutcomeCreate,
) -> None:
    """Incrementally update a template's aggregate performance stats."""
    n = template.total_uses
    template.total_uses = n + 1
    if outcome.success == "yes":
        template.success_count += 1

    # Running average for quality score
    if outcome.quality_score is not None:
        template.avg_quality_score = (
            (template.avg_quality_score * n + outcome.quality_score) / (n + 1)
        )

    # Running average for rounds
    template.avg_rounds_to_consensus = (
        (template.avg_rounds_to_consensus * n + outcome.rounds_completed) / (n + 1)
    )

    # Running average for cost
    template.avg_cost_usd = (
        (template.avg_cost_usd * n + outcome.cost_usd) / (n + 1)
    )

    template.updated_at = datetime.utcnow()


def _compute_task_type_stats(
    db: Session,
    tenant_id: uuid.UUID,
    template_id: uuid.UUID,
    task_type: str,
) -> Dict:
    """Compute performance stats for a template filtered by task_type."""
    outcomes = (
        db.query(CoalitionOutcome)
        .filter(
            CoalitionOutcome.tenant_id == tenant_id,
            CoalitionOutcome.template_id == template_id,
            CoalitionOutcome.task_type == task_type,
        )
        .all()
    )
    if not outcomes:
        return {"total": 0, "success_count": 0, "avg_quality": 0.0, "avg_cost": 0.0, "avg_rounds": 0.0}

    total = len(outcomes)
    success_count = sum(1 for o in outcomes if o.success == "yes")
    qualities = [o.quality_score for o in outcomes if o.quality_score is not None]
    avg_quality = sum(qualities) / len(qualities) if qualities else 0.0
    avg_cost = sum(o.cost_usd for o in outcomes) / total
    avg_rounds = sum(o.rounds_completed for o in outcomes) / total

    return {
        "total": total,
        "success_count": success_count,
        "avg_quality": avg_quality,
        "avg_cost": avg_cost,
        "avg_rounds": avg_rounds,
    }


def recommend_coalition(
    db: Session,
    tenant_id: uuid.UUID,
    task_type: str,
    min_uses: int = 2,
) -> List[Dict]:
    """Recommend the best coalition template for a task type based on historical outcomes.

    Stats are computed per task_type from coalition_outcomes, not from
    global template aggregates. This ensures a coalition's performance on
    sales work doesn't influence its recommendation for code work.
    """
    templates = (
        db.query(CoalitionTemplate)
        .filter(
            CoalitionTemplate.tenant_id == tenant_id,
            CoalitionTemplate.status == "active",
        )
        .all()
    )

    # Filter to templates that match the task type (or have no type restriction)
    candidates = []
    for t in templates:
        if not t.task_types or task_type in t.task_types:
            stats = _compute_task_type_stats(db, tenant_id, t.id, task_type)
            if stats["total"] >= min_uses:
                candidates.append((t, stats))

    if not candidates:
        return []

    # Score each candidate using per-task-type stats
    max_quality = max(s["avg_quality"] for _, s in candidates) or 1.0
    max_cost = max(s["avg_cost"] for _, s in candidates) or 1.0

    scored = []
    for t, stats in candidates:
        success_rate = stats["success_count"] / max(stats["total"], 1)
        norm_quality = stats["avg_quality"] / max_quality if max_quality > 0 else 0
        cost_efficiency = 1.0 - (stats["avg_cost"] / max_cost) if max_cost > 0 else 1.0

        score = success_rate * 0.5 + norm_quality * 0.3 + cost_efficiency * 0.2

        scored.append({
            "template_id": str(t.id),
            "name": t.name,
            "pattern": t.pattern,
            "role_agent_map": t.role_agent_map,
            "score": round(score, 3),
            "reasoning": (
                f"success={success_rate:.0%}, quality={stats['avg_quality']:.1f}, "
                f"cost=${stats['avg_cost']:.3f}, rounds={stats['avg_rounds']:.1f} "
                f"(task_type={task_type}, {stats['total']} outcomes)"
            ),
            "total_uses": stats["total"],
            "success_rate": round(success_rate, 3),
            "avg_quality": round(stats["avg_quality"], 2),
        })

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:5]


def list_outcomes(
    db: Session,
    tenant_id: uuid.UUID,
    task_type: Optional[str] = None,
    template_id: Optional[uuid.UUID] = None,
    limit: int = 50,
) -> List[CoalitionOutcome]:
    q = db.query(CoalitionOutcome).filter(CoalitionOutcome.tenant_id == tenant_id)
    if task_type:
        q = q.filter(CoalitionOutcome.task_type == task_type)
    if template_id:
        q = q.filter(CoalitionOutcome.template_id == template_id)
    return q.order_by(CoalitionOutcome.created_at.desc()).limit(limit).all()
