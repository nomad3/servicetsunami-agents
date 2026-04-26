"""Library revision service — write & read audit history for skill / agent edits.

Used by the chat-side `update_skill_definition` / `update_agent_definition`
MCP tools (PR5 / Phase 4) so every config change is traceable to an actor
and reversible from the audit log.
"""
import logging
import uuid
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.library_revision import LibraryRevision

logger = logging.getLogger(__name__)


def record_revision(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    target_type: str,
    target_ref: str,
    actor_user_id: Optional[uuid.UUID],
    reason: Optional[str],
    before_value: Optional[dict],
    after_value: Optional[dict],
) -> LibraryRevision:
    """Append a revision row. Caller is responsible for the actual mutation."""
    if target_type not in ("skill", "agent"):
        raise ValueError(f"Unsupported target_type '{target_type}'.")

    revision = LibraryRevision(
        tenant_id=tenant_id,
        target_type=target_type,
        target_ref=target_ref,
        actor_user_id=actor_user_id,
        reason=reason,
        before_value=before_value,
        after_value=after_value,
    )
    db.add(revision)
    db.commit()
    db.refresh(revision)
    return revision


def list_revisions(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    target_type: Optional[str] = None,
    target_ref: Optional[str] = None,
    limit: int = 50,
) -> List[LibraryRevision]:
    """Return the most recent revisions for this tenant, newest first."""
    q = db.query(LibraryRevision).filter(LibraryRevision.tenant_id == tenant_id)
    if target_type:
        q = q.filter(LibraryRevision.target_type == target_type)
    if target_ref:
        q = q.filter(LibraryRevision.target_ref == target_ref)
    return q.order_by(LibraryRevision.created_at.desc()).limit(limit).all()
