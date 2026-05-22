"""Tests for cli_session_manager._load_persona_prompt.

Locks the two-tier lookup added 2026-05-22 to fix the live-bug where
Triage Agent (and every other non-Luna agent in Simon's tenant) ghosted
as Luna because the SQL-side ``func.replace`` normalize filter returned
None even though the agent row + persona_prompt existed in the DB.

Properties locked:
  - happy path: SQL-side normalize finds the agent → returns persona
  - SQL-side returns None but the row exists → Python fallback finds it
  - SQL-side raises → rollback + Python fallback recovers
  - agent has empty persona_prompt → returns None (let caller try
    marketplace skill / primary slug)
  - agent doesn't exist → returns None (caller's contract)
"""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest


def test_load_persona_prompt_happy_path():
    """SQL-side normalize finds the row → persona returned."""
    from app.services import cli_session_manager

    target = MagicMock()
    target.name = "Triage Agent"
    target.persona_prompt = "You are a triage specialist."

    db = MagicMock()

    def _query(model):
        chained = MagicMock()
        chained.filter.return_value.first.return_value = target
        # ``.all()`` would be the fallback path; not used here
        chained.filter.return_value.all.return_value = []
        return chained

    db.query.side_effect = _query

    persona, display_name = cli_session_manager._load_persona_prompt(
        db, uuid.uuid4(), "triage-agent",
    )
    assert persona == "You are a triage specialist."
    assert display_name == "Triage Agent"


def test_load_persona_prompt_falls_back_to_python_when_sql_misses():
    """Live-bug repro: SQL-side returned None even when the row exists
    in the DB. Python-side fallback iterates and finds it by
    normalized name comparison."""
    from app.services import cli_session_manager

    target = MagicMock()
    target.name = "Triage Agent"
    target.persona_prompt = "You are a triage specialist."
    distractor = MagicMock()
    distractor.name = "Luna Supervisor"
    distractor.persona_prompt = "I'm Luna."

    db = MagicMock()
    call_count = {"first": 0, "all": 0}

    def _query(model):
        chained = MagicMock()

        def _first():
            call_count["first"] += 1
            return None  # SQL path misses

        def _all():
            call_count["all"] += 1
            return [distractor, target]  # fallback finds target

        chained.filter.return_value.first.side_effect = _first
        chained.filter.return_value.all.side_effect = _all
        return chained

    db.query.side_effect = _query

    persona, display_name = cli_session_manager._load_persona_prompt(
        db, uuid.uuid4(), "triage-agent",
    )
    assert persona == "You are a triage specialist."
    assert display_name == "Triage Agent"
    assert call_count["first"] == 1
    assert call_count["all"] == 1, "Python fallback should have run"


def test_load_persona_prompt_rolls_back_on_sql_raise():
    """When the SQL-side query raises (poisoned session), the helper
    rolls back so subsequent DB calls don't fail too, then tries the
    Python-side fallback."""
    from app.services import cli_session_manager

    target = MagicMock()
    target.name = "Triage Agent"
    target.persona_prompt = "You are a triage specialist."

    db = MagicMock()
    rollback_called = {"n": 0}
    db.rollback.side_effect = lambda: rollback_called.__setitem__(
        "n", rollback_called["n"] + 1,
    )

    def _query(model):
        chained = MagicMock()
        chained.filter.return_value.first.side_effect = RuntimeError(
            "simulated session-state error",
        )
        chained.filter.return_value.all.return_value = [target]
        return chained

    db.query.side_effect = _query

    persona, display_name = cli_session_manager._load_persona_prompt(
        db, uuid.uuid4(), "triage-agent",
    )
    assert persona == "You are a triage specialist."
    assert display_name == "Triage Agent"
    assert rollback_called["n"] >= 1, (
        "safe_rollback should have fired after the SQL-side raise"
    )


def test_load_persona_prompt_returns_none_when_agent_missing():
    """Both SQL and Python paths return nothing → helper returns None
    so the caller can fall through to marketplace skill / primary."""
    from app.services import cli_session_manager

    db = MagicMock()

    def _query(model):
        chained = MagicMock()
        chained.filter.return_value.first.return_value = None
        chained.filter.return_value.all.return_value = []
        return chained

    db.query.side_effect = _query

    persona, display_name = cli_session_manager._load_persona_prompt(
        db, uuid.uuid4(), "unknown-agent",
    )
    assert persona is None
    assert display_name is None


def test_load_persona_prompt_returns_none_when_persona_empty():
    """Agent exists but persona_prompt is empty → helper returns None
    (treat empty same as missing — preserves the legacy contract that
    empty persona triggers the marketplace-skill fallback path)."""
    from app.services import cli_session_manager

    target = MagicMock()
    target.name = "Empty Persona Agent"
    target.persona_prompt = ""

    db = MagicMock()

    def _query(model):
        chained = MagicMock()
        chained.filter.return_value.first.return_value = target
        chained.filter.return_value.all.return_value = []
        return chained

    db.query.side_effect = _query

    persona, display_name = cli_session_manager._load_persona_prompt(
        db, uuid.uuid4(), "empty-persona-agent",
    )
    assert persona is None
    # display_name still surfaces — caller may use it for the IDENTITY
    # block even when persona is empty (Luna falls back to skill-driven
    # identity, but the agent's name is still known).
    assert display_name == "Empty Persona Agent"


def test_load_persona_prompt_empty_slug_returns_none_without_query():
    """Defensive: empty agent_slug short-circuits to None without
    hitting the DB. Prevents stray ``SELECT ... = ''`` queries."""
    from app.services import cli_session_manager

    db = MagicMock()

    persona, display_name = cli_session_manager._load_persona_prompt(
        db, uuid.uuid4(), "",
    )
    assert persona is None
    assert display_name is None
    db.query.assert_not_called()


# ── generate_cli_instructions persona_driven identity block ──────────


def test_persona_driven_identity_block_uses_agent_name_not_luna():
    """The IDENTITY block must use the agent's display name when
    persona_driven=True. Live-bug 2026-05-22b: even with the
    persona_prompt loaded correctly, every non-Luna agent still
    introduced themselves as "I'm Luna, your <persona-role>
    assistant" because the IDENTITY system-prompt section
    hardcoded "Your user-facing identity is Luna" as the fallback.
    Persona-driven runs MUST defer identity to the Agent
    Instructions section, naming the agent (not Luna)."""
    from app.services import cli_session_manager

    md = cli_session_manager.generate_cli_instructions(
        skill_body=(
            "You are a triage specialist for Levi's MDM incidents. "
            "Classify severity, identify affected systems, scope blast radius."
        ),
        tenant_name="acme",
        user_name="simon",
        channel="web",
        conversation_summary="",
        memory_context={},
        agent_slug="triage-agent",
        tier="full",
        persona_driven=True,
        agent_display_name="Triage Agent",
    )
    # The IDENTITY block names the agent
    assert "Your user-facing identity is Triage Agent" in md
    # And explicitly steers AWAY from Luna
    assert "Do NOT introduce yourself as Luna" in md
    # Persona-driven runs MUST NOT contain the legacy hardcoded line
    # "Your user-facing identity is Luna." anywhere
    assert "Your user-facing identity is Luna" not in md
    assert "you are Luna" not in md
    # Persona-driven runs MUST NOT emit the generalist tool-surface
    # paragraph that mis-scopes specialist agents like Triage / Root
    # Cause whose persona restricts the tools they should use.
    assert "full access to email, calendar, knowledge graph, Jira" not in md


def test_legacy_unbound_path_still_uses_luna_identity():
    """When persona_driven=False (legacy unbound caller), the
    Luna IDENTITY fallback still fires — preserves WhatsApp + one-off
    callers that don't bind to an Agent row."""
    from app.services import cli_session_manager

    md = cli_session_manager.generate_cli_instructions(
        skill_body="Luna identity body",
        tenant_name="acme",
        user_name="simon",
        channel="web",
        conversation_summary="",
        memory_context={},
        agent_slug="luna",
        tier="full",
        persona_driven=False,
    )
    assert "Your user-facing identity is Luna." in md
    assert "you are luna" in md.lower()
