"""Service layer for shared blackboard collaboration."""

from datetime import datetime
from typing import List, Optional
import uuid

from sqlalchemy import text
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


def _validate_plan_ref(db: Session, tenant_id: uuid.UUID, plan_id: Optional[uuid.UUID]) -> None:
    if not plan_id:
        return
    from app.models.plan import Plan
    exists = db.query(Plan).filter(Plan.id == plan_id, Plan.tenant_id == tenant_id).first()
    if not exists:
        raise ValueError(f"Plan {plan_id} not found in this tenant")


def _validate_goal_ref(db: Session, tenant_id: uuid.UUID, goal_id: Optional[uuid.UUID]) -> None:
    if not goal_id:
        return
    from app.models.goal_record import GoalRecord
    exists = db.query(GoalRecord).filter(GoalRecord.id == goal_id, GoalRecord.tenant_id == tenant_id).first()
    if not exists:
        raise ValueError(f"Goal {goal_id} not found in this tenant")


def _next_version(db: Session, board_id: uuid.UUID) -> int:
    """Atomically increment and return the next board version using a row lock."""
    row = db.execute(
        text("UPDATE blackboards SET version = version + 1, updated_at = NOW() "
             "WHERE id = CAST(:bid AS uuid) RETURNING version"),
        {"bid": str(board_id)},
    ).fetchone()
    return row.version if row else 0


def create_blackboard(
    db: Session,
    tenant_id: uuid.UUID,
    board_in: BlackboardCreate,
) -> Blackboard:
    _validate_plan_ref(db, tenant_id, board_in.plan_id)
    _validate_goal_ref(db, tenant_id, board_in.goal_id)

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
    """Append an entry to the blackboard. Version assigned atomically."""
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
    # Record supersession as a NEW entry (append-only), don't mutate the old row
    if entry_in.supersedes_entry_id:
        superseded = db.query(BlackboardEntry).filter(
            BlackboardEntry.id == entry_in.supersedes_entry_id,
            BlackboardEntry.blackboard_id == board_id,
        ).first()
        if not superseded:
            raise ValueError(f"Superseded entry {entry_in.supersedes_entry_id} not found on this blackboard")

    # Atomically get next version (row-level lock on blackboards row)
    new_version = _next_version(db, board_id)

    entry = BlackboardEntry(
        blackboard_id=board_id,
        board_version=new_version,
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
    authenticated_user_id: Optional[uuid.UUID] = None,
) -> Optional[BlackboardEntry]:
    """Resolve an entry by appending a resolution entry (append-only).

    Authority check: resolver must have >= authority of the entry author,
    OR be the original author. The authenticated_user_id is recorded for
    audit since agent identity is currently self-declared.
    """
    board = get_blackboard(db, tenant_id, board_id)
    if not board:
        return None

    entry = db.query(BlackboardEntry).filter(
        BlackboardEntry.id == entry_id,
        BlackboardEntry.blackboard_id == board_id,
    ).first()
    if not entry:
        return None

    # Authority check
    resolver_authority = AUTHORITY_HIERARCHY.get(resolved_by_role, 0)
    author_authority = AUTHORITY_HIERARCHY.get(entry.author_role, 0)
    if resolver_authority < author_authority and resolved_by_agent != entry.author_agent_slug:
        raise ValueError(
            f"Agent '{resolved_by_agent}' (role={resolved_by_role}) lacks authority "
            f"to resolve entry by '{entry.author_agent_slug}' (role={entry.author_role})"
        )

    # Append-only: create a resolution entry rather than mutating the original
    new_version = _next_version(db, board_id)

    resolution_entry = BlackboardEntry(
        blackboard_id=board_id,
        board_version=new_version,
        entry_type="resolution",
        content=f"Resolved entry to '{resolution_status}': {resolution_reason or 'no reason given'}",
        evidence=[{"authenticated_user_id": str(authenticated_user_id)}] if authenticated_user_id else [],
        confidence=1.0,
        author_agent_slug=resolved_by_agent,
        author_role=resolved_by_role,
        parent_entry_id=entry_id,
        status=resolution_status,
        resolved_by_agent=resolved_by_agent,
        resolution_reason=resolution_reason,
    )
    db.add(resolution_entry)
    db.commit()
    db.refresh(resolution_entry)
    return resolution_entry


def get_active_entries(
    db: Session,
    tenant_id: uuid.UUID,
    board_id: uuid.UUID,
    entry_type: Optional[str] = None,
) -> List[BlackboardEntry]:
    """Get current working state: entries that haven't been superseded or resolved.

    An entry is superseded if another entry references it via supersedes_entry_id.
    An entry is resolved if a resolution entry references it via parent_entry_id
    with entry_type='resolution'.
    """
    board = get_blackboard(db, tenant_id, board_id)
    if not board:
        return []

    # Get IDs of superseded entries
    superseded_ids = {
        row.supersedes_entry_id
        for row in db.query(BlackboardEntry.supersedes_entry_id)
        .filter(
            BlackboardEntry.blackboard_id == board_id,
            BlackboardEntry.supersedes_entry_id.isnot(None),
        ).all()
    }

    # Get IDs of resolved entries
    resolved_ids = {
        row.parent_entry_id
        for row in db.query(BlackboardEntry.parent_entry_id)
        .filter(
            BlackboardEntry.blackboard_id == board_id,
            BlackboardEntry.entry_type == "resolution",
            BlackboardEntry.parent_entry_id.isnot(None),
        ).all()
    }

    excluded = superseded_ids | resolved_ids

    q = (
        db.query(BlackboardEntry)
        .filter(
            BlackboardEntry.blackboard_id == board_id,
            BlackboardEntry.entry_type != "resolution",
        )
    )
    if entry_type:
        q = q.filter(BlackboardEntry.entry_type == entry_type)

    entries = q.order_by(BlackboardEntry.board_version.asc()).all()
    return [e for e in entries if e.id not in excluded]


def get_disagreements(
    db: Session,
    tenant_id: uuid.UUID,
    board_id: uuid.UUID,
) -> List[BlackboardEntry]:
    """Get unresolved disagreements on the blackboard."""
    board = get_blackboard(db, tenant_id, board_id)
    if not board:
        return []

    # Get IDs of resolved entries
    resolved_ids = {
        row.parent_entry_id
        for row in db.query(BlackboardEntry.parent_entry_id)
        .filter(
            BlackboardEntry.blackboard_id == board_id,
            BlackboardEntry.entry_type == "resolution",
            BlackboardEntry.parent_entry_id.isnot(None),
        ).all()
    }

    disagreements = (
        db.query(BlackboardEntry)
        .filter(
            BlackboardEntry.blackboard_id == board_id,
            BlackboardEntry.entry_type == "disagreement",
        )
        .order_by(BlackboardEntry.created_at.asc())
        .all()
    )
    return [d for d in disagreements if d.id not in resolved_ids]


def get_version_diff(
    db: Session,
    tenant_id: uuid.UUID,
    board_id: uuid.UUID,
    from_version: int,
    to_version: Optional[int] = None,
) -> List[BlackboardEntry]:
    """Get entries added between two versions (for replay/diff).

    Since the blackboard is append-only, this is a complete and accurate
    replay of all state changes between the two versions.
    """
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
