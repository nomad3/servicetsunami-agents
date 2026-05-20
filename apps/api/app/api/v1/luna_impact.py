"""Luna-impact baseline dashboard (task #327).

Single read-only endpoint that aggregates the Layer-1 measurable signals
defined in docs/plans/2026-05-20-luna-metacognition-and-dreams-canonical.md §6:
stability, routing, affect, coordination, metacog.

Mounted under /api/v1/luna so future Luna-scoped endpoints (e.g.
/luna/dreams, /luna/posture) can share the namespace.

Tenant-scoped: every metric is filtered by `get_current_user.tenant_id`.
Log-derived metrics degrade to null + `_unavailable_metrics` rather
than crashing the endpoint — see services/luna_impact.py.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.user import User
from app.services.luna_impact import (
    DEFAULT_WINDOW_DAYS,
    MAX_WINDOW_DAYS,
    compute_impact,
)


router = APIRouter()


@router.get("/impact")
def get_luna_impact(
    window_days: int = Query(
        default=DEFAULT_WINDOW_DAYS,
        ge=1,
        le=MAX_WINDOW_DAYS,
        description="Look-back window in days (1-90).",
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return Luna's Layer-1 impact baseline for the calling tenant.

    See the response schema in
    docs/plans/2026-05-20-luna-metacognition-and-dreams-canonical.md §6.
    Unmeasurable metrics (typically log-only) come back as null and the
    payload includes `_unavailable_metrics: [<dotted_path>, ...]`.
    """
    return compute_impact(
        db,
        tenant_id=current_user.tenant_id,
        window_days=window_days,
    )
