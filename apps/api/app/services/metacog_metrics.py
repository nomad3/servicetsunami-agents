"""Prometheus instrumentation for the metacognition layer (M3 of #616).

Closes design item §3.3 from
`docs/plans/2026-05-20-luna-metacognition-and-dreams-canonical.md`:

    > Surface ECE per (tenant, decision_kind) so we can detect a
    > drift in Luna's confidence calibration without batch-running a
    > calibration job. Counters for prediction/observation volume let
    > us watch the trace pipeline at the same time.

Design choices:

- Mirrors `emotion_engine_metrics.py` (#607) shape exactly. The
  pure-functional `metacog` module stays unmodified; instrumentation
  lives at the IO/wire boundary (`cli_session_manager` for write
  emission, and the `/metacog/calibration` endpoint for the ECE
  gauge which is computed on-demand from stored traces).

- Counters use `(tenant_id, decision_kind)` labels. Both are
  bounded-cardinality: tenants are tens-to-thousands; decision_kind
  is the locked frozenset in `schemas/metacog.py:DECISION_KINDS` (5
  values today). The ECE gauge gets the same labels so the operator
  dashboard can render a calibration heat-map per decision kind per
  tenant.

- The confidence histogram is reliability-curve fodder: bucket
  predictions into the same 10 bins Luna locked for ECE (§8.2 of
  the canonical design), so the rendered curve can be reconstructed
  from `metacog_confidence_buckets` alone if the calibration
  endpoint is being rate-limited.

- The module is import-safe: if `prometheus_client` is unavailable
  (the platform's metrics endpoint already shields against this via
  a 503 in `apps/api/app/api/v1/metrics.py`), the counters fall
  back to no-op stubs so the chat hot path never crashes on a
  missing-dep edge case. Same precedent as emotion_engine_metrics.
"""
from __future__ import annotations

from typing import Optional

try:
    from prometheus_client import Counter, Gauge, Histogram

    _PROMETHEUS_AVAILABLE = True
except ImportError:  # pragma: no cover — graceful degradation
    _PROMETHEUS_AVAILABLE = False

    class _NoOp:
        """No-op stand-in for prometheus_client.Counter/Gauge/Histogram
        when the library isn't installed. Keeps call sites identical."""

        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def labels(self, *_args, **_kwargs) -> "_NoOp":
            return self

        def inc(self, *_args, **_kwargs) -> None:
            pass

        def set(self, *_args, **_kwargs) -> None:
            pass

        def observe(self, *_args, **_kwargs) -> None:
            pass

    Counter = _NoOp  # type: ignore[assignment,misc]
    Gauge = _NoOp  # type: ignore[assignment,misc]
    Histogram = _NoOp  # type: ignore[assignment,misc]


# ── Metric definitions ────────────────────────────────────────────────


# Total ConfidencePrediction rows written via metacog_io.write_prediction.
# Per-tenant per-decision-kind so the dashboard can spot a runtime wire
# that silently stopped emitting (e.g. M2 chat hook regression).
metacog_predictions_total = Counter(
    "metacog_predictions_total",
    "Total ConfidencePrediction rows persisted by the metacog layer, "
    "per-tenant per-decision_kind. Diverging from observations_total "
    "indicates unpaired predictions accumulating (in-flight or lost).",
    labelnames=("tenant_id", "decision_kind"),
)

# Total OutcomeObservation rows written via metacog_io.write_observation.
# Companion to predictions_total — divergence flags a leak in either
# direction (predictions without observations = hung dispatch;
# observations without predictions = late-bound writes the join can't
# pair).
metacog_observations_total = Counter(
    "metacog_observations_total",
    "Total OutcomeObservation rows persisted by the metacog layer, "
    "per-tenant per-decision_kind.",
    labelnames=("tenant_id", "decision_kind"),
)

# Per-(tenant, decision_kind) Expected Calibration Error. Updated lazily
# by the /metacog/calibration endpoint — Phase 1 does NOT recompute on
# every write (would O(N) the chat hot path). Operators scrape this
# alongside the reliability curve histogram below.
#
# Range: [0.0, 1.0]. 0.0 = perfectly calibrated; near-1.0 = systematic
# over- or under-confidence. Luna's locked rubric (§8.2) is 10 bins.
metacog_ece = Gauge(
    "metacog_ece",
    "Expected Calibration Error for the metacog layer, per-tenant "
    "per-decision_kind. Computed with 10 equal-width bins over "
    "predicted_confidence in [0, 1]. Updated when the calibration "
    "endpoint is queried.",
    labelnames=("tenant_id", "decision_kind"),
)

# Reliability-curve fodder. Bucket boundaries align with the ECE bins
# (§8.2) so an operator dashboard can render the curve from this alone.
# Histogram buckets are cumulative in prometheus_client's API — listing
# the upper edge of each 0.1-wide bin.
metacog_confidence_buckets = Histogram(
    "metacog_confidence_buckets",
    "Distribution of predicted_confidence values written into the "
    "metacog substrate, bucketed into the same 10 equal-width bins "
    "used for ECE. Operators can reconstruct a reliability curve from "
    "this + metacog_observations_total without round-tripping the "
    "calibration endpoint.",
    labelnames=("tenant_id", "decision_kind"),
    buckets=(0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0),
)


# ── Helpers ───────────────────────────────────────────────────────────


def record_prediction(
    *,
    tenant_id: Optional[str],
    decision_kind: str,
    predicted_confidence: Optional[float] = None,
) -> None:
    """Bump the prediction counter + observe the confidence histogram.

    Best-effort: never raises. Missing tenant_id labels as "unknown"
    so the counter still ticks and the cardinality stays bounded.

    `predicted_confidence` is optional — when supplied (which the M2
    wire-in does), the reliability-curve histogram gets an observation.
    Callers that don't have the float handy (defensive fallback) can
    omit it and only the counter ticks.
    """
    label = tenant_id or "unknown"
    try:
        metacog_predictions_total.labels(
            tenant_id=label, decision_kind=decision_kind,
        ).inc()
    except Exception:  # noqa: BLE001 — metrics never crash the hot path
        pass
    if predicted_confidence is not None:
        try:
            metacog_confidence_buckets.labels(
                tenant_id=label, decision_kind=decision_kind,
            ).observe(predicted_confidence)
        except Exception:  # noqa: BLE001
            pass


def record_observation(
    *,
    tenant_id: Optional[str],
    decision_kind: str,
) -> None:
    """Bump the observation counter for this (tenant, decision_kind).

    Best-effort: never raises. `decision_kind` is mirrored from the
    paired prediction by the M2 wire-in (cli_session_manager passes
    `rl_route_chat_response` for both sides). Keeping the label here
    instead of post-joining is cheaper at scrape time.
    """
    label = tenant_id or "unknown"
    try:
        metacog_observations_total.labels(
            tenant_id=label, decision_kind=decision_kind,
        ).inc()
    except Exception:  # noqa: BLE001
        pass


def set_ece(
    *,
    tenant_id: Optional[str],
    decision_kind: str,
    ece: float,
) -> None:
    """Set the ECE gauge for this (tenant, decision_kind).

    Called from the /metacog/calibration endpoint after computing the
    ECE — the gauge is a snapshot of the most recent calibration query,
    not a recompute on every write. Operators who want continuous ECE
    must scrape the endpoint on a cron.

    Best-effort: clamps to [0.0, 1.0] defensively and never raises.
    """
    label = tenant_id or "unknown"
    # Clamp defensively. ECE is mathematically in [0, 1] but a buggy
    # caller could pass NaN or out-of-range — a metric write should
    # never propagate that into Prometheus scrape output.
    try:
        clamped = max(0.0, min(1.0, float(ece)))
    except (TypeError, ValueError):
        return
    try:
        metacog_ece.labels(
            tenant_id=label, decision_kind=decision_kind,
        ).set(clamped)
    except Exception:  # noqa: BLE001
        pass


__all__ = [
    "metacog_predictions_total",
    "metacog_observations_total",
    "metacog_ece",
    "metacog_confidence_buckets",
    "record_prediction",
    "record_observation",
    "set_ece",
]
