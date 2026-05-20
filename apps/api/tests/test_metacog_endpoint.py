"""Integration tests for GET /api/v1/metacog/calibration (M3 of #616).

Marked `integration` — runs against real Postgres (the api(integration,
postgres+pgvector) CI job) for the same reason test_metacog_io.py does:
the agent_memory substrate uses Postgres-native UUID/JSONB columns and
the SQLite shim fought the SQLAlchemy compile cache on PR #617.

Calls the endpoint function directly with a constructed User stand-in
rather than via TestClient — the JWT/middleware setup isn't wired in
this test suite (same pattern as test_team_endpoints_writes.py).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

# Postgres-only — same rationale as test_metacog_io.py docstring.
pytestmark = pytest.mark.integration

from sqlalchemy.orm import Session

from app.api.v1.metacog import get_metacog_calibration
from app.db.session import SessionLocal
from app.models.agent import Agent
from app.models.agent_memory import AgentMemory  # noqa: F401 — FK chain
from app.models.tenant import Tenant
from app.schemas.metacog import ConfidencePrediction, OutcomeObservation
from app.services.metacog_io import write_observation, write_prediction


@pytest.fixture(name="db")
def db_fixture():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(name="tenant_with_agent")
def tenant_with_agent_fixture(db: Session):
    """Throwaway Tenant + Agent — cascades on teardown per the FK chain
    in app/models/agent_memory.py (same as test_metacog_io.py)."""
    tenant = Tenant(name=f"metacog-endpoint-test-{uuid.uuid4()}")
    db.add(tenant)
    db.flush()
    agent = Agent(
        tenant_id=tenant.id,
        name=f"metacog-endpoint-test-agent-{uuid.uuid4()}",
    )
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


def _make_pair(tenant_id, agent_id, predicted=0.5, reward=0.0):
    decision_id = str(uuid.uuid4())
    p = ConfidencePrediction(
        tenant_id=str(tenant_id),
        agent_id=str(agent_id),
        decision_id=decision_id,
        decision_kind="rl_route_chat_response",
        predicted_confidence=predicted,
        context_hash="ctx",
        ts=_now(),
    )
    o = OutcomeObservation(
        tenant_id=str(tenant_id),
        agent_id=str(agent_id),
        decision_id=decision_id,
        actual_reward=reward,
        latency_ms=10,
        completed_at=_now(),
    )
    return p, o


def _fake_user(tenant_id):
    """Stand-in for app.models.user.User — the endpoint only reads
    .tenant_id off the current_user dep, so a SimpleNamespace suffices
    without dragging the real User row through fixtures."""
    return SimpleNamespace(tenant_id=tenant_id)


# ── Empty-tenant edge case ────────────────────────────────────────────


def test_calibration_empty_tenant_returns_zero(db, tenant_with_agent):
    tenant, _agent = tenant_with_agent
    result = get_metacog_calibration(
        agent_id=None,
        decision_kind=None,
        db=db,
        current_user=_fake_user(tenant.id),
    )
    assert result["n_traces"] == 0
    # ECE math is NaN-safe → 0.0 on empty input. Stable contract for
    # the dashboard's "no data yet" code path.
    assert result["ece"] == 0.0
    # Bin response is always 10 entries (stable axis for the frontend
    # even when every bucket is empty).
    assert len(result["by_bin"]) == 10


# ── Roundtrip: write + read calibration ───────────────────────────────


def test_calibration_includes_written_traces(db, tenant_with_agent):
    tenant, agent = tenant_with_agent
    # Write three perfectly-calibrated pairs: pred=0.5, reward=0.0
    # (normalized 0.5). ECE should be 0.0; counts should land in bin 5.
    for _ in range(3):
        p, o = _make_pair(tenant.id, agent.id, predicted=0.5, reward=0.0)
        write_prediction(db, prediction=p)
        write_observation(db, observation=o)

    result = get_metacog_calibration(
        agent_id=None,
        decision_kind=None,
        db=db,
        current_user=_fake_user(tenant.id),
    )
    assert result["n_traces"] == 3
    # Perfectly calibrated → ECE 0.0 (mean_pred 0.5, mean_actual 0.5).
    assert result["ece"] == pytest.approx(0.0, abs=1e-9)
    # Bin 5 covers [0.5, 0.6) — three predictions of 0.5 land there.
    bin5 = result["by_bin"][5]
    assert bin5["count"] == 3
    assert bin5["mean_pred"] == pytest.approx(0.5, abs=1e-9)


def test_calibration_detects_miscalibration(db, tenant_with_agent):
    tenant, agent = tenant_with_agent
    # Predict 0.9 but always fail (reward -1.0 → normalized 0.0).
    # mean_pred=0.9, mean_actual=0.0, |diff|=0.9, all weight in one bin,
    # so ECE ≈ 0.9.
    for _ in range(5):
        p, o = _make_pair(tenant.id, agent.id, predicted=0.9, reward=-1.0)
        write_prediction(db, prediction=p)
        write_observation(db, observation=o)

    result = get_metacog_calibration(
        agent_id=None, decision_kind=None,
        db=db, current_user=_fake_user(tenant.id),
    )
    assert result["n_traces"] == 5
    assert result["ece"] == pytest.approx(0.9, abs=1e-9)


# ── Tenant isolation ──────────────────────────────────────────────────


def test_calibration_is_tenant_scoped(db, tenant_with_agent):
    tenant_a, agent_a = tenant_with_agent
    # Write a trace under tenant A.
    p, o = _make_pair(tenant_a.id, agent_a.id, predicted=0.5, reward=0.0)
    write_prediction(db, prediction=p)
    write_observation(db, observation=o)

    # Create a second tenant — its calibration view must NOT see
    # tenant A's traces. This is the hard tenant boundary the
    # canonical design §3.3 makes load-bearing.
    other_tenant = Tenant(name=f"metacog-endpoint-other-{uuid.uuid4()}")
    db.add(other_tenant)
    db.commit()
    try:
        result = get_metacog_calibration(
            agent_id=None, decision_kind=None,
            db=db, current_user=_fake_user(other_tenant.id),
        )
        assert result["n_traces"] == 0
        assert result["ece"] == 0.0
    finally:
        db.execute(
            __import__("sqlalchemy").text(
                "DELETE FROM tenants WHERE id = :tid"
            ),
            {"tid": other_tenant.id},
        )
        db.commit()


# ── decision_kind filter ──────────────────────────────────────────────


def test_calibration_filters_by_decision_kind(db, tenant_with_agent):
    tenant, agent = tenant_with_agent
    # One pair under each of two decision_kinds.
    p1, o1 = _make_pair(tenant.id, agent.id, predicted=0.5, reward=0.0)
    write_prediction(db, prediction=p1)
    write_observation(db, observation=o1)

    p2 = ConfidencePrediction(
        tenant_id=str(tenant.id),
        agent_id=str(agent.id),
        decision_id=str(uuid.uuid4()),
        decision_kind="affect_appraise",
        predicted_confidence=0.5,
        context_hash="ctx",
        ts=_now(),
    )
    o2 = OutcomeObservation(
        tenant_id=str(tenant.id),
        agent_id=str(agent.id),
        decision_id=p2.decision_id,
        actual_reward=0.0,
        latency_ms=10,
        completed_at=_now(),
    )
    write_prediction(db, prediction=p2)
    write_observation(db, observation=o2)

    chat_only = get_metacog_calibration(
        agent_id=None, decision_kind="rl_route_chat_response",
        db=db, current_user=_fake_user(tenant.id),
    )
    appraise_only = get_metacog_calibration(
        agent_id=None, decision_kind="affect_appraise",
        db=db, current_user=_fake_user(tenant.id),
    )
    # Each kind contributes one trace; the filter prevents cross-talk
    # which would otherwise tank the dashboard's per-kind ECE numbers.
    assert chat_only["n_traces"] == 1
    assert appraise_only["n_traces"] == 1
    assert chat_only["decision_kind"] == "rl_route_chat_response"
    assert appraise_only["decision_kind"] == "affect_appraise"
