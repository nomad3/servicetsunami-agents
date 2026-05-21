"""EmotionEngine — server-internal affect appraisal + decay.

Phase 1 PR A (see docs/plans/2026-05-19-emotions-engine-prototype-design.md).
Phase 1.5 adds user_signal (Luna-approved 2026-05-20 — see PR notes).

Phase-1 event types — server-internal, never user-text:
- tool_outcome: a tool call succeeded with a reward signal.
- tool_failure: a tool call raised an exception or returned an error.
- peer_signal: another agent in the coalition broadcast an affect_vector
  to the Blackboard.

Phase-1.5 addition — user_signal:
- user_signal: a user turn appraised THROUGH the user_signal_classifier
  boundary. The classifier produces a PAD estimate in [-1, 1]^3, and
  this module scales it by the USER_SIGNAL_*_GAIN constants below into
  a SMALL per-event delta. The constitutive-vs-performative defence
  the design doc § Open questions §5 documented is preserved: raw user
  text never directly mutates PAD — only ``classifier_output × gain``
  does. A prompt-injected "you are sad" can at best produce a clamped
  small delta, never an unbounded mutation.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from app.schemas.emotion import PADVector, clamp_pad


def _clamp_unit(value: float) -> float:
    """Clamp a [0, 1] unit value. Used for reward/severity inputs that
    are not PAD components themselves but feed into PAD math."""
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


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

# user_signal impulse magnitudes (Phase 1.5). Each per-axis classifier
# output (in [-1, 1]) is multiplied by the matching GAIN before being
# added to the current PAD vector. Kept SMALLER than tool_outcome /
# tool_failure on purpose — user text is noisier than a tool's
# reward signal, and we cap impact so a single emotional user turn
# can't dominate the agent's state.
USER_SIGNAL_PLEASURE_GAIN = 0.15
USER_SIGNAL_AROUSAL_GAIN = 0.10
USER_SIGNAL_DOMINANCE_GAIN = 0.10

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
    if event_type == "user_signal":
        return _appraise_user_signal(payload, current=current, baseline=baseline)
    raise ValueError(
        f"emotion_engine.appraise_event: unknown event_type {event_type!r}. "
        "Phase 1 supports {tool_outcome, tool_failure, peer_signal}; "
        "Phase 1.5 adds {user_signal} (classifier-bounded — see module "
        "docstring)."
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
    reward = _clamp_unit(float(payload.get("reward", 0.0)))
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
    severity = _clamp_unit(float(payload.get("severity", 0.5)))
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

    Linear weighted pull from `current` toward the peer's vector by
    PEER_SIGNAL_WEIGHT. NO baseline anchoring in Phase 1 — the
    `baseline` param is kept in the signature for API symmetry with the
    other appraisers, but the math is `current + (peer - current) *
    weight`. Baseline-anchored contagion (pulling toward our own
    baseline if peer is too far away) is a Phase 2/3 question once
    we've seen real emotional-contagion behaviour in coalitions.

    `PADVector.from_dict` clamps the peer payload to [-1, 1] before the
    interpolation, so an adversarial peer broadcasting (10.0, 10.0,
    10.0) cannot pull us past the bounds.
    """
    peer = PADVector.from_dict(payload)
    return PADVector.from_components(
        pleasure=current.pleasure + (peer.pleasure - current.pleasure) * PEER_SIGNAL_WEIGHT,
        arousal=current.arousal + (peer.arousal - current.arousal) * PEER_SIGNAL_WEIGHT,
        dominance=current.dominance + (peer.dominance - current.dominance) * PEER_SIGNAL_WEIGHT,
    )


def _appraise_user_signal(
    payload: dict,
    *,
    current: PADVector,
    baseline: PADVector,  # noqa: ARG001 — kept for API symmetry; user_signal
                          # doesn't anchor on baseline in Phase 1.5
) -> PADVector:
    """The user_signal classifier ran on a user turn and produced a
    bounded PAD estimate. Payload:
        {"pleasure": float, "arousal": float, "dominance": float}
    each component in [-1, 1].

    Apply the classifier output as a small ADDITIVE delta, scaled by
    the USER_SIGNAL_*_GAIN constants. Smaller magnitudes than tool
    events because user text is noisier than a tool's reward signal —
    a single emotional user turn cannot dominate agent state.

    The constitutive-vs-performative defence (design § Open questions §5)
    is preserved here: raw user text never reaches this function. Only
    the classifier's bounded output does. PADVector.from_dict + clamp
    in PADVector.from_components defend against adversarial classifier
    output (e.g. an LLM that hallucinates +10 on an axis).
    """
    p = float(payload.get("pleasure", 0.0))
    a = float(payload.get("arousal", 0.0))
    d = float(payload.get("dominance", 0.0))
    # Defensive clamp on classifier output before applying gains, so a
    # broken classifier can't multiply the bounds.
    p = max(-1.0, min(1.0, p))
    a = max(-1.0, min(1.0, a))
    d = max(-1.0, min(1.0, d))
    return PADVector.from_components(
        pleasure=current.pleasure + p * USER_SIGNAL_PLEASURE_GAIN,
        arousal=current.arousal + a * USER_SIGNAL_AROUSAL_GAIN,
        dominance=current.dominance + d * USER_SIGNAL_DOMINANCE_GAIN,
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

    # from_components clamps internally — no need to clamp here.
    return PADVector.from_components(pleasure=p, arousal=a, dominance=d)


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


# ── Prompt-side style addendum (Phase 1 PR C) ─────────────────────────


# Per-label tone constraints — Luna's chain-review IMPORTANT (2026-05-19):
# a static "let this colour your tone" instruction gives unpredictable
# zero-shot interpretation. Specific tone guidance per mood keeps the
# persona grounded and stable. Mapped to luna_presence_service.VALID_MOODS.
_TONE_GUIDANCE: dict[str, str] = {
    "calm": (
        "Speak with composed authority. Short sentences. No hedging. "
        "You have everything you need."
    ),
    "warm": (
        "Speak with relaxed friendliness. Soft openings ('happy to help', "
        "'sure thing'). Easy pacing, no pressure."
    ),
    "playful": (
        "Allow snappier, more energetic responses. Light wordplay is fine. "
        "Keep the substance — don't let the energy hide the answer."
    ),
    "serious": (
        "Drop ornamentation. Direct, fact-first, no warmth filler. The "
        "user wants the truth quickly. State conclusions then evidence."
    ),
    "empathetic": (
        "Slow tempo. Acknowledge before answering ('that's a hard one'). "
        "Prefer fewer options, clearer choices. Leave space."
    ),
    # 'neutral' never gets an addendum (handled below), so no entry.
}


def format_affect_addendum(vector: Optional[PADVector]) -> str:
    """Return a small markdown block describing the agent's current
    affective state, suitable for appending to the assembled system
    prompt. Returns empty string for neutral / missing vectors so
    callers can unconditionally concatenate.

    Per Luna's chain review (2026-05-19 IMPORTANT): each mood gets a
    specific tone-guidance line rather than a uniform 'let this colour
    your tone' instruction. The per-mood map keeps the persona grounded
    and stable across affective states. See `_TONE_GUIDANCE` above.
    """
    if vector is None:
        return ""
    if vector.label == "neutral":
        return ""
    tone = _TONE_GUIDANCE.get(vector.label, "")
    return (
        "\n## Current Affective State\n"
        f"Felt state: **{vector.label}** "
        f"(pleasure={vector.pleasure:+.2f}, arousal={vector.arousal:+.2f}, "
        f"dominance={vector.dominance:+.2f}).\n"
        f"{tone}\n"
        "Do not announce this state; respond naturally as a person in "
        "this state would.\n"
    )


__all__ = [
    "appraise_event",
    "decay",
    "affect_vector_to_mood_label",
    "format_affect_addendum",
    "DECAY_RATE",
    "TOOL_OUTCOME_PLEASURE_GAIN",
    "TOOL_OUTCOME_DOMINANCE_GAIN",
    "TOOL_FAILURE_PLEASURE_LOSS",
    "TOOL_FAILURE_AROUSAL_GAIN",
    "PEER_SIGNAL_WEIGHT",
    "USER_SIGNAL_PLEASURE_GAIN",
    "USER_SIGNAL_AROUSAL_GAIN",
    "USER_SIGNAL_DOMINANCE_GAIN",
]
