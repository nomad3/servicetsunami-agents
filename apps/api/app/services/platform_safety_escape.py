"""Platform Safety Floor — admin escape mechanism.

Time-boxed override for security researchers / red-team / law-
enforcement-cooperation / corpus-curation use cases. See design §7.

Invariants:
  - NOT a kill-switch. Operators cannot invoke this.
  - NOT operator-visible. Even the grant existence is hidden from
    the regular operator surface.
  - Requires platform-admin JWT (require_superuser) + reason + 2FA
    (frontend gate; backend assumes the JWT proof carries it).
  - Scoped to (user_id, session_id, category). Only relaxes the
    floor for THAT specific scope; everyone else still gets the
    floor.
  - Auto-expires. Default 1h, max 24h.
  - Every grant lifecycle event + every block-during-window event
    written to ``platform_safety_admin_audit``.

Used by ``platform_safety_io.consult_with_audit`` to short-circuit
the floor when an active grant matches the current (user, session,
category) tuple.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import and_, or_
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.safety_defaults import VALID_CATEGORIES, category_for_label
from app.models.platform_safety_escape import (
    PlatformSafetyAdminAudit,
    PlatformSafetyEscapeGrant,
)

log = logging.getLogger(__name__)


# Grant duration bounds.
ESCAPE_MIN_SECONDS = 60
ESCAPE_DEFAULT_SECONDS = 3600
ESCAPE_MAX_SECONDS = 24 * 3600


# Wildcard category meaning "any category covered". Used by the
# corpus-curation case where the admin needs to see what would
# block at ANY tier.
WILDCARD_CATEGORY = "*"


def _validate_category(category: str) -> str:
    """Accept either a real category or the wildcard."""
    if category == WILDCARD_CATEGORY:
        return WILDCARD_CATEGORY
    # Will raise ValueError on drift
    category_for_label(category)
    return category


def create_grant(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    issued_by_user_id: uuid.UUID,
    scoped_user_id: uuid.UUID,
    scoped_session_id: uuid.UUID,
    category: str,
    reason: str,
    duration_seconds: int = ESCAPE_DEFAULT_SECONDS,
) -> Optional[PlatformSafetyEscapeGrant]:
    """Create a new escape grant + record an audit event.

    Returns the grant on success, None on validation or SQL failure.
    """
    if not reason or not reason.strip():
        log.warning(
            "platform_safety_escape: refusing grant without reason "
            "(tenant=%s admin=%s)", tenant_id, issued_by_user_id,
        )
        return None
    try:
        category = _validate_category(category)
    except ValueError as exc:
        log.warning(
            "platform_safety_escape: refusing grant for unknown "
            "category %r: %s", category, exc,
        )
        return None

    duration = max(
        ESCAPE_MIN_SECONDS,
        min(int(duration_seconds), ESCAPE_MAX_SECONDS),
    )
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=duration)

    grant = PlatformSafetyEscapeGrant(
        tenant_id=tenant_id,
        issued_by_user_id=issued_by_user_id,
        scoped_user_id=scoped_user_id,
        scoped_session_id=scoped_session_id,
        category=category,
        reason=reason.strip(),
        expires_at=expires_at,
    )
    try:
        db.add(grant)
        db.flush()
        db.add(PlatformSafetyAdminAudit(
            event_type="grant_created",
            tenant_id=tenant_id,
            actor_user_id=issued_by_user_id,
            grant_id=grant.id,
            category=category,
            detail=(
                f"duration_seconds={duration} expires_at={expires_at.isoformat()} "
                f"reason={reason.strip()[:200]}"
            ),
        ))
        db.commit()
    except SQLAlchemyError as exc:
        log.error(
            "platform_safety_escape: grant insert failed: %s", exc,
        )
        try:
            db.rollback()
        except SQLAlchemyError:
            pass
        return None

    log.info(
        "PLATFORM_SAFETY_ESCAPE_GRANTED tenant=%s admin=%s scope=%s/%s "
        "category=%s expires_at=%s",
        tenant_id, issued_by_user_id, scoped_user_id, scoped_session_id,
        category, expires_at.isoformat(),
    )
    return grant


def revoke_grant(
    db: Session,
    *,
    grant_id: uuid.UUID,
    actor_user_id: uuid.UUID,
) -> bool:
    """Mark an unexpired grant as revoked. Idempotent."""
    grant = (
        db.query(PlatformSafetyEscapeGrant)
        .filter(PlatformSafetyEscapeGrant.id == grant_id)
        .first()
    )
    if grant is None:
        return False
    if grant.revoked_at is not None:
        return True  # already revoked
    grant.revoked_at = datetime.now(timezone.utc)
    db.add(PlatformSafetyAdminAudit(
        event_type="grant_revoked",
        tenant_id=grant.tenant_id,
        actor_user_id=actor_user_id,
        grant_id=grant.id,
        category=grant.category,
        detail="manual revoke before expiry",
    ))
    try:
        db.commit()
    except SQLAlchemyError as exc:
        log.error("platform_safety_escape: revoke commit failed: %s", exc)
        try:
            db.rollback()
        except SQLAlchemyError:
            pass
        return False
    return True


def is_active_grant_for(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    user_id: Optional[uuid.UUID],
    session_id: Optional[uuid.UUID],
    category: str,
) -> Optional[PlatformSafetyEscapeGrant]:
    """Return the active grant that covers this (user, session,
    category) tuple, or None.

    A grant matches when:
      - tenant_id matches
      - scoped_user_id == user_id AND scoped_session_id == session_id
      - revoked_at IS NULL
      - expires_at > now()
      - grant.category == category OR grant.category == WILDCARD_CATEGORY

    Anonymous (user_id=None) or sessionless (session_id=None) calls
    return None — the escape mechanism requires both to be known so
    it cannot apply tenant-wide accidentally.
    """
    if user_id is None or session_id is None:
        return None
    try:
        now = datetime.now(timezone.utc)
        return (
            db.query(PlatformSafetyEscapeGrant)
            .filter(
                PlatformSafetyEscapeGrant.tenant_id == tenant_id,
                PlatformSafetyEscapeGrant.scoped_user_id == user_id,
                PlatformSafetyEscapeGrant.scoped_session_id == session_id,
                PlatformSafetyEscapeGrant.revoked_at.is_(None),
                PlatformSafetyEscapeGrant.expires_at > now,
                or_(
                    PlatformSafetyEscapeGrant.category == category,
                    PlatformSafetyEscapeGrant.category == WILDCARD_CATEGORY,
                ),
            )
            .order_by(PlatformSafetyEscapeGrant.created_at.desc())
            .first()
        )
    except SQLAlchemyError as exc:
        log.warning(
            "platform_safety_escape: active-grant lookup failed: %s",
            exc,
        )
        return None


def record_block_during_grant(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    grant: PlatformSafetyEscapeGrant,
    blocked_category: str,
) -> None:
    """A floor refusal fired while a grant was active but its
    category didn't cover the block. Audit-only — does not change
    the verdict.
    """
    try:
        db.add(PlatformSafetyAdminAudit(
            event_type="block_in_window",
            tenant_id=tenant_id,
            actor_user_id=None,
            grant_id=grant.id,
            category=blocked_category,
            detail=(
                f"grant_category={grant.category} "
                f"grant_expires_at={grant.expires_at.isoformat()}"
            ),
        ))
        db.commit()
    except SQLAlchemyError as exc:
        log.warning(
            "platform_safety_escape: block_in_window audit failed: %s",
            exc,
        )
        try:
            db.rollback()
        except SQLAlchemyError:
            pass


__all__ = [
    "ESCAPE_MIN_SECONDS",
    "ESCAPE_DEFAULT_SECONDS",
    "ESCAPE_MAX_SECONDS",
    "WILDCARD_CATEGORY",
    "create_grant",
    "revoke_grant",
    "is_active_grant_for",
    "record_block_during_grant",
]
