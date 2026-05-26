"""Tests for the Luna Learn HTTP shim — typed-exception → status-code mapping.

Task 1.2a of the Luna Learn from Media plan
(docs/superpowers/plans/2026-05-25-luna-learn-from-media-plan.md).

The shim wraps the ``POST /agentprovision/v1/tools/{tool_name}`` dispatch
in ``src/server.py``. It intercepts the typed exceptions raised by
``src.mcp_tools.learning`` primitives and converts them into HTTP
responses with::

    status_code = _EXC_STATUS[type(exc)]   # e.g. 451 for MediaPrivate
    body        = {"error_type": "<ClassName>", "message": "<str(exc)>"}

The ``error_type`` body field is authoritative for branching in the
Temporal activities (T3.1); the status code is a fast-path hint.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.server import app
from src.mcp_tools import learning as L


_CASES = [
    ("MediaTooLong", 413),
    ("MediaPrivate", 451),
    ("MediaNotFound", 404),
    ("MediaGeoBlocked", 403),
    ("MediaAntiScrape", 429),
    ("DraftInvalid", 422),
    ("DraftForbiddenShellout", 424),
    ("ReviewerNotProvisioned", 503),
    ("ReviewTimeout", 504),
    ("SlugExhausted", 409),
]


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset_tools_registry():
    """Each test installs its own fake tool into ``L.TOOLS``; reset
    after to avoid leaking state across tests."""
    snapshot = dict(L.TOOLS)
    yield
    L.TOOLS.clear()
    L.TOOLS.update(snapshot)


@pytest.mark.parametrize("exc_name,status", _CASES)
def test_typed_exception_maps_to_status(client, exc_name, status):
    """Each typed exception class produces the documented HTTP status
    and an ``error_type`` body that matches the class name."""
    exc_cls = getattr(L, exc_name)

    async def _boom(**_kwargs):
        raise exc_cls("boom")

    L.TOOLS["extract_media"] = _boom

    r = client.post(
        "/agentprovision/v1/tools/extract_media",
        json={"url": "https://example.com/x"},
        headers={"X-Internal-Key": "test-mcp-key"},
    )

    assert r.status_code == status, (
        f"{exc_name} should map to {status}, got {r.status_code}: {r.text}"
    )
    body = r.json()
    assert body["error_type"] == exc_name
    assert "boom" in body["message"]


def test_unknown_exception_falls_through_to_500(client):
    """Any non-LearningToolError exception becomes
    ``500 + error_type='UnknownError'``."""
    async def _boom(**_kwargs):
        raise RuntimeError("kaboom")

    L.TOOLS["extract_media"] = _boom

    r = client.post(
        "/agentprovision/v1/tools/extract_media",
        json={"url": "x"},
        headers={"X-Internal-Key": "test-mcp-key"},
    )
    assert r.status_code == 500
    body = r.json()
    assert body["error_type"] == "UnknownError"
    assert "kaboom" in body["message"]


def test_unknown_tool_name_returns_404(client):
    """A POST to a tool name that's not in the registry returns 404
    with ``error_type='ToolNotFound'`` so the activity can distinguish
    a typo / version-skew from a real MediaNotFound."""
    r = client.post(
        "/agentprovision/v1/tools/no_such_tool",
        json={},
        headers={"X-Internal-Key": "test-mcp-key"},
    )
    assert r.status_code == 404
    body = r.json()
    assert body["error_type"] == "ToolNotFound"


def test_happy_path_returns_data_as_json(client):
    """When the tool returns a value, the shim passes it through as the
    JSON body with HTTP 200."""
    async def _ok(**kwargs):
        return {"audio_path": "/tmp/x", "echo": kwargs}

    L.TOOLS["extract_media"] = _ok

    r = client.post(
        "/agentprovision/v1/tools/extract_media",
        json={"url": "https://example.com/y", "max_duration_s": 900},
        headers={"X-Internal-Key": "test-mcp-key"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["audio_path"] == "/tmp/x"
    assert body["echo"]["url"] == "https://example.com/y"
    assert body["echo"]["max_duration_s"] == 900


def test_sync_tool_callable_is_supported(client):
    """The shim should also accept synchronous callables for cheap
    in-process tools (defensive: T1.2 primitives are all async, but the
    registry contract shouldn't lock that in)."""
    def _sync_ok(**kwargs):
        return {"hello": "world"}

    L.TOOLS["extract_media"] = _sync_ok

    r = client.post(
        "/agentprovision/v1/tools/extract_media",
        json={},
        headers={"X-Internal-Key": "test-mcp-key"},
    )
    assert r.status_code == 200
    assert r.json() == {"hello": "world"}
