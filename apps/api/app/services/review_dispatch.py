"""Temporal dispatch shim for `alpha review`.

Kept in its own module so the router (`api/v1/reviews.py`) can import
it lazily and the test suite can monkeypatch `dispatch_review_workflow`
without booting a Temporal client.

Parallels `agent_router.dispatch_coalition` (#440) — fire-and-forget,
agentprovision-orchestration queue, daemon thread so we don't hold the
request thread.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid

logger = logging.getLogger(__name__)


def dispatch_review_workflow(
    tenant_id: uuid.UUID,
    review_id: uuid.UUID,
) -> None:
    """Fire-and-forget ReviewWorkflow dispatch.

    The workflow loads the ReviewCoalition row, fans the review prompt
    out to all CLIs in `review.clis` in parallel via child
    ChatCliWorkflow executions, then posts each CLI's output back
    through POST /api/v1/reviews/{id}/record. The consensus aggregator
    runs synchronously inside `record_cli_findings` when the last CLI
    reports.
    """
    from temporalio.client import Client

    from app.core.config import settings

    def _runner() -> None:
        async def _go() -> None:
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

        try:
            asyncio.run(_go())
        except Exception as e:
            logger.warning(
                "ReviewWorkflow dispatch failed for review %s: %s",
                review_id,
                e,
            )

    threading.Thread(target=_runner, daemon=True).start()
