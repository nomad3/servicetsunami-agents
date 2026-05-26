"""Tests for T4.1a — ``LearningService.dispatch``.

The dispatch helper is a thin wrapper around ``temporalio.client.Client``:
all we want to pin is that

  * a Temporal client is opened against the configured ``TEMPORAL_ADDRESS``,
  * ``start_workflow`` is invoked with ``"LearnFromMediaWorkflow"``, the
    intent's ``model_dump()`` payload, the orchestration task queue, and
    a tenant-prefixed workflow_id, and
  * the workflow_id is returned synchronously without awaiting the
    workflow result (fire-and-forget per spec §1.10).

The Temporal client is fully mocked so the tests don't require a live
Temporal server. We patch the class at the module-under-test path so the
``Client.connect`` call inside ``LearningService`` resolves to our mock.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.schemas.learning import LearningIntent
from app.services.learning_service import LearningService


# ── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture
def mock_temporal_client():
    """Yield (patched Client, patched client_instance, start_workflow mock)."""
    fake_client = AsyncMock()
    fake_client.start_workflow = AsyncMock(return_value=AsyncMock(id="ignored"))
    with patch(
        "app.services.learning_service.Client.connect",
        new=AsyncMock(return_value=fake_client),
    ) as connect_mock:
        yield connect_mock, fake_client, fake_client.start_workflow


# ── Dispatch shape ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dispatch_with_url_intent_returns_workflow_id(mock_temporal_client):
    _connect, _client, start_workflow = mock_temporal_client
    intent = LearningIntent(
        source_url="https://youtube.com/watch?v=abcdefghijk",
        tenant_id="tenant-aremko",
        actor_user_id="user-simon",
    )

    workflow_id = await LearningService.dispatch(intent)

    assert workflow_id.startswith("luna-learn-tenant-aremko-")
    # uuid4().hex[:12] suffix → total length is prefix + 12.
    assert len(workflow_id) == len("luna-learn-tenant-aremko-") + 12
    start_workflow.assert_awaited_once()


@pytest.mark.asyncio
async def test_dispatch_passes_workflow_name_and_task_queue(mock_temporal_client):
    _connect, _client, start_workflow = mock_temporal_client
    intent = LearningIntent(
        source_url="https://youtu.be/abcdefghijk",
        tenant_id="t1",
        actor_user_id="u1",
    )

    await LearningService.dispatch(intent)

    args, kwargs = start_workflow.call_args
    # first positional = workflow name; second positional = payload
    assert args[0] == "LearnFromMediaWorkflow"
    assert args[1]["source_url"] == "https://youtu.be/abcdefghijk"
    assert kwargs["task_queue"] == "agentprovision-orchestration"
    assert kwargs["id"] == _last_workflow_id_from_start(start_workflow)


@pytest.mark.asyncio
async def test_dispatch_with_attachment_intent(mock_temporal_client):
    _connect, _client, start_workflow = mock_temporal_client
    intent = LearningIntent(
        attachment_path="/tmp/cardio.mp3",
        tenant_id="tenant-brett",
        actor_user_id="user-brett",
    )

    workflow_id = await LearningService.dispatch(intent)

    assert workflow_id.startswith("luna-learn-tenant-brett-")
    payload = start_workflow.call_args.args[1]
    assert payload["attachment_path"] == "/tmp/cardio.mp3"
    assert payload["source_url"] is None


@pytest.mark.asyncio
async def test_dispatch_with_resume_job_id_passes_through(mock_temporal_client):
    _connect, _client, start_workflow = mock_temporal_client
    intent = LearningIntent(
        resume_job_id="job-123",
        tenant_id="t1",
        actor_user_id="u1",
    )

    await LearningService.dispatch(intent)

    payload = start_workflow.call_args.args[1]
    assert payload["resume_job_id"] == "job-123"
    assert payload["source_url"] is None
    assert payload["attachment_path"] is None


@pytest.mark.asyncio
async def test_dispatch_connects_to_configured_temporal_address(mock_temporal_client):
    connect_mock, _client, _start = mock_temporal_client
    intent = LearningIntent(
        source_url="https://youtu.be/abcdefghijk",
        tenant_id="t1",
        actor_user_id="u1",
    )

    from app.core.config import settings

    await LearningService.dispatch(intent)

    connect_mock.assert_awaited_once_with(settings.TEMPORAL_ADDRESS)


@pytest.mark.asyncio
async def test_dispatch_returns_unique_ids_on_repeated_calls(mock_temporal_client):
    """Two back-to-back dispatches for the same intent must not collide.

    A deterministic hash-based workflow_id would surface as
    WorkflowExecutionAlreadyStarted on legitimate retries; the uuid suffix
    keeps each invocation independent (spec §1.11 idempotency lives in
    cache state, not in workflow_id collisions).
    """
    intent = LearningIntent(
        source_url="https://youtu.be/abcdefghijk",
        tenant_id="t1",
        actor_user_id="u1",
    )

    id_a = await LearningService.dispatch(intent)
    id_b = await LearningService.dispatch(intent)

    assert id_a != id_b


# ── Helpers ────────────────────────────────────────────────────────


def _last_workflow_id_from_start(start_workflow_mock) -> str:
    return start_workflow_mock.call_args.kwargs["id"]
