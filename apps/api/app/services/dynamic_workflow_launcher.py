"""Launch dynamic workflows by template name.

Replaces all direct static Temporal workflow starts with DynamicWorkflowExecutor.
Looks up the native template by name, builds the executor input, and starts it.
"""
import logging
import uuid
from datetime import timedelta
from typing import Optional

from sqlalchemy.orm import Session
from temporalio.client import Client

from app.core.config import settings
from app.models.dynamic_workflow import DynamicWorkflow

logger = logging.getLogger(__name__)

TASK_QUEUE = "servicetsunami-orchestration"


async def start_dynamic_workflow(
    db: Session,
    template_name: str,
    tenant_id: uuid.UUID,
    input_data: Optional[dict] = None,
    workflow_id_prefix: Optional[str] = None,
    task_queue: Optional[str] = None,
) -> str:
    """Start a DynamicWorkflowExecutor for a native template by name.

    Returns the Temporal workflow ID.
    """
    # Look up the native template
    template = db.query(DynamicWorkflow).filter(
        DynamicWorkflow.name == template_name,
        DynamicWorkflow.tier == "native",
    ).first()

    if not template:
        raise ValueError(f"Native template '{template_name}' not found in DB")

    # Build executor input
    run_id = str(uuid.uuid4())
    prefix = workflow_id_prefix or template_name.lower().replace(" ", "-")
    temporal_wf_id = f"dyn-{prefix}-{tenant_id.hex[:8]}-{run_id[:8]}"

    executor_input = {
        "workflow_id": str(template.id),
        "definition": template.definition,
        "trigger_config": template.trigger_config,
        "tenant_id": str(tenant_id),
        "input_data": input_data or {},
        "run_id": run_id,
    }

    client = await Client.connect(settings.TEMPORAL_ADDRESS)
    await client.start_workflow(
        "DynamicWorkflowExecutor",
        executor_input,
        id=temporal_wf_id,
        task_queue=task_queue or TASK_QUEUE,
        execution_timeout=timedelta(hours=24),
    )

    logger.info(
        "Started dynamic workflow '%s' for tenant %s (temporal_id=%s)",
        template_name, str(tenant_id)[:8], temporal_wf_id,
    )
    return temporal_wf_id


async def start_dynamic_workflow_by_name(
    template_name: str,
    tenant_id: str,
    input_data: Optional[dict] = None,
    task_queue: Optional[str] = None,
) -> str:
    """Convenience wrapper that creates its own DB session.

    For use in activities/startup where no session is passed.
    """
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        return await start_dynamic_workflow(
            db=db,
            template_name=template_name,
            tenant_id=uuid.UUID(tenant_id) if isinstance(tenant_id, str) else tenant_id,
            input_data=input_data,
            task_queue=task_queue,
        )
    finally:
        db.close()
