"""Unit tests for the metacognition schemas (M1 of #616).

Pure-dataclass tests — no DB. Locks the load-bearing invariants:
DECISION_KINDS membership, predicted_confidence in [0,1],
actual_reward in [-1,1], MetacogTrace's decision_id + tenant_id
matching on construction.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.schemas.metacog import (
    DECISION_KINDS,
    ConfidencePrediction,
    MetacogTrace,
    OutcomeObservation,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── ConfidencePrediction ──────────────────────────────────────────────


def test_prediction_accepts_canonical_shape():
    p = ConfidencePrediction(
        tenant_id="00000000-0000-0000-0000-000000000001",
        agent_id="00000000-0000-0000-0000-000000000002",
        decision_id="00000000-0000-0000-0000-000000000003",
        decision_kind="rl_route_chat_response",
        predicted_confidence=0.72,
        context_hash="abc123",
        ts=_now(),
    )
    assert p.predicted_confidence == 0.72
    assert p.decision_kind in DECISION_KINDS


def test_prediction_rejects_unknown_kind():
    with pytest.raises(ValueError, match="decision_kind must be one of"):
        ConfidencePrediction(
            tenant_id="00000000-0000-0000-0000-000000000001",
            agent_id="00000000-0000-0000-0000-000000000002",
            decision_id="00000000-0000-0000-0000-000000000003",
            decision_kind="weather_forecast",
            predicted_confidence=0.5,
            context_hash="x",
            ts=_now(),
        )


def test_prediction_rejects_out_of_range_confidence():
    for bad in (-0.01, 1.01, 5.0, -1.0):
        with pytest.raises(ValueError, match="predicted_confidence must be in"):
            ConfidencePrediction(
                tenant_id="t",
                agent_id="a",
                decision_id="d",
                decision_kind="rl_route_chat_response",
                predicted_confidence=bad,
                context_hash="x",
                ts=_now(),
            )


def test_prediction_accepts_boundary_confidences():
    """0.0 and 1.0 are valid — exactly-uncertain and exactly-certain."""
    for ok in (0.0, 1.0):
        ConfidencePrediction(
            tenant_id="t",
            agent_id="a",
            decision_id=f"d-{ok}",
            decision_kind="rl_route_chat_response",
            predicted_confidence=ok,
            context_hash="x",
            ts=_now(),
        )


def test_prediction_to_dict_roundtrips_all_fields():
    p = ConfidencePrediction(
        tenant_id="t", agent_id="a", decision_id="d",
        decision_kind="affect_appraise",
        predicted_confidence=0.3, context_hash="h", ts=_now(),
    )
    d = p.to_dict()
    assert d["decision_kind"] == "affect_appraise"
    assert d["predicted_confidence"] == 0.3
    # Reconstruction must yield an identical object
    assert ConfidencePrediction(**d) == p


# ── OutcomeObservation ────────────────────────────────────────────────


def test_observation_accepts_canonical_shape():
    o = OutcomeObservation(
        tenant_id="t", agent_id="a", decision_id="d",
        actual_reward=0.4, latency_ms=120, completed_at=_now(),
    )
    assert o.actual_reward == 0.4
    assert o.error is None


def test_observation_rejects_out_of_range_reward():
    for bad in (-1.01, 1.01, 5.0):
        with pytest.raises(ValueError, match="actual_reward must be in"):
            OutcomeObservation(
                tenant_id="t", agent_id="a", decision_id="d",
                actual_reward=bad, latency_ms=10, completed_at=_now(),
            )


def test_observation_rejects_negative_latency():
    with pytest.raises(ValueError, match="latency_ms must be non-negative"):
        OutcomeObservation(
            tenant_id="t", agent_id="a", decision_id="d",
            actual_reward=0.0, latency_ms=-1, completed_at=_now(),
        )


def test_observation_carries_error_string():
    o = OutcomeObservation(
        tenant_id="t", agent_id="a", decision_id="d",
        actual_reward=-0.5, latency_ms=99, completed_at=_now(),
        error="timeout",
    )
    assert o.error == "timeout"


# ── MetacogTrace ──────────────────────────────────────────────────────


def test_trace_pairs_matching_decision_id():
    p = ConfidencePrediction(
        tenant_id="t", agent_id="a", decision_id="d-1",
        decision_kind="rl_route_chat_response",
        predicted_confidence=0.6, context_hash="x", ts=_now(),
    )
    o = OutcomeObservation(
        tenant_id="t", agent_id="a", decision_id="d-1",
        actual_reward=0.2, latency_ms=50, completed_at=_now(),
    )
    t = MetacogTrace(prediction=p, observation=o)
    assert t.prediction.decision_id == t.observation.decision_id


def test_trace_rejects_decision_id_mismatch():
    p = ConfidencePrediction(
        tenant_id="t", agent_id="a", decision_id="d-1",
        decision_kind="rl_route_chat_response",
        predicted_confidence=0.6, context_hash="x", ts=_now(),
    )
    o = OutcomeObservation(
        tenant_id="t", agent_id="a", decision_id="d-2",
        actual_reward=0.2, latency_ms=50, completed_at=_now(),
    )
    with pytest.raises(ValueError, match="decision_id"):
        MetacogTrace(prediction=p, observation=o)


def test_trace_rejects_tenant_mismatch():
    p = ConfidencePrediction(
        tenant_id="t-1", agent_id="a", decision_id="d-1",
        decision_kind="rl_route_chat_response",
        predicted_confidence=0.6, context_hash="x", ts=_now(),
    )
    o = OutcomeObservation(
        tenant_id="t-2", agent_id="a", decision_id="d-1",
        actual_reward=0.2, latency_ms=50, completed_at=_now(),
    )
    with pytest.raises(ValueError, match="tenant_id"):
        MetacogTrace(prediction=p, observation=o)


def test_trace_rejects_agent_mismatch():
    """Superpowers IMPORTANT #1 — split-attribution guard. A
    prediction made by agent-A paired with an observation attributed
    to agent-B is suspicious and must raise."""
    p = ConfidencePrediction(
        tenant_id="t", agent_id="a-1", decision_id="d-1",
        decision_kind="rl_route_chat_response",
        predicted_confidence=0.6, context_hash="x", ts=_now(),
    )
    o = OutcomeObservation(
        tenant_id="t", agent_id="a-2", decision_id="d-1",
        actual_reward=0.2, latency_ms=50, completed_at=_now(),
    )
    with pytest.raises(ValueError, match="agent_id"):
        MetacogTrace(prediction=p, observation=o)


def test_trace_normalized_reward_rescales_to_unit_interval():
    """[-1, 1] reward → [0, 1] for ECE comparison."""
    p = ConfidencePrediction(
        tenant_id="t", agent_id="a", decision_id="d",
        decision_kind="rl_route_chat_response",
        predicted_confidence=0.5, context_hash="x", ts=_now(),
    )
    # actual_reward = -1.0 → normalized 0.0
    o_low = OutcomeObservation(
        tenant_id="t", agent_id="a", decision_id="d", actual_reward=-1.0,
        latency_ms=1, completed_at=_now(),
    )
    assert MetacogTrace(prediction=p, observation=o_low).normalized_reward == 0.0

    # actual_reward = 0.0 → normalized 0.5
    o_mid = OutcomeObservation(
        tenant_id="t", agent_id="a", decision_id="d", actual_reward=0.0,
        latency_ms=1, completed_at=_now(),
    )
    assert MetacogTrace(prediction=p, observation=o_mid).normalized_reward == 0.5

    # actual_reward = 1.0 → normalized 1.0
    o_high = OutcomeObservation(
        tenant_id="t", agent_id="a", decision_id="d", actual_reward=1.0,
        latency_ms=1, completed_at=_now(),
    )
    assert MetacogTrace(prediction=p, observation=o_high).normalized_reward == 1.0
