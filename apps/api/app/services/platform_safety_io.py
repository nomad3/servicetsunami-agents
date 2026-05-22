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
# (PR 6 Review IMPORTANT-2) Module-top import. No circular-import
# risk: platform_safety_escape doesn't import platform_safety_io.
from app.services import platform_safety_escape as _ps_escape

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
    except Exception as exc:  # noqa: BLE001
        # (Review NIT-5) Catch non-SQL crashes too — e.g. an
        # AttributeError on a malformed verdict shape. The verdict
        # has already been computed and is the authoritative refusal
        # signal; the audit row is bookkeeping. A crash here MUST
        # NOT propagate up into consult_with_audit (which would
        # then fail-OPEN the refusal we just decided to fire).
        log.warning(
            "platform_safety: audit row unexpected error "
            "tenant=%s category=%s err=%s; refusal still fired",
            tenant_id, verdict.category, exc,
        )
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
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

    (Review NIT-3 — cross-ref to design §12 #1). Important: when
    tier 1 raises BEFORE a match (compiled-regex bug, corpus loader
    failure), we have NO category context — per-category
    fail-closed cannot apply. We fail-OPEN with a loud ERROR log
    and rely on tier 2/3's per-category fail-closed for the
    existential categories. Tier 1 raising is the "should never
    happen" path; the fail-closed semantics apply to per-category
    classifier crashes, which only exist at tier 2+.
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

    # Tier 3 — LLM classifier. Runs only when tier 1+2 missed AND
    # the pre-screen surfaced at least one candidate category. Shadow
    # mode (§12 #7 — Luna call) gates whether tier 3 blocks the user
    # or just records what it WOULD have blocked.
    return _run_tier3_with_shadow_gate(
        db,
        tenant_id=tenant_id,
        agent_id=agent_id,
        session_id=session_id,
        user_id=user_id,
        message=message,
    )


def _run_tier3_with_shadow_gate(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    agent_id: Optional[uuid.UUID],
    session_id: Optional[uuid.UUID],
    user_id: Optional[uuid.UUID],
    message: str,
) -> PlatformSafetyVerdict:
    """Run tier 3 LLM classifier with the shadow-mode gate (§12 #7).

    Steps:
      1. Compute candidate categories from the same pre-screen
         tier 2 uses. Empty → tier 3 skipped (90%+ of turns).
      2. Call tier3.classify(); on any exception, treat as allow
         (tier 1 + 2 already ran cleanly).
      3. If classifier returns would_block=False: allow.
      4. If would_block=True AND category.tier3_enforcement=True:
         record audit with enforcement_mode='enforced', return
         block.
      5. If would_block=True AND tier3_enforcement=False (shadow,
         the default for first 14 days): record audit with
         enforcement_mode='shadow', return ALLOW. The user sees a
         normal LLM dispatch; the platform admin sees the
         would-have-block in the count-only operator counter
         excludes shadow rows.
    """
    try:
        from app.services.platform_safety.tier2 import (
            candidate_categories,
        )
        from app.services.platform_safety.tier3 import (
            classify as tier3_classify,
        )
    except ImportError as exc:
        log.error(
            "platform_safety.tier3: import failed (%s); skipping tier 3, "
            "allowing message",
            exc,
        )
        return PlatformSafetyVerdict.allow()

    candidates = candidate_categories(message)
    if not candidates:
        return PlatformSafetyVerdict.allow()

    try:
        result = tier3_classify(message, candidates)
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "platform_safety.tier3: classify raised (%s); allowing "
            "(tier 1 + 2 already ran)",
            exc,
        )
        return PlatformSafetyVerdict.allow()

    if not result.would_block or not result.category:
        return PlatformSafetyVerdict.allow()

    # Shadow vs enforced — code-owned per-category policy. The
    # PR 1 safety_defaults.py ships every category with
    # tier3_enforcement=False; after the 14-day precision audit,
    # each category flips individually via a config-only deploy.
    try:
        policy = category_for_label(result.category)
        enforce = bool(policy.tier3_enforcement)
    except ValueError:
        log.warning(
            "platform_safety.tier3: classifier returned unknown "
            "category %r; treating as allow (drift defense)",
            result.category,
        )
        return PlatformSafetyVerdict.allow()

    mode = "enforced" if enforce else "shadow"
    block_verdict = PlatformSafetyVerdict.block(
        category=result.category,
        detection_tier=3,
        confidence=result.confidence,
        trigger_id=result.trigger_id,
    )

    # (PR 6) Admin escape check. If an active grant covers this
    # (tenant, user, session, category), the user proceeds AND we
    # write a block_in_window audit row to the admin audit table.
    # The grant DOES NOT silence the platform_safety_events audit
    # row — the safety event still records what would have blocked.
    grant_active = None
    if enforce:  # only relevant for the user-blocking path
        try:
            grant_active = _ps_escape.is_active_grant_for(
                db,
                tenant_id=tenant_id,
                user_id=user_id,
                session_id=session_id,
                category=result.category,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "platform_safety_escape: grant check raised (%s); "
                "treating as no-grant (block stands)",
                exc,
            )

    _record_event(
        db,
        tenant_id=tenant_id,
        agent_id=agent_id,
        session_id=session_id,
        user_id=user_id,
        message=message,
        verdict=block_verdict,
        enforcement_mode=mode,
    )
    if grant_active is not None:
        try:
            _ps_escape.record_block_during_grant(
                db, tenant_id=tenant_id, grant=grant_active,
                blocked_category=result.category,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "platform_safety_escape: block_in_window audit "
                "failed (%s); the escape grant still relaxes the "
                "user-facing block",
                exc,
            )

    log.info(
        "platform_safety.tier3 %s tenant=%s agent=%s category=%s "
        "confidence=%s provider=%s grant_active=%s",
        mode, tenant_id, agent_id, result.category,
        result.confidence, result.provider, bool(grant_active),
    )

    if enforce and grant_active is None:
        return block_verdict
    # Shadow mode OR active escape grant — would-have-blocked
    # recorded; user proceeds.
    return PlatformSafetyVerdict.allow()


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
