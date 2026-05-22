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

    result = cli_session_manager._load_persona_prompt(
        db, uuid.uuid4(), "triage-agent",
    )
    assert result == "You are a triage specialist."


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

    result = cli_session_manager._load_persona_prompt(
        db, uuid.uuid4(), "triage-agent",
    )
    assert result == "You are a triage specialist."
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

    result = cli_session_manager._load_persona_prompt(
        db, uuid.uuid4(), "triage-agent",
    )
    assert result == "You are a triage specialist."
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

    result = cli_session_manager._load_persona_prompt(
        db, uuid.uuid4(), "unknown-agent",
    )
    assert result is None


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

    result = cli_session_manager._load_persona_prompt(
        db, uuid.uuid4(), "empty-persona-agent",
    )
    assert result is None


def test_load_persona_prompt_empty_slug_returns_none_without_query():
    """Defensive: empty agent_slug short-circuits to None without
    hitting the DB. Prevents stray ``SELECT ... = ''`` queries."""
    from app.services import cli_session_manager

    db = MagicMock()

    result = cli_session_manager._load_persona_prompt(
        db, uuid.uuid4(), "",
    )
    assert result is None
    db.query.assert_not_called()
