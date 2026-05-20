"""DB-touching tests for app.services.reflection_io — O1 substrate.

Marked `integration` and runs against the real Postgres exposed by
the api(integration, postgres+pgvector) CI job. Same pattern as
`test_metacog_io.py`: throwaway Tenant + Agent per test, cascade
delete in teardown.

This file is Postgres-only (UUID + JSONB native types). The earlier
SQLite-shim approach for metacog_io fought SQLAlchemy's compile cache
— real Postgres avoids the issue entirely.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

pytestmark = pytest.mark.integration

from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.agent import Agent
from app.models.agent_memory import AgentMemory  # noqa: F401 — FK chain
from app.models.tenant import Tenant
from app.schemas.reflection import NightlyReflection
from app.services.reflection_io import (
    get_reflection_count,
    list_reflections,
    write_reflection,
)


@pytest.fixture(name="db")
def db_fixture():
    """A real Postgres session. Same SessionLocal as the api."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(name="tenant_with_agent")
def tenant_with_agent_fixture(db: Session):
    """Throwaway Tenant + Agent. Tenant cascade deletes agent and
    agent_memories (FK is ON DELETE CASCADE)."""
    tenant = Tenant(name=f"reflection-test-{uuid.uuid4()}")
    db.add(tenant)
    db.flush()
    agent = Agent(tenant_id=tenant.id, name=f"reflection-test-agent-{uuid.uuid4()}")
    db.add(agent)
    db.commit()
    yield tenant, agent
    try:
        db.execute(
            __import__("sqlalchemy").text(
                "DELETE FROM tenants WHERE id = :tid"
            ),
            {"tid": tenant.id},
        )
        db.commit()
    except Exception:
        db.rollback()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_reflection(
    tenant_id,
    agent_id,
    *,
    day="2026-05-20",
    kind="next_move",
    content="default reflection content",
    confidence=0.6,
    source_memory_ids=None,
) -> NightlyReflection:
    return NightlyReflection(
        tenant_id=str(tenant_id),
        agent_id=str(agent_id),
        day=day,
        kind=kind,
        content=content,
        source_memory_ids=source_memory_ids or [str(uuid.uuid4())],
        confidence=confidence,
        ts=_now(),
    )


# ── write_reflection ──────────────────────────────────────────────────


def test_write_reflection_persists_and_roundtrips(db, tenant_with_agent):
    tenant, agent = tenant_with_agent
    r = _make_reflection(
        tenant.id, agent.id,
        content="Tomorrow: ship dreams-O2 workflow.",
        confidence=0.75,
    )
    row_id = write_reflection(db, reflection=r)
    assert row_id is not None

    fetched = list_reflections(db, tenant_id=tenant.id)
    assert len(fetched) == 1
    assert fetched[0].content == "Tomorrow: ship dreams-O2 workflow."
    assert fetched[0].confidence == 0.75
    assert fetched[0].kind == "next_move"


def test_write_reflection_rejects_tenant_boundary_violation(
    db, tenant_with_agent,
):
    tenant, agent = tenant_with_agent
    other_tenant_id = uuid.uuid4()
    foreign = _make_reflection(other_tenant_id, agent.id)
    row_id = write_reflection(
        db,
        reflection=foreign,
        current_tenant_id=tenant.id,
    )
    assert row_id is None
    assert list_reflections(db, tenant_id=tenant.id) == []
    assert list_reflections(db, tenant_id=other_tenant_id) == []


def test_write_reflection_rejects_malformed_uuids(db):
    bad = NightlyReflection(
        tenant_id="not-a-uuid",
        agent_id="also-not-a-uuid",
        day="2026-05-20",
        kind="idea",
        content="malformed parents",
        source_memory_ids=["m-1"],
        confidence=0.5,
        ts=_now(),
    )
    assert write_reflection(db, reflection=bad) is None


# ── list_reflections filtering ────────────────────────────────────────


def test_list_reflections_filters_by_day(db, tenant_with_agent):
    tenant, agent = tenant_with_agent
    write_reflection(
        db,
        reflection=_make_reflection(tenant.id, agent.id, day="2026-05-18"),
    )
    write_reflection(
        db,
        reflection=_make_reflection(tenant.id, agent.id, day="2026-05-19"),
    )
    write_reflection(
        db,
        reflection=_make_reflection(tenant.id, agent.id, day="2026-05-20"),
    )

    today = list_reflections(db, tenant_id=tenant.id, day="2026-05-19")
    assert len(today) == 1
    assert today[0].day == "2026-05-19"


def test_list_reflections_filters_by_kind(db, tenant_with_agent):
    tenant, agent = tenant_with_agent
    write_reflection(
        db,
        reflection=_make_reflection(tenant.id, agent.id, kind="risk"),
    )
    write_reflection(
        db,
        reflection=_make_reflection(tenant.id, agent.id, kind="idea"),
    )
    write_reflection(
        db,
        reflection=_make_reflection(tenant.id, agent.id, kind="next_move"),
    )

    risks = list_reflections(db, tenant_id=tenant.id, kind="risk")
    assert len(risks) == 1
    assert risks[0].kind == "risk"


def test_list_reflections_filters_by_day_and_kind(db, tenant_with_agent):
    tenant, agent = tenant_with_agent
    # Same day, different kinds
    write_reflection(
        db,
        reflection=_make_reflection(
            tenant.id, agent.id, day="2026-05-20", kind="risk",
        ),
    )
    write_reflection(
        db,
        reflection=_make_reflection(
            tenant.id, agent.id, day="2026-05-20", kind="idea",
        ),
    )
    # Different day, matching kind
    write_reflection(
        db,
        reflection=_make_reflection(
            tenant.id, agent.id, day="2026-05-19", kind="risk",
        ),
    )

    hits = list_reflections(
        db, tenant_id=tenant.id, day="2026-05-20", kind="risk",
    )
    assert len(hits) == 1
    assert hits[0].day == "2026-05-20"
    assert hits[0].kind == "risk"


def test_list_reflections_filters_by_agent(db, tenant_with_agent):
    tenant, agent_a = tenant_with_agent
    agent_b = Agent(
        tenant_id=tenant.id, name=f"reflection-other-{uuid.uuid4()}"
    )
    db.add(agent_b)
    db.commit()

    write_reflection(db, reflection=_make_reflection(tenant.id, agent_a.id))
    write_reflection(db, reflection=_make_reflection(tenant.id, agent_b.id))

    a_only = list_reflections(db, tenant_id=tenant.id, agent_id=agent_a.id)
    assert len(a_only) == 1
    assert a_only[0].agent_id == str(agent_a.id)


def test_list_reflections_empty_for_unknown_day(db, tenant_with_agent):
    tenant, agent = tenant_with_agent
    write_reflection(
        db,
        reflection=_make_reflection(tenant.id, agent.id, day="2026-05-20"),
    )
    assert list_reflections(db, tenant_id=tenant.id, day="1999-01-01") == []


def test_list_reflections_tenant_isolated(db, tenant_with_agent):
    tenant, agent = tenant_with_agent
    write_reflection(db, reflection=_make_reflection(tenant.id, agent.id))

    other_tenant = Tenant(name=f"reflection-other-{uuid.uuid4()}")
    db.add(other_tenant)
    db.commit()
    try:
        assert list_reflections(db, tenant_id=other_tenant.id) == []
    finally:
        db.execute(
            __import__("sqlalchemy").text(
                "DELETE FROM tenants WHERE id = :tid"
            ),
            {"tid": other_tenant.id},
        )
        db.commit()


def test_list_reflections_ordered_desc_by_created_at(db, tenant_with_agent):
    """Morning-review surface relies on freshest-first ordering."""
    tenant, agent = tenant_with_agent
    write_reflection(
        db,
        reflection=_make_reflection(tenant.id, agent.id, content="first reflection"),
    )
    write_reflection(
        db,
        reflection=_make_reflection(tenant.id, agent.id, content="second reflection"),
    )
    write_reflection(
        db,
        reflection=_make_reflection(tenant.id, agent.id, content="third reflection"),
    )

    rows = list_reflections(db, tenant_id=tenant.id)
    assert len(rows) == 3
    # Freshest first
    assert rows[0].content == "third reflection"
    assert rows[-1].content == "first reflection"


# ── get_reflection_count ──────────────────────────────────────────────


def test_get_reflection_count_total(db, tenant_with_agent):
    tenant, agent = tenant_with_agent
    assert get_reflection_count(db, tenant_id=tenant.id) == 0
    write_reflection(db, reflection=_make_reflection(tenant.id, agent.id))
    write_reflection(db, reflection=_make_reflection(tenant.id, agent.id))
    assert get_reflection_count(db, tenant_id=tenant.id) == 2


def test_get_reflection_count_by_day(db, tenant_with_agent):
    tenant, agent = tenant_with_agent
    write_reflection(
        db,
        reflection=_make_reflection(tenant.id, agent.id, day="2026-05-20"),
    )
    write_reflection(
        db,
        reflection=_make_reflection(tenant.id, agent.id, day="2026-05-20"),
    )
    write_reflection(
        db,
        reflection=_make_reflection(tenant.id, agent.id, day="2026-05-19"),
    )
    assert get_reflection_count(db, tenant_id=tenant.id, day="2026-05-20") == 2
    assert get_reflection_count(db, tenant_id=tenant.id, day="2026-05-19") == 1
    assert get_reflection_count(db, tenant_id=tenant.id, day="1999-01-01") == 0
