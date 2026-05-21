"""Tests for the user_signal appraiser added in Phase 1.5.

Locked properties:
- ``appraise_event(event_type='user_signal', ...)`` no longer raises;
  it routes to ``_appraise_user_signal``.
- The classifier output is scaled by USER_SIGNAL_*_GAIN and added
  to the current PAD — no replacement, only delta.
- Output is always inside PADVector bounds (defensive clamp inside
  the appraiser AND PADVector.from_components).
- Adversarial classifier output (e.g. +10 on an axis) cannot push
  the agent state past the bounds.
"""
from __future__ import annotations

from app.schemas.emotion import PADVector
from app.services.emotion_engine import (
    USER_SIGNAL_AROUSAL_GAIN,
    USER_SIGNAL_DOMINANCE_GAIN,
    USER_SIGNAL_PLEASURE_GAIN,
    appraise_event,
)


def _neutral() -> PADVector:
    return PADVector.neutral()


def test_user_signal_event_type_now_supported():
    """The Phase 1 ValueError on user_signal is gone — Phase 1.5
    routes it to the new handler."""
    result = appraise_event(
        "user_signal",
        {"pleasure": 0.5, "arousal": 0.3, "dominance": -0.2},
        current=_neutral(),
        baseline=_neutral(),
    )
    assert isinstance(result, PADVector)


def test_positive_user_signal_nudges_pleasure_up():
    base = _neutral()
    result = appraise_event(
        "user_signal",
        {"pleasure": 0.8, "arousal": 0.0, "dominance": 0.0},
        current=base,
        baseline=base,
    )
    expected_pleasure = base.pleasure + 0.8 * USER_SIGNAL_PLEASURE_GAIN
    assert result.pleasure == expected_pleasure
    # Other axes unchanged
    assert result.arousal == base.arousal
    assert result.dominance == base.dominance


def test_high_arousal_user_signal_raises_arousal():
    base = _neutral()
    result = appraise_event(
        "user_signal",
        {"pleasure": 0.0, "arousal": 1.0, "dominance": 0.0},
        current=base,
        baseline=base,
    )
    expected_arousal = base.arousal + 1.0 * USER_SIGNAL_AROUSAL_GAIN
    assert result.arousal == expected_arousal


def test_commanding_user_signal_raises_dominance():
    base = _neutral()
    result = appraise_event(
        "user_signal",
        {"pleasure": 0.0, "arousal": 0.0, "dominance": 0.7},
        current=base,
        baseline=base,
    )
    expected_dominance = base.dominance + 0.7 * USER_SIGNAL_DOMINANCE_GAIN
    assert result.dominance == expected_dominance


def test_negative_user_signal_pulls_pleasure_down():
    base = _neutral()
    result = appraise_event(
        "user_signal",
        {"pleasure": -1.0, "arousal": 0.0, "dominance": 0.0},
        current=base,
        baseline=base,
    )
    assert result.pleasure < base.pleasure


def test_user_signal_gains_are_smaller_than_tool_events():
    """Locked invariant: user_signal must NOT dominate tool events.
    A single emotional user turn cannot move PAD as much as a
    successful tool call.

    Locks each axis against the actual TOOL_* constants (not a
    hard-coded ceiling) — superpowers review I3 fix. A regression
    that bumped USER_SIGNAL_AROUSAL_GAIN above TOOL_OUTCOME_AROUSAL_GAIN
    would now break the test.
    """
    from app.services.emotion_engine import (
        TOOL_FAILURE_AROUSAL_GAIN,
        TOOL_FAILURE_DOMINANCE_LOSS,
        TOOL_FAILURE_PLEASURE_LOSS,
        TOOL_OUTCOME_AROUSAL_GAIN,
        TOOL_OUTCOME_DOMINANCE_GAIN,
        TOOL_OUTCOME_PLEASURE_GAIN,
    )
    # Pleasure: strictly smaller — pleasure is the most-load-bearing
    # axis for "user dominance" prevention.
    assert USER_SIGNAL_PLEASURE_GAIN < TOOL_OUTCOME_PLEASURE_GAIN
    assert USER_SIGNAL_PLEASURE_GAIN < TOOL_FAILURE_PLEASURE_LOSS
    # Arousal and dominance: ≤ tool gains. Phase 1.5 deliberately ties
    # USER_SIGNAL_AROUSAL_GAIN to TOOL_OUTCOME_AROUSAL_GAIN (both 0.10)
    # since user-text arousal is the noisiest axis but tool outcomes
    # also nudge arousal mildly. Strict-less would force a constant
    # bump without strong design justification.
    assert USER_SIGNAL_AROUSAL_GAIN <= TOOL_OUTCOME_AROUSAL_GAIN
    assert USER_SIGNAL_AROUSAL_GAIN <= TOOL_FAILURE_AROUSAL_GAIN
    assert USER_SIGNAL_DOMINANCE_GAIN <= TOOL_OUTCOME_DOMINANCE_GAIN
    assert USER_SIGNAL_DOMINANCE_GAIN <= TOOL_FAILURE_DOMINANCE_LOSS


def test_adversarial_classifier_output_cannot_exceed_bounds():
    """If a broken classifier emits +10 on an axis, the appraiser must
    clamp before applying the gain and the resulting PAD must remain
    in [-1, 1]. Locked defence against prompt-injection-style attacks
    routed through the classifier."""
    base = PADVector.from_components(pleasure=0.9, arousal=0.9, dominance=0.9)
    result = appraise_event(
        "user_signal",
        {"pleasure": 10.0, "arousal": 10.0, "dominance": 10.0},
        current=base,
        baseline=base,
    )
    assert -1.0 <= result.pleasure <= 1.0
    assert -1.0 <= result.arousal <= 1.0
    assert -1.0 <= result.dominance <= 1.0


def test_missing_payload_keys_default_to_zero():
    """A classifier that only emitted 'pleasure' should still produce
    a valid result with arousal/dominance unchanged from current."""
    base = PADVector.from_components(pleasure=0.1, arousal=0.2, dominance=0.3)
    result = appraise_event(
        "user_signal",
        {"pleasure": 0.5},
        current=base,
        baseline=base,
    )
    # arousal/dominance get treated as 0 → delta=0 → unchanged
    assert result.arousal == base.arousal
    assert result.dominance == base.dominance


def test_unknown_event_type_message_mentions_phase_1_5():
    """Error message should advertise the new event type so the next
    person grepping for 'user_signal' finds the right path."""
    import pytest
    with pytest.raises(ValueError, match="user_signal"):
        appraise_event(
            "made_up_event",
            {},
            current=_neutral(),
            baseline=_neutral(),
        )
