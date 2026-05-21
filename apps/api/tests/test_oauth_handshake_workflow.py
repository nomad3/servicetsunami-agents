"""Tests for OAuthHandshakeWorkflow Phase D-1 scaffold (#295)."""
from __future__ import annotations

import uuid as _uuid
from unittest.mock import AsyncMock

import pytest
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from app.workflows.oauth_handshake_workflow import OAuthHandshakeWorkflow

pytestmark = pytest.mark.asyncio

TASK_QUEUE = "test-oauth-handshake"


async def _run(env: WorkflowEnvironment, *, mock_handshake, provider: str):
    from temporalio import activity

    @activity.defn(name="oauth.run_oauth_handshake")
    async def _handshake(p, t, c, v, r):
        return await mock_handshake(p, t, c, v, r)

    async with Worker(
        env.client,
        task_queue=TASK_QUEUE,
        workflows=[OAuthHandshakeWorkflow],
        activities=[_handshake],
    ):
        return await env.client.execute_workflow(
            OAuthHandshakeWorkflow.run,
            args=[provider, "tenant-1", "code-abc", "verifier-xyz", "https://x/cb"],
            id=f"test-oauth-{_uuid.uuid4().hex[:12]}",
            task_queue=TASK_QUEUE,
        )


async def test_workflow_returns_activity_result_unchanged():
    mock = AsyncMock(return_value={
        "success": True,
        "provider": "gemini_cli",
        "tenant_id": "tenant-1",
        "reason": "ok",
        "access_token_stored": True,
        "refresh_token_stored": True,
        "expires_in": 3600,
    })
    async with await WorkflowEnvironment.start_time_skipping() as env:
        result = await _run(env, mock_handshake=mock, provider="gemini_cli")
    assert result["success"] is True
    assert result["access_token_stored"] is True
    mock.assert_awaited_once_with(
        "gemini_cli", "tenant-1", "code-abc", "verifier-xyz", "https://x/cb",
    )


async def test_workflow_propagates_handshake_failure():
    """Phase D-1 stub returns success=False. The workflow MUST surface
    that verbatim so api-side callers see a clean signal to keep
    using the legacy subprocess.run path."""
    mock = AsyncMock(return_value={
        "success": False,
        "provider": "claude",
        "tenant_id": "tenant-1",
        "reason": "phase_d1_stub",
        "access_token_stored": False,
        "refresh_token_stored": False,
        "expires_in": None,
    })
    async with await WorkflowEnvironment.start_time_skipping() as env:
        result = await _run(env, mock_handshake=mock, provider="claude")
    assert result["success"] is False
    assert result["reason"] == "phase_d1_stub"


async def test_workflow_passes_all_oauth_args_to_activity():
    """Locks the activity argument shape — Phase D-2 implementation
    can rely on this 5-arg signature."""
    mock = AsyncMock(return_value={"success": False, "reason": "stub"})
    async with await WorkflowEnvironment.start_time_skipping() as env:
        await _run(env, mock_handshake=mock, provider="higgsfield")
    args = mock.await_args.args
    assert args == ("higgsfield", "tenant-1", "code-abc", "verifier-xyz", "https://x/cb")
