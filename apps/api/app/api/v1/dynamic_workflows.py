"""Dynamic workflows API — CRUD, execution, and template marketplace."""

import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api import deps
from app.models.dynamic_workflow import DynamicWorkflow, WorkflowRun, WorkflowStepLog
from app.schemas.dynamic_workflow import (
    DynamicWorkflowCreate,
    DynamicWorkflowInDB,
    DynamicWorkflowUpdate,
    WorkflowRunInDB,
    WorkflowRunRequest,
    WorkflowStepLogInDB,
)

router = APIRouter()


# ── CRUD ──────────────────────────────────────────────────────────

@router.post("", response_model=DynamicWorkflowInDB, status_code=201)
def create_workflow(
    payload: DynamicWorkflowCreate,
    db: Session = Depends(deps.get_db),
    current_user=Depends(deps.get_current_active_user),
):
    """Create a new dynamic workflow."""
    wf = DynamicWorkflow(
        tenant_id=current_user.tenant_id,
        name=payload.name,
        description=payload.description,
        definition=payload.definition.model_dump(by_alias=True),
        trigger_config=payload.trigger_config.model_dump() if payload.trigger_config else None,
        tags=payload.tags,
        created_by=current_user.id,
    )
    db.add(wf)
    db.commit()
    db.refresh(wf)
    return wf


@router.get("", response_model=list[DynamicWorkflowInDB])
def list_workflows(
    status: Optional[str] = None,
    db: Session = Depends(deps.get_db),
    current_user=Depends(deps.get_current_active_user),
):
    """List tenant's dynamic workflows."""
    q = db.query(DynamicWorkflow).filter(DynamicWorkflow.tenant_id == current_user.tenant_id)
    if status:
        q = q.filter(DynamicWorkflow.status == status)
    return q.order_by(DynamicWorkflow.updated_at.desc()).all()


@router.get("/{workflow_id}", response_model=DynamicWorkflowInDB)
def get_workflow(
    workflow_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    current_user=Depends(deps.get_current_active_user),
):
    """Get a single workflow."""
    wf = db.query(DynamicWorkflow).filter(
        DynamicWorkflow.id == workflow_id,
        DynamicWorkflow.tenant_id == current_user.tenant_id,
    ).first()
    if not wf:
        raise HTTPException(404, "Workflow not found")
    return wf


@router.put("/{workflow_id}", response_model=DynamicWorkflowInDB)
def update_workflow(
    workflow_id: uuid.UUID,
    payload: DynamicWorkflowUpdate,
    db: Session = Depends(deps.get_db),
    current_user=Depends(deps.get_current_active_user),
):
    """Update a workflow."""
    wf = db.query(DynamicWorkflow).filter(
        DynamicWorkflow.id == workflow_id,
        DynamicWorkflow.tenant_id == current_user.tenant_id,
    ).first()
    if not wf:
        raise HTTPException(404, "Workflow not found")

    if payload.name is not None:
        wf.name = payload.name
    if payload.description is not None:
        wf.description = payload.description
    if payload.definition is not None:
        wf.definition = payload.definition.model_dump(by_alias=True)
        wf.version += 1
    if payload.trigger_config is not None:
        wf.trigger_config = payload.trigger_config.model_dump()
    if payload.tags is not None:
        wf.tags = payload.tags
    if payload.status is not None:
        wf.status = payload.status

    wf.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(wf)
    return wf


@router.delete("/{workflow_id}", status_code=204)
def delete_workflow(
    workflow_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    current_user=Depends(deps.get_current_active_user),
):
    """Delete a workflow and all its runs."""
    wf = db.query(DynamicWorkflow).filter(
        DynamicWorkflow.id == workflow_id,
        DynamicWorkflow.tenant_id == current_user.tenant_id,
    ).first()
    if not wf:
        raise HTTPException(404, "Workflow not found")
    db.delete(wf)
    db.commit()


# ── Status Changes ────────────────────────────────────────────────

@router.post("/{workflow_id}/activate")
def activate_workflow(
    workflow_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    current_user=Depends(deps.get_current_active_user),
):
    """Activate a workflow (enable triggers)."""
    wf = db.query(DynamicWorkflow).filter(
        DynamicWorkflow.id == workflow_id,
        DynamicWorkflow.tenant_id == current_user.tenant_id,
    ).first()
    if not wf:
        raise HTTPException(404, "Workflow not found")
    wf.status = "active"
    wf.updated_at = datetime.utcnow()
    db.commit()
    return {"status": "active", "id": str(wf.id)}


@router.post("/{workflow_id}/pause")
def pause_workflow(
    workflow_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    current_user=Depends(deps.get_current_active_user),
):
    """Pause a workflow (disable triggers)."""
    wf = db.query(DynamicWorkflow).filter(
        DynamicWorkflow.id == workflow_id,
        DynamicWorkflow.tenant_id == current_user.tenant_id,
    ).first()
    if not wf:
        raise HTTPException(404, "Workflow not found")
    wf.status = "paused"
    wf.updated_at = datetime.utcnow()
    db.commit()
    return {"status": "paused", "id": str(wf.id)}


# ── Execution ─────────────────────────────────────────────────────

@router.post("/{workflow_id}/run", response_model=WorkflowRunInDB)
async def run_workflow(
    workflow_id: uuid.UUID,
    payload: WorkflowRunRequest = WorkflowRunRequest(),
    db: Session = Depends(deps.get_db),
    current_user=Depends(deps.get_current_active_user),
):
    """Trigger a manual workflow run via Temporal."""
    wf = db.query(DynamicWorkflow).filter(
        DynamicWorkflow.id == workflow_id,
        DynamicWorkflow.tenant_id == current_user.tenant_id,
    ).first()
    if not wf:
        raise HTTPException(404, "Workflow not found")

    # Create run record
    run = WorkflowRun(
        tenant_id=current_user.tenant_id,
        workflow_id=wf.id,
        workflow_version=wf.version,
        trigger_type="manual",
        status="running",
        input_data=payload.input_data,
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    # Start Temporal workflow
    from temporalio.client import Client
    from app.core.config import settings
    from app.workflows.dynamic_executor import DynamicWorkflowExecutor, DynamicWorkflowInput

    try:
        client = await Client.connect(settings.TEMPORAL_ADDRESS)
        temporal_wf_id = f"dynamic-{wf.id}-{run.id}"

        await client.start_workflow(
            DynamicWorkflowExecutor.run,
            DynamicWorkflowInput(
                workflow_id=str(wf.id),
                run_id=str(run.id),
                tenant_id=str(current_user.tenant_id),
                definition=wf.definition,
                input_data=payload.input_data or {},
            ),
            id=temporal_wf_id,
            task_queue="servicetsunami-orchestration",
        )

        run.temporal_workflow_id = temporal_wf_id
        db.commit()
    except Exception as e:
        run.status = "failed"
        run.error = str(e)
        db.commit()
        raise HTTPException(500, f"Failed to start workflow: {e}")

    return run


@router.get("/{workflow_id}/runs", response_model=list[WorkflowRunInDB])
def list_runs(
    workflow_id: uuid.UUID,
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(deps.get_db),
    current_user=Depends(deps.get_current_active_user),
):
    """List runs for a workflow."""
    return (
        db.query(WorkflowRun)
        .filter(WorkflowRun.workflow_id == workflow_id, WorkflowRun.tenant_id == current_user.tenant_id)
        .order_by(WorkflowRun.started_at.desc())
        .limit(limit)
        .all()
    )


@router.get("/runs/{run_id}")
def get_run(
    run_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    current_user=Depends(deps.get_current_active_user),
):
    """Get run details with step logs."""
    run = db.query(WorkflowRun).filter(
        WorkflowRun.id == run_id,
        WorkflowRun.tenant_id == current_user.tenant_id,
    ).first()
    if not run:
        raise HTTPException(404, "Run not found")

    steps = (
        db.query(WorkflowStepLog)
        .filter(WorkflowStepLog.run_id == run_id)
        .order_by(WorkflowStepLog.started_at)
        .all()
    )

    return {
        "run": WorkflowRunInDB.model_validate(run),
        "steps": [WorkflowStepLogInDB.model_validate(s) for s in steps],
    }


@router.post("/runs/{run_id}/approve/{step_id}")
async def approve_step(
    run_id: uuid.UUID,
    step_id: str,
    approved: bool = True,
    db: Session = Depends(deps.get_db),
    current_user=Depends(deps.get_current_active_user),
):
    """Send approval signal to a waiting workflow step."""
    run = db.query(WorkflowRun).filter(
        WorkflowRun.id == run_id,
        WorkflowRun.tenant_id == current_user.tenant_id,
    ).first()
    if not run or not run.temporal_workflow_id:
        raise HTTPException(404, "Run not found")

    from temporalio.client import Client
    from app.core.config import settings

    client = await Client.connect(settings.TEMPORAL_ADDRESS)
    handle = client.get_workflow_handle(run.temporal_workflow_id)
    await handle.signal("approve_step", step_id, approved)

    return {"approved": approved, "step_id": step_id}


# ── Templates ─────────────────────────────────────────────────────

@router.get("/templates/browse", response_model=list[DynamicWorkflowInDB])
def browse_templates(
    tier: Optional[str] = None,
    db: Session = Depends(deps.get_db),
    current_user=Depends(deps.get_current_active_user),
):
    """Browse available workflow templates."""
    q = db.query(DynamicWorkflow).filter(
        (DynamicWorkflow.tier.in_(["native", "community"])) | (DynamicWorkflow.public == True)
    )
    if tier:
        q = q.filter(DynamicWorkflow.tier == tier)
    return q.order_by(DynamicWorkflow.installs.desc()).all()


@router.post("/templates/{template_id}/install", response_model=DynamicWorkflowInDB)
def install_template(
    template_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    current_user=Depends(deps.get_current_active_user),
):
    """Install a template — creates a copy in the tenant's workflows."""
    template = db.query(DynamicWorkflow).filter(DynamicWorkflow.id == template_id).first()
    if not template:
        raise HTTPException(404, "Template not found")

    copy = DynamicWorkflow(
        tenant_id=current_user.tenant_id,
        name=template.name,
        description=template.description,
        definition=template.definition,
        trigger_config=template.trigger_config,
        tags=template.tags,
        tier="custom",
        source_template_id=template.id,
        created_by=current_user.id,
    )
    db.add(copy)

    template.installs = (template.installs or 0) + 1
    db.commit()
    db.refresh(copy)
    return copy
