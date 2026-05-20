"""DB-touching tests for app.services.metacog_io (M1 of #616).

Uses a PER-TEST SQLite engine with comprehensive PG-only type
shimming — NOT the shared Base.metadata pattern that bit us four
times this session (#610/#612/#613 cascade).

Why this fixture is heavy:
  - SQLAlchemy ORM-mapped classes (AgentMemory etc.) are bound to
    Base.metadata. We can't sidestep that without rewriting the IO
    layer to take a table arg. So we DO temporarily mutate column
    types — but ALL PG-only types in the tables we create:
      UUID(as_uuid=True) → String(36) via _SqliteUuidShim
      JSONB             → JSON (cross-dialect)
      ARRAY(...)        → JSON (we don't exercise array ops here)
      Vector(...)       → JSON (NULL-only in our writes)
      INET              → String(45)
  - try/finally always restores. Single-threaded only (no xdist) —
    the file's own warning makes that explicit.

Superpowers BLOCKER fix (PR #617 review): the v1 fixture only shimmed
UUIDs and missed JSONB/ARRAY/Vector columns on agents +
agent_memories. All 11 IO tests failed at create_all. This v2
shims everything.
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest
from sqlalchemy import JSON, String, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.types import TypeDecorator

from app.db.base import Base
from app.models.agent import Agent
from app.models.agent_memory import AgentMemory  # noqa: F401 — registers table
from app.models.tenant import Tenant
from app.schemas.metacog import ConfidencePrediction, OutcomeObservation
from app.services.metacog_io import (
    list_observations,
    list_predictions,
    list_traces,
    write_observation,
    write_prediction,
)


# Single-threaded-only: this fixture mutates Base.metadata columns
# for its lifetime. xdist would race. Same rule the existing
# test_refresh_tokens.py file already uses.
pytestmark = pytest.mark.serial


# ── Per-test SQLite isolation harness ─────────────────────────────────


class _SqliteUuidShim(TypeDecorator):
    """UUID ↔ CHAR(36) bridge for SQLite."""

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


# Class-name-based dispatch is more robust than isinstance against
# Postgres-only type classes — those classes are imported from
# sqlalchemy.dialects.postgresql and pg_vector, so we don't want a
# hard import dependency here.
_PG_TYPE_CLASSES_TO_REPLACE = {
    "UUID": lambda: _SqliteUuidShim(),
    "JSONB": lambda: JSON(),
    "ARRAY": lambda: JSON(),
    "Vector": lambda: JSON(),
    "INET": lambda: String(45),
}


def _swap_pg_types_on_table(table) -> dict:
    """For each column whose type's class name is in
    _PG_TYPE_CLASSES_TO_REPLACE, swap the type to a SQLite-friendly
    fallback. Returns the {col_name: original_type} dict so the
    caller can restore later.

    Idempotent: skips columns whose type was already shimmed.
    """
    originals: dict[str, object] = {}
    for col in table.c:
        type_class_name = col.type.__class__.__name__
        if type_class_name in _PG_TYPE_CLASSES_TO_REPLACE:
            originals[col.name] = col.type
            col.type = _PG_TYPE_CLASSES_TO_REPLACE[type_class_name]()
    return originals


@contextmanager
def _per_test_sqlite():
    """Yield a Session bound to a fresh in-memory SQLite engine with
    the three tables metacog_io touches (tenants, agents,
    agent_memories). All PG-only column types are swapped to
    SQLite-friendly fallbacks for the duration; restored in
    try/finally so nothing leaks out."""
    original_types: dict[str, dict[str, object]] = {}
    table_names = ("tenants", "agents", "agent_memories")
    try:
        for tbl_name in table_names:
            tbl = Base.metadata.tables[tbl_name]
            original_types[tbl_name] = _swap_pg_types_on_table(tbl)

        # StaticPool keeps every connection on the SAME in-memory
        # SQLite. Without it, create_all() runs on connection A and
        # the session's queries land on connection B which sees an
        # empty database — that was the CI failure mode where
        # write_prediction committed fine but refresh(row) raised
        # ObjectDeletedError. Local container happened to dodge this
        # because of an environment quirk in app.db.session.
        engine = create_engine(
            "sqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(
            engine,
            tables=[Base.metadata.tables[n] for n in table_names],
        )
        # expire_on_commit=False keeps the fixture-built Tenant + Agent
        # rows usable after db.commit() — without it, CI hit
        # ObjectDeletedError on tenant.id refresh (local container
        # didn't reproduce, but CI's pytest collection order tripped
        # the default expire_on_commit=True semantics).
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
def tenant_with_agent(db: Session):
    """Returns (tenant, agent) — agent_memories.agent_id is a real
    FK so we need a real agent row in the tenant before any
    write_prediction / write_observation will land cleanly."""
    tenant = Tenant(name="Metacog Test Tenant")
    db.add(tenant)
    db.flush()  # populate tenant.id
    agent = Agent(tenant_id=tenant.id, name="Test Agent")
    db.add(agent)
    db.flush()
    db.commit()
    return tenant, agent


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_prediction(
    tenant_id, agent_id,
    decision_id=None, predicted=0.5,
    kind="rl_route_chat_response",
) -> ConfidencePrediction:
    return ConfidencePrediction(
        tenant_id=str(tenant_id),
        agent_id=str(agent_id),
        decision_id=str(decision_id or uuid.uuid4()),
        decision_kind=kind,
        predicted_confidence=predicted,
        context_hash="ctx",
        ts=_now(),
    )


def _make_observation(
    tenant_id, agent_id, decision_id, reward=0.0,
) -> OutcomeObservation:
    return OutcomeObservation(
        tenant_id=str(tenant_id),
        agent_id=str(agent_id),
        decision_id=str(decision_id),
        actual_reward=reward,
        latency_ms=10,
        completed_at=_now(),
    )


# ── write_prediction ──────────────────────────────────────────────────


def test_write_prediction_persists_and_roundtrips(db, tenant_with_agent):
    tenant, agent = tenant_with_agent
    p = _make_prediction(tenant.id, agent.id, predicted=0.77)
    row_id = write_prediction(db, prediction=p)
    assert row_id is not None

    # Diagnostic: raw count confirmed row persists; the bug is in
    # the tenant_id filter inside list_predictions. Dump what's
    # actually stored vs what we're filtering for.
    from sqlalchemy import text as _sql_text
    raw_rows = db.execute(_sql_text(
        "SELECT id, tenant_id, agent_id, memory_type FROM agent_memories"
    )).fetchall()
    assert len(raw_rows) == 1, (
        f"raw rows found {len(raw_rows)}; expected 1"
    )
    stored_tenant_id = raw_rows[0][1]
    queried_tenant_id = str(tenant.id)
    assert stored_tenant_id == queried_tenant_id, (
        f"stored tenant_id={stored_tenant_id!r} (type={type(stored_tenant_id).__name__}) "
        f"!= queried tenant_id={queried_tenant_id!r}"
    )

    # Diagnostic: bypass list_predictions, query directly via ORM.
    # If filter-by-memory-type passes but filter-by-tenant-id fails,
    # the shim's bind processor isn't being applied for the ORM query.
    from app.models.agent_memory import AgentMemory as _AM
    by_kind = db.query(_AM).filter(
        _AM.memory_type == "metacog_confidence_prediction",
    ).all()
    assert len(by_kind) == 1, (
        f"ORM filter by memory_type only: got {len(by_kind)}"
    )
    by_kind_and_tenant = db.query(_AM).filter(
        _AM.memory_type == "metacog_confidence_prediction",
        _AM.tenant_id == tenant.id,
    ).all()
    assert len(by_kind_and_tenant) == 1, (
        f"ORM filter w/ tenant_id=uuid.UUID: got {len(by_kind_and_tenant)} "
        f"(stored={stored_tenant_id!r}, tenant.id={tenant.id!r} type={type(tenant.id).__name__})"
    )

    fetched = list_predictions(db, tenant_id=tenant.id)
    assert len(fetched) == 1
    assert fetched[0].predicted_confidence == 0.77
    assert fetched[0].decision_id == p.decision_id


def test_write_prediction_rejects_tenant_boundary_violation(
    db, tenant_with_agent,
):
    """A caller claiming to be tenant-A cannot persist a prediction
    serialized for tenant-B."""
    tenant, agent = tenant_with_agent
    other_tenant_id = uuid.uuid4()
    foreign_pred = _make_prediction(other_tenant_id, agent.id)
    row_id = write_prediction(
        db,
        prediction=foreign_pred,
        current_tenant_id=tenant.id,  # claim tenant
    )
    assert row_id is None
    # Nothing persisted for either tenant
    assert list_predictions(db, tenant_id=tenant.id) == []
    assert list_predictions(db, tenant_id=other_tenant_id) == []


def test_write_prediction_rejects_malformed_uuids(db):
    """Defensive: don't crash on garbage IDs, just return None."""
    bad = ConfidencePrediction(
        tenant_id="not-a-uuid",
        agent_id="also-not-a-uuid",
        decision_id="d",
        decision_kind="rl_route_chat_response",
        predicted_confidence=0.5,
        context_hash="x",
        ts=_now(),
    )
    assert write_prediction(db, prediction=bad) is None


# ── write_observation ─────────────────────────────────────────────────


def test_write_observation_persists_and_roundtrips(db, tenant_with_agent):
    tenant, agent = tenant_with_agent
    decision_id = uuid.uuid4()
    o = _make_observation(tenant.id, agent.id, decision_id, reward=0.42)
    row_id = write_observation(db, observation=o)
    assert row_id is not None

    fetched = list_observations(db, tenant_id=tenant.id)
    assert len(fetched) == 1
    assert fetched[0].actual_reward == 0.42
    assert fetched[0].decision_id == str(decision_id)


def test_write_observation_rejects_tenant_boundary_violation(
    db, tenant_with_agent,
):
    tenant, agent = tenant_with_agent
    other_tenant_id = uuid.uuid4()
    foreign_obs = _make_observation(other_tenant_id, agent.id, uuid.uuid4())
    row_id = write_observation(
        db,
        observation=foreign_obs,
        current_tenant_id=tenant.id,
    )
    assert row_id is None


# ── list_predictions filtering ────────────────────────────────────────


def test_list_predictions_filters_by_agent(db, tenant_with_agent):
    tenant, agent_a = tenant_with_agent
    agent_b = Agent(tenant_id=tenant.id, name="Other Agent")
    db.add(agent_b)
    db.commit()

    write_prediction(db, prediction=_make_prediction(tenant.id, agent_a.id))
    write_prediction(db, prediction=_make_prediction(tenant.id, agent_b.id))

    a_only = list_predictions(db, tenant_id=tenant.id, agent_id=agent_a.id)
    assert len(a_only) == 1
    assert a_only[0].agent_id == str(agent_a.id)


def test_list_predictions_filters_by_decision_kind(db, tenant_with_agent):
    tenant, agent = tenant_with_agent
    write_prediction(
        db,
        prediction=_make_prediction(
            tenant.id, agent.id, kind="rl_route_chat_response",
        ),
    )
    write_prediction(
        db,
        prediction=_make_prediction(
            tenant.id, agent.id, kind="affect_appraise",
        ),
    )

    chat_only = list_predictions(
        db, tenant_id=tenant.id, decision_kind="rl_route_chat_response",
    )
    assert len(chat_only) == 1
    assert chat_only[0].decision_kind == "rl_route_chat_response"


def test_list_predictions_tenant_isolated(db, tenant_with_agent):
    tenant, agent = tenant_with_agent
    write_prediction(db, prediction=_make_prediction(tenant.id, agent.id))

    other_tenant = Tenant(name="Other")
    db.add(other_tenant)
    db.commit()
    # Other tenant has no agents → can't even write a prediction there
    assert list_predictions(db, tenant_id=other_tenant.id) == []


# ── list_traces (read-side join) ──────────────────────────────────────


def test_list_traces_pairs_prediction_with_observation(
    db, tenant_with_agent,
):
    tenant, agent = tenant_with_agent
    decision_id = uuid.uuid4()
    p = _make_prediction(tenant.id, agent.id, decision_id=decision_id)
    o = _make_observation(tenant.id, agent.id, decision_id, reward=0.4)

    write_prediction(db, prediction=p)
    write_observation(db, observation=o)

    traces = list_traces(db, tenant_id=tenant.id)
    assert len(traces) == 1
    assert traces[0].prediction.decision_id == str(decision_id)
    assert traces[0].observation.actual_reward == 0.4


def test_list_traces_drops_unpaired_predictions(db, tenant_with_agent):
    """A prediction without an observation = in-flight; not yet a
    trace. The read path silently drops it."""
    tenant, agent = tenant_with_agent
    write_prediction(db, prediction=_make_prediction(tenant.id, agent.id))
    # No observation written
    assert list_traces(db, tenant_id=tenant.id) == []


def test_list_traces_drops_unpaired_observations(db, tenant_with_agent):
    """An observation without a prediction = orphan; can't be
    calibrated. Dropped silently."""
    tenant, agent = tenant_with_agent
    write_observation(
        db,
        observation=_make_observation(tenant.id, agent.id, uuid.uuid4()),
    )
    assert list_traces(db, tenant_id=tenant.id) == []
