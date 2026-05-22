"""Platform Safety Floor — admin + operator visibility surface.

Per the design (§5 + §12 #3, Luna sign-off):

  - **Platform admin view**: aggregate counts of all
    ``platform_safety_events`` across all tenants, category
    breakdown, trend over time. Authed via ``require_superuser``.
    Sees enforced + shadow refusals.

  - **Operator counter**: count of the operator's own tenant's
    ENFORCED refusals (excludes shadow rows via the partial index
    from migration 145). Returned with a 5-minute jitter/delay to
    defeat sub-second probing of pattern boundaries (Luna §12 #3).

Both endpoints surface COUNTS only — never message hashes, never
trigger ids, never raw text. The detail-level data (hashes for
repeat-attempt detection) is platform-admin-only via the escape
endpoint shipping in PR 6.
"""
from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api import deps
from app.core.safety_defaults import VALID_CATEGORIES
from app.models.platform_safety_event import PlatformSafetyEvent
from app.models.user import User


router = APIRouter()


# ── Response models ──────────────────────────────────────────────────


class CategoryCount(BaseModel):
    category: str
    count: int


class AdminSafetyEventsResponse(BaseModel):
    """Aggregate counts for the platform-admin dashboard.

    Returns total + per-category + per-enforcement-mode counts within
    the requested time window. Default window: last 24h.
    """

    window_hours: int
    total: int
    by_category: list[CategoryCount]
    # (Review NIT) Drift bucket — events whose category was removed
    # from PLATFORM_SAFETY_CATEGORIES between the row write and now
    # are aggregated here. Without this, ``sum(by_category.count)``
    # could differ from ``total`` silently. Surface drift so admins
    # notice when historical categories age out.
    unknown_category: int = 0
    enforced: int
    shadow: int


class OperatorSafetyCounterResponse(BaseModel):
    """Count-only feedback for the operator's own tenant.

    Excludes shadow rows. Carries a deliberately-jittered timestamp
    instead of "as of now" so the operator can't poll the endpoint
    in tight loops and watch the counter tick — that would let them
    probe pattern boundaries.

    ``as_of`` is the snapshot's reference moment (jittered up to 5
    minutes in the past per §12 #3). ``count`` is the number of
    ENFORCED refusals on the tenant since ``as_of - window_hours``.
    """

    window_hours: int
    count: int
    as_of: datetime


# ── Helpers ──────────────────────────────────────────────────────────


_OPERATOR_JITTER_MAX_SECONDS = 5 * 60  # 5 minutes, Luna §12 #3
_MAX_WINDOW_HOURS = 24 * 7  # 7 days — anything longer goes to the
                              # platform-admin path. Follow-up:
                              # admin endpoint may want a 30-day
                              # compliance window cap separately.

# Cryptographically-strong jitter source (Review NIT). PRNG would
# be sufficient for the threat model (defeating sub-second probing
# of pattern boundaries via an operator UI), but `secrets.SystemRandom`
# is a drop-in upgrade that removes any possible reasoning about PRNG
# state leakage. Same call signature, same distribution.
_jitter_rng = secrets.SystemRandom()


def _jittered_now() -> datetime:
    """Return ``now() - random[0, 5min]``. Two operator polls a few
    seconds apart see different jitter offsets, so the counter appears
    'about right' but probing a sub-second boundary is foiled.
    Cryptographically-strong randomness — see ``_jitter_rng``."""
    jitter_seconds = _jitter_rng.uniform(0, _OPERATOR_JITTER_MAX_SECONDS)
    return datetime.now(timezone.utc) - timedelta(seconds=jitter_seconds)


# ── Operator counter (count-only, jittered) ──────────────────────────


@router.get(
    "/luna/values/safety-counter",
    response_model=OperatorSafetyCounterResponse,
)
def get_operator_safety_counter(
    window_hours: int = Query(
        default=24, ge=1, le=_MAX_WINDOW_HOURS,
    ),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Count-only feedback for the operator's own tenant.

    Mounted under ``/luna/values/`` (the operator value-layer surface)
    rather than ``/admin/`` because operators — not platform admins —
    are the audience. Surfaces "N safety refusals in the last hour"
    so operators can differentiate floor refusals from product bugs
    without giving them a probe channel.
    """
    as_of = _jittered_now()
    cutoff = as_of - timedelta(hours=window_hours)
    count = (
        db.query(func.count(PlatformSafetyEvent.id))
        .filter(
            PlatformSafetyEvent.tenant_id == current_user.tenant_id,
            PlatformSafetyEvent.enforcement_mode == "enforced",
            PlatformSafetyEvent.created_at >= cutoff,
            PlatformSafetyEvent.created_at <= as_of,
        )
        .scalar()
        or 0
    )
    return OperatorSafetyCounterResponse(
        window_hours=window_hours,
        count=count,
        as_of=as_of,
    )


# ── Platform-admin view (no jitter, full visibility) ────────────────


@router.get(
    "/admin/safety-events",
    response_model=AdminSafetyEventsResponse,
)
def get_admin_safety_events(
    window_hours: int = Query(default=24, ge=1, le=_MAX_WINDOW_HOURS),
    tenant_id: Optional[uuid.UUID] = Query(default=None),
    db: Session = Depends(deps.get_db),
    _current_user: User = Depends(deps.require_superuser),
):
    """Aggregate counts for the platform-admin dashboard.

    Includes shadow refusals (so we can validate the 14-day shadow-
    mode precision per §12 #7). No jitter — admins see the real
    timestamps. Optional ``tenant_id`` filter for drill-down.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)

    base_query = (
        db.query(PlatformSafetyEvent)
        .filter(PlatformSafetyEvent.created_at >= cutoff)
    )
    if tenant_id is not None:
        base_query = base_query.filter(
            PlatformSafetyEvent.tenant_id == tenant_id,
        )

    # Total
    total = base_query.count()

    # Category breakdown
    cat_rows = (
        base_query
        .with_entities(
            PlatformSafetyEvent.category,
            func.count(PlatformSafetyEvent.id),
        )
        .group_by(PlatformSafetyEvent.category)
        .order_by(func.count(PlatformSafetyEvent.id).desc())
        .all()
    )
    by_category = [
        CategoryCount(category=cat, count=cnt)
        for cat, cnt in cat_rows
        if cat in VALID_CATEGORIES
    ]
    # (Review NIT) Drift bucket — events whose category was removed
    # from PLATFORM_SAFETY_CATEGORIES are summed into a single
    # `unknown_category` count so the response stays internally
    # consistent: sum(by_category.count) + unknown_category == total.
    unknown_category = sum(
        cnt for cat, cnt in cat_rows if cat not in VALID_CATEGORIES
    )

    # Enforced vs shadow split (§12 #7)
    enforced = (
        base_query
        .filter(PlatformSafetyEvent.enforcement_mode == "enforced")
        .count()
    )
    shadow = (
        base_query
        .filter(PlatformSafetyEvent.enforcement_mode == "shadow")
        .count()
    )

    return AdminSafetyEventsResponse(
        window_hours=window_hours,
        total=total,
        by_category=by_category,
        unknown_category=unknown_category,
        enforced=enforced,
        shadow=shadow,
    )
