"""Tests for src.mcp_tools.values (PR 2 of #647)."""
from __future__ import annotations

import uuid

import pytest

from src.mcp_tools import values as vt


@pytest.fixture
def patch_httpx(monkeypatch, make_client):
    def _install(side_effect=None, default_status=200, default_json=None):
        client = make_client(
            default_status=default_status,
            default_json=default_json,
            side_effect=side_effect,
        )
        monkeypatch.setattr(vt.httpx, "AsyncClient", lambda *a, **kw: client)
        return client
    return _install


@pytest.mark.asyncio
async def test_get_value_set_happy_path(patch_httpx, mock_ctx):
    patch_httpx(
        default_status=200,
        default_json={
            "tenant_id": "t-1",
            "agent_id": "a-1",
            "protect": [
                {"slug": "production-main", "description": "prod",
                 "added_at": "x", "added_by": "operator",
                 "evidence_memory_ids": []},
            ],
            "pursue": [],
            "avoid": [],
            "version": 1,
            "updated_at": "2026-05-21T00:00:00Z",
        },
    )
    out = await vt.get_agent_value_set(
        agent_id="a-1", tenant_id=str(uuid.uuid4()), ctx=mock_ctx,
    )
    assert out["status"] == "success"
    assert out["protect"][0]["slug"] == "production-main"
    assert out["version"] == 1


@pytest.mark.asyncio
async def test_get_value_set_requires_tenant(mock_ctx):
    out = await vt.get_agent_value_set(
        agent_id="a-1", tenant_id="", ctx=None,
    )
    assert out["status"] == "error"
    assert "tenant_id required" in out["error"]


@pytest.mark.asyncio
async def test_get_value_set_404_propagates(patch_httpx, mock_ctx):
    patch_httpx(default_status=404, default_json={})
    out = await vt.get_agent_value_set(
        agent_id="missing", tenant_id=str(uuid.uuid4()), ctx=mock_ctx,
    )
    assert out["status"] == "error"
    assert "not found" in out["error"]


@pytest.mark.asyncio
async def test_get_value_set_401_propagates(patch_httpx, mock_ctx):
    patch_httpx(default_status=401, default_json={})
    out = await vt.get_agent_value_set(
        agent_id="a-1", tenant_id=str(uuid.uuid4()), ctx=mock_ctx,
    )
    assert out["status"] == "error"
    assert "invalid internal key" in out["error"]


@pytest.mark.asyncio
async def test_get_value_set_transport_error_returns_error_dict(
    monkeypatch, mock_ctx,
):
    """httpx.ConnectError must NOT raise — return structured error."""
    import httpx

    class _RaisingClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, *args, **kwargs):
            raise httpx.ConnectError("api unreachable")

    monkeypatch.setattr(
        vt.httpx, "AsyncClient", lambda *a, **kw: _RaisingClient(),
    )
    out = await vt.get_agent_value_set(
        agent_id="a-1", tenant_id=str(uuid.uuid4()), ctx=mock_ctx,
    )
    assert out["status"] == "error"
    assert "transport" in out["error"]
