import logging
from datetime import datetime
from typing import List, Optional

import uuid
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_current_user
from app.models.user import User
from app.schemas.agent_task import AgentTask, AgentTaskCreate, AgentTaskUpdate
from app.schemas.execution_trace import ExecutionTrace as ExecutionTraceSchema
from app.services import agent_tasks as service
from app.services import execution_traces as trace_service

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("", response_model=AgentTask, status_code=201)
async def create_task(
    task_in: AgentTaskCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Create a new task for an agent.

    WhatsApp tasks (task_type='whatsapp', context.skill='whatsapp') are
    auto-executed immediately via the neonize WhatsApp service instead of
    going through the full TaskExecutionWorkflow.
    """
    try:
        task = service.create_task(db, task_in, current_user.tenant_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Auto-execute WhatsApp send tasks
    if (
        task.task_type == "whatsapp"
        and task.context
        and task.context.get("skill") == "whatsapp"
    ):
        await _execute_whatsapp_task(task, db, str(current_user.tenant_id))

    return task


async def _execute_whatsapp_task(task, db: Session, tenant_id: str):
    """Send a WhatsApp message and update the task status."""
    from app.services.whatsapp_service import whatsapp_service

    payload = task.context.get("payload", {})
    action = payload.get("action")
    recipient = payload.get("recipient_phone", "")
    message_body = payload.get("message_body", "")
    account_id = payload.get("account_id", "default")

    if action not in ("send_message", "send_template"):
        return  # Unknown action, leave task queued for manual handling

    # For templates, format as plain text (neonize doesn't support WA templates)
    if action == "send_template":
        template_name = payload.get("template_name", "")
        template_params = payload.get("template_params", {})
        message_body = message_body or f"[{template_name}] {template_params}"

    if not recipient or not message_body:
        task.status = "failed"
        task.error = "Missing recipient_phone or message_body"
        task.completed_at = datetime.utcnow()
        db.commit()
        db.refresh(task)
        return

    task.status = "executing"
    task.started_at = datetime.utcnow()
    db.commit()

    try:
        result = await whatsapp_service.send_message(
            tenant_id=tenant_id,
            account_id=account_id,
            to=recipient,
            message=message_body,
        )
        if result.get("status") == "error":
            task.status = "failed"
            task.error = result.get("error", "WhatsApp send failed")
        else:
            task.status = "completed"
            task.output = {
                "message_id": result.get("message_id"),
                "recipient": recipient,
                "status": "sent",
            }
    except Exception as e:
        logger.exception("WhatsApp task %s failed", task.id)
        task.status = "failed"
        task.error = str(e)

    task.completed_at = datetime.utcnow()
    db.commit()
    db.refresh(task)


@router.get("", response_model=List[AgentTask])
def list_tasks(
    skip: int = 0,
    limit: int = 100,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """List all tasks."""
    return service.get_tasks(db, current_user.tenant_id, skip, limit, status)


@router.get("/{task_id}", response_model=AgentTask)
def get_task(
    task_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get task by ID."""
    task = service.get_task(db, task_id, current_user.tenant_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.patch("/{task_id}", response_model=AgentTask)
def update_task(
    task_id: uuid.UUID,
    task_in: AgentTaskUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Update a task."""
    task = service.update_task(db, task_id, current_user.tenant_id, task_in)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.get("/{task_id}/trace", response_model=List[ExecutionTraceSchema])
def get_task_trace(
    task_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get execution trace for a task."""
    task = service.get_task(db, task_id, current_user.tenant_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return trace_service.get_traces_by_task(db, task_id, current_user.tenant_id)


@router.post("/{task_id}/approve", response_model=AgentTask)
def approve_task(
    task_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Approve a task waiting for approval, setting status to executing."""
    task = service.get_task(db, task_id, current_user.tenant_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.status != "waiting_for_approval":
        raise HTTPException(status_code=400, detail="Task is not waiting for approval")
    task.status = "executing"
    task.started_at = datetime.utcnow()
    db.commit()
    db.refresh(task)
    return task


@router.post("/{task_id}/reject", response_model=AgentTask)
def reject_task(
    task_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Reject a task waiting for approval, setting status to failed."""
    task = service.get_task(db, task_id, current_user.tenant_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.status != "waiting_for_approval":
        raise HTTPException(status_code=400, detail="Task is not waiting for approval")
    task.status = "failed"
    task.error = "Rejected by user"
    task.completed_at = datetime.utcnow()
    db.commit()
    db.refresh(task)
    return task


class WorkflowApprovalDecision(BaseModel):
    """Body for approving/rejecting a human_approval workflow step linked to a task."""
    decision: str  # "approved" | "rejected"
    comment: Optional[str] = None
    # Optional: caller may pass these explicitly if the task context doesn't contain them.
    run_id: Optional[str] = None
    step_id: Optional[str] = None


@router.post("/{task_id}/workflow-approve")
async def workflow_approve_task(
    task_id: uuid.UUID,
    body: WorkflowApprovalDecision,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Send an approval_decision Temporal signal for a human_approval workflow step.

    The task's context may contain ``workflow_run_id`` and ``step_id`` that
    identify which workflow run and step to signal.  Callers can also pass
    ``run_id`` / ``step_id`` directly in the request body as a fallback.

    Returns ``{"status": "signal_sent", "decision": decision}`` on success or
    ``{"status": "not_implemented", ...}`` when the Temporal client is
    unreachable.
    """
    task = service.get_task(db, task_id, current_user.tenant_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if body.decision not in ("approved", "rejected"):
        raise HTTPException(status_code=400, detail="decision must be 'approved' or 'rejected'")

    # Resolve run_id and step_id from task context or explicit body fields.
    task_context = task.context or {}
    run_id = body.run_id or task_context.get("workflow_run_id")
    step_id = body.step_id or task_context.get("approval_step_id", "approval")

    if not run_id:
        raise HTTPException(
            status_code=422,
            detail=(
                "Cannot resolve workflow run: provide run_id in the request body "
                "or store workflow_run_id in the task context."
            ),
        )

    # Look up the WorkflowRun to get the Temporal workflow ID.
    try:
        from app.models.dynamic_workflow import WorkflowRun
        run = db.query(WorkflowRun).filter(
            WorkflowRun.id == run_id,
            WorkflowRun.tenant_id == current_user.tenant_id,
        ).first()
    except Exception as exc:
        logger.warning("DB lookup for WorkflowRun %s failed: %s", run_id, exc)
        run = None

    if not run or not run.temporal_workflow_id:
        raise HTTPException(
            status_code=404,
            detail="Workflow run not found or has no associated Temporal workflow ID.",
        )

    # Send the Temporal signal.
    try:
        from app.core.config import settings
        from temporalio.client import Client

        client = await Client.connect(settings.TEMPORAL_ADDRESS)
        handle = client.get_workflow_handle(run.temporal_workflow_id)
        await handle.signal("approval_decision", step_id, body.decision)

        logger.info(
            "approval_decision signal sent: run=%s step=%s decision=%s by user=%s",
            run_id, step_id, body.decision, current_user.id,
        )
        return {"status": "signal_sent", "decision": body.decision, "step_id": step_id, "run_id": run_id}

    except Exception as exc:
        logger.warning("Temporal signal failed for run=%s: %s", run_id, exc)
        return {
            "status": "not_implemented",
            "reason": f"Temporal signal client error: {exc}",
            "decision": body.decision,
        }
