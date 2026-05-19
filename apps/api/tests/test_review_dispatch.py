"""Tests for ``app.services.review_dispatch.dispatch_review_workflow``.

These pin the post-fix behavior of PR #574:

  * ``dispatch_review_workflow`` is an async coroutine that awaits
    ``Client.connect`` + ``Client.start_workflow`` directly. We patch
    both and assert the workflow is started with the expected args.
  * The endpoint ``POST /api/v1/reviews/start`` (and ``/reply``)
    awaits the dispatch — a Temporal outage is swallowed by the
    endpoint (review row still usable via /record), but
    ``start_workflow`` MUST be called when Temporal is reachable.

The OLD code path used a daemon thread + ``asyncio.run`` from the
sync request handler. Under gunicorn workers the thread sometimes
died before ``start_workflow`` was reached — the row existed but no
Temporal workflow did. The old test (if any) didn't exercise the
real path because it patched the public ``dispatch_review_workflow``
symbol with a no-op. The new tests patch ``Client.connect`` instead
so the real ``await client.start_workflow(...)`` is exercised end to
end.
"""
from __future__ import annotations

import os
os.environ["TESTING"] = "True"

import asyncio
import uuid
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ──────────────────────────────────────────────────────────────────────────
# Direct unit test — patch temporalio.client.Client and assert call args
# ──────────────────────────────────────────────────────────────────────────


def _make_fake_client(recorded: List[Dict[str, Any]]) -> AsyncMock:
    """Return an AsyncMock that records ``start_workflow`` calls."""
    fake = AsyncMock()

    async def _record_start_workflow(*args, **kwargs):
        recorded.append({"args": args, "kwargs": kwargs})
        return MagicMock()  # WorkflowHandle stand-in

    fake.start_workflow = _record_start_workflow
    return fake


def test_dispatch_review_workflow_awaits_start_workflow():
    """``dispatch_review_workflow`` MUST call ``client.start_workflow``
    with ``ReviewWorkflow`` and the correct task queue.
    """
    from app.services import review_dispatch

    tenant_id = uuid.uuid4()
    review_id = uuid.uuid4()
    recorded: List[Dict[str, Any]] = []

    fake_client = _make_fake_client(recorded)

    async def _fake_connect(*_a, **_kw):
        return fake_client

    with patch("temporalio.client.Client.connect", _fake_connect):
        asyncio.run(review_dispatch.dispatch_review_workflow(
            tenant_id=tenant_id,
            review_id=review_id,
        ))

    assert len(recorded) == 1, "start_workflow must be awaited exactly once"
    call = recorded[0]
    assert call["args"][0] == "ReviewWorkflow"
    assert call["kwargs"]["arg"] == {
        "tenant_id": str(tenant_id),
        "review_id": str(review_id),
    }
    assert call["kwargs"]["task_queue"] == "agentprovision-orchestration"
    assert call["kwargs"]["id"].startswith(f"review-{review_id}-")


def test_dispatch_review_workflow_propagates_connect_failure():
    """A Temporal outage on ``Client.connect`` MUST raise. The endpoint
    is responsible for translating into a warning/log; the dispatcher
    itself does not silently swallow (that was the daemon-thread bug).
    """
    from app.services import review_dispatch

    async def _boom(*_a, **_kw):
        raise ConnectionError("temporal down")

    with patch("temporalio.client.Client.connect", _boom):
        with pytest.raises(ConnectionError, match="temporal down"):
            asyncio.run(review_dispatch.dispatch_review_workflow(
                tenant_id=uuid.uuid4(),
                review_id=uuid.uuid4(),
            ))


def test_dispatch_review_workflow_no_daemon_thread_module_attrs():
    """Defence-in-depth: the module must NOT re-introduce a ``threading``
    import. The fire-and-forget daemon-thread bug (PR #574) re-appears
    every time someone wraps the async call in ``threading.Thread``.
    """
    import app.services.review_dispatch as rd

    assert not hasattr(rd, "threading"), (
        "review_dispatch must not import threading — the daemon-thread "
        "fire-and-forget pattern is the bug this PR fixes."
    )


# ──────────────────────────────────────────────────────────────────────────
# In-memory dispatched-workflow queue: assert N legs -> N dispatches
# ──────────────────────────────────────────────────────────────────────────


def test_n_dispatches_grow_in_memory_queue():
    """End-to-end-ish: dispatching N times grows an in-memory queue by
    N. Mirrors the eval-runner test's per-leg invariant.
    """
    from app.services import review_dispatch

    queue: List[str] = []
    fake_client = _make_fake_client(queue)

    async def _fake_connect(*_a, **_kw):
        return fake_client

    async def _dispatch_n(n: int) -> List[uuid.UUID]:
        review_ids: List[uuid.UUID] = []
        for _ in range(n):
            rid = uuid.uuid4()
            review_ids.append(rid)
            await review_dispatch.dispatch_review_workflow(
                tenant_id=uuid.uuid4(),
                review_id=rid,
            )
        return review_ids

    with patch("temporalio.client.Client.connect", _fake_connect):
        ids = asyncio.run(_dispatch_n(5))

    assert len(queue) == 5
    queued_review_ids = {q["kwargs"]["arg"]["review_id"] for q in queue}
    assert queued_review_ids == {str(rid) for rid in ids}
