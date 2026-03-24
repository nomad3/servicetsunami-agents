"""Service layer for shared blackboard collaboration."""

from datetime import datetime
from typing import List, Optional
import uuid

from sqlalchemy.orm import Session

from app.models.blackboard import Blackboard, BlackboardEntry
from app.schemas.blackboard import BlackboardCreate, BlackboardEntryCreate


AUTHORITY_HIERARCHY = {
    "auditor": 6,
    "synthesizer": 5,
    "verifier": 4,
    "critic": 3,
    "executor": 2,
    "planner": 2,
    "researcher": 1,
    "contributor": 0,
}


def create_blackboard(
    db: Session,
    tenant_id: uuid.UUID,
    board_in: BlackboardCreate,
) -> Blackboard:
    board = Blackboard(
        tenant_id=tenant_id,
        title=board_in.title,
        plan_id=board_in.plan_id,
        goal_id=board_in.goal_id,
        status="active",
        version=0,
    )
    db.add(board)
    db.commit()
    db.refresh(board)
    return board


def get_blackboard(
    db: Session,
    tenant_id: uuid.UUID,
    board_id: uuid.UUID,
) -> Optional[Blackboard]:
    return (
        db.query(Blackboard)
        .filter(Blackboard.id == board_id, Blackboard.tenant_id == tenant_id)
        .first()
    )


def list_blackboards(
    db: Session,
    tenant_id: uuid.UUID,
    status: Optional[str] = None,
    limit: int = 50,
) -> List[Blackboard]:
    q = db.query(Blackboard).filter(Blackboard.tenant_id == tenant_id)
    if status:
        q = q.filter(Blackboard.status == status)
    return q.order_by(Blackboard.updated_at.desc()).limit(limit).all()


def get_blackboard_detail(
    db: Session,
    tenant_id: uuid.UUID,
    board_id: uuid.UUID,
) -> Optional[dict]:
    board = get_blackboard(db, tenant_id, board_id)
    if not board:
        return None
    entries = (
        db.query(BlackboardEntry)
        .filter(BlackboardEntry.blackboard_id == board_id)
        .order_by(BlackboardEntry.board_version.asc(), BlackboardEntry.created_at.asc())
        .all()
    )
    return {"board": board, "entries": entries}


def add_entry(
    db: Session,
    tenant_id: uuid.UUID,
    board_id: uuid.UUID,
    entry_in: BlackboardEntryCreate,
) -> Optional[BlackboardEntry]:
    """Append an entry to the blackboard. Increments board version."""
    board = get_blackboard(db, tenant_id, board_id)
    if not board or board.status != "active":
        return None

    # Validate parent entry belongs to same blackboard
    if entry_in.parent_entry_id:
        parent = db.query(BlackboardEntry).filter(
            BlackboardEntry.id == entry_in.parent_entry_id,
            BlackboardEntry.blackboard_id == board_id,
        ).first()
        if not parent:
            raise ValueError(f"Parent entry {entry_in.parent_entry_id} not found on this blackboard")

    # Validate supersedes entry belongs to same blackboard
    if entry_in.supersedes_entry_id:
        superseded = db.query(BlackboardEntry).filter(
            BlackboardEntry.id == entry_in.supersedes_entry_id,
            BlackboardEntry.blackboard_id == board_id,
        ).first()
        if not superseded:
            raise ValueError(f"Superseded entry {entry_in.supersedes_entry_id} not found on this blackboard")
        superseded.status = "superseded"

    board.version += 1
    board.updated_at = datetime.utcnow()

    entry = BlackboardEntry(
        blackboard_id=board_id,
        board_version=board.version,
        entry_type=entry_in.entry_type.value,
        content=entry_in.content,
        evidence=entry_in.evidence,
        confidence=entry_in.confidence,
        author_agent_slug=entry_in.author_agent_slug,
        author_role=entry_in.author_role.value,
        parent_entry_id=entry_in.parent_entry_id,
        supersedes_entry_id=entry_in.supersedes_entry_id,
        status="proposed",
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def resolve_entry(
    db: Session,
    tenant_id: uuid.UUID,
    board_id: uuid.UUID,
    entry_id: uuid.UUID,
    resolution_status: str,
    resolved_by_agent: str,
    resolved_by_role: str = "contributor",
    resolution_reason: Optional[str] = None,
) -> Optional[BlackboardEntry]:
    """Resolve an entry. Only the owner or a higher-authority role can resolve."""
    board = get_blackboard(db, tenant_id, board_id)
    if not board:
        return None

    entry = db.query(BlackboardEntry).filter(
        BlackboardEntry.id == entry_id,
        BlackboardEntry.blackboard_id == board_id,
    ).first()
    if not entry:
        return None

    # Authority check: resolver must have >= authority of the entry author
    resolver_authority = AUTHORITY_HIERARCHY.get(resolved_by_role, 0)
    author_authority = AUTHORITY_HIERARCHY.get(entry.author_role, 0)
    if resolver_authority < author_authority and resolved_by_agent != entry.author_agent_slug:
        raise ValueError(
            f"Agent '{resolved_by_agent}' (role={resolved_by_role}) lacks authority "
            f"to resolve entry by '{entry.author_agent_slug}' (role={entry.author_role})"
        )

    entry.status = resolution_status
    entry.resolved_by_agent = resolved_by_agent
    entry.resolution_reason = resolution_reason

    board.version += 1
    board.updated_at = datetime.utcnow()

    db.commit()
    db.refresh(entry)
    return entry


def get_active_entries(
    db: Session,
    tenant_id: uuid.UUID,
    board_id: uuid.UUID,
    entry_type: Optional[str] = None,
) -> List[BlackboardEntry]:
    """Get non-superseded, non-resolved entries (the current working state)."""
    board = get_blackboard(db, tenant_id, board_id)
    if not board:
        return []
    q = (
        db.query(BlackboardEntry)
        .filter(
            BlackboardEntry.blackboard_id == board_id,
            BlackboardEntry.status.in_(["proposed", "accepted", "disputed"]),
        )
    )
    if entry_type:
        q = q.filter(BlackboardEntry.entry_type == entry_type)
    return q.order_by(BlackboardEntry.board_version.asc()).all()


def get_disagreements(
    db: Session,
    tenant_id: uuid.UUID,
    board_id: uuid.UUID,
) -> List[BlackboardEntry]:
    """Get unresolved disagreements on the blackboard."""
    board = get_blackboard(db, tenant_id, board_id)
    if not board:
        return []
    return (
        db.query(BlackboardEntry)
        .filter(
            BlackboardEntry.blackboard_id == board_id,
            BlackboardEntry.entry_type == "disagreement",
            BlackboardEntry.status.in_(["proposed", "disputed"]),
        )
        .order_by(BlackboardEntry.created_at.asc())
        .all()
    )


def get_version_diff(
    db: Session,
    tenant_id: uuid.UUID,
    board_id: uuid.UUID,
    from_version: int,
    to_version: Optional[int] = None,
) -> List[BlackboardEntry]:
    """Get entries added between two versions for replay/diff."""
    board = get_blackboard(db, tenant_id, board_id)
    if not board:
        return []
    q = db.query(BlackboardEntry).filter(
        BlackboardEntry.blackboard_id == board_id,
        BlackboardEntry.board_version > from_version,
    )
    if to_version is not None:
        q = q.filter(BlackboardEntry.board_version <= to_version)
    return q.order_by(BlackboardEntry.board_version.asc(), BlackboardEntry.created_at.asc()).all()
