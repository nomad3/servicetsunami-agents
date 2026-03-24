"""Service layer for goal records with tenant isolation."""

from datetime import datetime
from typing import List, Optional
import uuid

from sqlalchemy.orm import Session

from app.models.goal_record import GoalRecord
from app.schemas.goal_record import GoalRecordCreate, GoalRecordUpdate, GoalState


def _validate_parent_goal(
    db: Session,
    tenant_id: uuid.UUID,
    parent_goal_id: Optional[uuid.UUID],
) -> None:
    if not parent_goal_id:
        return
    parent = get_goal(db, tenant_id, parent_goal_id)
    if not parent:
        raise ValueError(f"Parent goal {parent_goal_id} not found in this tenant")


def create_goal(
    db: Session,
    tenant_id: uuid.UUID,
    goal_in: GoalRecordCreate,
    created_by: Optional[uuid.UUID] = None,
) -> GoalRecord:
    _validate_parent_goal(db, tenant_id, goal_in.parent_goal_id)
    goal = GoalRecord(
        tenant_id=tenant_id,
        owner_agent_slug=goal_in.owner_agent_slug,
        created_by=created_by,
        title=goal_in.title,
        description=goal_in.description,
        objective_type=goal_in.objective_type.value,
        priority=goal_in.priority.value,
        state="proposed",
        success_criteria=goal_in.success_criteria,
        deadline=goal_in.deadline,
        related_entity_ids=goal_in.related_entity_ids,
        parent_goal_id=goal_in.parent_goal_id,
    )
    db.add(goal)
    db.commit()
    db.refresh(goal)
    return goal


def get_goal(
    db: Session,
    tenant_id: uuid.UUID,
    goal_id: uuid.UUID,
) -> Optional[GoalRecord]:
    return (
        db.query(GoalRecord)
        .filter(GoalRecord.id == goal_id, GoalRecord.tenant_id == tenant_id)
        .first()
    )


def list_goals(
    db: Session,
    tenant_id: uuid.UUID,
    owner_agent_slug: Optional[str] = None,
    state: Optional[str] = None,
    limit: int = 100,
) -> List[GoalRecord]:
    q = db.query(GoalRecord).filter(GoalRecord.tenant_id == tenant_id)
    if owner_agent_slug:
        q = q.filter(GoalRecord.owner_agent_slug == owner_agent_slug)
    if state:
        q = q.filter(GoalRecord.state == state)
    return q.order_by(GoalRecord.created_at.desc()).limit(limit).all()


def update_goal(
    db: Session,
    tenant_id: uuid.UUID,
    goal_id: uuid.UUID,
    goal_in: GoalRecordUpdate,
) -> Optional[GoalRecord]:
    goal = get_goal(db, tenant_id, goal_id)
    if not goal:
        return None

    update_data = goal_in.model_dump(exclude_unset=True)

    if "parent_goal_id" in update_data:
        _validate_parent_goal(db, tenant_id, update_data["parent_goal_id"])

    if "state" in update_data:
        new_state = update_data["state"]
        if isinstance(new_state, GoalState):
            new_state = new_state.value
        update_data["state"] = new_state

        if new_state == "completed":
            update_data["completed_at"] = datetime.utcnow()
            update_data["progress_pct"] = 100
            update_data["abandoned_at"] = None
            update_data["abandoned_reason"] = None
        elif new_state == "abandoned":
            update_data["abandoned_at"] = datetime.utcnow()
            update_data["completed_at"] = None
        elif new_state in ("proposed", "active", "blocked"):
            update_data["completed_at"] = None
            update_data["abandoned_at"] = None
            update_data["abandoned_reason"] = None

    for key, value in update_data.items():
        if hasattr(goal, key):
            setattr(goal, key, value)

    goal.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(goal)
    return goal


def delete_goal(
    db: Session,
    tenant_id: uuid.UUID,
    goal_id: uuid.UUID,
) -> bool:
    from app.models.commitment_record import CommitmentRecord

    goal = get_goal(db, tenant_id, goal_id)
    if not goal:
        return False
    # Nullify child goal parent refs
    db.query(GoalRecord).filter(
        GoalRecord.parent_goal_id == goal_id,
        GoalRecord.tenant_id == tenant_id,
    ).update({"parent_goal_id": None})
    # Nullify linked commitment refs
    db.query(CommitmentRecord).filter(
        CommitmentRecord.goal_id == goal_id,
        CommitmentRecord.tenant_id == tenant_id,
    ).update({"goal_id": None})
    db.delete(goal)
    db.commit()
    return True


def list_active_goals_for_agent(
    db: Session,
    tenant_id: uuid.UUID,
    agent_slug: str,
) -> List[GoalRecord]:
    """Load active and proposed goals for runtime injection."""
    return (
        db.query(GoalRecord)
        .filter(
            GoalRecord.tenant_id == tenant_id,
            GoalRecord.owner_agent_slug == agent_slug,
            GoalRecord.state.in_(["proposed", "active", "blocked"]),
        )
        .order_by(GoalRecord.priority.asc(), GoalRecord.created_at.asc())
        .all()
    )
