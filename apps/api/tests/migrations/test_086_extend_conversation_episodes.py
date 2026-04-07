import os
import pytest
from sqlalchemy import create_engine, inspect, text


@pytest.fixture
def engine():
    return create_engine(os.environ["DATABASE_URL"])


def test_conversation_episodes_has_window_columns(engine):
    cols = {c["name"] for c in inspect(engine).get_columns("conversation_episodes")}
    assert "window_start" in cols
    assert "window_end" in cols
    assert "trigger_reason" in cols
    assert "agent_slug" in cols
    assert "generated_by" in cols


def test_conversation_episodes_unique_window_constraint(engine):
    with engine.connect() as c:
        result = c.execute(text("""
            SELECT conname FROM pg_constraint
            WHERE conrelid = 'conversation_episodes'::regclass
              AND contype = 'u'
              AND conname = 'uk_conv_episodes_session_window'
        """)).first()
        assert result is not None


def test_conversation_episode_orm_accepts_new_columns(engine):
    """ORM round-trip — verify the model knows about the new columns.
    Uses a transaction that's rolled back so no test data is committed."""
    from datetime import datetime, timezone
    from uuid import uuid4
    from sqlalchemy.orm import sessionmaker
    from app.models.conversation_episode import ConversationEpisode

    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        # Find any tenant_id we can use without creating one
        tenant_row = db.execute(text("SELECT id FROM tenants LIMIT 1")).first()
        if tenant_row is None:
            pytest.skip("no tenants in DB to test ORM round-trip against")
        ep = ConversationEpisode(
            tenant_id=tenant_row.id,
            summary="ORM round-trip test",
            window_start=datetime.now(timezone.utc),
            window_end=datetime.now(timezone.utc),
            trigger_reason="test",
            generated_by="test",
            agent_slug="test_agent",
        )
        db.add(ep)
        db.flush()
        assert ep.id is not None
    finally:
        db.rollback()
        db.close()
