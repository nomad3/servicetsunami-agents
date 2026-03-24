"""Service layer for commitment records with tenant isolation."""

from datetime import datetime
from typing import List, Optional
import uuid

from sqlalchemy.orm import Session

from app.models.commitment_record import CommitmentRecord
from app.schemas.commitment_record import (
    CommitmentRecordCreate,
    CommitmentRecordUpdate,
    CommitmentState,
)


def _validate_goal_ref(
    db: Session,
    tenant_id: uuid.UUID,
    goal_id: Optional[uuid.UUID],
) -> None:
    if not goal_id:
        return
    from app.models.goal_record import GoalRecord
    goal = (
        db.query(GoalRecord)
        .filter(GoalRecord.id == goal_id, GoalRecord.tenant_id == tenant_id)
        .first()
    )
    if not goal:
        raise ValueError(f"Goal {goal_id} not found in this tenant")


def create_commitment(
    db: Session,
    tenant_id: uuid.UUID,
    commitment_in: CommitmentRecordCreate,
    created_by: Optional[uuid.UUID] = None,
) -> CommitmentRecord:
    _validate_goal_ref(db, tenant_id, commitment_in.goal_id)
    commitment = CommitmentRecord(
        tenant_id=tenant_id,
        owner_agent_slug=commitment_in.owner_agent_slug,
        created_by=created_by,
        title=commitment_in.title,
        description=commitment_in.description,
        commitment_type=commitment_in.commitment_type.value,
        priority=commitment_in.priority.value,
        state="open",
        source_type=commitment_in.source_type.value,
        source_ref=commitment_in.source_ref,
        due_at=commitment_in.due_at,
        goal_id=commitment_in.goal_id,
        related_entity_ids=commitment_in.related_entity_ids,
    )
    db.add(commitment)
    db.commit()
    db.refresh(commitment)
    return commitment


def get_commitment(
    db: Session,
    tenant_id: uuid.UUID,
    commitment_id: uuid.UUID,
) -> Optional[CommitmentRecord]:
    return (
        db.query(CommitmentRecord)
        .filter(
            CommitmentRecord.id == commitment_id,
            CommitmentRecord.tenant_id == tenant_id,
        )
        .first()
    )


def list_commitments(
    db: Session,
    tenant_id: uuid.UUID,
    owner_agent_slug: Optional[str] = None,
    state: Optional[str] = None,
    goal_id: Optional[uuid.UUID] = None,
    limit: int = 100,
) -> List[CommitmentRecord]:
    q = db.query(CommitmentRecord).filter(CommitmentRecord.tenant_id == tenant_id)
    if owner_agent_slug:
        q = q.filter(CommitmentRecord.owner_agent_slug == owner_agent_slug)
    if state:
        q = q.filter(CommitmentRecord.state == state)
    if goal_id:
        q = q.filter(CommitmentRecord.goal_id == goal_id)
    return q.order_by(CommitmentRecord.created_at.desc()).limit(limit).all()


def update_commitment(
    db: Session,
    tenant_id: uuid.UUID,
    commitment_id: uuid.UUID,
    commitment_in: CommitmentRecordUpdate,
) -> Optional[CommitmentRecord]:
    commitment = get_commitment(db, tenant_id, commitment_id)
    if not commitment:
        return None

    update_data = commitment_in.model_dump(exclude_unset=True)

    if "goal_id" in update_data:
        _validate_goal_ref(db, tenant_id, update_data["goal_id"])

    if "state" in update_data:
        new_state = update_data["state"]
        if isinstance(new_state, CommitmentState):
            new_state = new_state.value
        update_data["state"] = new_state

        if new_state == "fulfilled":
            update_data["fulfilled_at"] = datetime.utcnow()
            update_data["broken_at"] = None
            update_data["broken_reason"] = None
        elif new_state == "broken":
            update_data["broken_at"] = datetime.utcnow()
            update_data["fulfilled_at"] = None
        elif new_state in ("open", "in_progress"):
            update_data["fulfilled_at"] = None
            update_data["broken_at"] = None
            update_data["broken_reason"] = None
        elif new_state == "cancelled":
            update_data["fulfilled_at"] = None
            update_data["broken_at"] = None

    for key, value in update_data.items():
        if hasattr(commitment, key):
            setattr(commitment, key, value)

    commitment.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(commitment)
    return commitment


def delete_commitment(
    db: Session,
    tenant_id: uuid.UUID,
    commitment_id: uuid.UUID,
) -> bool:
    commitment = get_commitment(db, tenant_id, commitment_id)
    if not commitment:
        return False
    db.delete(commitment)
    db.commit()
    return True


def list_open_commitments_for_agent(
    db: Session,
    tenant_id: uuid.UUID,
    agent_slug: str,
) -> List[CommitmentRecord]:
    """Load open and in-progress commitments for runtime injection."""
    return (
        db.query(CommitmentRecord)
        .filter(
            CommitmentRecord.tenant_id == tenant_id,
            CommitmentRecord.owner_agent_slug == agent_slug,
            CommitmentRecord.state.in_(["open", "in_progress"]),
        )
        .order_by(CommitmentRecord.due_at.asc().nullslast(), CommitmentRecord.created_at.asc())
        .all()
    )


def list_overdue_commitments(
    db: Session,
    tenant_id: uuid.UUID,
    owner_agent_slug: Optional[str] = None,
) -> List[CommitmentRecord]:
    """Find commitments past their due date that are still open."""
    q = (
        db.query(CommitmentRecord)
        .filter(
            CommitmentRecord.tenant_id == tenant_id,
            CommitmentRecord.state.in_(["open", "in_progress"]),
            CommitmentRecord.due_at.isnot(None),
            CommitmentRecord.due_at < datetime.utcnow(),
        )
    )
    if owner_agent_slug:
        q = q.filter(CommitmentRecord.owner_agent_slug == owner_agent_slug)
    return q.order_by(CommitmentRecord.due_at.asc()).all()
