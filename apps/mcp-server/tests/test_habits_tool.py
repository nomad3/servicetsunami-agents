"""Tests for src.mcp_tools.habits (#297 platform-side cut)."""
from __future__ import annotations

import uuid

import pytest

from src.mcp_tools import habits as hb


@pytest.fixture
def patch_httpx(monkeypatch, make_client):
    def _install(side_effect=None, default_status=200, default_json=None):
        client = make_client(
            default_status=default_status,
            default_json=default_json,
            side_effect=side_effect,
        )
        monkeypatch.setattr(hb.httpx, "AsyncClient", lambda *a, **kw: client)
        return client
    return _install


@pytest.mark.asyncio
async def test_log_habit_observation_happy_path(patch_httpx, mock_ctx):
    patch_httpx(
        default_status=200,
        default_json={"memory_id": "mem-1"},
    )
    out = await hb.log_habit_observation(
        habit_name="posture",
        signal_kind="score",
        value=0.4,
        tenant_id=str(uuid.uuid4()),
        ctx=mock_ctx,
    )
    assert out["status"] == "success"
    assert out["memory_id"] == "mem-1"


@pytest.mark.asyncio
async def test_log_habit_observation_rejects_unknown_habit(mock_ctx):
    out = await hb.log_habit_observation(
        habit_name="dance_breaks",  # not in allow-list
        signal_kind="event",
        value=True,
        tenant_id=str(uuid.uuid4()),
        ctx=mock_ctx,
    )
    assert out["status"] == "error"
    assert "habit_name must be one of" in out["error"]


@pytest.mark.asyncio
async def test_log_habit_observation_rejects_unknown_signal_kind(mock_ctx):
    out = await hb.log_habit_observation(
        habit_name="posture",
        signal_kind="lots_of_text",  # not in allow-list
        value="0.4",
        tenant_id=str(uuid.uuid4()),
        ctx=mock_ctx,
    )
    assert out["status"] == "error"
    assert "signal_kind must be one of" in out["error"]


@pytest.mark.asyncio
async def test_log_habit_observation_rejects_out_of_range_confidence(mock_ctx):
    out = await hb.log_habit_observation(
        habit_name="hydration",
        signal_kind="event",
        value=True,
        confidence=1.5,
        tenant_id=str(uuid.uuid4()),
        ctx=mock_ctx,
    )
    assert out["status"] == "error"
    assert "confidence must be in" in out["error"]


@pytest.mark.asyncio
async def test_log_habit_observation_requires_tenant(mock_ctx):
    out = await hb.log_habit_observation(
        habit_name="focus",
        signal_kind="duration",
        value=1800,
        tenant_id="",
        ctx=None,
    )
    assert out["status"] == "error"
    assert "tenant_id required" in out["error"]


@pytest.mark.asyncio
async def test_log_habit_observation_propagates_upstream_4xx(patch_httpx, mock_ctx):
    patch_httpx(default_status=403, default_json={}, side_effect=None)
    from tests.conftest import _DummyResponse  # type: ignore

    def _side_effect(method, url, kwargs):
        return _DummyResponse(403, {}, text="cross-tenant write denied")

    patch_httpx(side_effect=_side_effect)
    out = await hb.log_habit_observation(
        habit_name="focus",
        signal_kind="score",
        value=0.7,
        tenant_id=str(uuid.uuid4()),
        ctx=mock_ctx,
    )
    assert out["status"] == "error"
    assert "403" in out["error"]
