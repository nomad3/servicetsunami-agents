"""Unit tests for emotion_engine_io — Phase 1 PR B.

DB-touching tests. Uses the SQLite in-memory pattern from
test_memory_system.py — fresh schema per test, no shared state.
"""
from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.models.agent_memory import AgentMemory  # noqa: F401 — registers table
from app.models.conversation_episode import ConversationEpisode
from app.models.tenant import Tenant
from app.schemas.emotion import PADVector
from app.services.emotion_engine_io import (
    appraise_and_record_tool_outcome,
    get_affect_baseline,
    get_affect_trace,
    record_affect_on_episode,
    session_belongs_to_tenant,
)


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
    tenant = Tenant(name="Emotion Engine Test Tenant A")
    db_session.add(tenant)
    db_session.commit()
    db_session.refresh(tenant)
    return tenant


@pytest.fixture(name="other_tenant")
def other_tenant_fixture(db_session: Session):
    tenant = Tenant(name="Emotion Engine Test Tenant B")
    db_session.add(tenant)
    db_session.commit()
    db_session.refresh(tenant)
    return tenant


# ── Helpers ───────────────────────────────────────────────────────────


def _make_episode(
    db: Session,
    tenant_id: uuid.UUID,
    *,
    session_id: uuid.UUID | None = None,
    affect_vector: dict | None = None,
    summary: str = "test episode",
) -> ConversationEpisode:
    ep = ConversationEpisode(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        session_id=session_id,
        summary=summary,
        affect_vector=affect_vector,
        created_at=datetime.utcnow(),
    )
    db.add(ep)
    db.commit()
    db.refresh(ep)
    return ep


# ── record_affect_on_episode ──────────────────────────────────────────


def test_record_affect_writes_jsonb(db_session, test_tenant):
    ep = _make_episode(db_session, test_tenant.id)
    vector = PADVector.from_components(pleasure=0.4, arousal=0.2, dominance=0.3)

    result = record_affect_on_episode(
        db_session,
        episode_id=ep.id,
        tenant_id=test_tenant.id,
        vector=vector,
    )
    assert result is not None
    assert result.affect_vector["pleasure"] == pytest.approx(0.4)
    assert result.affect_vector["arousal"] == pytest.approx(0.2)
    assert result.affect_vector["label"] in {"calm", "warm", "playful", "serious", "empathetic", "neutral"}


def test_record_affect_foreign_tenant_returns_none(db_session, test_tenant, other_tenant):
    ep = _make_episode(db_session, other_tenant.id)
    vector = PADVector.from_components(pleasure=0.4, arousal=0.2, dominance=0.3)

    result = record_affect_on_episode(
        db_session,
        episode_id=ep.id,
        tenant_id=test_tenant.id,
        vector=vector,
    )
    assert result is None  # foreign tenant -> no-op


def test_record_affect_missing_episode_returns_none(db_session, test_tenant):
    result = record_affect_on_episode(
        db_session,
        episode_id=uuid.uuid4(),  # doesn't exist
        tenant_id=test_tenant.id,
        vector=PADVector.neutral(),
    )
    assert result is None


# ── get_affect_trace + session_belongs_to_tenant ──────────────────────


def test_affect_trace_returns_episodes_in_chronological_order(db_session, test_tenant):
    session_id = uuid.uuid4()
    _make_episode(db_session, test_tenant.id, session_id=session_id, summary="first")
    _make_episode(db_session, test_tenant.id, session_id=session_id, summary="second")

    trace = get_affect_trace(
        db_session,
        session_id=session_id,
        tenant_id=test_tenant.id,
    )
    assert len(trace) == 2
    # Chronological order: earlier created_at first.
    assert trace[0]["created_at"] <= trace[1]["created_at"]


def test_affect_trace_foreign_tenant_empty(db_session, test_tenant, other_tenant):
    session_id = uuid.uuid4()
    _make_episode(db_session, other_tenant.id, session_id=session_id)

    trace = get_affect_trace(
        db_session,
        session_id=session_id,
        tenant_id=test_tenant.id,
    )
    assert trace == []


def test_session_belongs_to_tenant_true(db_session, test_tenant):
    session_id = uuid.uuid4()
    _make_episode(db_session, test_tenant.id, session_id=session_id)
    assert session_belongs_to_tenant(
        db_session, session_id=session_id, tenant_id=test_tenant.id
    ) is True


def test_session_belongs_to_tenant_false_for_foreign(db_session, test_tenant, other_tenant):
    session_id = uuid.uuid4()
    _make_episode(db_session, other_tenant.id, session_id=session_id)
    assert session_belongs_to_tenant(
        db_session, session_id=session_id, tenant_id=test_tenant.id
    ) is False


def test_session_belongs_to_tenant_false_for_nonexistent(db_session, test_tenant):
    assert session_belongs_to_tenant(
        db_session, session_id=uuid.uuid4(), tenant_id=test_tenant.id
    ) is False


# ── get_affect_baseline ───────────────────────────────────────────────


def test_affect_baseline_missing_returns_neutral(db_session, test_tenant):
    """Agent has no AgentMemory row at all -> neutral baseline."""
    result = get_affect_baseline(
        db_session,
        agent_id=uuid.uuid4(),
        tenant_id=test_tenant.id,
    )
    assert result.pleasure == 0.0
    assert result.arousal == 0.0
    assert result.dominance == 0.0


# ── appraise_and_record_tool_outcome ──────────────────────────────────


def test_appraise_and_record_tool_outcome_writes_positive_shift(db_session, test_tenant):
    ep = _make_episode(db_session, test_tenant.id, affect_vector=None)

    result = appraise_and_record_tool_outcome(
        db_session,
        episode_id=ep.id,
        tenant_id=test_tenant.id,
        agent_id=uuid.uuid4(),
        reward=1.0,
    )
    assert result is not None
    # Tool outcome with full reward should shift pleasure, arousal, AND
    # dominance up from the neutral baseline (N3 of review: assert all
    # three axes, not just pleasure + dominance).
    assert result.pleasure > 0
    assert result.arousal > 0
    assert result.dominance > 0

    # Database row updated.
    db_session.refresh(ep)
    assert ep.affect_vector is not None
    assert ep.affect_vector["pleasure"] > 0


def test_get_affect_baseline_returns_persisted_vector(db_session, test_tenant):
    """N4 of review: round-trip test for get_affect_baseline reading an
    actually-set baseline (not just the neutral fallback)."""
    agent_id = uuid.uuid4()
    baseline_vec = PADVector.from_components(
        pleasure=0.4, arousal=-0.2, dominance=0.5
    ).to_dict()

    memory = AgentMemory(
        id=uuid.uuid4(),
        agent_id=agent_id,
        tenant_id=test_tenant.id,
        memory_type="trait",
        content="baseline trait",
        affect_baseline=baseline_vec,
    )
    db_session.add(memory)
    db_session.commit()

    result = get_affect_baseline(
        db_session,
        agent_id=agent_id,
        tenant_id=test_tenant.id,
    )
    assert result.pleasure == pytest.approx(0.4)
    assert result.arousal == pytest.approx(-0.2)
    assert result.dominance == pytest.approx(0.5)
    assert result.label != "neutral"


def test_appraise_and_record_tool_outcome_chains_from_existing(db_session, test_tenant):
    """If the episode already has an affect_vector, the new appraisal
    starts from that, not from neutral."""
    starting = PADVector.from_components(pleasure=0.5, arousal=0.1, dominance=0.2)
    ep = _make_episode(db_session, test_tenant.id, affect_vector=starting.to_dict())

    result = appraise_and_record_tool_outcome(
        db_session,
        episode_id=ep.id,
        tenant_id=test_tenant.id,
        agent_id=uuid.uuid4(),
        reward=1.0,
    )
    assert result is not None
    # Should land above the starting pleasure.
    assert result.pleasure > starting.pleasure


def test_appraise_and_record_tool_outcome_foreign_tenant_returns_none(
    db_session, test_tenant, other_tenant
):
    ep = _make_episode(db_session, other_tenant.id)

    result = appraise_and_record_tool_outcome(
        db_session,
        episode_id=ep.id,
        tenant_id=test_tenant.id,
        agent_id=uuid.uuid4(),
        reward=1.0,
    )
    assert result is None
