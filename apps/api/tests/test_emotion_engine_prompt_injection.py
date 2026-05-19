"""Unit tests for PR C — prompt-side PAD style injection.

Tests both the pure formatting function and the DB-backed session
look-up that cli_session_manager calls.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.models.agent_memory import AgentMemory  # noqa: F401 — registers table
from app.models.conversation_episode import ConversationEpisode
from app.models.tenant import Tenant
from app.schemas.emotion import PADVector
from app.services.emotion_engine import format_affect_addendum
from app.services.emotion_engine_io import (
    build_affect_addendum_for_session,
    get_latest_session_affect,
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
    tenant = Tenant(name="Emotion PR C Tenant")
    db_session.add(tenant)
    db_session.commit()
    db_session.refresh(tenant)
    return tenant


# ── format_affect_addendum (pure function) ────────────────────────────


def test_format_affect_addendum_none_returns_empty_string():
    assert format_affect_addendum(None) == ""


def test_format_affect_addendum_neutral_returns_empty_string():
    """Neutral state -> empty string so callers can unconditionally
    concatenate without bloating the prompt."""
    assert format_affect_addendum(PADVector.neutral()) == ""


def test_format_affect_addendum_non_neutral_contains_label():
    vec = PADVector.from_components(pleasure=0.7, arousal=0.7, dominance=0.5)
    out = format_affect_addendum(vec)
    assert "Current Affective State" in out
    assert "playful" in out
    assert "pleasure=+0.70" in out
    assert "arousal=+0.70" in out
    assert "dominance=+0.50" in out


def test_format_affect_addendum_negative_pleasure_renders_minus_sign():
    vec = PADVector.from_components(pleasure=-0.6, arousal=0.5, dominance=-0.4)
    out = format_affect_addendum(vec)
    assert "pleasure=-0.60" in out
    # Low pleasure + low dominance -> "empathetic"
    assert "empathetic" in out


def test_format_affect_addendum_does_not_announce_state():
    """The addendum must tell the model 'do not announce' the state so
    we don't get performative 'I am feeling sad now' surface text."""
    vec = PADVector.from_components(pleasure=0.5, arousal=0.5, dominance=0.0)
    out = format_affect_addendum(vec)
    assert "Do not announce" in out


def test_format_affect_addendum_includes_per_mood_tone_guidance():
    """Luna's chain-review IMPORTANT (2026-05-19): each mood label maps
    to specific tone-guidance, not a uniform 'colour your tone'
    instruction."""
    moods_to_check = [
        ("playful", PADVector.from_components(pleasure=0.7, arousal=0.7, dominance=0.5)),
        ("serious", PADVector.from_components(pleasure=-0.7, arousal=0.3, dominance=0.5)),
        ("empathetic", PADVector.from_components(pleasure=-0.7, arousal=-0.3, dominance=-0.5)),
        ("calm", PADVector.from_components(pleasure=0.7, arousal=-0.5, dominance=0.5)),
        ("warm", PADVector.from_components(pleasure=0.7, arousal=-0.5, dominance=-0.5)),
    ]
    addenda = {mood: format_affect_addendum(vec) for mood, vec in moods_to_check}
    for mood, text in addenda.items():
        assert text, f"{mood} addendum was empty"
    assert "composed authority" in addenda["calm"]
    assert "relaxed friendliness" in addenda["warm"]
    assert "snappier" in addenda["playful"]
    assert "fact-first" in addenda["serious"]
    assert "Slow tempo" in addenda["empathetic"]


# ── get_latest_session_affect (DB-backed) ─────────────────────────────


def test_get_latest_session_affect_none_when_no_episodes(db_session, test_tenant):
    assert get_latest_session_affect(
        db_session,
        session_id=uuid.uuid4(),
        tenant_id=test_tenant.id,
    ) is None


def test_get_latest_session_affect_returns_most_recent(db_session, test_tenant):
    session_id = uuid.uuid4()
    now = datetime.utcnow()

    older_vec = PADVector.from_components(pleasure=0.1, arousal=0.0, dominance=0.0).to_dict()
    newer_vec = PADVector.from_components(pleasure=0.8, arousal=0.4, dominance=0.6).to_dict()

    older = ConversationEpisode(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        session_id=session_id,
        summary="older",
        affect_vector=older_vec,
        created_at=now - timedelta(minutes=10),
    )
    newer = ConversationEpisode(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        session_id=session_id,
        summary="newer",
        affect_vector=newer_vec,
        created_at=now,
    )
    db_session.add_all([older, newer])
    db_session.commit()

    result = get_latest_session_affect(
        db_session,
        session_id=session_id,
        tenant_id=test_tenant.id,
    )
    assert result is not None
    assert result.pleasure == pytest.approx(0.8)


def test_get_latest_session_affect_skips_episodes_without_vector(db_session, test_tenant):
    """Only episodes WITH an affect_vector should be considered."""
    session_id = uuid.uuid4()
    now = datetime.utcnow()

    with_vec = PADVector.from_components(pleasure=0.5, arousal=0.0, dominance=0.0).to_dict()

    early = ConversationEpisode(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        session_id=session_id,
        summary="early",
        affect_vector=with_vec,
        created_at=now - timedelta(minutes=10),
    )
    late_blank = ConversationEpisode(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        session_id=session_id,
        summary="late but no vec",
        affect_vector=None,
        created_at=now,
    )
    db_session.add_all([early, late_blank])
    db_session.commit()

    result = get_latest_session_affect(
        db_session,
        session_id=session_id,
        tenant_id=test_tenant.id,
    )
    assert result is not None
    assert result.pleasure == pytest.approx(0.5)


# ── build_affect_addendum_for_session (full path) ─────────────────────


def test_build_affect_addendum_none_session_returns_empty(db_session, test_tenant):
    """Defensive: callers may pass None when no session is in flight."""
    assert build_affect_addendum_for_session(
        db_session,
        session_id=None,
        tenant_id=test_tenant.id,
    ) == ""


def test_build_affect_addendum_no_episode_returns_empty(db_session, test_tenant):
    assert build_affect_addendum_for_session(
        db_session,
        session_id=uuid.uuid4(),
        tenant_id=test_tenant.id,
    ) == ""


def test_build_affect_addendum_neutral_episode_returns_empty(db_session, test_tenant):
    session_id = uuid.uuid4()
    neutral = PADVector.neutral().to_dict()
    ep = ConversationEpisode(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        session_id=session_id,
        summary="neutral",
        affect_vector=neutral,
        created_at=datetime.utcnow(),
    )
    db_session.add(ep)
    db_session.commit()

    assert build_affect_addendum_for_session(
        db_session,
        session_id=session_id,
        tenant_id=test_tenant.id,
    ) == ""


def test_build_affect_addendum_active_state_returns_markdown(db_session, test_tenant):
    session_id = uuid.uuid4()
    vec = PADVector.from_components(pleasure=-0.7, arousal=0.6, dominance=-0.3).to_dict()
    ep = ConversationEpisode(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        session_id=session_id,
        summary="frustrated",
        affect_vector=vec,
        created_at=datetime.utcnow(),
    )
    db_session.add(ep)
    db_session.commit()

    out = build_affect_addendum_for_session(
        db_session,
        session_id=session_id,
        tenant_id=test_tenant.id,
    )
    assert "Current Affective State" in out
    assert "empathetic" in out  # low P, low D -> empathetic
