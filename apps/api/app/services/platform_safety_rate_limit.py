"""Platform Safety Floor — repeat-attempt detection.

Per design §10 Q5 (Luna sign-off): a user who hits the floor 5+
times in 60 seconds is probing the boundaries. We:

  - Log loudly (WARNING) so ops alerting catches it.
  - Emit a Prometheus counter for the safety dashboard.
  - DO NOT auto-ban — false positives cost more than delaying a
    real attacker (legitimate users get refused via the floor and
    might naturally retry).

This is detection-only for v1. The user-facing throttling decision
is operator-driven (ban, talk to user, etc).

Hot-path cheap: one extra COUNT query against the partial
`enforcement_mode='enforced'` index from migration 145. Skipped
when the block was shadow (no user-visible event to count).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.platform_safety_event import PlatformSafetyEvent

log = logging.getLogger(__name__)


# Tunable. Repeat-attempt threshold: 5 blocks in 60s.
REPEAT_ATTEMPT_THRESHOLD = 5
REPEAT_ATTEMPT_WINDOW_SECONDS = 60


# Try Prometheus client; degrade gracefully when not installed
# (e.g. test environments without the metrics dependency).
_repeat_attempt_counter = None
try:
    from prometheus_client import Counter

    _repeat_attempt_counter = Counter(
        "platform_safety_repeat_attempts_total",
        "Users who hit the platform safety floor 5+ times in 60s. "
        "Operators should review these for probe-style behavior.",
        ["tenant_id"],
    )
except ImportError:
    log.debug(
        "prometheus_client not installed; repeat-attempt counter "
        "is a no-op"
    )


def _increment_metric(tenant_id: uuid.UUID) -> None:
    """Best-effort Prometheus increment. Suppress all errors —
    metrics MUST NOT break the chat hot path."""
    if _repeat_attempt_counter is None:
        return
    try:
        _repeat_attempt_counter.labels(tenant_id=str(tenant_id)).inc()
    except Exception:  # noqa: BLE001
        pass


def check_repeat_attempts(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    user_id: Optional[uuid.UUID],
) -> Optional[int]:
    """If the user has hit the floor REPEAT_ATTEMPT_THRESHOLD or more
    times within REPEAT_ATTEMPT_WINDOW_SECONDS, emit a WARNING log
    and increment the Prometheus counter. Returns the block-count
    when the threshold was hit, else None.

    Anonymous (user_id=None) calls are skipped — the attempt
    pattern can't be attributed without a user. Shadow rows are
    excluded via the enforcement_mode filter.

    Cheap query: uses the partial `enforcement_mode='enforced'`
    index from migration 145 + the (tenant_id, created_at)
    composite. ≤5ms in steady state.

    Best-effort: SQL failure → log + None. The chat hot path
    already returned the refusal to the user before this check
    runs; failing this is bookkeeping only.
    """
    if user_id is None:
        return None
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(
            seconds=REPEAT_ATTEMPT_WINDOW_SECONDS,
        )
        count = (
            db.query(func.count(PlatformSafetyEvent.id))
            .filter(
                PlatformSafetyEvent.tenant_id == tenant_id,
                PlatformSafetyEvent.user_id == user_id,
                PlatformSafetyEvent.enforcement_mode == "enforced",
                PlatformSafetyEvent.created_at >= cutoff,
            )
            .scalar()
            or 0
        )
    except SQLAlchemyError as exc:
        log.warning(
            "platform_safety.rate_limit: count query failed "
            "tenant=%s user=%s err=%s",
            tenant_id, user_id, exc,
        )
        return None

    if count >= REPEAT_ATTEMPT_THRESHOLD:
        log.warning(
            "PLATFORM_SAFETY_REPEAT_ATTEMPT tenant=%s user=%s "
            "count=%d window_seconds=%d threshold=%d — operator "
            "should review for probe-style behavior",
            tenant_id, user_id, count, REPEAT_ATTEMPT_WINDOW_SECONDS,
            REPEAT_ATTEMPT_THRESHOLD,
        )
        _increment_metric(tenant_id)
        return count
    return None


__all__ = [
    "REPEAT_ATTEMPT_THRESHOLD",
    "REPEAT_ATTEMPT_WINDOW_SECONDS",
    "check_repeat_attempts",
]
