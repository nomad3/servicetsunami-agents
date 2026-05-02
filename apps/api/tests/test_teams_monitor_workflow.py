"""Tests for the Teams Monitor workflow + activity boundary.

The workflow itself is just a Temporal scheduling shell — it sleeps,
calls the activity, sleeps, continues as new. The interesting logic
lives in `teams_service.monitor_tick` which has its own test coverage
(test_teams_service_security.py).

These tests cover the activity wrapper's contract: it must NEVER raise
inside Temporal (would break continue_as_new), and it must surface a
structured dict so the workflow logger can report the tick result.
"""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.workflows.activities.teams_monitor import teams_monitor_tick


@pytest.mark.asyncio
async def test_teams_monitor_tick_returns_service_result():
    """Happy path — service result is returned verbatim."""
    fake_result = {"ok": True, "fetched": 3, "replied": 1, "blocked": 0}
    with patch(
        "app.services.teams_service.teams_service.monitor_tick",
        new=AsyncMock(return_value=fake_result),
    ):
        result = await teams_monitor_tick("tenant-1", "default")
    assert result == fake_result


@pytest.mark.asyncio
async def test_teams_monitor_tick_swallows_exceptions():
    """The activity must NOT raise — that would terminate the workflow's
    continue_as_new chain. A bad tick should return a structured error
    dict and let the workflow keep scheduling future ticks."""
    with patch(
        "app.services.teams_service.teams_service.monitor_tick",
        new=AsyncMock(side_effect=RuntimeError("graph blew up")),
    ):
        result = await teams_monitor_tick("tenant-1", "default")
    assert isinstance(result, dict)
    assert result.get("ok") is False
    assert "graph blew up" in result.get("reason", "")


@pytest.mark.asyncio
async def test_teams_monitor_tick_propagates_cancellation():
    """asyncio.CancelledError must propagate so Temporal can shut the
    worker down cleanly. Activity does NOT catch it — only general
    Exceptions."""
    with patch(
        "app.services.teams_service.teams_service.monitor_tick",
        new=AsyncMock(side_effect=asyncio.CancelledError()),
    ):
        raised = False
        try:
            await teams_monitor_tick("tenant-1", "default")
        except asyncio.CancelledError:
            raised = True
        assert raised, "CancelledError must propagate, not be swallowed"


@pytest.mark.asyncio
async def test_teams_monitor_tick_handles_none_return():
    """If the service returns None for any reason, the activity should
    surface a structured error dict rather than letting None propagate
    into the workflow logger (which would format weirdly)."""
    with patch(
        "app.services.teams_service.teams_service.monitor_tick",
        new=AsyncMock(return_value=None),
    ):
        result = await teams_monitor_tick("tenant-1", "default")
    assert isinstance(result, dict)
    assert result.get("ok") is False
