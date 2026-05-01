"""Sync wrapper to dispatch PostChatMemoryWorkflow from sync code paths."""
import asyncio
import logging
import threading
import time
from uuid import UUID
from temporalio.client import Client
from app.core.config import settings
from app.workflows.post_chat_memory import PostChatMemoryWorkflow

logger = logging.getLogger(__name__)

def dispatch_post_chat_memory(
    tenant_id: UUID,
    session_id: UUID,
    user_message_id: UUID,
    assistant_message_id: UUID,
):
    """Trigger the PostChatMemoryWorkflow in Temporal (fire-and-forget)."""

    async def _dispatch():
        try:
            client = await Client.connect(settings.TEMPORAL_ADDRESS)
            await client.start_workflow(
                PostChatMemoryWorkflow.run,
                args=[
                    str(tenant_id),
                    str(session_id),
                    str(user_message_id),
                    str(assistant_message_id)
                ],
                id=f"pcm-{session_id}-{int(time.time())}",
                task_queue="agentprovision-orchestration",
            )
            logger.debug("Dispatched PostChatMemoryWorkflow for session %s", str(session_id)[:8])
        except Exception as e:
            logger.error(
                "PostChatMemoryWorkflow dispatch failed for session %s (Temporal at %s): %s",
                str(session_id)[:8],
                settings.TEMPORAL_ADDRESS,
                e,
            )

    # Fire and forget via background thread to avoid blocking the request
    # thread or interfering with any existing event loop. `asyncio.run`
    # (vs. manual new_event_loop/run_until_complete/close) drains pending
    # tasks before closing — manual close was leaving httpx aclose() tasks
    # orphaned, surfacing later as `RuntimeError: Event loop is closed`
    # when the GC tried to run them.
    threading.Thread(target=lambda: asyncio.run(_dispatch()), daemon=True).start()
