"""Tests for /api/v1/luna/values (PR 2 of #647).

Per memory feedback_test_router_startup: load the router graph as
the integration check. Plus endpoint-shape unit tests with stubbed
auth + DB.
"""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest


def test_values_router_imports_clean():
    """Locked: importing the v1 routes graph MUST succeed. Catches
    the typo / unmapped-import class of failure that took 55 min to
    debug on 2026-05-19 (feedback_test_router_startup)."""
    from app.api.v1 import routes  # noqa: F401
    from app.api.v1 import values

    paths = {r.path for r in values.router.routes}
    assert "/luna/values" in paths
    assert "/luna/values/agents/{agent_id}" in paths
    assert "/internal/values/agents/{agent_id}" in paths


def _build_app_with_stubs(stub_user, stub_db, stub_agent=None):
    """Spin up a FastAPI app with the values router + stubbed
    dependencies. Returns (TestClient, capturing_dict)."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import deps as api_deps
    from app.api.v1.values import router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[api_deps.get_current_user] = lambda: stub_user
    app.dependency_overrides[api_deps.get_db] = lambda: stub_db
    return TestClient(app)


class _StubUser:
    def __init__(self):
        self.id = uuid.uuid4()
        self.tenant_id = uuid.uuid4()


class _StubAgent:
    def __init__(self, tenant_id):
        self.id = uuid.uuid4()
        self.tenant_id = tenant_id
        self.name = "Luna"


def test_put_rejects_blank_slug():
    """A 400 on blank slug stops the operator from accidentally
    writing a no-op value item that would never match anything."""
    user = _StubUser()
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = _StubAgent(
        user.tenant_id
    )

    client = _build_app_with_stubs(user, db)
    resp = client.put(
        "/luna/values",
        json={
            "protect": [{"slug": "  ", "description": ""}],
            "pursue": [],
            "avoid": [],
        },
    )
    assert resp.status_code == 422  # pydantic validation


def test_put_rejects_item_list_over_cap():
    """Operator hygiene: each list capped at 50 items. A
    misconfigured client pushing 1000 protect items must be
    rejected, not silently accepted (matcher is O(items × text))."""
    user = _StubUser()
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = _StubAgent(
        user.tenant_id
    )

    client = _build_app_with_stubs(user, db)
    big_protect = [
        {"slug": f"item-{i}", "description": "x"} for i in range(60)
    ]
    resp = client.put(
        "/luna/values",
        json={"protect": big_protect, "pursue": [], "avoid": []},
    )
    assert resp.status_code == 422


def test_get_returns_404_when_tenant_has_no_agents():
    """Operator with no agent set up yet sees 404 from the default
    GET — locked since the route resolves a default agent and the
    JWT tenant has no Luna persona.

    Mock chain matches _resolve_default_agent's
    .filter(...).order_by(...).first() shape after the IMPORTANT-2
    deterministic-ordering fix."""
    user = _StubUser()
    db = MagicMock()
    db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None

    client = _build_app_with_stubs(user, db)
    resp = client.get("/luna/values")
    assert resp.status_code == 404
    assert "no agents" in resp.text


def test_per_agent_get_404s_on_foreign_tenant_agent():
    """Cross-tenant read protection: agent_id from another tenant
    returns 404 regardless of whether it exists somewhere."""
    user = _StubUser()
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None

    client = _build_app_with_stubs(user, db)
    other_agent_id = uuid.uuid4()
    resp = client.get(f"/luna/values/agents/{other_agent_id}")
    assert resp.status_code == 404


def test_internal_endpoint_rejects_missing_internal_key():
    user = _StubUser()
    db = MagicMock()
    client = _build_app_with_stubs(user, db)
    agent_id = uuid.uuid4()
    resp = client.get(
        f"/internal/values/agents/{agent_id}",
        headers={"X-Tenant-Id": str(uuid.uuid4())},
    )
    assert resp.status_code == 401


def test_put_happy_path_invokes_write_with_server_forced_added_by(
    monkeypatch,
):
    """(Review IMPORTANT-1) The body MUST NOT be able to smuggle
    added_by — even if the operator sends one in the JSON, the
    server forces it to 'operator'. ValueItemIn's pydantic schema
    only declares slug/description/evidence_memory_ids; extra fields
    get dropped silently. This test sends a body with attacker-set
    added_by and asserts the persisted call still uses 'operator'."""
    from app.services import agent_value_set_io
    user = _StubUser()
    db = MagicMock()
    db.query.return_value.filter.return_value.order_by.return_value.first.return_value = _StubAgent(
        user.tenant_id
    )

    captured = {}

    def _fake_write(db, *, tenant_id, agent_id, protect, pursue, avoid):
        captured["protect"] = protect
        # Return a minimal AgentValueSet-shaped object for the response
        from app.services.agent_value_set import AgentValueSet, ValueItem
        return AgentValueSet(
            protect=[ValueItem.from_dict(p) for p in protect],
            pursue=[], avoid=[], version=1,
            updated_at="2026-05-21T00:00:00+00:00",
        )

    monkeypatch.setattr(agent_value_set_io, "write_value_set", _fake_write)

    client = _build_app_with_stubs(user, db)
    resp = client.put(
        "/luna/values",
        json={
            "protect": [
                {
                    "slug": "production-main",
                    "description": "the prod main branch",
                    # Attacker-controlled smuggle attempt:
                    "added_by": "attacker",
                    "added_at": "1970-01-01T00:00:00+00:00",
                },
            ],
            "pursue": [],
            "avoid": [],
        },
    )
    assert resp.status_code == 200, resp.text
    # Server-forced added_by reached the IO layer
    assert captured["protect"][0]["added_by"] == "operator", (
        f"server allowed body to smuggle added_by; got "
        f"{captured['protect'][0]['added_by']!r}"
    )
    # Smuggled added_at was overridden with server time (NOT 1970)
    assert not captured["protect"][0]["added_at"].startswith("1970"), (
        "server allowed body to smuggle added_at"
    )


def test_per_agent_put_404s_on_foreign_tenant_agent():
    """(Review IMPORTANT-1) Cross-tenant write protection. PUT to
    another tenant's agent_id must 404 the same way GET does —
    locks the symmetric protection."""
    user = _StubUser()
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None

    client = _build_app_with_stubs(user, db)
    other_agent_id = uuid.uuid4()
    resp = client.put(
        f"/luna/values/agents/{other_agent_id}",
        json={"protect": [], "pursue": [], "avoid": []},
    )
    assert resp.status_code == 404


def test_internal_endpoint_happy_path(monkeypatch):
    """(Review IMPORTANT-1) Valid X-Internal-Key + X-Tenant-Id +
    agent_id in the tenant returns a populated ValueSetOut. The
    two existing internal tests covered negative auth only."""
    from app.core.config import settings
    from app.services import agent_value_set_io
    from app.services.agent_value_set import AgentValueSet, ValueItem

    tenant_id = uuid.uuid4()
    agent = _StubAgent(tenant_id)

    user = _StubUser()  # unused — internal route doesn't read from user dep
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = agent

    fixed_value_set = AgentValueSet(
        protect=[ValueItem.from_dict({
            "slug": "production-main",
            "description": "prod main branch",
            "added_at": "2026-05-21T00:00:00+00:00",
            "added_by": "operator",
            "evidence_memory_ids": [],
        })],
        pursue=[], avoid=[],
        version=2,
        updated_at="2026-05-21T01:00:00+00:00",
    )
    monkeypatch.setattr(
        agent_value_set_io, "read_value_set",
        lambda *a, **kw: fixed_value_set,
    )

    client = _build_app_with_stubs(user, db)
    resp = client.get(
        f"/internal/values/agents/{agent.id}",
        headers={
            "X-Internal-Key": settings.API_INTERNAL_KEY,
            "X-Tenant-Id": str(tenant_id),
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["tenant_id"] == str(tenant_id)
    assert body["agent_id"] == str(agent.id)
    assert body["protect"][0]["slug"] == "production-main"
    assert body["version"] == 2


def test_internal_endpoint_rejects_missing_tenant_header():
    """X-Internal-Key is checked first; with a valid key but no
    X-Tenant-Id the endpoint MUST return 400 rather than infer
    tenant from anywhere else (no other path leak)."""
    from app.core.config import settings
    user = _StubUser()
    db = MagicMock()
    client = _build_app_with_stubs(user, db)
    agent_id = uuid.uuid4()
    resp = client.get(
        f"/internal/values/agents/{agent_id}",
        headers={"X-Internal-Key": settings.API_INTERNAL_KEY},
    )
    assert resp.status_code == 400
    assert "X-Tenant-Id" in resp.text
