"""Metacognition pure-function layer (M1 of #616).

Serialize/deserialize for the agent_memory substrate + ECE
calibration helper. Pure functions only — no DB session, no logging.
The IO layer (metacog_io.py) wraps these with persistence concerns.

Mirrors the team_engine.py pattern (#608): pure logic here so the
IO layer's tests can hit DB while these tests stay fast.
"""
from __future__ import annotations

import json
import logging
from typing import Iterable, Optional

from app.schemas.metacog import (
    ConfidencePrediction,
    MetacogTrace,
    OutcomeObservation,
)

logger = logging.getLogger(__name__)


# ── Memory type discriminators ────────────────────────────────────────
PREDICTION_MEMORY_TYPE = "metacog_confidence_prediction"
OBSERVATION_MEMORY_TYPE = "metacog_outcome_observation"


# ── Serialize / deserialize ───────────────────────────────────────────


def serialize_prediction(prediction: ConfidencePrediction) -> str:
    """JSON-encode a ConfidencePrediction for agent_memory.content."""
    return json.dumps(prediction.to_dict(), sort_keys=True)


def deserialize_prediction(blob: str) -> Optional[ConfidencePrediction]:
    """Best-effort decode. Returns None on malformed content rather
    than raising — the caller (read path) skips and logs."""
    try:
        data = json.loads(blob)
        return ConfidencePrediction(**data)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.debug(
            "metacog.deserialize_prediction: malformed blob — %s", exc
        )
        return None


def serialize_observation(observation: OutcomeObservation) -> str:
    """JSON-encode an OutcomeObservation for agent_memory.content."""
    return json.dumps(observation.to_dict(), sort_keys=True)


def deserialize_observation(blob: str) -> Optional[OutcomeObservation]:
    try:
        data = json.loads(blob)
        return OutcomeObservation(**data)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.debug(
            "metacog.deserialize_observation: malformed blob — %s", exc
        )
        return None


# ── ECE / calibration ─────────────────────────────────────────────────


def expected_calibration_error(
    traces: Iterable[MetacogTrace],
    bins: int = 10,
) -> float:
    """Expected Calibration Error (Naeini et al., 2015).

    Lower is better; 0.0 = perfectly calibrated. The classic metric:
    bin predictions into `bins` equal-width buckets in [0, 1], for
    each non-empty bucket compute |mean_predicted − mean_actual|, then
    weight by bucket size and sum.

    Luna's call (#616 §9): start with 10 bins. Operator can pass
    `bins=20` for high-volume decision kinds if calibration drifts
    show low-resolution artefacts.

    Returns NaN-safe 0.0 when input is empty. Trace inputs that fail
    the (prediction.decision_id == observation.decision_id) invariant
    are MetacogTrace construction errors and don't reach here.
    """
    if bins <= 0:
        raise ValueError(f"bins must be positive, got {bins}")

    materialized = list(traces)
    n_total = len(materialized)
    if n_total == 0:
        return 0.0

    bin_width = 1.0 / bins
    buckets: list[list[MetacogTrace]] = [[] for _ in range(bins)]
    # Bucket convention (locked per superpowers IMPORTANT #2): each
    # bucket is half-open [lo, hi) on the lower edge except the last,
    # which is closed [0.9, 1.0]. Predictions exactly at a bucket
    # boundary (0.1, 0.2, …) land in the UPPER bucket — standard
    # Naeini convention. Do not change this without also updating
    # test_ece_at_bucket_boundary_lands_in_upper.
    for t in materialized:
        pred = t.prediction.predicted_confidence
        # The min() clamp handles pred == 1.0 → would otherwise yield
        # `bins`, which is out of range.
        idx = min(int(pred / bin_width), bins - 1)
        buckets[idx].append(t)

    ece = 0.0
    for bucket in buckets:
        if not bucket:
            continue
        mean_pred = sum(b.prediction.predicted_confidence for b in bucket) / len(bucket)
        mean_actual = sum(b.normalized_reward for b in bucket) / len(bucket)
        ece += (len(bucket) / n_total) * abs(mean_pred - mean_actual)
    return ece


# ── Join helper ───────────────────────────────────────────────────────


def join_traces(
    predictions: Iterable[ConfidencePrediction],
    observations: Iterable[OutcomeObservation],
) -> list[MetacogTrace]:
    """Pair predictions with their matching observations by
    decision_id. Returns MetacogTrace list; unpaired predictions or
    observations are silently dropped (an observation without its
    prediction can't be calibrated; a prediction without its
    observation is just in-flight).

    Stable: input order is preserved for predictions; the resulting
    list contains a trace for each prediction that has a matching
    observation, in prediction order.

    Tenant-mismatch / agent-mismatch traces are dropped silently
    here (per-row log was N×spammy at scale; superpowers IMPORTANT
    #3). A single batch-summary warning fires when any mismatches
    occur — M3 will replace that with a Prometheus counter.
    """
    obs_by_id = {o.decision_id: o for o in observations}
    out: list[MetacogTrace] = []
    skipped = 0
    for p in predictions:
        o = obs_by_id.get(p.decision_id)
        if o is None:
            continue
        try:
            out.append(MetacogTrace(prediction=p, observation=o))
        except ValueError:
            # Tenant/agent mismatch or other invariant breach. Counted
            # in batch-summary log below; not per-row to avoid spam.
            skipped += 1
    if skipped:
        logger.warning(
            "metacog.join_traces: skipped %d malformed trace(s) "
            "(tenant/agent mismatch). Phase-3 wiring will surface "
            "this as a Prometheus counter.",
            skipped,
        )
    return out


__all__ = [
    "PREDICTION_MEMORY_TYPE",
    "OBSERVATION_MEMORY_TYPE",
    "serialize_prediction",
    "deserialize_prediction",
    "serialize_observation",
    "deserialize_observation",
    "expected_calibration_error",
    "join_traces",
]
