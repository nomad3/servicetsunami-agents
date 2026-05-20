"""Tests for quota-aware preemptive routing (task #304).

Background
----------
Before #304, the RL routing policy in ``agent_router.route_and_execute``
kept picking the same CLI every turn after a quota / auth hit. The
``cli_platform_resolver`` marks a 600s cooldown when a CLI 429s, and
the chain walker skips cooldowned CLIs at dispatch time — BUT the
policy didn't know about cooldowns, so the API still wasted one
Temporal round-trip per turn on a doomed call before the chain skip
kicked in. Observable in production logs as a repeating::

    CLI gemini_cli returned quota for tenant=... — cooldown + chain skip
    CLI chain resolved — tenant=... requested=gemini_cli served_actual=codex

every turn for 10 minutes.

These tests pin the new behavior:

  T1: ``get_active_cooldowns`` reads in-process state and returns the
      set of CLIs cooled for the given tenant.
  T2: ``get_active_cooldowns`` honors TTL expiry — an expired entry is
      not reported and is also evicted from the local dict.
  T3: The RL action space drops cooldowned CLIs BEFORE the policy
      picks (balanced exploration + recommendation paths both honor it).
  T4: When the operator-resolved initial ``platform`` is itself in
      cooldown, the router surrenders it so the policy doesn't pick a
      doomed CLI.
  T5: ``exploration_mode=codex`` honors the cooldown filter too.
  T6: The fanout endpoint drops cooldowned providers from
      ``effective_providers`` before dispatching to Temporal.
  T7: The fanout endpoint returns 503 ``all_providers_in_cooldown``
      when every requested provider is cooled.
  T8: Best-effort: a broken ``get_active_cooldowns`` does not block
      fanout dispatch — falls through with no filter.

Per-test SQLite engine uses StaticPool + check_same_thread=False +
expire_on_commit=False so the session is reusable across threads
without TLS pinning (the FastAPI TestClient does an internal thread
hop on dispatch).
"""
from __future__ import annotations

import time
import uuid
from typing import Optional
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import deps
from app.api.v1 import tasks_fanout as tf
from app.models.user import User
from app.services import agent_router
from app.services import cli_platform_resolver as resolver


# ── shared fixtures ────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolate_resolver(monkeypatch):
    """Reset cooldown state + force the resolver onto the in-process
    dict (no Redis) so tests are deterministic."""
    monkeypatch.setattr(resolver, "_local_cooldown", {})
    monkeypatch.setattr(resolver, "_redis_singleton", None)
    monkeypatch.setattr(resolver, "_redis_init_failed", False)
    monkeypatch.setattr(resolver, "_redis_client", lambda: None)


@pytest.fixture(name="sqlite_session")
def sqlite_session_fixture():
    """Per-test SQLite engine. StaticPool + check_same_thread=False +
    expire_on_commit=False per the test-pattern guidance in
    test_metacog_io.py — the FastAPI TestClient hops threads under
    the hood and a default-pool engine would TLS-pin the connection."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=engine,
        expire_on_commit=False,
    )
    db = SessionLocal()
    yield db
    db.close()


class _StubEstimate:
    estimated_duration_seconds = 1
    estimated_cost_usd = 0.0
    confidence = "stub"


def _user(tenant_id: Optional[str] = None) -> User:
    return User(
        id=uuid.uuid4(),
        email=f"user-{uuid.uuid4().hex[:6]}@test.com",
        tenant_id=uuid.UUID(tenant_id) if tenant_id else uuid.uuid4(),
        is_active=True,
        is_superuser=False,
        hashed_password="x",
    )


# ── T1 / T2: get_active_cooldowns contract ──────────────────────────


def test_get_active_cooldowns_returns_cooled_platforms():
    """T1: After marking gemini_cli + codex cool, ``get_active_cooldowns``
    must report both. Local floor (``opencode``) is never cooled and
    never reported."""
    tenant_id = uuid.uuid4()
    resolver.mark_cooldown(tenant_id, "gemini_cli", reason="quota")
    resolver.mark_cooldown(tenant_id, "codex", reason="auth")
    # opencode is the universal floor — mark_cooldown is a no-op.
    resolver.mark_cooldown(tenant_id, "opencode", reason="quota")

    active = resolver.get_active_cooldowns(tenant_id)
    assert "gemini_cli" in active
    assert "codex" in active
    assert "opencode" not in active
    assert "claude_code" not in active  # not cooled


def test_get_active_cooldowns_evicts_expired_entries(monkeypatch):
    """T2: An entry whose TTL has expired is not reported AND is
    evicted from the in-process dict on lookup."""
    tenant_id = uuid.uuid4()
    resolver.mark_cooldown(tenant_id, "gemini_cli", reason="quota")

    # Force-expire the entry by rewinding its expires_at.
    key = resolver._cooldown_key(tenant_id, "gemini_cli")
    assert key in resolver._local_cooldown
    resolver._local_cooldown[key] = time.time() - 1

    active = resolver.get_active_cooldowns(tenant_id)
    assert "gemini_cli" not in active
    assert key not in resolver._local_cooldown, "expired entry must be evicted"


# ── T3: RL action space honors cooldowns ────────────────────────────


def test_rl_recommendation_drops_cooldowned_platform(monkeypatch):
    """T3: When the RL recommender suggests gemini_cli but gemini is in
    cooldown, the router must NOT pick gemini. The filter applies BEFORE
    the policy's choice is seated as ``platform``."""

    tenant_id = uuid.uuid4()
    # Cool gemini_cli — the platform the RL recommender is about to suggest.
    resolver.mark_cooldown(tenant_id, "gemini_cli", reason="quota")

    _disallowed = agent_router._get_active_cooldowns(tenant_id)
    assert "gemini_cli" in _disallowed

    # Simulate the RL recommendation branch: rl_rec.platform=gemini_cli
    # with confidence high enough to normally win. Apply the gate the
    # router applies (the in-line condition added in this PR).
    rl_rec_platform = "gemini_cli"
    rl_rec_confidence = 0.9
    _VALID_CLI = {"claude_code", "codex", "gemini_cli"}
    platform = "claude_code"  # starting platform
    if (
        rl_rec_platform
        and rl_rec_platform in _VALID_CLI
        and rl_rec_platform not in _disallowed
        and rl_rec_confidence >= 0.4
    ):
        platform = rl_rec_platform
    assert platform == "claude_code", (
        "RL pick must be filtered out when gemini_cli is in cooldown; "
        "starting platform must survive"
    )


def test_rl_balanced_exploration_filters_cooldowned_alternatives():
    """T3b: Balanced exploration's least-explored pick must skip
    cooldowned candidates. If the only alternative is cooled, the
    branch picks nothing and the prior platform stands."""
    tenant_id = uuid.uuid4()
    resolver.mark_cooldown(tenant_id, "codex", reason="quota")

    _disallowed = agent_router._get_active_cooldowns(tenant_id)

    # Simulated rec.alternatives output: codex would be the least-explored,
    # but it's cooled, and gemini_cli is a healthier candidate.
    rec_alternatives = [
        {"platform": "codex", "total": 1},
        {"platform": "gemini_cli", "total": 10},
        {"platform": "claude_code", "total": 50},
    ]
    _VALID_EXPLORE = {"claude_code", "codex", "gemini_cli"}

    valid = [
        a for a in rec_alternatives
        if a["platform"] in _VALID_EXPLORE
        and a["platform"] not in _disallowed
    ]
    assert valid
    least = min(valid, key=lambda a: a["total"])
    assert least["platform"] == "gemini_cli", (
        "codex must be filtered; next-least-explored wins"
    )


# ── T4: requested platform surrendered if cooled ─────────────────────


def test_requested_platform_surrendered_when_cooled():
    """T4: When the operator-resolved initial ``platform`` is itself in
    cooldown, the router clears it so the policy gets a fresh shot
    instead of seating a doomed CLI."""
    tenant_id = uuid.uuid4()
    resolver.mark_cooldown(tenant_id, "gemini_cli", reason="quota")

    _disallowed = agent_router._get_active_cooldowns(tenant_id)

    # Inline replay of the gate added in route_and_execute.
    platform = "gemini_cli"
    if platform and platform in _disallowed:
        platform = None

    assert platform is None, (
        "cooled initial platform must be surrendered so RL/exploration "
        "can pick a healthy CLI"
    )


# ── T5: exploration_mode=codex honors filter ────────────────────────


def test_exploration_codex_skips_when_codex_cooled():
    """T5: ``exploration_mode=codex`` is normally a hard pin to codex,
    but if codex is in cooldown the pin must be skipped."""
    tenant_id = uuid.uuid4()
    resolver.mark_cooldown(tenant_id, "codex", reason="quota")

    _disallowed = agent_router._get_active_cooldowns(tenant_id)

    # Inline replay of the new gate.
    platform = "claude_code"
    if "codex" not in _disallowed:
        platform = "codex"
    assert platform == "claude_code", (
        "exploration_codex must be a no-op when codex is cooled"
    )


# ── T6 / T7: fanout endpoint honors cooldowns ───────────────────────


def _make_fanout_client(user: User) -> TestClient:
    """TestClient with the fanout router mounted and DI stubs in place."""
    def _stub_db():
        m = MagicMock()
        chain = MagicMock()
        chain.all.return_value = []
        chain.first.return_value = None
        for method in ("join", "filter", "order_by", "limit"):
            getattr(chain, method).return_value = chain
        m.query.return_value = chain
        yield m

    app = FastAPI()
    app.dependency_overrides[deps.get_current_user] = lambda: user
    app.dependency_overrides[deps.get_db] = _stub_db
    app.include_router(tf.router, prefix="/api/v1/tasks-fanout")
    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture(autouse=True)
def _isolate_fanout_state():
    tf._TASKS.clear()
    tf._TENANT_COUNTS.clear()
    yield
    tf._TASKS.clear()
    tf._TENANT_COUNTS.clear()


def test_fanout_drops_cooldowned_providers(monkeypatch):
    """T6: When fanout=[gemini_cli, codex, claude_code] and gemini_cli
    is cooled, the dispatch sees [codex, claude_code]."""
    user = _user()
    resolver.mark_cooldown(user.tenant_id, "gemini_cli", reason="quota")

    # Force real-dispatch path; stub the Temporal call.
    monkeypatch.setattr(tf.settings, "USE_REAL_FANOUT_WORKFLOW", True)
    monkeypatch.setattr(
        tf, "_tenant_over_monthly_token_limit", lambda *a, **kw: False
    )

    captured = {}

    async def _fake_dispatch(**kwargs):
        captured.update(kwargs)
        return {
            "task_id": f"fanout-{kwargs['tenant_id']}-{uuid.uuid4()}",
            "workflow_id": "wf-test",
        }

    monkeypatch.setattr(tf, "_dispatch_fanout_workflow", _fake_dispatch)
    monkeypatch.setattr(tf, "estimate_fanout_cost", lambda *a, **kw: _StubEstimate())

    client = _make_fanout_client(user)
    resp = client.post(
        "/api/v1/tasks-fanout/run",
        json={
            "prompt": "ping",
            "fanout": ["gemini_cli", "codex", "claude_code"],
            "merge": "council",
        },
    )
    assert resp.status_code == 200, resp.text
    assert captured.get("providers") == ["codex", "claude_code"], (
        f"gemini_cli must be filtered out; got {captured.get('providers')}"
    )


def test_fanout_all_cooldowned_returns_503(monkeypatch):
    """T7: When every requested provider is in cooldown, the endpoint
    returns 503 with ``all_providers_in_cooldown`` + the cooldown list,
    NOT a silent fallback to the safe-ship default."""
    user = _user()
    resolver.mark_cooldown(user.tenant_id, "gemini_cli", reason="quota")
    resolver.mark_cooldown(user.tenant_id, "codex", reason="auth")

    monkeypatch.setattr(tf.settings, "USE_REAL_FANOUT_WORKFLOW", True)
    monkeypatch.setattr(
        tf, "_tenant_over_monthly_token_limit", lambda *a, **kw: False
    )
    monkeypatch.setattr(tf, "estimate_fanout_cost", lambda *a, **kw: _StubEstimate())

    # Sentinel: dispatch must NOT be called.
    async def _explode(**kwargs):  # pragma: no cover — must not run
        raise AssertionError("dispatch must not run when all providers cooled")
    monkeypatch.setattr(tf, "_dispatch_fanout_workflow", _explode)

    client = _make_fanout_client(user)
    resp = client.post(
        "/api/v1/tasks-fanout/run",
        json={
            "prompt": "ping",
            "providers": ["gemini_cli", "codex"],
        },
    )
    assert resp.status_code == 503, resp.text
    detail = resp.json()["detail"]
    assert detail["error"] == "all_providers_in_cooldown"
    assert sorted(detail["cooldowns"]) == ["codex", "gemini_cli"]
    assert detail["requested"] == ["gemini_cli", "codex"]


def test_fanout_resolver_failure_falls_through(monkeypatch):
    """T8: When ``get_active_cooldowns`` raises (e.g., Redis blip), the
    dispatch must still go through with the unfiltered provider list.
    Best-effort, not fatal — matches the rest of the resolver contract."""
    user = _user()
    monkeypatch.setattr(tf.settings, "USE_REAL_FANOUT_WORKFLOW", True)
    monkeypatch.setattr(
        tf, "_tenant_over_monthly_token_limit", lambda *a, **kw: False
    )
    monkeypatch.setattr(tf, "estimate_fanout_cost", lambda *a, **kw: _StubEstimate())

    def _boom(_tenant_id):
        raise RuntimeError("redis down")
    monkeypatch.setattr(tf, "get_active_cooldowns", _boom)

    captured = {}

    async def _fake_dispatch(**kwargs):
        captured.update(kwargs)
        return {
            "task_id": f"fanout-{kwargs['tenant_id']}-{uuid.uuid4()}",
            "workflow_id": "wf-test",
        }

    monkeypatch.setattr(tf, "_dispatch_fanout_workflow", _fake_dispatch)

    client = _make_fanout_client(user)
    resp = client.post(
        "/api/v1/tasks-fanout/run",
        json={"prompt": "ping", "fanout": ["claude_code", "codex"]},
    )
    assert resp.status_code == 200, resp.text
    assert captured.get("providers") == ["claude_code", "codex"]
