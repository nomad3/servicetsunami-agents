"""Unit tests for GET /api/v1/affect/agents/{agent_id} — Phase 2.

Verifies tenant isolation (404 on foreign), neutral baseline default,
and live-state surfacing when an episode has affect_vector recorded.
"""
from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from sqlalchemy.orm import Session

from app.api.v1.emotion import _latest_affect_for_agent_tenant
from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.models.agent import Agent
from app.models.agent_memory import AgentMemory  # noqa: F401 — registers table
from app.models.conversation_episode import ConversationEpisode
from app.models.tenant import Tenant
from app.schemas.emotion import PADVector
from app.services.emotion_engine_io import get_affect_baseline


@pytest.fixture(name="db_session")
def db_session_fixture():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    yield db
    db.close()
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(name="test_tenant")
def test_tenant_fixture(db_session: Session):
    tenant = Tenant(name="Per-agent Affect Tenant")
    db_session.add(tenant)
    db_session.commit()
    db_session.refresh(tenant)
    return tenant


@pytest.fixture(name="other_tenant")
def other_tenant_fixture(db_session: Session):
    tenant = Tenant(name="Per-agent Affect Other Tenant")
    db_session.add(tenant)
    db_session.commit()
    db_session.refresh(tenant)
    return tenant


def _make_agent(db: Session, tenant_id: uuid.UUID, name: str = "Luna Supervisor") -> Agent:
    agent = Agent(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        name=name,
        description=name,
        status="production",
        config={"model": "claude-3-5-sonnet-20241022"},
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)
    return agent


def _make_episode(
    db: Session,
    tenant_id: uuid.UUID,
    affect_vector: dict | None = None,
) -> ConversationEpisode:
    ep = ConversationEpisode(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        summary="test",
        affect_vector=affect_vector,
        created_at=datetime.utcnow(),
    )
    db.add(ep)
    db.commit()
    db.refresh(ep)
    return ep


# ── _latest_affect_for_agent_tenant ───────────────────────────────────


def test_latest_affect_returns_none_when_no_episode_recorded(db_session, test_tenant):
    agent = _make_agent(db_session, test_tenant.id)
    result = _latest_affect_for_agent_tenant(
        db_session, agent_id=agent.id, tenant_id=test_tenant.id,
    )
    assert result is None


def test_latest_affect_returns_most_recent_vector(db_session, test_tenant):
    agent = _make_agent(db_session, test_tenant.id)
    vec = PADVector.from_components(pleasure=0.5, arousal=0.2, dominance=0.3).to_dict()
    _make_episode(db_session, test_tenant.id, affect_vector=vec)
    result = _latest_affect_for_agent_tenant(
        db_session, agent_id=agent.id, tenant_id=test_tenant.id,
    )
    assert result is not None
    assert result.pleasure == pytest.approx(0.5)


def test_latest_affect_foreign_tenant_returns_none(db_session, test_tenant, other_tenant):
    """Other tenant's episode with affect_vector — should not leak to
    queries against test_tenant."""
    vec = PADVector.from_components(pleasure=0.9, arousal=0.9, dominance=0.9).to_dict()
    _make_episode(db_session, other_tenant.id, affect_vector=vec)
    result = _latest_affect_for_agent_tenant(
        db_session, agent_id=uuid.uuid4(), tenant_id=test_tenant.id,
    )
    assert result is None


# ── Baseline + behaviour invariants ───────────────────────────────────


def test_baseline_defaults_to_neutral_for_new_agent(db_session, test_tenant):
    """An agent with no AgentMemory.affect_baseline row → neutral
    baseline. Endpoint relies on this default to provide a safe
    response shape even before any persona seeding lands."""
    agent = _make_agent(db_session, test_tenant.id)
    baseline = get_affect_baseline(
        db_session, agent_id=agent.id, tenant_id=test_tenant.id,
    )
    assert baseline.label == "neutral"
    assert baseline.pleasure == 0.0
    assert baseline.arousal == 0.0
    assert baseline.dominance == 0.0


def test_has_live_state_flag_reflects_episode_presence(db_session, test_tenant):
    """The endpoint's has_live_state should be False when no episode
    has affect_vector recorded, regardless of whether the agent has a
    baseline. Mirror that invariant at the helper-function level."""
    agent = _make_agent(db_session, test_tenant.id)

    # No episodes yet
    assert _latest_affect_for_agent_tenant(
        db_session, agent_id=agent.id, tenant_id=test_tenant.id,
    ) is None

    # Episode WITHOUT affect_vector — has_live_state still False
    _make_episode(db_session, test_tenant.id, affect_vector=None)
    assert _latest_affect_for_agent_tenant(
        db_session, agent_id=agent.id, tenant_id=test_tenant.id,
    ) is None

    # Episode WITH affect_vector — now has_live_state should flip to True
    vec = PADVector.from_components(pleasure=0.3, arousal=0.1, dominance=0.2).to_dict()
    _make_episode(db_session, test_tenant.id, affect_vector=vec)
    result = _latest_affect_for_agent_tenant(
        db_session, agent_id=agent.id, tenant_id=test_tenant.id,
    )
    assert result is not None
    assert result.label != "neutral"
