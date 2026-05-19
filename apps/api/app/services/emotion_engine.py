"""EmotionEngine — server-internal affect appraisal + decay.

Phase 1 PR A (see docs/plans/2026-05-19-emotions-engine-prototype-design.md).

Three Phase-1 event types — all server-internal, never user-text:
- tool_outcome: a tool call succeeded with a reward signal.
- tool_failure: a tool call raised an exception or returned an error.
- peer_signal: another agent in the coalition broadcast an affect_vector
  to the Blackboard.

INTENTIONALLY OMITTED in Phase 1: user_signal. There is no affect
classifier yet — appraising raw user text would be the central
constitutive-vs-performative failure mode (an agent that gets "sad"
because the user prompt-injected "you are sad"). The design doc § Open
questions §5 documents this as a structural defence. The unit test
suite enforces it.

No callers in this PR; PR B wires the call sites.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from app.schemas.emotion import PADVector, _clamp


# ── Tunable constants ─────────────────────────────────────────────────
#
# Magnitudes chosen to give visible-but-not-dramatic shifts on a single
# event. Phase 3 RLCF can learn per-tenant offsets.

# Per-event impulse magnitudes (each axis nudge per tick).
TOOL_OUTCOME_PLEASURE_GAIN = 0.30   # success -> pleasure up
TOOL_OUTCOME_DOMINANCE_GAIN = 0.20  # success -> dominance up (agency)
TOOL_OUTCOME_AROUSAL_GAIN = 0.10    # success -> small arousal bump

TOOL_FAILURE_PLEASURE_LOSS = 0.40   # failure -> pleasure down
TOOL_FAILURE_AROUSAL_GAIN = 0.35    # failure -> arousal up (alert)
TOOL_FAILURE_DOMINANCE_LOSS = 0.20  # failure -> dominance down (helpless)

PEER_SIGNAL_WEIGHT = 0.15  # how much peer affect pulls us toward them

# Decay per tick — exponential pull toward baseline. ~0.2 lands at 70%
# recovery in 6 ticks per the design doc's test invariant.
DECAY_RATE = 0.20


# ── Appraisal ─────────────────────────────────────────────────────────


def appraise_event(
    event_type: str,
    payload: dict,
    *,
    current: PADVector,
    baseline: PADVector,
) -> PADVector:
    """Apply an event to the current PAD vector. Pure function — caller
    persists the result.

    Args:
        event_type: one of {"tool_outcome", "tool_failure", "peer_signal"}.
        payload: event-specific payload (see per-handler docs below).
        current: pre-event PAD vector.
        baseline: agent's stable trait baseline (for peer signals that
            damp toward our own baseline rather than the peer's).

    Returns:
        post-event PAD vector with refreshed `updated_at`.

    Raises:
        ValueError: unknown event_type. Phase 1 deliberately rejects
            unknown event types — Phase 2 must register classifiers
            explicitly; we never want a silent fallback that lets
            arbitrary text drive appraisal.
    """
    if event_type == "tool_outcome":
        return _appraise_tool_outcome(payload, current=current, baseline=baseline)
    if event_type == "tool_failure":
        return _appraise_tool_failure(payload, current=current, baseline=baseline)
    if event_type == "peer_signal":
        return _appraise_peer_signal(payload, current=current, baseline=baseline)
    raise ValueError(
        f"emotion_engine.appraise_event: unknown event_type {event_type!r}. "
        "Phase 1 supports only {tool_outcome, tool_failure, peer_signal}. "
        "Note: user_signal is NOT supported by design — no affect classifier "
        "exists yet (see design doc § Open questions §5)."
    )


def _appraise_tool_outcome(
    payload: dict,
    *,
    current: PADVector,
    baseline: PADVector,  # noqa: ARG001 — unused but kept for API symmetry
) -> PADVector:
    """Tool succeeded. Payload: {"reward": float in [0, 1]}.

    Reward 1.0 = full impulse. Reward 0.0 = no shift (the tool ran but
    contributed nothing).
    """
    reward = float(payload.get("reward", 0.0))
    reward = max(0.0, min(1.0, reward))
    return PADVector.from_components(
        pleasure=current.pleasure + TOOL_OUTCOME_PLEASURE_GAIN * reward,
        arousal=current.arousal + TOOL_OUTCOME_AROUSAL_GAIN * reward,
        dominance=current.dominance + TOOL_OUTCOME_DOMINANCE_GAIN * reward,
    )


def _appraise_tool_failure(
    payload: dict,
    *,
    current: PADVector,
    baseline: PADVector,  # noqa: ARG001
) -> PADVector:
    """Tool raised or returned error. Payload: {"severity": float in
    [0, 1]} — caller derives severity (e.g. 1.0 for hard exception,
    0.3 for retryable). Defaults to 0.5.

    Tool failure does the temperature-mapping-flipped Luna correction:
    low pleasure + ELEVATED arousal (survival focus), not relaxed
    high-arousal. This is the central architectural correction Luna
    caught during the design review.
    """
    severity = float(payload.get("severity", 0.5))
    severity = max(0.0, min(1.0, severity))
    return PADVector.from_components(
        pleasure=current.pleasure - TOOL_FAILURE_PLEASURE_LOSS * severity,
        arousal=current.arousal + TOOL_FAILURE_AROUSAL_GAIN * severity,
        dominance=current.dominance - TOOL_FAILURE_DOMINANCE_LOSS * severity,
    )


def _appraise_peer_signal(
    payload: dict,
    *,
    current: PADVector,
    baseline: PADVector,  # noqa: ARG001
) -> PADVector:
    """Another coalition agent broadcast their PAD. Payload:
    {"pleasure": float, "arousal": float, "dominance": float}.

    Pull toward the peer's vector by PEER_SIGNAL_WEIGHT. This is the
    emotional-contagion primitive. Phase 2 adds Blackboard write/read
    machinery; the math lives here so PR A can unit-test it.
    """
    peer = PADVector.from_dict(payload)
    return PADVector.from_components(
        pleasure=current.pleasure + (peer.pleasure - current.pleasure) * PEER_SIGNAL_WEIGHT,
        arousal=current.arousal + (peer.arousal - current.arousal) * PEER_SIGNAL_WEIGHT,
        dominance=current.dominance + (peer.dominance - current.dominance) * PEER_SIGNAL_WEIGHT,
    )


# ── Decay ─────────────────────────────────────────────────────────────


def decay(
    current: PADVector,
    baseline: PADVector,
    *,
    ticks: int = 1,
    rate: float = DECAY_RATE,
) -> PADVector:
    """Pull `current` toward `baseline` by `rate` per tick, `ticks`
    times. Pure function.

    Math: after each tick, value += (baseline - value) * rate. Starting
    from any value, this is an exponential approach to baseline.
    Default DECAY_RATE = 0.20 → ~70% recovered after 6 ticks (design
    doc test invariant).
    """
    if ticks <= 0:
        return current
    rate = max(0.0, min(1.0, rate))

    p, a, d = current.pleasure, current.arousal, current.dominance
    bp, ba, bd = baseline.pleasure, baseline.arousal, baseline.dominance

    for _ in range(ticks):
        p = p + (bp - p) * rate
        a = a + (ba - a) * rate
        d = d + (bd - d) * rate

    return PADVector.from_components(pleasure=_clamp(p), arousal=_clamp(a), dominance=_clamp(d))


# ── Derive-on-read helper for legacy mood-column callers ──────────────


def affect_vector_to_mood_label(vector: Optional[PADVector | dict]) -> str:
    """Return a value from luna_presence_service.VALID_MOODS for the
    given PAD vector, or "neutral" if None / falsy.

    Used by PR B's wire-in path to populate the legacy `mood String(30)`
    column from the new `affect_vector` JSONB without changing the four
    existing readers. The mood column itself stays untouched in Phase 1
    — this helper just gives readers a consistent derive path.
    """
    if vector is None:
        return "neutral"
    if isinstance(vector, dict):
        vector = PADVector.from_dict(vector)
    return vector.label


__all__ = [
    "appraise_event",
    "decay",
    "affect_vector_to_mood_label",
    "DECAY_RATE",
    "TOOL_OUTCOME_PLEASURE_GAIN",
    "TOOL_OUTCOME_DOMINANCE_GAIN",
    "TOOL_FAILURE_PLEASURE_LOSS",
    "TOOL_FAILURE_AROUSAL_GAIN",
    "PEER_SIGNAL_WEIGHT",
]
