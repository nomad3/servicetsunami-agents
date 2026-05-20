"""DB-touching tests for app.services.metacog_io (M1 of #616).

Marked `integration` and runs against the real Postgres exposed by
the api(integration, postgres+pgvector) CI job. The earlier SQLite
shim approach kept fighting SQLAlchemy's compiled-statement cache
(the column type was monkey-patched but the bind_processor had
already been baked in at first compile). Real Postgres handles
postgresql.UUID natively, so the type-decorator dance disappears.

Each test creates a throwaway Tenant + Agent in setup, exercises
metacog_io, and DELETE-cascades the tenant in teardown so nothing
leaks. Same pattern used by other integration tests in the suite.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest

# This file is Postgres-only (UUID/JSONB native types). Runs in the
# api(integration, postgres+pgvector) CI job, NOT the SQLite unit
# pass. The earlier shim-based approach fought SQLAlchemy's compile
# cache; real Postgres avoids the issue entirely.
pytestmark = pytest.mark.integration

from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.agent import Agent
from app.models.agent_memory import AgentMemory  # noqa: F401 — FK chain
from app.models.tenant import Tenant
from app.schemas.metacog import ConfidencePrediction, OutcomeObservation
from app.services.metacog_io import (
    list_observations,
    list_predictions,
    list_traces,
    write_observation,
    write_prediction,
)


@pytest.fixture(name="db")
def db_fixture():
    """A real Postgres session. Uses the production SessionLocal so
    we exercise the same engine + connection pool the api uses."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(name="tenant_with_agent")
def tenant_with_agent_fixture(db: Session):
    """Throwaway Tenant + Agent. The tenant cascades to the agent and
    to any agent_memory rows on delete (the FK is ON DELETE CASCADE
    per app/models/agent_memory.py)."""
    tenant = Tenant(name=f"metacog-test-{uuid.uuid4()}")
    db.add(tenant)
    db.flush()
    agent = Agent(tenant_id=tenant.id, name=f"metacog-test-agent-{uuid.uuid4()}")
    db.add(agent)
    db.commit()
    yield tenant, agent
    # Teardown — delete cascades to agent + agent_memories.
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

    fetched = list_predictions(db, tenant_id=tenant.id)
    assert len(fetched) == 1
    assert fetched[0].predicted_confidence == 0.77
    assert fetched[0].decision_id == p.decision_id


def test_write_prediction_rejects_tenant_boundary_violation(
    db, tenant_with_agent,
):
    tenant, agent = tenant_with_agent
    other_tenant_id = uuid.uuid4()
    foreign_pred = _make_prediction(other_tenant_id, agent.id)
    row_id = write_prediction(
        db,
        prediction=foreign_pred,
        current_tenant_id=tenant.id,
    )
    assert row_id is None
    assert list_predictions(db, tenant_id=tenant.id) == []
    assert list_predictions(db, tenant_id=other_tenant_id) == []


def test_write_prediction_rejects_malformed_uuids(db):
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
    agent_b = Agent(tenant_id=tenant.id, name=f"metacog-other-{uuid.uuid4()}")
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

    other_tenant = Tenant(name=f"metacog-other-{uuid.uuid4()}")
    db.add(other_tenant)
    db.commit()
    try:
        assert list_predictions(db, tenant_id=other_tenant.id) == []
    finally:
        db.execute(
            __import__("sqlalchemy").text(
                "DELETE FROM tenants WHERE id = :tid"
            ),
            {"tid": other_tenant.id},
        )
        db.commit()


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
    tenant, agent = tenant_with_agent
    write_prediction(db, prediction=_make_prediction(tenant.id, agent.id))
    assert list_traces(db, tenant_id=tenant.id) == []


def test_list_traces_drops_unpaired_observations(db, tenant_with_agent):
    tenant, agent = tenant_with_agent
    write_observation(
        db,
        observation=_make_observation(tenant.id, agent.id, uuid.uuid4()),
    )
    assert list_traces(db, tenant_id=tenant.id) == []
