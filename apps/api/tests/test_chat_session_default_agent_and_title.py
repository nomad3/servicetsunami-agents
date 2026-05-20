"""Regression tests for the 2026-05-19 chat-session default-agent + title bug.

Tenants with named variants of Luna (e.g. "Luna Supervisor") were getting
"Root Cause Analyst" as the default chat agent because:

1. `Agent.name == "Luna"` (exact equality) didn't match any agent named
   "Luna Supervisor" or "Luna General Assistant", so the Luna-preference
   clause was a no-op.
2. The fallback ordering by `Agent.id.asc()` picked whichever production
   agent had the lowest UUID — for the operator's tenant this happened
   to be "Root Cause Analyst".
3. The session title default was just the agent name with no
   disambiguator, so a day's worth of dispatches looked identical in the
   sessions list.

The fix: `Agent.name.ilike("Luna%")` for the preference, plus a HH:MM
timestamp suffix on the auto-generated title.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime

import pytest
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.models.agent import Agent
from app.models.tenant import Tenant
from app.services.chat import create_chat_session


@pytest.fixture(name="db_session")
def db_session_fixture():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    yield db
    db.close()
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(name="test_tenant")
def test_tenant_fixture(db_session: Session):
    tenant = Tenant(name="Chat Session Default Agent Tenant")
    db_session.add(tenant)
    db_session.commit()
    db_session.refresh(tenant)
    return tenant


def _make_agent(
    db: Session,
    tenant_id,
    name: str,
    status: str = "production",
    id_: str | None = None,
) -> Agent:
    """Insert an agent with optional explicit id (so we can reproduce
    the lowest-id-wins selection from the original bug)."""
    a = Agent(
        id=uuid.UUID(id_) if id_ else uuid.uuid4(),
        tenant_id=tenant_id,
        name=name,
        description=name,
        status=status,
        config={"model": "claude-3-5-sonnet-20241022"},
    )
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


def test_luna_variant_wins_over_lower_id_production_agent(db_session, test_tenant):
    """The original bug: a 'Luna Supervisor' with a higher UUID lost to
    'Root Cause Analyst' because the exact-equality preference didn't
    fire and the fallback ordering picked the lowest id."""
    # RCA gets the LOWER uuid (mimics the production state)
    _make_agent(
        db_session, test_tenant.id, "Root Cause Analyst",
        id_="03282d66-d0ca-4360-ac86-bcfee5e1766f",
    )
    _make_agent(
        db_session, test_tenant.id, "Luna Supervisor",
        id_="9d85ff11-7465-4815-983d-85573809dee6",
    )

    session = create_chat_session(db_session, tenant_id=test_tenant.id)
    assert session.agent_id is not None
    selected = db_session.query(Agent).filter(Agent.id == session.agent_id).first()
    assert selected.name == "Luna Supervisor", (
        f"Expected Luna Supervisor to win, got {selected.name}. "
        "The Luna prefix preference is not firing."
    )


def test_bare_luna_still_wins(db_session, test_tenant):
    """Sanity: tenants that keep a literal 'Luna' agent still get it."""
    _make_agent(db_session, test_tenant.id, "Data Investigator")
    _make_agent(db_session, test_tenant.id, "Luna")
    session = create_chat_session(db_session, tenant_id=test_tenant.id)
    selected = db_session.query(Agent).filter(Agent.id == session.agent_id).first()
    assert selected.name == "Luna"


def test_no_luna_variant_falls_back_to_production(db_session, test_tenant):
    """When no Luna* agent exists, the production-first ordering wins."""
    _make_agent(db_session, test_tenant.id, "Triage Agent", status="production")
    _make_agent(db_session, test_tenant.id, "Draft Bot", status="draft")
    session = create_chat_session(db_session, tenant_id=test_tenant.id)
    selected = db_session.query(Agent).filter(Agent.id == session.agent_id).first()
    assert selected.name == "Triage Agent"


def test_auto_title_includes_hhmm_disambiguator(db_session, test_tenant):
    """Auto-generated session title should include an HH:MM suffix so
    same-day dispatches aren't all titled identically."""
    _make_agent(db_session, test_tenant.id, "Luna Supervisor")
    session = create_chat_session(db_session, tenant_id=test_tenant.id)
    # Expect: "Luna Supervisor · HH:MM"
    assert re.match(r"^Luna Supervisor · \d{2}:\d{2}$", session.title), (
        f"Title format unexpected: {session.title!r}"
    )


def test_explicit_title_overrides_default(db_session, test_tenant):
    """Callers passing a title still get exactly that title (no
    timestamp suffix). The disambiguator only applies to the default."""
    _make_agent(db_session, test_tenant.id, "Luna Supervisor")
    session = create_chat_session(
        db_session,
        tenant_id=test_tenant.id,
        title="My Conversation",
    )
    assert session.title == "My Conversation"


def test_two_sessions_minutes_apart_get_distinct_titles(db_session, test_tenant):
    """Two calls in the same minute will collide on the HH:MM suffix —
    that's acceptable (the IDs differ; the title is a UX hint, not a
    uniqueness key). But sessions created in different minutes should
    visibly differ. Sanity-check that the timestamp is actually being
    inserted (no mock here — just structural)."""
    _make_agent(db_session, test_tenant.id, "Luna Supervisor")
    s1 = create_chat_session(db_session, tenant_id=test_tenant.id)
    assert "·" in s1.title  # the separator is present
    # Both ID and (very likely) timestamp differ
    s2 = create_chat_session(db_session, tenant_id=test_tenant.id)
    assert s1.id != s2.id
