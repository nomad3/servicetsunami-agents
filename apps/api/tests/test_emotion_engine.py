"""Unit tests for EmotionEngine — Phase 1 PR A.

Covers the test plan in docs/plans/2026-05-19-emotions-engine-prototype-design.md
§ "Test plan (Phase 1)":

- appraise_event(tool_outcome reward=1.0) shifts pleasure & dominance positive
- decay returns to baseline within 6 ticks of no input (~70% recovery)
- mood label maps every PAD octant to luna_presence_service.VALID_MOODS
- constitutive-vs-performative invariant: no user_signal path exists in Phase 1
"""
from __future__ import annotations

import itertools

import pytest

from app.schemas.emotion import PADVector, _pad_to_mood_label, clamp_pad
from app.services.emotion_engine import (
    DECAY_RATE,
    TOOL_OUTCOME_DOMINANCE_GAIN,
    TOOL_OUTCOME_PLEASURE_GAIN,
    affect_vector_to_mood_label,
    appraise_event,
    decay,
)
from app.services.luna_presence_service import VALID_MOODS


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def neutral_pad() -> PADVector:
    return PADVector.neutral()


@pytest.fixture
def neutral_baseline() -> PADVector:
    return PADVector.neutral()


# ── Appraisal: tool_outcome ───────────────────────────────────────────


def test_tool_outcome_full_reward_shifts_pleasure_and_dominance_positive(
    neutral_pad, neutral_baseline
):
    result = appraise_event(
        "tool_outcome",
        {"reward": 1.0},
        current=neutral_pad,
        baseline=neutral_baseline,
    )
    assert result.pleasure == pytest.approx(TOOL_OUTCOME_PLEASURE_GAIN)
    assert result.dominance == pytest.approx(TOOL_OUTCOME_DOMINANCE_GAIN)
    assert result.pleasure > 0
    assert result.dominance > 0


def test_tool_outcome_zero_reward_no_shift(neutral_pad, neutral_baseline):
    result = appraise_event(
        "tool_outcome",
        {"reward": 0.0},
        current=neutral_pad,
        baseline=neutral_baseline,
    )
    assert result.pleasure == pytest.approx(0.0)
    assert result.arousal == pytest.approx(0.0)
    assert result.dominance == pytest.approx(0.0)


def test_tool_outcome_clamps_at_upper_bound(neutral_baseline):
    high = PADVector.from_components(pleasure=0.95, arousal=0.0, dominance=0.0)
    result = appraise_event(
        "tool_outcome",
        {"reward": 1.0},
        current=high,
        baseline=neutral_baseline,
    )
    assert result.pleasure == pytest.approx(1.0)  # clamped to PAD_MAX


# ── Appraisal: tool_failure (the Luna temperature-flip correction) ────


def test_tool_failure_shifts_pleasure_down_arousal_up(neutral_pad, neutral_baseline):
    """The architectural correction Luna caught during design review:
    failure → low pleasure + HIGH arousal (survival focus), NOT
    low pleasure + low arousal (relaxed sad)."""
    result = appraise_event(
        "tool_failure",
        {"severity": 1.0},
        current=neutral_pad,
        baseline=neutral_baseline,
    )
    assert result.pleasure < 0, "failure must reduce pleasure"
    assert result.arousal > 0, "failure must elevate arousal (Luna's correction)"
    assert result.dominance < 0, "failure reduces dominance (helplessness)"


def test_tool_failure_default_severity(neutral_pad, neutral_baseline):
    """Missing severity defaults to 0.5 — partial impulse, not zero."""
    result = appraise_event(
        "tool_failure", {}, current=neutral_pad, baseline=neutral_baseline
    )
    assert result.pleasure < 0
    assert result.arousal > 0


# ── Appraisal: peer_signal (emotional contagion) ──────────────────────


def test_peer_signal_pulls_toward_peer(neutral_pad, neutral_baseline):
    peer_payload = {"pleasure": 1.0, "arousal": 0.0, "dominance": 1.0}
    result = appraise_event(
        "peer_signal", peer_payload, current=neutral_pad, baseline=neutral_baseline
    )
    # Should move some fraction of the way toward the peer.
    assert 0 < result.pleasure < 1.0
    assert 0 < result.dominance < 1.0
    # Arousal stays near 0 because peer arousal is 0.
    assert result.arousal == pytest.approx(0.0, abs=1e-6)


# ── Unknown event type — the structural defence ────────────────────────


def test_unknown_event_type_raises(neutral_pad, neutral_baseline):
    """Phase 1 deliberately rejects unknown event types. user_signal
    MUST not silently fall through — there is no affect classifier and
    appraising raw user text is the central constitutive-vs-performative
    failure mode."""
    with pytest.raises(ValueError, match="unknown event_type"):
        appraise_event(
            "user_signal",
            {"text": "I am sad"},
            current=neutral_pad,
            baseline=neutral_baseline,
        )


def test_no_user_signal_path_exists():
    """Constitutive-vs-performative invariant: the EmotionEngine module
    must not expose any function whose name suggests it processes user
    text. This is the test the design doc § "Test plan" calls out as
    the central guarantee that affect is constitutive (server-internal)
    not performative (user-controlled)."""
    import app.services.emotion_engine as em

    forbidden = ["appraise_user_text", "appraise_user_message", "user_signal", "process_user"]
    public_names = [n for n in dir(em) if not n.startswith("_")]
    for f in forbidden:
        assert f not in public_names, (
            f"emotion_engine exposes {f!r} — Phase 1 must NOT have any "
            "user-text appraisal pathway. See design doc § Open questions §5."
        )


# ── Decay ─────────────────────────────────────────────────────────────


def test_decay_returns_to_baseline_within_6_ticks(neutral_baseline):
    """Test plan invariant: decay function returns to baseline within
    6 ticks of no input. With DECAY_RATE = 0.2, after 6 ticks we're at
    (1 - 0.8^6) ≈ 0.738 of the way there."""
    elevated = PADVector.from_components(pleasure=1.0, arousal=1.0, dominance=1.0)
    result = decay(elevated, neutral_baseline, ticks=6)
    # 6 ticks * 0.2 rate -> ~73.8% recovery.
    expected_remaining = 1.0 * (1 - DECAY_RATE) ** 6
    assert result.pleasure == pytest.approx(expected_remaining, abs=1e-6)
    assert result.arousal == pytest.approx(expected_remaining, abs=1e-6)
    assert result.dominance == pytest.approx(expected_remaining, abs=1e-6)
    # Sanity: > 70% recovered (i.e. remaining < 30%)
    assert result.pleasure < 0.30


def test_decay_zero_ticks_no_change(neutral_baseline):
    elevated = PADVector.from_components(pleasure=0.7, arousal=0.5, dominance=0.3)
    result = decay(elevated, neutral_baseline, ticks=0)
    assert result.pleasure == pytest.approx(elevated.pleasure)
    assert result.arousal == pytest.approx(elevated.arousal)
    assert result.dominance == pytest.approx(elevated.dominance)


def test_decay_toward_nonzero_baseline():
    """Baseline doesn't have to be zero — agents with a curious-warm
    personality might have baseline P=+0.3, D=+0.2."""
    baseline = PADVector.from_components(pleasure=0.3, arousal=-0.1, dominance=0.2)
    elevated = PADVector.from_components(pleasure=1.0, arousal=1.0, dominance=1.0)
    result = decay(elevated, baseline, ticks=20)
    # 20 ticks of 0.2 decay -> essentially fully relaxed to baseline.
    assert result.pleasure == pytest.approx(baseline.pleasure, abs=0.02)
    assert result.arousal == pytest.approx(baseline.arousal, abs=0.02)
    assert result.dominance == pytest.approx(baseline.dominance, abs=0.02)


# ── Mood label mapping (legacy-reader compatibility) ──────────────────


def test_every_pad_octant_maps_to_valid_mood():
    """Test plan invariant: affect_vector_to_mood_label returns a value
    in luna_presence_service.VALID_MOODS for every PAD octant."""
    # Sample each of 8 octants + origin.
    values = [-0.7, 0.0, 0.7]
    for p, a, d in itertools.product(values, repeat=3):
        vec = PADVector.from_components(pleasure=p, arousal=a, dominance=d)
        label = vec.label
        assert label in VALID_MOODS, (
            f"PAD ({p}, {a}, {d}) -> {label!r}, not in VALID_MOODS {VALID_MOODS}"
        )


def test_near_origin_maps_to_neutral():
    vec = PADVector.from_components(pleasure=0.05, arousal=-0.05, dominance=0.02)
    assert vec.label == "neutral"


def test_high_pleasure_high_arousal_is_playful():
    vec = PADVector.from_components(pleasure=0.7, arousal=0.7, dominance=0.5)
    assert vec.label == "playful"


def test_high_pleasure_low_arousal_high_dominance_is_calm():
    vec = PADVector.from_components(pleasure=0.7, arousal=-0.5, dominance=0.5)
    assert vec.label == "calm"


def test_high_pleasure_low_arousal_low_dominance_is_warm():
    vec = PADVector.from_components(pleasure=0.7, arousal=-0.5, dominance=-0.5)
    assert vec.label == "warm"


def test_low_pleasure_high_dominance_is_serious():
    vec = PADVector.from_components(pleasure=-0.7, arousal=0.3, dominance=0.5)
    assert vec.label == "serious"


def test_low_pleasure_low_dominance_is_empathetic():
    vec = PADVector.from_components(pleasure=-0.7, arousal=-0.3, dominance=-0.5)
    assert vec.label == "empathetic"


def test_affect_vector_to_mood_label_none_returns_neutral():
    assert affect_vector_to_mood_label(None) == "neutral"


def test_affect_vector_to_mood_label_accepts_dict():
    """Helper accepts both PADVector and the raw JSONB dict, so callers
    reading directly from the column don't need to hydrate first."""
    label = affect_vector_to_mood_label({"pleasure": 0.7, "arousal": 0.7, "dominance": 0.3})
    assert label == "playful"


# ── PADVector self-tests ──────────────────────────────────────────────


def test_padvector_clamps_inputs():
    vec = PADVector.from_components(pleasure=2.5, arousal=-3.0, dominance=0.0)
    assert vec.pleasure == 1.0
    assert vec.arousal == -1.0
    assert vec.dominance == 0.0


def test_padvector_serialises_round_trip():
    original = PADVector.from_components(pleasure=0.4, arousal=-0.2, dominance=0.6)
    hydrated = PADVector.from_dict(original.to_dict())
    assert hydrated.pleasure == pytest.approx(original.pleasure)
    assert hydrated.arousal == pytest.approx(original.arousal)
    assert hydrated.dominance == pytest.approx(original.dominance)
    assert hydrated.label == original.label


def test_padvector_to_dict_contains_all_fields():
    vec = PADVector.from_components(pleasure=0.1, arousal=0.2, dominance=0.3)
    d = vec.to_dict()
    assert set(d.keys()) == {"pleasure", "arousal", "dominance", "label", "updated_at"}


def test_padvector_neutral_factory():
    vec = PADVector.neutral()
    assert vec.pleasure == 0.0
    assert vec.arousal == 0.0
    assert vec.dominance == 0.0
    assert vec.label == "neutral"


# ── Pure-function helper sanity ───────────────────────────────────────


def test_pad_to_mood_label_pure_no_side_effects():
    """_pad_to_mood_label is called by the model layer before the
    PADVector class is fully constructed, so it MUST be a pure function
    with no dependency on PADVector. This test fences that invariant."""
    label = _pad_to_mood_label(0.5, 0.5, 0.5)
    assert label in VALID_MOODS


def test_clamp_pad_public_helper():
    """clamp_pad is the public clamping helper (was leading-underscore
    `_clamp` previously; N1 of superpowers code-review). The leading-
    underscore alias is preserved for back-compat but new code uses
    clamp_pad."""
    assert clamp_pad(2.5) == 1.0
    assert clamp_pad(-3.0) == -1.0
    assert clamp_pad(0.5) == 0.5
