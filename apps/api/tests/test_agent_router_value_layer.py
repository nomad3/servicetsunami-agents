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
    opt in via the kill-switch."""
    from app.services import agent_router
    from app.services.agent_value_set import ValueVerdict

    allow_verdict = ValueVerdict.allow(
        reason="kill_switch_off", point="routing",
    )

    consult_calls = {"n": 0}

    def _consult(*a, **kw):
        consult_calls["n"] += 1
        return allow_verdict

    monkeypatch.setattr(
        agent_router.agent_value_set_io, "consult_routing", _consult,
    )
    # Stub out everything downstream so the test stops as soon as
    # the post-consult path is reached. We assert via the consult-
    # called counter, not the actual response.
    monkeypatch.setattr(
        agent_router, "match_intent",
        lambda *a, **kw: None,
    )

    # Greeting template won't fire on a non-greeting message; it
    # returns None. Subsequent dispatch we let raise so the test
    # exits early.
    monkeypatch.setattr(
        agent_router, "_greeting_template",
        lambda *a, **kw: None,
    )

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

    # We force a downstream raise so we know if the code reached
    # past the value-layer gate. Then we check that consult fired.
    monkeypatch.setattr(
        agent_router, "_resolve_cli_chain",
        lambda *a, **kw: (_ for _ in ()).throw(
            RuntimeError("reached past value-layer gate as expected")
        ),
    )

    with pytest.raises(Exception):
        agent_router.route_and_execute(
            db,
            tenant_id=fake_agent.tenant_id,
            user_id=uuid.uuid4(),
            message="what is 2 + 2",
            agent_slug="luna",
        )

    assert consult_calls["n"] == 1, (
        "consult_routing should be called exactly once per dispatch"
    )


def test_value_layer_consult_crash_fails_open(monkeypatch):
    """When consult_routing itself raises (DB transient error during
    agent lookup, etc.), the router logs + proceeds. The chat hot
    path must NEVER die because the value layer hit a snag."""
    from app.services import agent_router

    def _crash(*a, **kw):
        raise RuntimeError("simulated DB transient error")

    monkeypatch.setattr(
        agent_router.agent_value_set_io, "consult_routing", _crash,
    )
    monkeypatch.setattr(
        agent_router, "match_intent",
        lambda *a, **kw: None,
    )
    monkeypatch.setattr(
        agent_router, "_greeting_template",
        lambda *a, **kw: None,
    )

    db = MagicMock()
    fake_agent = MagicMock()
    fake_agent.id = uuid.uuid4()
    fake_agent.tenant_id = uuid.uuid4()

    def _query(model):
        chained = MagicMock()
        chained.filter.return_value.first.return_value = fake_agent
        return chained

    db.query.side_effect = _query

    # Should NOT raise from the consult crash; downstream raise
    # confirms we proceeded.
    monkeypatch.setattr(
        agent_router, "_resolve_cli_chain",
        lambda *a, **kw: (_ for _ in ()).throw(
            RuntimeError("reached past value-layer gate as expected")
        ),
    )

    with pytest.raises(Exception, match="reached past value-layer gate"):
        agent_router.route_and_execute(
            db,
            tenant_id=fake_agent.tenant_id,
            user_id=uuid.uuid4(),
            message="any message",
            agent_slug="luna",
        )


def test_value_layer_consult_skipped_when_agent_slug_unresolved(
    monkeypatch,
):
    """When the agent lookup returns None (unrecognized slug),
    consult_routing is NOT invoked — we don't have an agent_id to
    pass. The router continues with the legacy dispatch path."""
    from app.services import agent_router

    consult_calls = {"n": 0}

    def _consult(*a, **kw):
        consult_calls["n"] += 1
        from app.services.agent_value_set import ValueVerdict
        return ValueVerdict.allow(reason="kill_switch_off", point="routing")

    monkeypatch.setattr(
        agent_router.agent_value_set_io, "consult_routing", _consult,
    )
    monkeypatch.setattr(
        agent_router, "match_intent", lambda *a, **kw: None,
    )
    monkeypatch.setattr(
        agent_router, "_greeting_template", lambda *a, **kw: None,
    )

    db = MagicMock()

    def _query(model):
        chained = MagicMock()
        # Agent lookup returns None — unrecognized slug
        chained.filter.return_value.first.return_value = None
        return chained

    db.query.side_effect = _query

    monkeypatch.setattr(
        agent_router, "_resolve_cli_chain",
        lambda *a, **kw: (_ for _ in ()).throw(
            RuntimeError("reached past value-layer gate")
        ),
    )

    with pytest.raises(Exception):
        agent_router.route_and_execute(
            db,
            tenant_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            message="hi",
            agent_slug="unknown-agent",
        )

    assert consult_calls["n"] == 0, (
        "consult_routing should NOT fire when agent lookup returns None"
    )
