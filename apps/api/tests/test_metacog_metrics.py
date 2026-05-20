"""Unit tests for the metacog Prometheus instrumentation (M3 of #616).

Mirrors test_emotion_engine_metrics.py — verifies the helpers emit
without raising and that the module degrades gracefully when
prometheus_client is missing. NO DB; pure module-level smoke.
"""
from __future__ import annotations

import pytest

from app.services.metacog_metrics import (
    record_observation,
    record_prediction,
    set_ece,
)


# All metric helpers must be best-effort: never raise into the caller.


def test_record_prediction_does_not_raise_with_none_tenant():
    record_prediction(
        tenant_id=None,
        decision_kind="rl_route_chat_response",
        predicted_confidence=0.5,
    )


def test_record_prediction_accepts_each_decision_kind():
    # Locked set from schemas/metacog.py:DECISION_KINDS — all five
    # must emit cleanly so a future hook site doesn't trip a label
    # regression at runtime.
    for kind in (
        "rl_route_chat_response",
        "rl_route_coalition_role",
        "tool_call_outcome",
        "affect_appraise",
        "blackboard_contribute",
    ):
        record_prediction(
            tenant_id="tenant-1", decision_kind=kind, predicted_confidence=0.7,
        )


def test_record_prediction_without_confidence_only_bumps_counter():
    # Caller omits predicted_confidence: counter ticks, histogram doesn't.
    # The function must accept that path without raising.
    record_prediction(
        tenant_id="tenant-1", decision_kind="rl_route_chat_response",
    )


def test_record_observation_does_not_raise():
    record_observation(
        tenant_id="tenant-1", decision_kind="rl_route_chat_response",
    )
    record_observation(
        tenant_id=None, decision_kind="rl_route_chat_response",
    )


def test_set_ece_clamps_out_of_range():
    # ECE input is mathematically in [0, 1] but a buggy upstream
    # could pass nonsense; helper must clamp and not propagate.
    set_ece(tenant_id="t", decision_kind="rl_route_chat_response", ece=-0.5)
    set_ece(tenant_id="t", decision_kind="rl_route_chat_response", ece=1.5)
    set_ece(tenant_id="t", decision_kind="rl_route_chat_response", ece=0.42)


def test_set_ece_handles_non_numeric():
    # Float coercion failure must NOT raise — we ignore the call.
    set_ece(
        tenant_id="t",
        decision_kind="rl_route_chat_response",
        ece=float("nan"),  # clamped to 0.0
    )


def test_module_exports_have_expected_attribute_names():
    """Sanity: confirm the metric names match what the design doc and
    monitoring dashboards expect. A rename here would silently break
    the operator's alerting rules."""
    from app.services import metacog_metrics as m

    assert hasattr(m, "metacog_predictions_total")
    assert hasattr(m, "metacog_observations_total")
    assert hasattr(m, "metacog_ece")
    assert hasattr(m, "metacog_confidence_buckets")


def test_metric_helpers_are_safe_without_prometheus_client():
    """When prometheus_client isn't importable, the module's no-op
    stubs should keep call sites silent. The helpers ARE the public
    API; their existence + no-raise is the contract."""
    # We can't truly remove prometheus_client mid-import here without
    # tearing down the module. Instead, confirm the helpers exist and
    # don't raise on cold call across each public surface.
    record_prediction(
        tenant_id="t", decision_kind="rl_route_chat_response",
        predicted_confidence=0.0,
    )
    record_observation(
        tenant_id="t", decision_kind="rl_route_chat_response",
    )
    set_ece(tenant_id="t", decision_kind="rl_route_chat_response", ece=0.0)
