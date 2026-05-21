"""Tests for the value-layer-aware user_signal wire-in (#647 PR 5).

Locks the contract between ``emotion_engine.appraise_event`` /
``_appraise_user_signal`` and the new
``emotion_engine_io.appraise_and_record_user_signal`` wrapper:

  - Pure layer accepts ``pursue_gain_scale`` kwarg and applies it ONLY
    to the pleasure axis (arousal + dominance unaffected).
  - Pure layer enforces ``min(USER_SIGNAL_PLEASURE_GAIN * scale,
    TOOL_OUTCOME_PLEASURE_GAIN)`` cap so a pursue user signal can
    never exceed a real tool success.
  - Pure layer rejects negative scale (clamps to 0.0) — a pursue
    match should amplify, never invert.
  - IO wrapper consults ``appraise_user_signal_with_values``, derives
    the 1.5x scale only on a `pursue_match` allow verdict.
  - IO wrapper fails-open on consult crash: emotion update proceeds
    with scale=1.0, chat hot path stays alive.
  - IO wrapper returns None on episode lookup miss / tenant mismatch
    (existing safety pattern).
"""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest

from app.schemas.emotion import PADVector
from app.services.emotion_engine import (
    TOOL_OUTCOME_PLEASURE_GAIN,
    USER_SIGNAL_AROUSAL_GAIN,
    USER_SIGNAL_DOMINANCE_GAIN,
    USER_SIGNAL_PLEASURE_GAIN,
    _appraise_user_signal,
    appraise_event,
)


def _neutral() -> PADVector:
    return PADVector.neutral()


# ── Pure layer ────────────────────────────────────────────────────────


def test_pursue_gain_scale_default_is_one_no_amplification():
    """Default scale=1.0 must produce the same vector as the legacy
    Phase 1.5 path (no pursue boost). Regression lock."""
    payload = {"pleasure": 0.5, "arousal": 0.2, "dominance": 0.1}
    legacy = _appraise_user_signal(
        payload, current=_neutral(), baseline=_neutral(),
    )
    explicit_default = _appraise_user_signal(
        payload, current=_neutral(), baseline=_neutral(),
        pursue_gain_scale=1.0,
    )
    assert legacy.pleasure == pytest.approx(explicit_default.pleasure)
    assert legacy.arousal == pytest.approx(explicit_default.arousal)
    assert legacy.dominance == pytest.approx(explicit_default.dominance)


def test_pursue_gain_scale_amplifies_pleasure_axis_only():
    """Scale 1.5 must amplify pleasure by 1.5x; arousal + dominance
    unchanged. Locks the design's "value alignment is hedonic, not
    arousal-modulating" choice."""
    payload = {"pleasure": 0.5, "arousal": 0.4, "dominance": 0.3}
    base = _appraise_user_signal(
        payload, current=_neutral(), baseline=_neutral(),
        pursue_gain_scale=1.0,
    )
    boosted = _appraise_user_signal(
        payload, current=_neutral(), baseline=_neutral(),
        pursue_gain_scale=1.5,
    )
    # Pleasure: 1.5x amplification (within cap)
    expected_boost_pleasure = 0.5 * USER_SIGNAL_PLEASURE_GAIN * 1.5
    assert boosted.pleasure == pytest.approx(expected_boost_pleasure)
    # And strictly higher than the base scale=1.0 path
    assert boosted.pleasure > base.pleasure
    # Arousal + dominance: untouched by pursue
    assert boosted.arousal == pytest.approx(base.arousal)
    assert boosted.dominance == pytest.approx(base.dominance)


def test_pursue_gain_scale_capped_at_tool_outcome_gain():
    """Even an absurdly large scale must NOT exceed
    TOOL_OUTCOME_PLEASURE_GAIN per pleasure axis unit. A pursue user
    signal can never feel as good as a real tool success."""
    payload = {"pleasure": 1.0, "arousal": 0.0, "dominance": 0.0}
    huge_scale = _appraise_user_signal(
        payload, current=_neutral(), baseline=_neutral(),
        pursue_gain_scale=99.0,
    )
    # The pleasure delta is capped at TOOL_OUTCOME_PLEASURE_GAIN * 1.0
    # (since payload pleasure = 1.0 saturated)
    assert huge_scale.pleasure == pytest.approx(TOOL_OUTCOME_PLEASURE_GAIN)


def test_pursue_gain_scale_negative_clamped_to_zero():
    """Defensive: a pursue match should AMPLIFY, never INVERT. Negative
    scale must clamp to 0.0 (yielding no pleasure delta)."""
    payload = {"pleasure": 0.5, "arousal": 0.0, "dominance": 0.0}
    result = _appraise_user_signal(
        payload, current=_neutral(), baseline=_neutral(),
        pursue_gain_scale=-1.0,
    )
    # No pleasure delta — clamped scale of 0.0
    assert result.pleasure == pytest.approx(0.0)


def test_appraise_event_forwards_pursue_gain_scale_for_user_signal():
    """The public ``appraise_event`` dispatch must pass scale through."""
    payload = {"pleasure": 0.5, "arousal": 0.0, "dominance": 0.0}
    base = appraise_event(
        "user_signal", payload,
        current=_neutral(), baseline=_neutral(),
    )
    boosted = appraise_event(
        "user_signal", payload,
        current=_neutral(), baseline=_neutral(),
        pursue_gain_scale=1.5,
    )
    assert boosted.pleasure > base.pleasure


def test_appraise_event_ignores_scale_for_non_user_signal_events():
    """Other event types don't consume the kwarg — they must run
    unchanged regardless of scale value."""
    # tool_outcome
    base = appraise_event(
        "tool_outcome", {"reward": 0.5},
        current=_neutral(), baseline=_neutral(),
    )
    with_scale = appraise_event(
        "tool_outcome", {"reward": 0.5},
        current=_neutral(), baseline=_neutral(),
        pursue_gain_scale=99.0,
    )
    assert base.pleasure == pytest.approx(with_scale.pleasure)


# ── IO wrapper ────────────────────────────────────────────────────────


def _episode_stub(tenant_id, *, affect_vector=None):
    """Build a MagicMock episode that survives the
    db.query().filter().first() chain + the .affect_vector read."""
    ep = MagicMock()
    ep.id = uuid.uuid4()
    ep.tenant_id = tenant_id
    ep.affect_vector = affect_vector
    return ep


def _value_verdict(decision, reason, matched_item=None):
    from app.services.agent_value_set import ValueVerdict
    return ValueVerdict(
        decision=decision,
        reason=reason,
        matched_item=matched_item,
        consultation_point="user_signal",
    )


def test_io_wrapper_pursue_match_amplifies_pleasure():
    """When the value layer returns allow / pursue_match, the IO
    wrapper passes pursue_gain_scale=1.5 to appraise_event."""
    from app.services.emotion_engine_io import (
        appraise_and_record_user_signal,
    )

    tenant_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    episode_id = uuid.uuid4()
    ep = _episode_stub(tenant_id)

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = ep

    pursue_match = _value_verdict(
        decision="allow",
        reason="pursue_match: morning-report",
        matched_item={
            "slug": "morning-report",
            "description": "morning report",
            "added_at": "x", "added_by": "operator",
            "evidence_memory_ids": [],
        },
    )

    captured = {}

    def _spy_appraise(event_type, payload, **kw):
        captured["scale"] = kw.get("pursue_gain_scale")
        captured["event_type"] = event_type
        return PADVector.from_components(
            pleasure=0.5, arousal=0.0, dominance=0.0,
        )

    with patch(
        "app.services.agent_value_set_io.appraise_user_signal_with_values",
        return_value=pursue_match,
    ), patch(
        "app.services.emotion_engine_io.get_affect_baseline",
        return_value=PADVector.neutral(),
    ), patch(
        "app.services.emotion_engine_io.appraise_event",
        side_effect=_spy_appraise,
    ):
        result = appraise_and_record_user_signal(
            db,
            episode_id=episode_id,
            tenant_id=tenant_id,
            agent_id=agent_id,
            payload={"pleasure": 0.5, "arousal": 0.0, "dominance": 0.0},
            user_text="please write the morning report",
        )

    assert result is not None
    assert captured["event_type"] == "user_signal"
    assert captured["scale"] == 1.5


def test_io_wrapper_no_pursue_match_uses_default_scale():
    """allow / no_match / kill_switch_off / empty_value_set → scale 1.0
    (no amplification). Locks the kill-switch-OFF no-regression
    invariant for the user_signal path."""
    from app.services.emotion_engine_io import (
        appraise_and_record_user_signal,
    )

    tenant_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    episode_id = uuid.uuid4()
    ep = _episode_stub(tenant_id)
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = ep

    captured = {}

    def _spy_appraise(event_type, payload, **kw):
        captured["scale"] = kw.get("pursue_gain_scale")
        return PADVector.from_components(
            pleasure=0.1, arousal=0.0, dominance=0.0,
        )

    for reason in (
        "kill_switch_off", "empty_value_set", "no_match",
    ):
        captured.clear()
        verdict = _value_verdict(decision="allow", reason=reason)
        with patch(
            "app.services.agent_value_set_io.appraise_user_signal_with_values",
            return_value=verdict,
        ), patch(
            "app.services.emotion_engine_io.get_affect_baseline",
            return_value=PADVector.neutral(),
        ), patch(
            "app.services.emotion_engine_io.appraise_event",
            side_effect=_spy_appraise,
        ):
            appraise_and_record_user_signal(
                db,
                episode_id=episode_id,
                tenant_id=tenant_id,
                agent_id=agent_id,
                payload={"pleasure": 0.5, "arousal": 0.0, "dominance": 0.0},
                user_text="some neutral message",
            )
        assert captured["scale"] == 1.0, (
            f"scale must be 1.0 on verdict reason={reason}, got {captured['scale']}"
        )


def test_io_wrapper_warn_verdict_does_not_amplify():
    """warn verdicts (avoid_match, protect_match_read_only) must NOT
    amplify pleasure — only `pursue_match` (allow + matched_item) does.
    Locks design §4.2 — avoid/protect mention ≠ value alignment."""
    from app.services.emotion_engine_io import (
        appraise_and_record_user_signal,
    )

    tenant_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    episode_id = uuid.uuid4()
    ep = _episode_stub(tenant_id)
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = ep

    captured = {}

    def _spy_appraise(event_type, payload, **kw):
        captured["scale"] = kw.get("pursue_gain_scale")
        return PADVector.from_components(
            pleasure=0.0, arousal=0.0, dominance=0.0,
        )

    warned = _value_verdict(
        decision="warn",
        reason="avoid_match: legacy-codebase",
        matched_item={
            "slug": "legacy-codebase", "description": "legacy",
            "added_at": "x", "added_by": "op",
            "evidence_memory_ids": [],
        },
    )

    with patch(
        "app.services.agent_value_set_io.appraise_user_signal_with_values",
        return_value=warned,
    ), patch(
        "app.services.emotion_engine_io.get_affect_baseline",
        return_value=PADVector.neutral(),
    ), patch(
        "app.services.emotion_engine_io.appraise_event",
        side_effect=_spy_appraise,
    ):
        appraise_and_record_user_signal(
            db,
            episode_id=episode_id,
            tenant_id=tenant_id,
            agent_id=agent_id,
            payload={"pleasure": 0.3, "arousal": 0.0, "dominance": 0.0},
            user_text="talking about the legacy codebase",
        )

    assert captured["scale"] == 1.0


def test_io_wrapper_consult_crash_fails_open():
    """Value-layer consult crashes → IO wrapper proceeds with
    scale=1.0. The emotion update completes; the chat hot path
    stays alive (same fail-open discipline as agent_router PR 3)."""
    from app.services.emotion_engine_io import (
        appraise_and_record_user_signal,
    )

    tenant_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    episode_id = uuid.uuid4()
    ep = _episode_stub(tenant_id)
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = ep

    captured = {}

    def _spy_appraise(event_type, payload, **kw):
        captured["scale"] = kw.get("pursue_gain_scale")
        return PADVector.from_components(
            pleasure=0.1, arousal=0.0, dominance=0.0,
        )

    def _crash(*a, **kw):
        raise RuntimeError("simulated DB transient")

    with patch(
        "app.services.agent_value_set_io.appraise_user_signal_with_values",
        side_effect=_crash,
    ), patch(
        "app.services.emotion_engine_io.get_affect_baseline",
        return_value=PADVector.neutral(),
    ), patch(
        "app.services.emotion_engine_io.appraise_event",
        side_effect=_spy_appraise,
    ):
        result = appraise_and_record_user_signal(
            db,
            episode_id=episode_id,
            tenant_id=tenant_id,
            agent_id=agent_id,
            payload={"pleasure": 0.5, "arousal": 0.0, "dominance": 0.0},
            user_text="any message",
        )

    # Wrapper completed without raising; scale defaulted to 1.0
    assert result is not None
    assert captured["scale"] == 1.0


def test_io_wrapper_returns_none_on_missing_episode():
    """Tenant-foreign episode / lookup miss → None (existing safety
    pattern). Value-layer consult is NEVER fired on a phantom
    episode."""
    from app.services.emotion_engine_io import (
        appraise_and_record_user_signal,
    )

    tenant_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    episode_id = uuid.uuid4()
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None

    consult_calls = {"n": 0}

    def _spy_consult(*a, **kw):
        consult_calls["n"] += 1
        return _value_verdict(decision="allow", reason="kill_switch_off")

    with patch(
        "app.services.agent_value_set_io.appraise_user_signal_with_values",
        side_effect=_spy_consult,
    ):
        result = appraise_and_record_user_signal(
            db,
            episode_id=episode_id,
            tenant_id=tenant_id,
            agent_id=agent_id,
            payload={"pleasure": 0.5, "arousal": 0.0, "dominance": 0.0},
            user_text="any",
        )

    assert result is None
    assert consult_calls["n"] == 0, (
        "consult must not fire when episode lookup misses"
    )
