"""Tests for record_session_tool_failure — Phase 2 wire-in helper.

The helper is what cli_session_manager calls from its error paths. It
bridges the session-level context the chat hot path has to the
episode-level context the emotion engine writes to.
"""
from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.models.agent_memory import AgentMemory  # noqa: F401
from app.models.conversation_episode import ConversationEpisode
from app.models.tenant import Tenant
from app.schemas.emotion import PADVector
from app.services.emotion_engine_io import record_session_tool_failure


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture(name="db_session")
def db_session_fixture():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    yield db
    db.close()
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(name="test_tenant")
def test_tenant_fixture(db_session: Session):
    tenant = Tenant(name="Emotion Phase 2 Tenant")
    db_session.add(tenant)
    db_session.commit()
    db_session.refresh(tenant)
    return tenant


@pytest.fixture(name="other_tenant")
def other_tenant_fixture(db_session: Session):
    tenant = Tenant(name="Emotion Phase 2 Other Tenant")
    db_session.add(tenant)
    db_session.commit()
    db_session.refresh(tenant)
    return tenant


def _make_episode(
    db: Session,
    tenant_id,
    *,
    session_id,
    affect_vector=None,
    created_offset_sec: int = 0,
) -> ConversationEpisode:
    from datetime import timedelta
    ep = ConversationEpisode(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        session_id=session_id,
        summary="test",
        affect_vector=affect_vector,
        created_at=datetime.utcnow() + timedelta(seconds=created_offset_sec),
    )
    db.add(ep)
    db.commit()
    db.refresh(ep)
    return ep


# ── Tests ─────────────────────────────────────────────────────────────


def test_session_id_none_returns_none(db_session, test_tenant):
    """Defensive: cli_session_manager may pass None when there's no
    session in db_session_memory."""
    assert record_session_tool_failure(
        db_session,
        session_id=None,
        tenant_id=test_tenant.id,
    ) is None


def test_no_episode_yet_returns_none(db_session, test_tenant):
    """First failure of a fresh session — PostChatMemoryWorkflow hasn't
    created an episode yet. Graceful no-op."""
    assert record_session_tool_failure(
        db_session,
        session_id=uuid.uuid4(),
        tenant_id=test_tenant.id,
    ) is None


def test_records_failure_on_latest_episode(db_session, test_tenant):
    """Happy path: session has an episode -> failure appraisal writes
    pleasure down + arousal UP onto it (Luna's temperature-flip)."""
    session_id = uuid.uuid4()
    ep = _make_episode(db_session, test_tenant.id, session_id=session_id)

    result = record_session_tool_failure(
        db_session,
        session_id=session_id,
        tenant_id=test_tenant.id,
        severity=1.0,
    )
    assert result is not None
    assert result.pleasure < 0, "failure must reduce pleasure"
    assert result.arousal > 0, "failure must raise arousal (Luna flip)"

    db_session.refresh(ep)
    assert ep.affect_vector is not None


def test_picks_most_recent_episode(db_session, test_tenant):
    """When a session has multiple episodes, the latest one receives
    the failure signal."""
    session_id = uuid.uuid4()
    older = _make_episode(db_session, test_tenant.id, session_id=session_id, created_offset_sec=-600)
    newer = _make_episode(db_session, test_tenant.id, session_id=session_id, created_offset_sec=0)

    record_session_tool_failure(
        db_session,
        session_id=session_id,
        tenant_id=test_tenant.id,
        severity=0.8,
    )

    db_session.refresh(older)
    db_session.refresh(newer)
    assert older.affect_vector is None, "older episode should remain untouched"
    assert newer.affect_vector is not None, "latest episode receives the appraisal"


def test_foreign_tenant_episode_is_no_op(db_session, test_tenant, other_tenant):
    """Cross-tenant safety: a session id that resolves to another
    tenant's episode is ignored at lookup time (tenant_id filter)."""
    session_id = uuid.uuid4()
    other_ep = _make_episode(db_session, other_tenant.id, session_id=session_id)

    result = record_session_tool_failure(
        db_session,
        session_id=session_id,
        tenant_id=test_tenant.id,  # different tenant from where the episode lives
        severity=1.0,
    )
    assert result is None

    # Other tenant's episode unchanged.
    db_session.refresh(other_ep)
    assert other_ep.affect_vector is None


def test_severity_clamped_at_helper_layer(db_session, test_tenant):
    """severity > 1 from a buggy caller still produces a valid PAD via
    the appraise_event clamp."""
    session_id = uuid.uuid4()
    _make_episode(db_session, test_tenant.id, session_id=session_id)

    result = record_session_tool_failure(
        db_session,
        session_id=session_id,
        tenant_id=test_tenant.id,
        severity=99.0,  # nonsensical caller input
    )
    assert result is not None
    # Still within bounds.
    assert -1.0 <= result.pleasure <= 1.0
    assert -1.0 <= result.arousal <= 1.0
    assert -1.0 <= result.dominance <= 1.0
