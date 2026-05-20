"""Tests for GET /api/v1/luna/impact — task #327.

Marked `integration` because the read paths touch JSONB columns
(conversation_episodes.affect_vector, agent_memory.content) and the
project's SQLite unit pass can't represent those. Runs in the
api(integration, postgres+pgvector) CI job, same lane as test_metacog_io
and test_team_engine_io.

Each test creates throwaway tenants/agents + the rows it needs, calls
the endpoint via TestClient with get_current_user overridden, and
deletes the tenant in teardown (FK cascades clean up the dependent
rows).
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

# JSONB / pgvector — Postgres only. Same gate as test_metacog_io.
pytestmark = pytest.mark.integration

os.environ.setdefault("TESTING", "True")

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api import deps
from app.api.v1 import luna_impact as luna_impact_router_module
from app.db.session import SessionLocal
from app.models.agent import Agent
from app.models.agent_memory import AgentMemory
from app.models.chat import ChatMessage, ChatSession
from app.models.conversation_episode import ConversationEpisode
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.metacog import ConfidencePrediction, OutcomeObservation
from app.services import luna_impact as luna_impact_service
from app.services.metacog_io import write_observation, write_prediction
from app.services.team_engine import (
    NORM_MEMORY_TYPE,
    ROLE_CONTRACT_MEMORY_TYPE,
)


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture(name="db")
def db_fixture():
    db = SessionLocal()
    # Ensure the api process-start clock is set so uptime-derived
    # metrics don't all show null (the test process imports this
    # module fresh; main.py's call to mark_process_start() doesn't
    # run when we don't import app.main).
    luna_impact_service.mark_process_start()
    try:
        yield db
    finally:
        db.close()


def _make_tenant(db: Session, label: str) -> Tenant:
    t = Tenant(name=f"luna-impact-{label}-{uuid.uuid4()}")
    db.add(t)
    db.flush()
    return t


def _make_agent(db: Session, tenant_id: uuid.UUID, name: str = "luna") -> Agent:
    a = Agent(tenant_id=tenant_id, name=f"{name}-{uuid.uuid4().hex[:6]}")
    db.add(a)
    db.flush()
    return a


def _delete_tenant(db: Session, tenant_id: uuid.UUID) -> None:
    try:
        db.execute(
            text("DELETE FROM tenants WHERE id = :tid"),
            {"tid": tenant_id},
        )
        db.commit()
    except Exception:
        db.rollback()


def _build_client(user: User, db: Session) -> TestClient:
    """Mount luna_impact's router on a throwaway FastAPI app with deps
    overridden. Keeps the test isolated from the full app's startup
    side-effects (skill sync, WhatsApp restore, etc.)."""
    app = FastAPI()
    app.dependency_overrides[deps.get_current_user] = lambda: user
    app.dependency_overrides[deps.get_db] = lambda: db
    app.include_router(
        luna_impact_router_module.router, prefix="/api/v1/luna",
    )
    return TestClient(app, raise_server_exceptions=False)


def _user_for(tenant_id: uuid.UUID) -> User:
    return User(
        id=uuid.uuid4(),
        email=f"u-{uuid.uuid4().hex[:6]}@test.com",
        tenant_id=tenant_id,
        is_active=True,
        is_superuser=False,
        hashed_password="x",
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Tests ─────────────────────────────────────────────────────────────


def test_empty_tenant_returns_zeros_no_crash(db: Session):
    """A brand-new tenant with no rows produces a complete payload of
    zeros (not nulls, not 500s). This is the canary test — every other
    test piles rows on top of this baseline."""
    tenant = _make_tenant(db, "empty")
    db.commit()
    try:
        client = _build_client(_user_for(tenant.id), db)
        resp = client.get("/api/v1/luna/impact?window_days=7")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["tenant_id"] == str(tenant.id)
        assert body["window_days"] == 7
        assert (
            body["stability"]["post_redeploy_grace_period_seconds"] == 180
        )
        assert body["routing"]["total_chat_turns"] == 0
        assert body["affect"]["sessions_with_affect_vector"] == 0
        assert body["affect"]["dominant_label"] is None
        assert body["coordination"]["active_role_contracts"] == 0
        assert body["metacog"]["predictions_persisted"] == 0
        assert body["metacog"]["observations_persisted"] == 0
        assert body["metacog"]["joined_traces_available_for_ece"] == 0
    finally:
        _delete_tenant(db, tenant.id)


def test_mixed_rows_counted_correctly(db: Session):
    """Populate one tenant with affect episodes, role contracts, and
    metacog predictions/observations. The endpoint should aggregate
    them faithfully."""
    tenant = _make_tenant(db, "mixed")
    agent = _make_agent(db, tenant.id)

    # ── Affect: two episodes with PAD vectors, one without ──────────
    db.add(
        ConversationEpisode(
            tenant_id=tenant.id,
            summary="happy turn",
            affect_vector={
                "pleasure": 0.6, "arousal": 0.2,
                "dominance": 0.4, "label": "content",
                "updated_at": _now_iso(),
            },
        )
    )
    db.add(
        ConversationEpisode(
            tenant_id=tenant.id,
            summary="another happy turn",
            affect_vector={
                "pleasure": 0.4, "arousal": 0.0,
                "dominance": 0.2, "label": "content",
                "updated_at": _now_iso(),
            },
        )
    )
    db.add(
        ConversationEpisode(
            tenant_id=tenant.id,
            summary="affect-less turn",
            affect_vector=None,
        )
    )

    # ── Coordination: two role-contract memories ────────────────────
    for slug in ("triage", "executor"):
        db.add(AgentMemory(
            tenant_id=tenant.id,
            agent_id=agent.id,
            memory_type=ROLE_CONTRACT_MEMORY_TYPE,
            content=f'{{"role": "{slug}"}}',
        ))
    # A norm memory — should NOT be counted as a role contract.
    db.add(AgentMemory(
        tenant_id=tenant.id,
        agent_id=agent.id,
        memory_type=NORM_MEMORY_TYPE,
        content='{"norm": "be polite"}',
    ))
    db.commit()

    # ── Metacog: one prediction + matching observation ──────────────
    decision_id = str(uuid.uuid4())
    write_prediction(
        db,
        prediction=ConfidencePrediction(
            tenant_id=str(tenant.id),
            agent_id=str(agent.id),
            decision_id=decision_id,
            decision_kind="rl_route_chat_response",
            predicted_confidence=0.7,
            context_hash="abc",
            ts=_now_iso(),
        ),
        current_tenant_id=tenant.id,
    )
    write_observation(
        db,
        observation=OutcomeObservation(
            tenant_id=str(tenant.id),
            agent_id=str(agent.id),
            decision_id=decision_id,
            actual_reward=0.5,
            latency_ms=120,
            completed_at=_now_iso(),
        ),
        current_tenant_id=tenant.id,
    )

    # ── Chat: one session with two messages ─────────────────────────
    session = ChatSession(tenant_id=tenant.id, agent_id=agent.id)
    db.add(session)
    db.flush()
    db.add(ChatMessage(session_id=session.id, role="user", content="hi"))
    db.add(ChatMessage(session_id=session.id, role="assistant", content="hi back"))
    db.commit()

    try:
        client = _build_client(_user_for(tenant.id), db)
        resp = client.get("/api/v1/luna/impact?window_days=7")
        assert resp.status_code == 200, resp.text
        body = resp.json()

        # Affect: 2 affect-bearing episodes, label dominated by "content"
        assert body["affect"]["sessions_with_affect_vector"] == 2
        assert body["affect"]["dominant_label"] == "content"
        assert body["affect"]["mean_pleasure"] == pytest.approx(0.5, abs=1e-3)

        # Coordination: only the two role-contract rows
        assert body["coordination"]["active_role_contracts"] == 2

        # Metacog: one prediction, one observation, one joined trace
        assert body["metacog"]["predictions_persisted"] == 1
        assert body["metacog"]["observations_persisted"] == 1
        assert body["metacog"]["joined_traces_available_for_ece"] == 1

        # Routing: chat turn aggregate
        assert body["routing"]["total_chat_turns"] == 2
    finally:
        _delete_tenant(db, tenant.id)


def test_tenant_isolation_b_data_not_visible_to_a(db: Session):
    """Tenant A and Tenant B both have data; the endpoint called as
    Tenant A must NOT include any of Tenant B's counts."""
    tenant_a = _make_tenant(db, "iso-a")
    tenant_b = _make_tenant(db, "iso-b")
    agent_b = _make_agent(db, tenant_b.id)

    # All rows below belong to tenant B.
    db.add(ConversationEpisode(
        tenant_id=tenant_b.id,
        summary="B's affect",
        affect_vector={
            "pleasure": 0.9, "arousal": 0.5, "dominance": 0.5,
            "label": "elated", "updated_at": _now_iso(),
        },
    ))
    db.add(AgentMemory(
        tenant_id=tenant_b.id,
        agent_id=agent_b.id,
        memory_type=ROLE_CONTRACT_MEMORY_TYPE,
        content='{"role": "b-only"}',
    ))
    session_b = ChatSession(tenant_id=tenant_b.id, agent_id=agent_b.id)
    db.add(session_b)
    db.flush()
    db.add(ChatMessage(session_id=session_b.id, role="user", content="hi b"))
    db.commit()

    try:
        # Tenant A's view — must see zeros for B's rows.
        client_a = _build_client(_user_for(tenant_a.id), db)
        resp_a = client_a.get("/api/v1/luna/impact")
        assert resp_a.status_code == 200, resp_a.text
        body_a = resp_a.json()
        assert body_a["tenant_id"] == str(tenant_a.id)
        assert body_a["affect"]["sessions_with_affect_vector"] == 0
        assert body_a["coordination"]["active_role_contracts"] == 0
        assert body_a["routing"]["total_chat_turns"] == 0

        # Tenant B's view — must see B's rows.
        client_b = _build_client(_user_for(tenant_b.id), db)
        resp_b = client_b.get("/api/v1/luna/impact")
        assert resp_b.status_code == 200, resp_b.text
        body_b = resp_b.json()
        assert body_b["affect"]["sessions_with_affect_vector"] == 1
        assert body_b["coordination"]["active_role_contracts"] == 1
        assert body_b["routing"]["total_chat_turns"] == 1
    finally:
        _delete_tenant(db, tenant_a.id)
        _delete_tenant(db, tenant_b.id)


def test_window_days_bounds_checking(db: Session):
    """window_days is clamped by FastAPI's Query(ge=1, le=90). Out-of-
    range requests get 422; valid endpoints respect the bound."""
    tenant = _make_tenant(db, "bounds")
    db.commit()
    try:
        client = _build_client(_user_for(tenant.id), db)

        # Below floor
        assert client.get("/api/v1/luna/impact?window_days=0").status_code == 422
        # Above ceiling
        assert client.get("/api/v1/luna/impact?window_days=91").status_code == 422
        # At boundaries — both accepted
        r_lo = client.get("/api/v1/luna/impact?window_days=1")
        r_hi = client.get("/api/v1/luna/impact?window_days=90")
        assert r_lo.status_code == 200
        assert r_hi.status_code == 200
        assert r_lo.json()["window_days"] == 1
        assert r_hi.json()["window_days"] == 90
    finally:
        _delete_tenant(db, tenant.id)


def test_unavailable_metrics_populated_when_log_read_fails(db: Session):
    """When the log reader raises, the endpoint reports null for the
    log-derived metrics AND lists them in `_unavailable_metrics` —
    no crash, no missing keys."""
    tenant = _make_tenant(db, "logfail")
    db.commit()
    try:
        client = _build_client(_user_for(tenant.id), db)
        with patch.object(
            luna_impact_service,
            "_read_log_tail",
            side_effect=OSError("simulated: log file unreadable"),
        ):
            resp = client.get("/api/v1/luna/impact")
        assert resp.status_code == 200, resp.text
        body = resp.json()

        # Log-derived metrics must be null
        assert body["routing"]["gemini_quota_failures"] is None
        assert body["routing"]["fallback_chain_invocations"] is None
        assert body["routing"]["mean_attempted_chain_length"] is None
        assert body["routing"]["preemptive_cooldown_skips"] is None
        assert body["coordination"]["coalition_dispatches_with_contract_routing"] is None

        # _unavailable_metrics surfaces them
        unavailable = body.get("_unavailable_metrics") or []
        assert "routing.gemini_quota_failures" in unavailable
        assert "routing.fallback_chain_invocations" in unavailable
        assert "routing.mean_attempted_chain_length" in unavailable
        assert "routing.preemptive_cooldown_skips" in unavailable
        assert (
            "coordination.coalition_dispatches_with_contract_routing"
            in unavailable
        )
    finally:
        _delete_tenant(db, tenant.id)
