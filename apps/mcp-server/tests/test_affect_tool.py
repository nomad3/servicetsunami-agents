"""Tests for src.mcp_tools.affect.

The MCP tool wraps the internal endpoint
``/api/v1/internal/affect/agents/{agent_id}``. We stub httpx so the
test stays isolated from the api container and exercises the
status-code → dict-shape mapping.
"""
from __future__ import annotations

import pytest

from src.mcp_tools import affect as af


@pytest.fixture
def patch_httpx(monkeypatch, make_client):
    def _install(side_effect=None, default_status=200, default_json=None):
        client = make_client(
            default_status=default_status,
            default_json=default_json,
            side_effect=side_effect,
        )
        monkeypatch.setattr(af.httpx, "AsyncClient", lambda *a, **kw: client)
        return client
    return _install


@pytest.mark.asyncio
async def test_get_agent_affect_returns_baseline_on_success(
    patch_httpx, mock_ctx,
):
    patch_httpx(
        default_status=200,
        default_json={
            "agent_id": "a-1",
            "agent_name": "Luna",
            "baseline": {
                "pleasure": 0.112, "arousal": 0.170,
                "dominance": 0.425, "label": "calm",
            },
            "current": None,
            "has_live_state": False,
        },
    )
    out = await af.get_agent_affect(
        agent_id="a-1", tenant_id="t-1", ctx=mock_ctx,
    )
    assert out["status"] == "success"
    assert out["agent_id"] == "a-1"
    assert out["baseline"]["pleasure"] == pytest.approx(0.112)
    assert out["has_live_state"] is False


@pytest.mark.asyncio
async def test_get_agent_affect_no_tenant_returns_error(mock_ctx):
    """Without a tenant from context or explicit kwarg, we refuse the
    call rather than emit the request (no fallback to a 'default' tenant
    — that would be a cross-tenant leak)."""
    out = await af.get_agent_affect(
        agent_id="a-1", tenant_id="", ctx=None,
    )
    assert out["status"] == "error"
    assert "tenant_id" in out["error"]


@pytest.mark.asyncio
async def test_get_agent_affect_404_propagates_not_found(
    patch_httpx, mock_ctx,
):
    """Foreign-tenant agent or non-existent UUID → 404 from upstream,
    surfaced as a clean error to the MCP client (no leakage that the
    agent might exist under a different tenant)."""
    patch_httpx(default_status=404, default_json={})
    out = await af.get_agent_affect(
        agent_id="missing", tenant_id="t-1", ctx=mock_ctx,
    )
    assert out["status"] == "error"
    assert "not found" in out["error"]


@pytest.mark.asyncio
async def test_get_agent_affect_401_propagates_invalid_key(
    patch_httpx, mock_ctx,
):
    """Misconfigured MCP_API_KEY → upstream 401. Caller sees the
    config error rather than a generic upstream failure."""
    patch_httpx(default_status=401, default_json={})
    out = await af.get_agent_affect(
        agent_id="a-1", tenant_id="t-1", ctx=mock_ctx,
    )
    assert out["status"] == "error"
    assert "invalid internal key" in out["error"]


@pytest.mark.asyncio
async def test_get_agent_affect_400_surfaces_missing_tenant_header(
    patch_httpx, mock_ctx,
):
    """If the api ever rejects the request (e.g. missing
    X-Tenant-Id), the 400 body propagates to the operator log."""
    patch_httpx(default_status=400, default_json={}, side_effect=None)
    # Override the default_json text by giving a side effect that
    # returns a 400 with a descriptive body.
    from tests.conftest import _DummyResponse  # type: ignore

    def _side_effect(method, url, kwargs):
        return _DummyResponse(400, {}, text="X-Tenant-Id required")

    patch_httpx(side_effect=_side_effect)
    out = await af.get_agent_affect(
        agent_id="a-1", tenant_id="t-1", ctx=mock_ctx,
    )
    assert out["status"] == "error"
    assert "bad request" in out["error"]


@pytest.mark.asyncio
async def test_get_agent_affect_upstream_5xx_surfaces_status(
    patch_httpx, mock_ctx,
):
    """A 503 from the api becomes a clean error message — caller can
    distinguish "agent unavailable" from "agent doesn't exist"."""
    patch_httpx(default_status=503, default_json={}, side_effect=None)
    from tests.conftest import _DummyResponse  # type: ignore

    def _side_effect(method, url, kwargs):
        return _DummyResponse(503, {}, text="db connection failed")

    patch_httpx(side_effect=_side_effect)
    out = await af.get_agent_affect(
        agent_id="a-1", tenant_id="t-1", ctx=mock_ctx,
    )
    assert out["status"] == "error"
    assert "503" in out["error"]


@pytest.mark.asyncio
async def test_get_agent_affect_transport_error_returns_error_dict(
    monkeypatch, mock_ctx,
):
    """httpx.ConnectError (api unreachable) is the most common
    deployment-window failure mode. We must NOT raise — return a
    structured error so the MCP client treats it like any other
    upstream miss."""
    import httpx

    class _RaisingClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, *args, **kwargs):
            raise httpx.ConnectError("api unreachable")

    monkeypatch.setattr(af.httpx, "AsyncClient", lambda *a, **kw: _RaisingClient())
    out = await af.get_agent_affect(
        agent_id="a-1", tenant_id="t-1", ctx=mock_ctx,
    )
    assert out["status"] == "error"
    assert "transport" in out["error"]
