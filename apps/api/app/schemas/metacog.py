"""Metacognition schemas — Phase 1 substrate (M1 of #616).

Two frozen dataclasses + a frozen enum-set keep the load-bearing
shapes immutable: pre-decision predictions and post-outcome
observations. Joining them via `decision_id` yields a `MetacogTrace`
that downstream tooling (ECE calibration, RL feedback in Phase 3)
can iterate over.

The agent_memory substrate carries these as JSON content with
discriminator memory_type, mirroring the Teamwork Engine pattern
shipped in #608 — no new tables, no migration.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional

# ── Allowed decision kinds ────────────────────────────────────────────
#
# Locks the set so a stale call-site can't smuggle in a misspelled
# kind that the calibration aggregator then silently ignores. New
# kinds added here MUST also be wired into a hook site in the same PR
# — otherwise the catalog drifts.
DECISION_KINDS = frozenset({
    "rl_route_chat_response",   # which CLI/model handles a chat turn
    "rl_route_coalition_role",  # which agent picks up a phase-role
    "tool_call_outcome",        # did this tool call succeed
    "affect_appraise",          # did the emotion appraisal hold up
    "blackboard_contribute",    # was this contribution accepted
})


@dataclass(frozen=True)
class ConfidencePrediction:
    """Pre-decision: the agent's own predicted confidence in the
    outcome it's about to commit to.

    All fields are required. `decision_id` is the shared key with the
    matching OutcomeObservation; callers MUST generate a fresh UUID
    per decision so the join is unambiguous. `context_hash` lets the
    calibration aggregator group structurally-similar decisions
    without leaking the raw context.
    """

    tenant_id: str
    agent_id: str
    decision_id: str
    decision_kind: str
    predicted_confidence: float
    context_hash: str
    ts: str  # ISO-8601 UTC

    def __post_init__(self) -> None:
        if self.decision_kind not in DECISION_KINDS:
            raise ValueError(
                f"decision_kind must be one of {sorted(DECISION_KINDS)}, "
                f"got {self.decision_kind!r}"
            )
        if not 0.0 <= self.predicted_confidence <= 1.0:
            raise ValueError(
                f"predicted_confidence must be in [0.0, 1.0], "
                f"got {self.predicted_confidence}"
            )

    def to_dict(self) -> dict:
        return asdict(self)


def normalize_reward(reward: float) -> float:
    """Rescale an actual_reward in [-1, 1] to [0, 1] for ECE/importance
    comparison against predicted_confidence. Module-level so IO and
    schema layers reuse the same definition (superpowers NIT #1)."""
    return (reward + 1.0) / 2.0


@dataclass(frozen=True)
class OutcomeObservation:
    """Post-outcome: the actual reward and latency the decision
    produced. Joins to a ConfidencePrediction via decision_id.

    `agent_id` lives ON the observation as well as the prediction so
    the IO layer can persist + read back the agent attribution
    without re-resolving it from the paired prediction (superpowers
    IMPORTANT #1). Without this a buggy caller could write an
    observation under the wrong agent's FK and split a trace across
    two agents in agent_memory.

    `actual_reward` is in [-1.0, 1.0] to match the RL signal
    convention (positive = better-than-baseline). The calibration
    helper rescales to [0, 1] internally for ECE bucketing.
    `error` is a short string when the outcome was a hard failure;
    leave None otherwise.
    """

    tenant_id: str
    agent_id: str
    decision_id: str
    actual_reward: float
    latency_ms: int
    completed_at: str  # ISO-8601 UTC
    error: Optional[str] = None

    def __post_init__(self) -> None:
        if not -1.0 <= self.actual_reward <= 1.0:
            raise ValueError(
                f"actual_reward must be in [-1.0, 1.0], "
                f"got {self.actual_reward}"
            )
        if self.latency_ms < 0:
            raise ValueError(
                f"latency_ms must be non-negative, got {self.latency_ms}"
            )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class MetacogTrace:
    """Joined view of a ConfidencePrediction + its matching
    OutcomeObservation. Aggregators (calibration_error, RL features)
    consume lists of these.
    """

    prediction: ConfidencePrediction
    observation: OutcomeObservation

    def __post_init__(self) -> None:
        if self.prediction.decision_id != self.observation.decision_id:
            raise ValueError(
                "MetacogTrace requires prediction.decision_id == "
                "observation.decision_id; "
                f"got {self.prediction.decision_id!r} vs "
                f"{self.observation.decision_id!r}"
            )
        if self.prediction.tenant_id != self.observation.tenant_id:
            raise ValueError(
                "MetacogTrace requires same tenant_id on both sides"
            )
        if self.prediction.agent_id != self.observation.agent_id:
            raise ValueError(
                "MetacogTrace requires same agent_id on both sides "
                "(superpowers IMPORTANT #1 — split-attribution guard)"
            )

    @property
    def normalized_reward(self) -> float:
        """Rescale actual_reward from [-1, 1] to [0, 1] so it can be
        compared against predicted_confidence on the same axis."""
        return normalize_reward(self.observation.actual_reward)


__all__ = [
    "DECISION_KINDS",
    "ConfidencePrediction",
    "OutcomeObservation",
    "MetacogTrace",
    "normalize_reward",
]
