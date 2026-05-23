"""Synthetic value-arbitration fixtures.

Per design §7: realistic-corpus fixtures from session history are a
follow-up. These are small, hand-crafted scenarios that exercise the
core algorithmic paths — pursue, avoid, conflict, tie, and
missing-provenance. They keep the unit tests self-contained and let
the arbitrator be exercised today without touching production data.

Each scenario builder returns
``(DecisionContext, list[ValueSignal], TrustWeights, list[Candidate])``
so tests can call ``arbitrate(*scenario)`` directly.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.services.value_arbitration import (
    Candidate,
    DecisionContext,
    Direction,
    SourceClass,
    Standing,
    TrustWeights,
    ValueSignal,
    ValueTarget,
)


# Stable IDs so trace assertions can be deterministic.
TENANT_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
AGENT_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
SESSION_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")
NOW = datetime(2026, 5, 23, 12, 0, 0, tzinfo=timezone.utc)


def _ctx(action_kind: str = "tool_call", action_ref: str = "send_email") -> DecisionContext:
    return DecisionContext(
        tenant_id=TENANT_ID,
        agent_id=AGENT_ID,
        session_id=SESSION_ID,
        action_kind=action_kind,
        action_ref=action_ref,
    )


def _sig(
    source: SourceClass,
    standing: Standing,
    direction: Direction,
    target_kind: str = "tool_call",
    target_ref: str = "send_email",
    confidence: float = 0.8,
    source_id: str = "src-1",
    agent_id=AGENT_ID,
    rationale: str = "",
) -> ValueSignal:
    return ValueSignal(
        source=source,
        source_id=source_id,
        timestamp=NOW,
        tenant_id=TENANT_ID,
        agent_id=agent_id,
        confidence=confidence,
        standing=standing,
        direction=direction,
        target=ValueTarget(kind=target_kind, ref=target_ref),
        rationale=rationale,
    )


# ── Scenario 1: clean pursue ──────────────────────────────────────────

def scenario_pursue():
    """Single pursue signal over one candidate → preferred."""
    context = _ctx()
    signals = [
        _sig(
            source=SourceClass.operator_intent,
            standing=Standing.strong_advisory,
            direction=Direction.pursue,
            confidence=1.0,
            source_id="op-1",
            rationale="ship the Den",
        ),
    ]
    weights = TrustWeights(default=1.0)
    candidates = [Candidate(kind="tool_call", ref="send_email")]
    return context, signals, weights, candidates


# ── Scenario 2: avoid wins over weaker pursue ─────────────────────────

def scenario_avoid():
    """Strong-advisory avoid outweighs a weaker advisory pursue."""
    context = _ctx()
    signals = [
        _sig(
            source=SourceClass.agent_value_set,
            standing=Standing.strong_advisory,
            direction=Direction.avoid,
            confidence=0.9,
            source_id="avs-1",
        ),
        _sig(
            source=SourceClass.user_of_moment,
            standing=Standing.advisory,
            direction=Direction.pursue,
            confidence=0.6,
            source_id="user-1",
        ),
    ]
    weights = TrustWeights(default=1.0)
    candidates = [
        Candidate(kind="tool_call", ref="send_email"),
        Candidate(kind="tool_call", ref="archive"),
    ]
    return context, signals, weights, candidates


# ── Scenario 3: tenant_norm veto blocks ───────────────────────────────

def scenario_tenant_veto():
    """Veto-bearing tenant_norm signal blocks regardless of pursue."""
    context = _ctx()
    signals = [
        _sig(
            source=SourceClass.tenant_norm,
            standing=Standing.veto_bearing,
            direction=Direction.veto,
            confidence=1.0,
            source_id="norm-pii",
            agent_id=None,  # tenant-scoped
            rationale="no PII in agent logs",
        ),
        _sig(
            source=SourceClass.operator_intent,
            standing=Standing.strong_advisory,
            direction=Direction.pursue,
            confidence=1.0,
            source_id="op-1",
        ),
    ]
    weights = TrustWeights(default=1.0)
    candidates = [Candidate(kind="tool_call", ref="send_email")]
    return context, signals, weights, candidates


# ── Scenario 4: tie within epsilon → abstain ──────────────────────────

def scenario_tie():
    """Two candidates with identical weighted-sum scores → abstain."""
    context = _ctx()
    signals = [
        # equal pursue on both candidates
        _sig(
            source=SourceClass.operator_intent,
            standing=Standing.strong_advisory,
            direction=Direction.pursue,
            target_kind="tool_call",
            target_ref="option_a",
            confidence=0.8,
            source_id="op-a",
        ),
        _sig(
            source=SourceClass.operator_intent,
            standing=Standing.strong_advisory,
            direction=Direction.pursue,
            target_kind="tool_call",
            target_ref="option_b",
            confidence=0.8,
            source_id="op-b",
        ),
    ]
    weights = TrustWeights(default=1.0)
    candidates = [
        Candidate(kind="tool_call", ref="option_a"),
        Candidate(kind="tool_call", ref="option_b"),
    ]
    return context, signals, weights, candidates


# ── Scenario 5: missing provenance gets rejected ──────────────────────

def scenario_missing_provenance():
    """Build a signal with missing confidence — caller will hit the boundary."""
    context = _ctx()
    bad = ValueSignal(
        source=SourceClass.peer_agent,
        source_id="peer-1",
        timestamp=NOW,
        tenant_id=TENANT_ID,
        agent_id=AGENT_ID,
        confidence=None,  # provenance breach
        standing=Standing.advisory,
        direction=Direction.pursue,
        target=ValueTarget(kind="tool_call", ref="send_email"),
    )
    good = _sig(
        source=SourceClass.operator_intent,
        standing=Standing.strong_advisory,
        direction=Direction.pursue,
        confidence=1.0,
        source_id="op-1",
    )
    weights = TrustWeights(default=1.0)
    candidates = [Candidate(kind="tool_call", ref="send_email")]
    return context, [bad, good], weights, candidates
