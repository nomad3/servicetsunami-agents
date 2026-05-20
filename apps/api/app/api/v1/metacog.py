"""Metacognition calibration endpoints — M3 of #616.

Read-only endpoint that surfaces ECE + the underlying reliability-curve
buckets for the calling tenant. Mirrors the emotion.py shape (#605):
tenant-scoped via the JWT, foreign-tenant data is never reachable
because `list_traces` already filters on `tenant_id`.

Wired into the API router with empty prefix so the path reads
`GET /api/v1/metacog/calibration` (matching the canonical design's
URL convention).
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.user import User
from app.services import metacog_io, metacog_metrics
from app.services.metacog import expected_calibration_error


router = APIRouter()


# Locked at 10 per Luna's §8.2 decision. Endpoint does NOT accept a
# `bins` query param: changing bin count silently between scrapes would
# make the Prometheus gauge meaningless (gauge labels don't carry the
# bin count). If we ever need higher resolution per decision_kind, add
# a per-kind constant table here, not a query param.
_ECE_BINS = 10


@router.get("/metacog/calibration")
def get_metacog_calibration(
    agent_id: Optional[uuid.UUID] = Query(default=None),
    decision_kind: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return the metacog calibration snapshot for the current tenant.

    Query params:
      - `agent_id` (optional): scope to a single agent in the tenant.
      - `decision_kind` (optional): scope to a single decision kind
        (one of `schemas/metacog.py:DECISION_KINDS`).

    Response shape:
        {
            "tenant_id": "<uuid>",
            "decision_kind": "<kind>" | null,
            "n_traces": <int>,
            "ece": <float in [0, 1]>,
            "by_bin": [
                {
                    "bin_low": <float>,
                    "bin_high": <float>,
                    "count": <int>,
                    "mean_pred": <float>,
                    "mean_actual": <float>,
                },
                ... (10 entries, one per bin; empty bins included with
                     count=0 so the consumer can render a stable axis)
            ]
        }

    Side effect: updates the `metacog_ece` Prometheus gauge for the
    queried (tenant, decision_kind) so a scrape will reflect the most
    recent calibration. Empty trace sets emit ECE=0.0 (the math layer's
    NaN-safe default) — operators MUST cross-reference n_traces before
    alerting on "perfect" calibration.
    """
    traces = metacog_io.list_traces(
        db,
        tenant_id=current_user.tenant_id,
        agent_id=agent_id,
        decision_kind=decision_kind,
    )

    ece = expected_calibration_error(traces, bins=_ECE_BINS)
    n_traces = len(traces)

    # Best-effort gauge update. Use the queried decision_kind label if
    # the caller scoped it; otherwise "*" so the operator can see the
    # tenant-wide rollup separately from per-kind snapshots without
    # collision. This keeps cardinality bounded by the locked
    # DECISION_KINDS set + one rollup label per tenant.
    gauge_label = decision_kind or "*"
    metacog_metrics.set_ece(
        tenant_id=str(current_user.tenant_id),
        decision_kind=gauge_label,
        ece=ece,
    )

    by_bin = _bucketize(traces, bins=_ECE_BINS)

    return {
        "tenant_id": str(current_user.tenant_id),
        "decision_kind": decision_kind,
        "n_traces": n_traces,
        "ece": ece,
        "by_bin": by_bin,
    }


def _bucketize(traces, bins: int) -> list[dict]:
    """Render per-bin counts + mean_pred + mean_actual for the response.

    Same bucket convention as `expected_calibration_error` — half-open
    [lo, hi) except the last bucket which is closed [0.9, 1.0]. Empty
    bins are included (count=0, means=0.0) so the response shape is
    stable for a frontend that wants to plot a 10-point axis without
    interpolating missing bins.
    """
    if bins <= 0:
        return []
    bin_width = 1.0 / bins
    buckets: list[list] = [[] for _ in range(bins)]
    for t in traces:
        pred = t.prediction.predicted_confidence
        idx = min(int(pred / bin_width), bins - 1)
        buckets[idx].append(t)

    out: list[dict] = []
    for i, bucket in enumerate(buckets):
        lo = i * bin_width
        hi = (i + 1) * bin_width if i < bins - 1 else 1.0
        if bucket:
            mean_pred = sum(b.prediction.predicted_confidence for b in bucket) / len(bucket)
            mean_actual = sum(b.normalized_reward for b in bucket) / len(bucket)
        else:
            mean_pred = 0.0
            mean_actual = 0.0
        out.append({
            "bin_low": lo,
            "bin_high": hi,
            "count": len(bucket),
            "mean_pred": mean_pred,
            "mean_actual": mean_actual,
        })
    return out
