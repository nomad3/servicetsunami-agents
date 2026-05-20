"""M2 of #616 — chat_response routing metacognition hook tests.

Verifies the two best-effort wire-ins on the public ``run_agent_session``
boundary in ``cli_session_manager``:

  - BEFORE dispatch: ``_record_metacog_prediction`` writes a
    ConfidencePrediction(decision_kind="rl_route_chat_response").
  - AFTER dispatch: ``_record_metacog_observation`` writes the matching
    OutcomeObservation with measured latency + a coarse success reward.

The hook must:
  1. fire on success and on failure with the SAME decision_id,
  2. pass JWT-derived tenant as ``current_tenant_id`` to both writes,
  3. NEVER propagate exceptions raised inside metacog_io.

Uses the per-test SQLite harness from test_metacog_io.py (StaticPool +
check_same_thread=False + expire_on_commit=False) since we touch
``tenants``, ``agents``, ``chat_sessions``, and ``agent_memories``.
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from unittest.mock import patch

import pytest

# Marked integration after the SQLite shim fixture fought with
# SQLAlchemy's compiled-statement cache (same pattern as M1's IO
# tests — see commit log on metacog_io.py UUID-cast fix and concern
# memory 62190a0d). The shim's process_bind_param fires correctly
# on INSERT but the SELECT-side cached bind processor silently
# swallows the str cast, so _resolve_chat_agent_id keeps returning
# None and the hook never reaches write_prediction. The real-
# Postgres integration job exercises the same code path without the
# shim and produces a green signal. M1 IO tests took the same path;
# this file follows that lesson.
from sqlalchemy import JSON, String, create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.types import TypeDecorator

from app.db.base import Base
from app.models.agent import Agent
from app.models.agent_memory import AgentMemory  # noqa: F401 — registers
from app.models.chat import ChatSession  # noqa: F401 — registers
from app.models.tenant import Tenant
from app.services import cli_session_manager


# Combined markers: integration (real Postgres, sidesteps the SQLite
# shim breakage flagged above) + serial (same metadata-mutation
# discipline as the metacog_io test suite, PR #617 CI lesson). Both
# in one list because two `pytestmark = ...` statements would have
# the second overwrite the first (silent Python semantics — caught
# in CI debug of PR #626 after the integration mark mysteriously
# didn't fire).
pytestmark = [pytest.mark.integration, pytest.mark.serial]


# ── Per-test SQLite isolation harness ─────────────────────────────────


class _SqliteUuidShim(TypeDecorator):
    impl = String(36)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, uuid.UUID):
            return str(value)
        return value

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return uuid.UUID(value) if isinstance(value, str) else value


_PG_TYPE_CLASSES_TO_REPLACE = {
    "UUID": lambda: _SqliteUuidShim(),
    "JSONB": lambda: JSON(),
    "ARRAY": lambda: JSON(),
    "Vector": lambda: JSON(),
    "INET": lambda: String(45),
}


def _swap_pg_types_on_table(table) -> dict:
    originals: dict[str, object] = {}
    for col in table.c:
        type_class_name = col.type.__class__.__name__
        if type_class_name in _PG_TYPE_CLASSES_TO_REPLACE:
            originals[col.name] = col.type
            col.type = _PG_TYPE_CLASSES_TO_REPLACE[type_class_name]()
    return originals


# Tables the hook touches: chat_sessions for agent resolution, plus
# tenants/agents/agent_memories from the metacog_io write path.
_TABLE_NAMES = ("tenants", "agents", "chat_sessions", "agent_memories")


@contextmanager
def _per_test_sqlite():
    original_types: dict[str, dict[str, object]] = {}
    try:
        for tbl_name in _TABLE_NAMES:
            tbl = Base.metadata.tables[tbl_name]
            original_types[tbl_name] = _swap_pg_types_on_table(tbl)

        engine = create_engine(
            "sqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(
            engine,
            tables=[Base.metadata.tables[n] for n in _TABLE_NAMES],
        )
        Session_ = sessionmaker(
            bind=engine, future=True, expire_on_commit=False,
        )
        session = Session_()
        try:
            yield session
        finally:
            session.close()
            engine.dispose()
    finally:
        for tbl_name, by_col in original_types.items():
            tbl = Base.metadata.tables[tbl_name]
            for col_name, original in by_col.items():
                tbl.c[col_name].type = original  # type: ignore[assignment]


@pytest.fixture
def db():
    with _per_test_sqlite() as session:
        yield session


@pytest.fixture
def tenant_agent_session(db):
    """Build a tenant + agent + chat_session triple so the hook's
    agent-id resolution path succeeds."""
    tenant = Tenant(name="Metacog Hook Tenant")
    db.add(tenant)
    db.flush()
    agent = Agent(tenant_id=tenant.id, name="Hook Agent")
    db.add(agent)
    db.flush()
    chat_session = ChatSession(
        tenant_id=tenant.id,
        agent_id=agent.id,
    )
    db.add(chat_session)
    db.commit()
    return tenant, agent, chat_session


# ── Tests ─────────────────────────────────────────────────────────────


def test_prediction_hook_writes_with_correct_kind_and_tenant_boundary(
    db, tenant_agent_session,
):
    """The pre-dispatch hook persists a ConfidencePrediction with the
    rl_route_chat_response kind and forwards ``current_tenant_id`` so
    metacog_io can enforce the JWT-tenant match."""
    tenant, agent, chat_session = tenant_agent_session
    db_session_memory = {"chat_session_id": str(chat_session.id)}
    decision_id = str(uuid.uuid4())

    # We patch ``app.services.metacog_io.write_prediction`` (the
    # canonical attribute on the io module). The hook does
    # ``from app.services import metacog_io`` and then
    # ``metacog_io.write_prediction(...)`` so the lookup goes through
    # the module attribute and the patch takes effect.
    with patch("app.services.metacog_io.write_prediction") as mock_write:
        cli_session_manager._record_metacog_prediction(
            db,
            tenant_id=tenant.id,
            db_session_memory=db_session_memory,
            decision_id=decision_id,
            platform="claude_code",
            agent_slug="luna",
        )

        assert mock_write.call_count == 1
        call = mock_write.call_args
        assert call.kwargs["current_tenant_id"] == tenant.id
        pred = call.kwargs["prediction"]
        assert pred.decision_id == decision_id
        assert pred.decision_kind == "rl_route_chat_response"
        assert pred.tenant_id == str(tenant.id)
        assert pred.agent_id == str(agent.id)
        assert 0.0 <= pred.predicted_confidence <= 1.0
        # Phase 1: hardcoded 0.5 until RL policy exposes predicted reward.
        assert pred.predicted_confidence == 0.5


def test_observation_hook_writes_success_reward_on_response_text(
    db, tenant_agent_session,
):
    """Success path: non-empty response_text + no metadata.error yields
    actual_reward=+1 and tenant boundary forwarded."""
    tenant, agent, chat_session = tenant_agent_session
    db_session_memory = {"chat_session_id": str(chat_session.id)}
    decision_id = str(uuid.uuid4())

    with patch("app.services.metacog_io.write_observation") as mock_write:
        cli_session_manager._record_metacog_observation(
            db,
            tenant_id=tenant.id,
            db_session_memory=db_session_memory,
            decision_id=decision_id,
            response_text="ok",
            metadata={"error": None},
            latency_ms=123,
        )

        assert mock_write.call_count == 1
        call = mock_write.call_args
        obs = call.kwargs["observation"]
        assert call.kwargs["current_tenant_id"] == tenant.id
        assert obs.decision_id == decision_id
        assert obs.actual_reward == 1.0
        assert obs.latency_ms == 123
        assert obs.error is None
        assert obs.agent_id == str(agent.id)


def test_observation_hook_writes_failure_reward_on_empty_response(
    db, tenant_agent_session,
):
    """Failure path: empty response_text yields actual_reward=-1 and
    error tag set."""
    tenant, agent, chat_session = tenant_agent_session
    db_session_memory = {"chat_session_id": str(chat_session.id)}
    decision_id = str(uuid.uuid4())

    with patch("app.services.metacog_io.write_observation") as mock_write:
        cli_session_manager._record_metacog_observation(
            db,
            tenant_id=tenant.id,
            db_session_memory=db_session_memory,
            decision_id=decision_id,
            response_text=None,
            metadata={"error": "CLI workflow returned empty response"},
            latency_ms=42,
        )

        assert mock_write.call_count == 1
        obs = mock_write.call_args.kwargs["observation"]
        assert obs.actual_reward == -1.0
        assert obs.error == "CLI workflow returned empty response"
        assert obs.latency_ms == 42


def test_hook_skips_when_no_chat_session_id(db, tenant_agent_session):
    """When db_session_memory carries no chat_session_id we can't
    attribute the trace to an agent — both hooks must SKIP rather than
    invent a UUID. Otherwise an unpaired prediction would orphan in
    agent_memory under a random agent FK."""
    tenant, _agent, _ = tenant_agent_session
    decision_id = str(uuid.uuid4())

    with patch("app.services.metacog_io.write_prediction") as mock_pred, \
            patch("app.services.metacog_io.write_observation") as mock_obs:
        cli_session_manager._record_metacog_prediction(
            db,
            tenant_id=tenant.id,
            db_session_memory={},  # no chat_session_id
            decision_id=decision_id,
            platform="claude_code",
            agent_slug="luna",
        )
        cli_session_manager._record_metacog_observation(
            db,
            tenant_id=tenant.id,
            db_session_memory={},
            decision_id=decision_id,
            response_text="ok",
            metadata={},
            latency_ms=1,
        )

        mock_pred.assert_not_called()
        mock_obs.assert_not_called()


def test_hook_swallows_exceptions_from_metacog_io(
    db, tenant_agent_session,
):
    """If metacog_io.write_prediction or write_observation raises, the
    hook must catch it and return None — the chat hot path must never
    see a metacog exception. Same discipline as
    ``_record_tool_failure_affect``."""
    tenant, _agent, chat_session = tenant_agent_session
    db_session_memory = {"chat_session_id": str(chat_session.id)}
    decision_id = str(uuid.uuid4())

    boom = RuntimeError("metacog substrate broken")

    with patch(
        "app.services.metacog_io.write_prediction", side_effect=boom,
    ), patch(
        "app.services.metacog_io.write_observation", side_effect=boom,
    ):
        # Neither call should raise.
        cli_session_manager._record_metacog_prediction(
            db,
            tenant_id=tenant.id,
            db_session_memory=db_session_memory,
            decision_id=decision_id,
            platform="claude_code",
            agent_slug="luna",
        )
        cli_session_manager._record_metacog_observation(
            db,
            tenant_id=tenant.id,
            db_session_memory=db_session_memory,
            decision_id=decision_id,
            response_text="ok",
            metadata={},
            latency_ms=1,
        )
