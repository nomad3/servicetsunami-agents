"""Tests for /api/v1/luna/reflections — O4 read surface.

Per memory ``feedback_test_router_startup``: exercising the router
import is the load-bearing check (catches the typo I shipped in
2026-05-19's emotion.py crash-loop). These tests cover the endpoint
logic itself plus the import.
"""
from __future__ import annotations

import uuid


def test_reflections_router_imports_clean():
    """Locked: importing the v1 routes graph MUST succeed. If
    reflections.py introduces a broken import path or unmapped
    reference, this catches it before the api starts crash-looping
    (the failure mode that took 55 min to debug in 2026-05-19)."""
    from app.api.v1 import routes  # noqa: F401
    from app.api.v1 import reflections
    # Sanity — the router was constructed and exposes the two routes.
    paths = {r.path for r in reflections.router.routes}
    assert "/luna/reflections" in paths
    assert "/luna/reflections/count" in paths


def test_reflections_endpoint_rejects_unknown_kind():
    """Misspelled ?kind=foo MUST 400 — otherwise the DB filter
    silently drops every row and the operator gets an empty list
    that LOOKS like 'no reflections today'."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import deps as api_deps
    from app.api.v1.reflections import router

    class _StubUser:
        id = uuid.uuid4()
        tenant_id = uuid.uuid4()

    async def _stub_user():
        return _StubUser()

    def _stub_db():
        class _NoOpDb:
            def query(self, *a, **kw):
                class _Q:
                    def filter(self, *a, **kw):
                        return self

                    def order_by(self, *a, **kw):
                        return self

                    def all(self):
                        return []

                return _Q()
        yield _NoOpDb()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[api_deps.get_current_user] = _stub_user
    app.dependency_overrides[api_deps.get_db] = _stub_db

    client = TestClient(app)
    resp = client.get("/luna/reflections?kind=invented_kind")
    assert resp.status_code == 400
    assert "kind must be one of" in resp.text
