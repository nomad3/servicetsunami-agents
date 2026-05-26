"""Luna Learn service-layer dispatch helper (T4.1a).

`LearningService.dispatch(intent)` connects to Temporal and starts the
``LearnFromMediaWorkflow``. Returns the workflow_id immediately
(fire-and-forget per spec §1.10).

This is the shared dispatch surface consumed by both entry points:

  * WhatsApp URL routing (T4.2) — when Luna receives a learning URL in chat.
  * `alpha learn` CLI command (T4.3) — via the HTTP route in T4.4c, which
    wraps this helper.

Keeping dispatch in a pure service module (rather than the HTTP route)
preserves the service / route boundary called out in reviewer note I4 —
both callers can share the exact same workflow_id derivation, task-queue
selection, and Temporal client setup without smuggling FastAPI imports
into the WhatsApp path.

Workflow id: ``f"luna-learn-{tenant_id}-{uuid4().hex[:12]}"`` — tenant
prefix keeps Temporal UI groupable per tenant; the random suffix avoids
collisions on rapid re-dispatch (a deterministic hash of the intent would
collapse legitimate retries into a single WorkflowExecutionAlreadyStarted
error, which is the opposite of what the resume path wants).
"""
from __future__ import annotations

import logging
from uuid import uuid4

from temporalio.client import Client

from app.core.config import settings
from app.schemas.learning import LearningIntent

logger = logging.getLogger(__name__)


# ── Task queue ─────────────────────────────────────────────────────
# Single orchestration queue (matches `apps/api/app/workers/orchestration_worker.py`
# and the rest of the dispatch helpers — `app.memory.dispatch`,
# `app.api.v1.memory_admin`, etc.). Kept as a module constant so the
# WhatsApp + CLI callers don't drift from the worker registration.
_TASK_QUEUE = "agentprovision-orchestration"


class LearningService:
    """Service-layer dispatcher for Luna Learn workflows."""

    @staticmethod
    async def dispatch(intent: LearningIntent) -> str:
        """Start a ``LearnFromMediaWorkflow`` and return its workflow_id.

        Fire-and-forget: we do not await ``handle.result()`` here. The
        workflow notifies the originating chat session via
        ``act_notify_session`` (T3.5) when it terminates.
        """
        client = await Client.connect(settings.TEMPORAL_ADDRESS)
        workflow_id = f"luna-learn-{intent.tenant_id}-{uuid4().hex[:12]}"

        await client.start_workflow(
            "LearnFromMediaWorkflow",
            intent.model_dump(),
            id=workflow_id,
            task_queue=_TASK_QUEUE,
        )

        logger.info(
            "LearningService dispatched workflow_id=%s tenant=%s resume=%s",
            workflow_id,
            intent.tenant_id,
            bool(intent.resume_job_id),
        )
        return workflow_id
