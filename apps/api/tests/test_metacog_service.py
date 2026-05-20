"""Pure-function tests for app.services.metacog (M1 of #616).

No DB — pure serialize/deserialize/calibration tests. Fast.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from app.schemas.metacog import (
    ConfidencePrediction,
    MetacogTrace,
    OutcomeObservation,
)
from app.services.metacog import (
    OBSERVATION_MEMORY_TYPE,
    PREDICTION_MEMORY_TYPE,
    deserialize_observation,
    deserialize_prediction,
    expected_calibration_error,
    join_traces,
    serialize_observation,
    serialize_prediction,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pred(
    decision_id="d", predicted=0.5, kind="rl_route_chat_response",
    tenant="t", agent="a",
) -> ConfidencePrediction:
    return ConfidencePrediction(
        tenant_id=tenant, agent_id=agent, decision_id=decision_id,
        decision_kind=kind, predicted_confidence=predicted,
        context_hash="ctx", ts=_now(),
    )


def _obs(
    decision_id="d", reward=0.0, tenant="t", agent="a",
    latency=10, error=None,
) -> OutcomeObservation:
    return OutcomeObservation(
        tenant_id=tenant, agent_id=agent, decision_id=decision_id,
        actual_reward=reward, latency_ms=latency,
        completed_at=_now(), error=error,
    )


# ── Memory type discriminators ────────────────────────────────────────


def test_memory_types_are_distinct():
    """A row with PREDICTION_MEMORY_TYPE must never be confused for an
    observation row by string equality alone."""
    assert PREDICTION_MEMORY_TYPE != OBSERVATION_MEMORY_TYPE
    assert "prediction" in PREDICTION_MEMORY_TYPE
    assert "observation" in OBSERVATION_MEMORY_TYPE


# ── Serialize / deserialize roundtrip ─────────────────────────────────


def test_prediction_roundtrips_via_json():
    p = _pred(predicted=0.42, kind="affect_appraise")
    blob = serialize_prediction(p)
    # Sorted keys means stable on-disk representation
    parsed = json.loads(blob)
    assert parsed["predicted_confidence"] == 0.42
    assert deserialize_prediction(blob) == p


def test_observation_roundtrips_via_json():
    o = _obs(reward=-0.3, latency=999, error="timeout")
    blob = serialize_observation(o)
    parsed = json.loads(blob)
    assert parsed["actual_reward"] == -0.3
    assert parsed["error"] == "timeout"
    assert deserialize_observation(blob) == o


def test_deserialize_returns_none_on_malformed_blob():
    """Read path must never raise on bad rows — corrupted content
    just gets silently dropped."""
    assert deserialize_prediction("not json") is None
    assert deserialize_prediction("{}") is None  # missing required fields
    assert deserialize_prediction(
        '{"decision_kind":"bogus","tenant_id":"t","agent_id":"a",'
        '"decision_id":"d","predicted_confidence":0.5,'
        '"context_hash":"x","ts":"now"}'
    ) is None  # invalid decision_kind

    assert deserialize_observation("not json") is None
    assert deserialize_observation("{}") is None
    assert deserialize_observation(
        '{"tenant_id":"t","agent_id":"a","decision_id":"d",'
        '"actual_reward":2.0,"latency_ms":1,"completed_at":"now"}'
    ) is None  # actual_reward out of range


# ── join_traces ───────────────────────────────────────────────────────


def test_join_pairs_matching_decision_ids():
    preds = [_pred("d-1"), _pred("d-2"), _pred("d-3")]
    obs = [_obs("d-2", reward=0.5), _obs("d-1", reward=0.1)]
    traces = join_traces(preds, obs)
    assert len(traces) == 2
    # Stable in input order on predictions: d-1 first, then d-2 (d-3 dropped)
    assert traces[0].prediction.decision_id == "d-1"
    assert traces[1].prediction.decision_id == "d-2"


def test_join_drops_unpaired():
    """Predictions without observations (still in-flight) and
    observations without predictions (orphaned) are dropped silently."""
    preds = [_pred("d-1")]
    obs = [_obs("d-2"), _obs("d-3")]
    assert join_traces(preds, obs) == []


def test_join_skips_tenant_mismatch():
    """A prediction in tenant t-1 and an observation in tenant t-2
    with the same decision_id is suspicious — MetacogTrace's
    tenant-match invariant rejects it; join logs and continues."""
    preds = [_pred("d-1", tenant="t-1")]
    obs = [_obs("d-1", tenant="t-2")]
    assert join_traces(preds, obs) == []


# ── expected_calibration_error ────────────────────────────────────────


def test_ece_zero_on_empty():
    assert expected_calibration_error([]) == 0.0


def test_ece_zero_on_perfect_calibration():
    """Predictions that exactly match the normalized reward — ECE = 0."""
    traces = []
    # actual_reward=0.0 → normalized 0.5; predict 0.5
    for i in range(10):
        p = _pred(decision_id=f"d-{i}", predicted=0.5)
        o = _obs(decision_id=f"d-{i}", reward=0.0)
        traces.append(MetacogTrace(prediction=p, observation=o))
    assert expected_calibration_error(traces) == pytest.approx(0.0)


def test_ece_peaks_on_overconfident_failure():
    """All predictions=0.9, all rewards=−1.0 (normalized 0.0). Every
    sample lands in the same bucket; ECE = 0.9."""
    traces = []
    for i in range(20):
        p = _pred(decision_id=f"d-{i}", predicted=0.9)
        o = _obs(decision_id=f"d-{i}", reward=-1.0)
        traces.append(MetacogTrace(prediction=p, observation=o))
    ece = expected_calibration_error(traces)
    assert ece == pytest.approx(0.9, abs=1e-6)


def test_ece_handles_boundary_prediction_of_one():
    """predicted_confidence = 1.0 must land in the last bucket, not
    out-of-range. Edge case the bin-index clamp protects against."""
    p = _pred(decision_id="d-edge", predicted=1.0)
    o = _obs(decision_id="d-edge", reward=1.0)  # normalized 1.0
    trace = MetacogTrace(prediction=p, observation=o)
    # Perfect at the high boundary → ECE = 0
    assert expected_calibration_error([trace]) == pytest.approx(0.0)


def test_ece_bin_count_validated():
    with pytest.raises(ValueError, match="bins must be positive"):
        expected_calibration_error([], bins=0)
    with pytest.raises(ValueError, match="bins must be positive"):
        expected_calibration_error([], bins=-3)


def test_ece_default_bin_count_is_ten():
    """Luna's #616 §9 call: 10 bins is the default. Lock it so a
    future refactor doesn't silently change calibration semantics."""
    import inspect
    sig = inspect.signature(expected_calibration_error)
    assert sig.parameters["bins"].default == 10


def test_ece_at_bucket_boundary_lands_in_upper():
    """Superpowers IMPORTANT #2 — lock the half-open [lo, hi)
    convention at lower edges. predicted_confidence = 0.1 with
    10 bins must land in bucket index 1, not 0. A future 'fix' that
    flips this convention would silently change calibration
    semantics for every existing tenant."""
    # Two traces with identical predicted=0.1 but different rewards.
    # If they land in the same (upper) bucket, ECE = |0.1 − 0.5| = 0.4.
    # If they land in DIFFERENT buckets, ECE would be different — and
    # the test would catch it.
    p1 = _pred(decision_id="d1", predicted=0.1)
    p2 = _pred(decision_id="d2", predicted=0.1)
    # normalize: reward=0.0 → 0.5
    o1 = _obs(decision_id="d1", reward=0.0)
    o2 = _obs(decision_id="d2", reward=0.0)
    traces = [
        MetacogTrace(prediction=p1, observation=o1),
        MetacogTrace(prediction=p2, observation=o2),
    ]
    # Both predictions in same bucket; mean pred 0.1, mean actual 0.5
    # → bucket contributes |0.1 − 0.5| × (2/2) = 0.4
    assert expected_calibration_error(traces, bins=10) == pytest.approx(0.4)
