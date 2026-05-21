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
    JWT tenant has no Luna persona."""
    user = _StubUser()
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None  # no agent

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
