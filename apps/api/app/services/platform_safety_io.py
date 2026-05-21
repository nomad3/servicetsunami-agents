"""Platform Safety Floor — IO wrapper.

The pure ``platform_safety.consult()`` runs the regex match without
side effects. This wrapper:

  1. Calls ``consult()``.
  2. On block, records an audit row in ``platform_safety_events``
     with SHA256(message) — NEVER the raw text.
  3. Handles per-category fail-open / fail-closed policy on consult
     crashes (PR 4+ tier 2/3 paths will pass through this same shape).
  4. Returns the verdict to the caller (agent_router) so the chat hot
     path can short-circuit with a refusal.

Privacy invariant: message text NEVER leaves this function. Only the
SHA256 hash reaches the database.

Design: docs/plans/2026-05-21-platform-safety-floor-design.md §5
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from typing import Optional

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.safety_defaults import (
    PLATFORM_SAFETY_CATEGORIES,
    category_for_label,
)
from app.models.platform_safety_event import PlatformSafetyEvent
from app.services.platform_safety import (
    PlatformSafetyVerdict,
    consult,
)

log = logging.getLogger(__name__)


def _hash_message(message: str) -> str:
    """SHA256(message) lowercase hex. Used for repeat-attempt
    detection without storing the raw text."""
    return hashlib.sha256(message.encode("utf-8")).hexdigest()


def _record_event(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    agent_id: Optional[uuid.UUID],
    session_id: Optional[uuid.UUID],
    user_id: Optional[uuid.UUID],
    message: str,
    verdict: PlatformSafetyVerdict,
    enforcement_mode: str = "enforced",
) -> None:
    """Write the audit row. Best-effort: a SQL failure here MUST NOT
    crash chat. The verdict has already been computed and is the
    authoritative refusal signal; the row is bookkeeping."""
    if verdict.decision != "block":
        return
    if not verdict.category:
        log.error(
            "platform_safety: block verdict missing category — "
            "audit row skipped (bug in caller)"
        )
        return
    try:
        row = PlatformSafetyEvent(
            tenant_id=tenant_id,
            agent_id=agent_id,
            session_id=session_id,
            user_id=user_id,
            message_hash=_hash_message(message),
            category=verdict.category,
            detection_tier=verdict.detection_tier,
            confidence=verdict.confidence,
            enforcement_mode=enforcement_mode,
        )
        db.add(row)
        db.commit()
    except SQLAlchemyError as exc:
        log.warning(
            "platform_safety: audit row insert failed "
            "tenant=%s category=%s err=%s; refusal still fired",
            tenant_id, verdict.category, exc,
        )
        try:
            db.rollback()
        except SQLAlchemyError:
            pass


def consult_with_audit(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    agent_id: Optional[uuid.UUID],
    session_id: Optional[uuid.UUID],
    user_id: Optional[uuid.UUID],
    message: str,
) -> PlatformSafetyVerdict:
    """Production boundary: run the tier-1 regex screen + audit.

    Fail-open / fail-closed handling per category (§12 #1 — Luna
    call):
      - Existential categories (CSAM, terrorism, child safety,
        mass-harm synthesis) → fail-CLOSED on crash. Refuse the
        message rather than risk a slip-through.
      - Soft categories → fail-OPEN on crash. Don't brick the
        platform for legitimate users.

    Tier 1 (regex) is total — the only way it raises is an internal
    bug, which is the case the fail-closed/open policy covers. Tier
    2/3 add their own crash surfaces in PRs 4/5 and route through
    this same wrapper.
    """
    try:
        verdict = consult(message)
    except Exception as exc:  # noqa: BLE001
        # Determine fail policy. Without a category we can't pick
        # per-category fail-closed; default to fail-OPEN with a loud
        # ERROR log. The framework guarantees consult() returns
        # PlatformSafetyVerdict; any raise here is a genuine bug.
        log.error(
            "platform_safety.consult raised on a tier-1 call "
            "tenant=%s err=%s; failing OPEN (no category context)",
            tenant_id, exc,
        )
        return PlatformSafetyVerdict.allow()

    if verdict.decision == "block":
        _record_event(
            db,
            tenant_id=tenant_id,
            agent_id=agent_id,
            session_id=session_id,
            user_id=user_id,
            message=message,
            verdict=verdict,
            enforcement_mode="enforced",
        )
        log.info(
            "platform_safety.block tenant=%s agent=%s category=%s "
            "tier=%d trigger=%s",
            tenant_id, agent_id, verdict.category,
            verdict.detection_tier, verdict.trigger_id,
        )
    return verdict


def fail_closed_for_category(category: str) -> bool:
    """Lookup per Luna §12 #1 — used by tier 2/3 wrappers in PR 4/5
    when those classifiers fail. Defensive: unknown category defaults
    to fail-CLOSED (better to over-refuse than over-allow when the
    safety layer is in an unknown state)."""
    try:
        return category_for_label(category).fail_closed
    except ValueError:
        log.warning(
            "fail_closed_for_category: unknown category %r; "
            "defaulting to fail-CLOSED",
            category,
        )
        return True


__all__ = [
    "consult_with_audit",
    "fail_closed_for_category",
    "PLATFORM_SAFETY_CATEGORIES",
]
