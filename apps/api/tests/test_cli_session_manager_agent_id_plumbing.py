"""Test for the Phase 3 agent_id plumbing in cli_session_manager.

Verifies that the failure-path emotion-layer wire-in resolves
agent_id from chat_session before invoking record_session_tool_failure.

Doesn't exercise the full _run_agent_session_legacy (too coupled to
external state); instead tests the resolution helper directly +
documents the integration via an end-to-end mock.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from unittest.mock import patch

import pytest
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.models.agent import Agent
from app.models.agent_memory import AgentMemory  # noqa: F401 — registers
from app.models.chat import ChatSession
from app.models.conversation_episode import ConversationEpisode
from app.models.tenant import Tenant


@pytest.fixture(name="db_session")
def db_session_fixture():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    yield db
    db.close()
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(name="test_tenant")
def test_tenant_fixture(db_session: Session):
    tenant = Tenant(name="agent_id Plumbing Tenant")
    db_session.add(tenant)
    db_session.commit()
    db_session.refresh(tenant)
    return tenant


def test_record_tool_failure_affect_resolves_agent_id_from_chat_session(
    db_session, test_tenant,
):
    """When db_session_memory carries a chat_session_id whose row has
    agent_id set, the helper should resolve and forward that agent_id
    to record_session_tool_failure instead of letting it fall back to
    a random UUID."""
    from app.services.cli_session_manager import _record_tool_failure_affect

    # Build an Agent + ChatSession + Episode
    agent = Agent(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        name="Luna Supervisor",
        description="test",
        status="production",
        config={"model": "x"},
    )
    db_session.add(agent)
    db_session.commit()
    db_session.refresh(agent)

    chat_session = ChatSession(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        agent_id=agent.id,
        title="test session",
        source="native",
    )
    db_session.add(chat_session)
    db_session.commit()
    db_session.refresh(chat_session)

    episode = ConversationEpisode(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        session_id=chat_session.id,
        summary="test",
        affect_vector=None,
        created_at=datetime.utcnow(),
    )
    db_session.add(episode)
    db_session.commit()

    db_session_memory = {"chat_session_id": str(chat_session.id)}

    with patch(
        "app.services.emotion_engine_io.record_session_tool_failure"
    ) as mock_record:
        _record_tool_failure_affect(
            db_session,
            db_session_memory,
            test_tenant.id,
            severity=0.5,
        )

    # The wire-in helper resolved agent_id from chat_session and passed
    # it through to record_session_tool_failure.
    assert mock_record.called
    kwargs = mock_record.call_args.kwargs
    assert kwargs["session_id"] == chat_session.id
    assert kwargs["tenant_id"] == test_tenant.id
    assert kwargs["agent_id"] == agent.id
    assert kwargs["severity"] == 0.5


def test_record_tool_failure_affect_handles_missing_chat_session(
    db_session, test_tenant,
):
    """When the chat_session_id doesn't resolve to a row (orphan or
    foreign tenant), agent_id should pass through as None — the IO
    helper's existing fallback handles the rest."""
    from app.services.cli_session_manager import _record_tool_failure_affect

    db_session_memory = {"chat_session_id": str(uuid.uuid4())}  # no row

    with patch(
        "app.services.emotion_engine_io.record_session_tool_failure"
    ) as mock_record:
        _record_tool_failure_affect(
            db_session,
            db_session_memory,
            test_tenant.id,
            severity=1.0,
        )

    assert mock_record.called
    kwargs = mock_record.call_args.kwargs
    assert kwargs["agent_id"] is None  # graceful fallthrough


def test_record_tool_failure_affect_handles_missing_session_id_key(
    db_session, test_tenant,
):
    """db_session_memory without chat_session_id key — early return,
    no call to record_session_tool_failure."""
    from app.services.cli_session_manager import _record_tool_failure_affect

    db_session_memory = {}

    with patch(
        "app.services.emotion_engine_io.record_session_tool_failure"
    ) as mock_record:
        _record_tool_failure_affect(
            db_session,
            db_session_memory,
            test_tenant.id,
            severity=0.5,
        )

    assert not mock_record.called  # never reaches the IO call
