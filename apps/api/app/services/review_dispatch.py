"""Temporal dispatch shim for `alpha review`.

Kept in its own module so the router (`api/v1/reviews.py`) can import
it lazily and the test suite can monkeypatch `dispatch_review_workflow`
without booting a Temporal client.

Parallels `agent_router.dispatch_coalition` (#440) — async-await directly
from the FastAPI request handler. We do NOT spawn a daemon thread: that
pattern (`threading.Thread(target=runner, daemon=True).start()` where
`runner` calls `asyncio.run(_go())`) silently failed under gunicorn
workers (the daemon thread could die before `asyncio.run` reached
`start_workflow`, leaving a review row with no Temporal workflow
behind it).

The endpoint is already an async coroutine; `await Client.connect(...)`
+ `await client.start_workflow(...)` adds <100ms and Temporal continues
the workflow server-side regardless of HTTP request lifecycle. This is
the "Temporal-native dispatch from the request handler" pattern from
docs/plans/2026-05-19-skill-eval-temporal-parent-pattern-adr.md.
"""

from __future__ import annotations

import logging
import uuid

logger = logging.getLogger(__name__)


async def dispatch_review_workflow(
    tenant_id: uuid.UUID,
    review_id: uuid.UUID,
) -> None:
    """Start a ReviewWorkflow for the given review (fire-and-forget at the
    Temporal level — we don't await its completion, but we DO await the
    `start_workflow` RPC so a Temporal outage surfaces as an exception
    here and the endpoint can translate it to a 503).

    The workflow loads the ReviewCoalition row, fans the review prompt
    out to all CLIs in `review.clis` in parallel via child
    ChatCliWorkflow executions, then posts each CLI's output back
    through POST /api/v1/reviews/{id}/record. The consensus aggregator
    runs synchronously inside `record_cli_findings` when the last CLI
    reports.

    Raises:
        Exception: propagated from `Client.connect` or `start_workflow`.
            The endpoint catches and logs; we deliberately do NOT swallow
            here so the caller can decide how to surface the failure.
    """
    from temporalio.client import Client

    from app.core.config import settings

    client = await Client.connect(settings.TEMPORAL_ADDRESS)
    arg = {
        "tenant_id": str(tenant_id),
        "review_id": str(review_id),
    }
    await client.start_workflow(
        "ReviewWorkflow",
        arg=arg,
        id=f"review-{review_id}-{uuid.uuid4().hex[:6]}",
        task_queue="agentprovision-orchestration",
    )
