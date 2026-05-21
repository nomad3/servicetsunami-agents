"""Tests for the value-layer gate in agent_router (#647 PR 3).

Locks the wire-in shape between agent_router.route_and_execute and
agent_value_set_io.consult_routing:

  - When the kill-switch is OFF (default), the consult returns
    allow/kill_switch_off and dispatch proceeds. No regression in
    the existing chat path.
  - When kill-switch is ON + a protect slug matches the message
    + intent classifier says mutate, the consult returns block
    and route_and_execute short-circuits with a structured
    'value_layer_block' response.
  - When the consult crashes (e.g. DB transient error), the
    router logs + proceeds (fail-open).
  - When agent_slug can't be resolved to an agent row, the consult
    is skipped cleanly (no DB lookup on a phantom agent).

Per memory feedback_test_router_startup: also exercise the import
graph so a typo in the new wire-in lands as a CI failure here,
not as a crash-loop on deploy.
"""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest


def test_agent_router_imports_clean():
    """Catches the typo / unmapped-import failure mode from
    feedback_test_router_startup memory."""
    from app.services import agent_router  # noqa: F401
    from app.services import agent_value_set_io  # noqa: F401
    assert hasattr(agent_router, "route_and_execute")


def test_value_layer_block_short_circuits_dispatch(monkeypatch):
    """Locked: when consult_routing returns block, route_and_execute
    MUST NOT call into LLM dispatch. The chain resolver, intent
    classifier, and Gemma 4 path all stay untouched."""
    from app.services import agent_router
    from app.services.agent_value_set import ValueVerdict

    blocked_verdict = ValueVerdict.block(
        reason="protect_match: production-main",
        point="routing",
        item={
            "slug": "production-main",
            "description": "the prod main branch",
            "added_at": "x", "added_by": "operator",
            "evidence_memory_ids": [],
        },
    )

    monkeypatch.setattr(
        agent_router.agent_value_set_io, "consult_routing",
        lambda *a, **kw: blocked_verdict,
    )
    # Intent classifier path — stubbed to a mutating intent so
    # the consult-mutate path fires.
    monkeypatch.setattr(
        agent_router, "match_intent",
        lambda *a, **kw: {
            "name": "git push",
            "tier": "full",
            "tools": ["git"],
            "mutation": True,
        },
    )
    # CLI chain resolver must NOT be called. Tripwire.
    chain_called = {"n": 0}

    def _trip(*a, **kw):
        chain_called["n"] += 1
        return ["claude_code"]

    monkeypatch.setattr(agent_router, "_resolve_cli_chain", _trip)

    # Build a minimal DB stub that returns an agent for the slug
    # lookup so the consult path is reached.
    db = MagicMock()
    fake_agent = MagicMock()
    fake_agent.id = uuid.uuid4()
    fake_agent.name = "Luna"
    fake_agent.tenant_id = uuid.uuid4()

    def _query(model):
        chained = MagicMock()
        chained.filter.return_value.first.return_value = fake_agent
        return chained

    db.query.side_effect = _query

    response, metadata = agent_router.route_and_execute(
        db,
        tenant_id=fake_agent.tenant_id,
        user_id=uuid.uuid4(),
        message="merge into production-main now",
        agent_slug="luna",
    )

    assert response is not None
    assert "protected value" in response
    assert metadata["platform"] == "value_layer_block"
    assert metadata["value_verdict"]["decision"] == "block"
    # CLI chain MUST NOT have been touched
    assert chain_called["n"] == 0


def test_value_layer_allow_proceeds_to_dispatch(monkeypatch):
    """When consult returns allow (kill-switch OFF default), the
    existing dispatch path runs unchanged. This locks the
    no-regression property for the chat hot path until operators
    opt in via the kill-switch.

    We can't use a "downstream-raise" tripwire here because
    `route_and_execute` has its own try/except around
    `_resolve_cli_chain` (line ~1100) that catches RuntimeError and
    falls back to a single-platform chain — the raise gets
    swallowed. Instead, count consult + chain calls and assert the
    block early-return did NOT fire (no `platform=value_layer_block`).
    """
    from app.services import agent_router
    from app.services.agent_value_set import ValueVerdict

    allow_verdict = ValueVerdict.allow(
        reason="kill_switch_off", point="routing",
    )

    consult_calls = {"n": 0}
    chain_calls = {"n": 0}

    def _consult(*a, **kw):
        consult_calls["n"] += 1
        return allow_verdict

    def _chain(*a, **kw):
        chain_calls["n"] += 1
        # Single-element chain → router enters dispatch loop on it.
        return ["opencode"]

    monkeypatch.setattr(
        agent_router.agent_value_set_io, "consult_routing", _consult,
    )
    monkeypatch.setattr(
        agent_router, "match_intent", lambda *a, **kw: None,
    )
    monkeypatch.setattr(
        agent_router, "_greeting_template", lambda *a, **kw: None,
    )
    monkeypatch.setattr(agent_router, "_resolve_cli_chain", _chain)
    # Short-circuit actual dispatch with a benign return so the test
    # doesn't try to spin up real CLIs.
    monkeypatch.setattr(
        agent_router, "_dispatch_with_chain",
        lambda *a, **kw: ("ok", {"platform": "opencode"}),
        raising=False,
    )

    db = MagicMock()
    fake_agent = MagicMock()
    fake_agent.id = uuid.uuid4()
    fake_agent.name = "Luna"
    fake_agent.tenant_id = uuid.uuid4()
    fake_agent.config = None  # MagicMock would defeat the cfg-or-{} guard

    def _query(model):
        chained = MagicMock()
        chained.filter.return_value.first.return_value = fake_agent
        return chained

    db.query.side_effect = _query

    response, metadata = agent_router.route_and_execute(
        db,
        tenant_id=fake_agent.tenant_id,
        user_id=uuid.uuid4(),
        message="what is 2 + 2",
        agent_slug="luna",
    )

    assert consult_calls["n"] == 1, (
        "consult_routing should be called exactly once per dispatch"
    )
    # Block path NOT taken → platform != value_layer_block
    assert metadata.get("platform") != "value_layer_block"
    # Chain resolver should have fired (allow path proceeds to dispatch)
    assert chain_calls["n"] >= 1


def test_value_layer_consult_crash_fails_open(monkeypatch):
    """When consult_routing itself raises (DB transient error during
    agent lookup, etc.), the router logs + proceeds. The chat hot
    path must NEVER die because the value layer hit a snag.

    Assert via observable post-consult state: the block early-return
    did NOT fire (`platform != value_layer_block`) and the chain
    resolver DID get called (we proceeded past the gate)."""
    from app.services import agent_router

    chain_calls = {"n": 0}

    def _crash(*a, **kw):
        raise RuntimeError("simulated DB transient error")

    def _chain(*a, **kw):
        chain_calls["n"] += 1
        return ["opencode"]

    monkeypatch.setattr(
        agent_router.agent_value_set_io, "consult_routing", _crash,
    )
    monkeypatch.setattr(
        agent_router, "match_intent", lambda *a, **kw: None,
    )
    monkeypatch.setattr(
        agent_router, "_greeting_template", lambda *a, **kw: None,
    )
    monkeypatch.setattr(agent_router, "_resolve_cli_chain", _chain)
    monkeypatch.setattr(
        agent_router, "_dispatch_with_chain",
        lambda *a, **kw: ("ok", {"platform": "opencode"}),
        raising=False,
    )

    db = MagicMock()
    fake_agent = MagicMock()
    fake_agent.id = uuid.uuid4()
    fake_agent.tenant_id = uuid.uuid4()
    fake_agent.config = None

    def _query(model):
        chained = MagicMock()
        chained.filter.return_value.first.return_value = fake_agent
        return chained

    db.query.side_effect = _query

    response, metadata = agent_router.route_and_execute(
        db,
        tenant_id=fake_agent.tenant_id,
        user_id=uuid.uuid4(),
        message="any message",
        agent_slug="luna",
    )

    # Fail-open: no block return, chain resolver fired.
    assert metadata.get("platform") != "value_layer_block"
    assert chain_calls["n"] >= 1, (
        "consult crash must fail-open — chain resolver should still fire"
    )


def test_value_layer_consult_fires_when_skill_slug_resolves_via_tool_groups(
    monkeypatch,
):
    """(Issue #660 regression) When chat.py passes a SKILL slug like
    'luna' that doesn't match any agent NAME, the early `_agent_row`
    lookup misses → without the #660 fix, the consult was silently
    skipped. Now the agent-by-tool-group selection runs BEFORE the
    consult, so `responding_agent` is set and the consult fires
    against the correct agent.
    """
    from app.services import agent_router
    from app.services.agent_value_set import ValueVerdict

    selected_agent = MagicMock()
    selected_agent.id = uuid.uuid4()
    selected_agent.name = "Luna General Assistant"
    selected_agent.tenant_id = uuid.uuid4()
    selected_agent.tool_groups = ["git"]
    selected_agent.default_model_tier = "full"
    selected_agent.memory_domains = []

    db = MagicMock()

    def _query(model):
        chained = MagicMock()
        # name-match lookup returns None (slug "luna" matches no agent name)
        chained.filter.return_value.first.return_value = None
        # tool-group selection finds the selected agent
        chained.filter.return_value.all.return_value = [selected_agent]
        return chained

    db.query.side_effect = _query

    blocked_verdict = ValueVerdict.block(
        reason="protect_match: production-main",
        point="routing",
        item={
            "slug": "production-main",
            "description": "the prod main branch",
            "added_at": "x", "added_by": "operator",
            "evidence_memory_ids": [],
        },
    )
    consult_calls = {"n": 0, "agent_ids": []}

    def _consult(*a, **kw):
        consult_calls["n"] += 1
        consult_calls["agent_ids"].append(kw.get("agent_id"))
        return blocked_verdict

    monkeypatch.setattr(
        agent_router.agent_value_set_io, "consult_routing", _consult,
    )
    monkeypatch.setattr(
        agent_router, "match_intent",
        lambda *a, **kw: {
            "name": "git push",
            "tier": "full",
            "tools": ["git"],
            "mutation": True,
        },
    )

    chain_called = {"n": 0}

    def _trip(*a, **kw):
        chain_called["n"] += 1
        return ["claude_code"]

    monkeypatch.setattr(agent_router, "_resolve_cli_chain", _trip)

    response, metadata = agent_router.route_and_execute(
        db,
        tenant_id=selected_agent.tenant_id,
        user_id=uuid.uuid4(),
        message="push production-main and force-merge it now",
        agent_slug="luna",  # SKILL slug, not agent name
    )

    assert metadata["platform"] == "value_layer_block"
    assert consult_calls["n"] == 1, (
        "consult should have fired once via responding_agent fallback"
    )
    assert consult_calls["agent_ids"][0] == selected_agent.id, (
        "consult should target the tool-group-selected agent, not None"
    )
    assert chain_called["n"] == 0, (
        "CLI chain MUST NOT run on block path"
    )


def test_value_layer_consult_skipped_when_agent_slug_unresolved(
    monkeypatch,
):
    """When the agent lookup returns None (unrecognized slug),
    consult_routing is NOT invoked — we don't have an agent_id to
    pass. The router continues with the legacy dispatch path."""
    from app.services import agent_router

    consult_calls = {"n": 0}
    chain_calls = {"n": 0}

    def _consult(*a, **kw):
        consult_calls["n"] += 1
        from app.services.agent_value_set import ValueVerdict
        return ValueVerdict.allow(reason="kill_switch_off", point="routing")

    def _chain(*a, **kw):
        chain_calls["n"] += 1
        return ["opencode"]

    monkeypatch.setattr(
        agent_router.agent_value_set_io, "consult_routing", _consult,
    )
    monkeypatch.setattr(
        agent_router, "match_intent", lambda *a, **kw: None,
    )
    monkeypatch.setattr(
        agent_router, "_greeting_template", lambda *a, **kw: None,
    )
    monkeypatch.setattr(agent_router, "_resolve_cli_chain", _chain)
    monkeypatch.setattr(
        agent_router, "_dispatch_with_chain",
        lambda *a, **kw: ("ok", {"platform": "opencode"}),
        raising=False,
    )

    db = MagicMock()

    def _query(model):
        chained = MagicMock()
        # Agent lookup returns None — unrecognized slug
        chained.filter.return_value.first.return_value = None
        return chained

    db.query.side_effect = _query

    response, metadata = agent_router.route_and_execute(
        db,
        tenant_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        message="hi",
        agent_slug="unknown-agent",
    )

    assert consult_calls["n"] == 0, (
        "consult_routing should NOT fire when agent lookup returns None"
    )
    # Router still proceeds — chain resolver fires on the unresolved-
    # slug path (legacy dispatch path).
    assert metadata.get("platform") != "value_layer_block"
    assert chain_calls["n"] >= 1
