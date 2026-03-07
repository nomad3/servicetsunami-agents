from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_current_user
from app.core.config import settings


_optional_oauth2 = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)


def _get_tenant_id_from_internal_or_user(
    tenant_id: Optional[str] = Query(None),
    x_internal_key: Optional[str] = Header(None, alias="X-Internal-Key"),
    token: Optional[str] = Depends(_optional_oauth2),
    db: Session = Depends(get_db),
) -> str:
    """Resolve tenant_id from JWT user or X-Internal-Key + tenant_id param.

    ADK->API calls pass X-Internal-Key header with tenant_id as query param.
    Browser calls pass a JWT Bearer token.
    """
    # Try internal key first
    if x_internal_key and x_internal_key in (
        getattr(settings, "API_INTERNAL_KEY", None),
        getattr(settings, "MCP_API_KEY", None),
    ):
        if not tenant_id:
            raise HTTPException(status_code=400, detail="tenant_id required with internal key auth")
        return tenant_id
    # Fall back to JWT
    if token:
        try:
            from jose import jwt as jose_jwt, JWTError
            payload = jose_jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
            email = payload.get("sub")
            if email:
                user = db.query(User).filter(User.email == email).first()
                if user:
                    return str(user.tenant_id)
        except Exception:
            pass
    raise HTTPException(status_code=401, detail="Authentication required")


from app.models.agent import Agent
from app.models.agent_task import AgentTask
from app.models.execution_trace import ExecutionTrace
from app.models.pipeline_run import PipelineRun
from app.models.user import User
from app.services.workflows import _get_temporal_client, TemporalNotConfiguredError

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# GET /workflows/stats — Aggregated dashboard stats
# ---------------------------------------------------------------------------
@router.get("/stats")
async def workflow_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Aggregated workflow/task statistics for the dashboard KPI cards."""
    tenant_id = current_user.tenant_id

    # DB-based task stats (scoped through Agent.tenant_id)
    task_rows = (
        db.query(AgentTask.status, func.count(AgentTask.id))
        .join(Agent, AgentTask.assigned_agent_id == Agent.id)
        .filter(Agent.tenant_id == tenant_id)
        .group_by(AgentTask.status)
        .all()
    )
    task_by_status = {status: count for status, count in task_rows}

    agg = (
        db.query(
            func.coalesce(func.sum(AgentTask.tokens_used), 0),
            func.coalesce(func.sum(AgentTask.cost), 0),
        )
        .join(Agent, AgentTask.assigned_agent_id == Agent.id)
        .filter(Agent.tenant_id == tenant_id)
        .first()
    )
    total_tokens = int(agg[0]) if agg else 0
    total_cost = float(agg[1]) if agg else 0.0

    total_tasks = sum(task_by_status.values())

    # Temporal workflow counts
    temporal_available = True
    temporal_running = 0
    temporal_completed = 0
    temporal_failed = 0
    temporal_total = 0

    try:
        client = await _get_temporal_client()
        query = 'ExecutionStatus = "Running"'
        async for _ in client.list_workflows(query=query):
            temporal_running += 1
        query_done = 'ExecutionStatus = "Completed"'
        async for _ in client.list_workflows(query=query_done):
            temporal_completed += 1
        query_fail = 'ExecutionStatus = "Failed"'
        async for _ in client.list_workflows(query=query_fail):
            temporal_failed += 1
        temporal_total = temporal_running + temporal_completed + temporal_failed
    except (TemporalNotConfiguredError, RuntimeError) as exc:
        logger.warning("Temporal unavailable for stats: %s", exc)
        temporal_available = False

    return {
        "temporal_available": temporal_available,
        "total_workflows": temporal_total or total_tasks,
        "running_count": task_by_status.get("executing", 0) + task_by_status.get("thinking", 0) + temporal_running,
        "completed_count": task_by_status.get("completed", 0),
        "failed_count": task_by_status.get("failed", 0),
        "queued_count": task_by_status.get("queued", 0),
        "waiting_input_count": task_by_status.get("waiting_input", 0),
        "total_tokens": total_tokens,
        "total_cost": round(total_cost, 4),
        "tasks_by_status": task_by_status,
        "temporal_workflows": {
            "running": temporal_running,
            "completed": temporal_completed,
            "failed": temporal_failed,
            "total": temporal_total,
        },
    }


# ---------------------------------------------------------------------------
# GET /workflows — List workflows (Temporal + DB tasks merged)
# ---------------------------------------------------------------------------
@router.get("")
async def list_workflows(
    workflow_type: Optional[str] = Query(None, description="Filter by workflow type"),
    status: Optional[str] = Query(None, description="Filter by status"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List Temporal workflows + DB agent tasks for the audit dashboard."""
    tenant_id = current_user.tenant_id
    results: List[dict] = []

    # --- Temporal workflows ---
    temporal_available = True
    try:
        client = await _get_temporal_client()
        query_parts = []
        if workflow_type:
            query_parts.append(f'WorkflowType = "{workflow_type}"')
        if status:
            status_map = {
                "running": "Running",
                "completed": "Completed",
                "failed": "Failed",
                "terminated": "Terminated",
                "canceled": "Canceled",
                "timed_out": "TimedOut",
            }
            temporal_status = status_map.get(status.lower())
            if temporal_status:
                query_parts.append(f'ExecutionStatus = "{temporal_status}"')

        query_str = " AND ".join(query_parts) if query_parts else ""
        count = 0
        async for wf in client.list_workflows(query=query_str):
            if count < skip:
                count += 1
                continue
            if len(results) >= limit:
                break
            memo = {}
            raw_memo = wf.memo() if callable(wf.memo) else wf.memo
            if raw_memo and isinstance(raw_memo, dict):
                for k, v in raw_memo.items():
                    try:
                        memo[k] = v
                    except Exception:
                        pass

            # Filter by tenant via memo
            memo_tenant = str(memo.get("tenant_id", ""))
            if memo_tenant and memo_tenant != str(tenant_id):
                continue

            wf_status = wf.status.name if wf.status else None
            results.append({
                "source": "temporal",
                "workflow_id": wf.id,
                "run_id": wf.run_id,
                "type": wf.workflow_type,
                "status": wf_status,
                "start_time": wf.start_time.isoformat() if wf.start_time else None,
                "close_time": wf.close_time.isoformat() if wf.close_time else None,
                "execution_time": wf.execution_time.isoformat() if wf.execution_time else None,
                "history_length": wf.history_length,
                "memo": memo,
                "objective": memo.get("objective", wf.workflow_type),
            })
            count += 1
    except (TemporalNotConfiguredError, RuntimeError) as exc:
        logger.warning("Temporal unavailable: %s", exc)
        temporal_available = False

    # --- DB agent tasks (always included as fallback / enrichment) ---
    task_query = (
        db.query(AgentTask)
        .join(Agent, AgentTask.assigned_agent_id == Agent.id)
        .filter(Agent.tenant_id == tenant_id)
    )
    if status:
        task_query = task_query.filter(AgentTask.status == status)
    tasks = task_query.order_by(AgentTask.created_at.desc()).offset(skip).limit(limit).all()

    # Get trace counts per task
    trace_counts = dict(
        db.query(ExecutionTrace.task_id, func.count(ExecutionTrace.id))
        .filter(ExecutionTrace.tenant_id == tenant_id)
        .group_by(ExecutionTrace.task_id)
        .all()
    )

    for t in tasks:
        results.append({
            "source": "agent_task",
            "workflow_id": None,
            "run_id": None,
            "type": t.task_type or "agent_task",
            "task_id": str(t.id),
            "status": t.status,
            "start_time": t.started_at.isoformat() if t.started_at else (t.created_at.isoformat() if t.created_at else None),
            "close_time": t.completed_at.isoformat() if t.completed_at else None,
            "objective": t.objective or "",
            "priority": t.priority,
            "confidence": t.confidence,
            "tokens_used": t.tokens_used,
            "cost": t.cost,
            "error": t.error,
            "trace_count": trace_counts.get(t.id, 0),
            "requires_approval": t.requires_approval,
            "human_requested": t.human_requested,
        })

    # Enrich with pipeline_run data where applicable
    pipeline_workflow_ids = [r["workflow_id"] for r in results if r.get("workflow_id")]
    if pipeline_workflow_ids:
        pipeline_runs = (
            db.query(PipelineRun)
            .filter(PipelineRun.workflow_id.in_(pipeline_workflow_ids))
            .all()
        )
        pr_map = {pr.workflow_id: pr for pr in pipeline_runs}
        for r in results:
            pr = pr_map.get(r.get("workflow_id"))
            if pr:
                r["pipeline_run"] = {
                    "pipeline_id": str(pr.pipeline_id),
                    "status": pr.status,
                    "started_at": pr.started_at.isoformat() if pr.started_at else None,
                    "completed_at": pr.completed_at.isoformat() if pr.completed_at else None,
                    "error": pr.error,
                }

    # Sort by start_time descending
    results.sort(key=lambda x: x.get("start_time") or "", reverse=True)

    return {
        "temporal_available": temporal_available,
        "total": len(results),
        "workflows": results,
    }


# ---------------------------------------------------------------------------
# POST /workflows/inbox-monitor/start
# ---------------------------------------------------------------------------
@router.post("/inbox-monitor/start")
async def start_inbox_monitor(
    check_interval_minutes: int = 15,
    tenant_id: str = Depends(_get_tenant_id_from_internal_or_user),
):
    """Start the proactive inbox monitor for the current tenant."""
    from temporalio.client import Client
    from app.workflows.inbox_monitor import InboxMonitorWorkflow
    workflow_id = f"inbox-monitor-{tenant_id}"
    interval = max(5, min(check_interval_minutes, 60)) * 60  # Clamp 5-60 min → seconds

    try:
        client = await Client.connect(settings.TEMPORAL_ADDRESS)
        handle = await client.start_workflow(
            InboxMonitorWorkflow.run,
            args=[tenant_id, interval],
            id=workflow_id,
            task_queue="servicetsunami-orchestration",
        )
        return {
            "status": "started",
            "workflow_id": workflow_id,
            "run_id": handle.result_run_id,
            "interval_minutes": check_interval_minutes,
        }
    except Exception as e:
        if "already started" in str(e).lower() or "already running" in str(e).lower():
            return {"status": "already_running", "workflow_id": workflow_id}
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# POST /workflows/inbox-monitor/stop
# ---------------------------------------------------------------------------
@router.post("/inbox-monitor/stop")
async def stop_inbox_monitor(
    tenant_id: str = Depends(_get_tenant_id_from_internal_or_user),
):
    """Stop the proactive inbox monitor for the current tenant."""
    from temporalio.client import Client
    workflow_id = f"inbox-monitor-{tenant_id}"

    try:
        client = await Client.connect(settings.TEMPORAL_ADDRESS)
        handle = client.get_workflow_handle(workflow_id)
        await handle.cancel()
        return {"status": "stopped", "workflow_id": workflow_id}
    except Exception as e:
        if "not found" in str(e).lower():
            return {"status": "not_running", "workflow_id": workflow_id}
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# GET /workflows/inbox-monitor/status
# ---------------------------------------------------------------------------
@router.get("/inbox-monitor/status")
async def inbox_monitor_status(
    tenant_id: str = Depends(_get_tenant_id_from_internal_or_user),
):
    """Check if the inbox monitor is running for the current tenant."""
    workflow_id = f"inbox-monitor-{tenant_id}"

    try:
        client = await _get_temporal_client()
        handle = client.get_workflow_handle(workflow_id)
        desc = await handle.describe()
        status = desc.status.name if desc.status else None
        return {
            "running": status == "RUNNING",
            "workflow_id": workflow_id,
            "status": status,
            "start_time": desc.start_time.isoformat() if desc.start_time else None,
        }
    except Exception:
        return {"running": False, "workflow_id": workflow_id, "status": None}


# ---------------------------------------------------------------------------
# GET /workflows/{workflow_id} — Describe single workflow
# ---------------------------------------------------------------------------
@router.get("/{workflow_id}")
async def get_workflow(
    workflow_id: str,
    current_user: User = Depends(get_current_user),
):
    """Get detailed description of a Temporal workflow."""
    try:
        client = await _get_temporal_client()
        handle = client.get_workflow_handle(workflow_id=workflow_id)
        description = await handle.describe()
        info = description.workflow_execution_info

        return {
            "workflow_id": info.id,
            "run_id": info.run_id,
            "type": info.workflow_type,
            "status": info.status.name if info.status else None,
            "start_time": info.start_time.isoformat() if info.start_time else None,
            "close_time": info.close_time.isoformat() if info.close_time else None,
            "execution_time": info.execution_time.isoformat() if info.execution_time else None,
            "history_length": info.history_length,
            "memo": dict(description.memo) if description.memo else {},
            "task_queue": info.task_queue,
        }
    except (TemporalNotConfiguredError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail=f"Temporal unavailable: {exc}")
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"Workflow not found: {exc}")


# ---------------------------------------------------------------------------
# GET /workflows/{workflow_id}/history — Workflow event history
# ---------------------------------------------------------------------------
@router.get("/{workflow_id}/history")
async def get_workflow_history(
    workflow_id: str,
    current_user: User = Depends(get_current_user),
):
    """Fetch and parse Temporal workflow history into an audit-friendly event list."""
    try:
        client = await _get_temporal_client()
        handle = client.get_workflow_handle(workflow_id=workflow_id)
        history = await handle.fetch_history()
    except (TemporalNotConfiguredError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail=f"Temporal unavailable: {exc}")
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"Workflow not found: {exc}")

    events = []
    scheduled_activities: dict = {}  # event_id -> {name, scheduled_time}

    for event in history.events:
        event_type = event.event_type.name if event.event_type else "UNKNOWN"
        event_time = event.event_time.isoformat() if event.event_time else None

        # Workflow lifecycle events
        if event_type == "EVENT_TYPE_WORKFLOW_EXECUTION_STARTED":
            attrs = event.workflow_execution_started_event_attributes
            events.append({
                "event_type": "workflow_started",
                "timestamp": event_time,
                "activity_name": None,
                "details": {
                    "workflow_type": attrs.workflow_type.name if attrs and attrs.workflow_type else None,
                    "task_queue": attrs.task_queue.name if attrs and attrs.task_queue else None,
                },
                "duration_ms": None,
            })
        elif event_type == "EVENT_TYPE_WORKFLOW_EXECUTION_COMPLETED":
            events.append({
                "event_type": "workflow_completed",
                "timestamp": event_time,
                "activity_name": None,
                "details": None,
                "duration_ms": None,
            })
        elif event_type == "EVENT_TYPE_WORKFLOW_EXECUTION_FAILED":
            attrs = event.workflow_execution_failed_event_attributes
            failure_msg = None
            if attrs and attrs.failure:
                failure_msg = attrs.failure.message
            events.append({
                "event_type": "workflow_failed",
                "timestamp": event_time,
                "activity_name": None,
                "details": {"error": failure_msg},
                "duration_ms": None,
            })
        elif event_type == "EVENT_TYPE_WORKFLOW_EXECUTION_TIMED_OUT":
            events.append({
                "event_type": "workflow_timed_out",
                "timestamp": event_time,
                "activity_name": None,
                "details": None,
                "duration_ms": None,
            })

        # Activity lifecycle events
        elif event_type == "EVENT_TYPE_ACTIVITY_TASK_SCHEDULED":
            attrs = event.activity_task_scheduled_event_attributes
            activity_name = attrs.activity_type.name if attrs and attrs.activity_type else "unknown"
            scheduled_activities[event.event_id] = {
                "name": activity_name,
                "scheduled_time": event.event_time,
            }
            events.append({
                "event_type": "activity_scheduled",
                "timestamp": event_time,
                "activity_name": activity_name,
                "details": None,
                "duration_ms": None,
            })
        elif event_type == "EVENT_TYPE_ACTIVITY_TASK_STARTED":
            attrs = event.activity_task_started_event_attributes
            sched_id = attrs.scheduled_event_id if attrs else None
            sched = scheduled_activities.get(sched_id, {})
            events.append({
                "event_type": "activity_started",
                "timestamp": event_time,
                "activity_name": sched.get("name", "unknown"),
                "details": None,
                "duration_ms": None,
            })
        elif event_type == "EVENT_TYPE_ACTIVITY_TASK_COMPLETED":
            attrs = event.activity_task_completed_event_attributes
            sched_id = attrs.scheduled_event_id if attrs else None
            sched = scheduled_activities.get(sched_id, {})
            duration_ms = None
            if sched.get("scheduled_time") and event.event_time:
                delta = event.event_time - sched["scheduled_time"]
                duration_ms = int(delta.total_seconds() * 1000)
            events.append({
                "event_type": "activity_completed",
                "timestamp": event_time,
                "activity_name": sched.get("name", "unknown"),
                "details": None,
                "duration_ms": duration_ms,
            })
        elif event_type == "EVENT_TYPE_ACTIVITY_TASK_FAILED":
            attrs = event.activity_task_failed_event_attributes
            sched_id = attrs.scheduled_event_id if attrs else None
            sched = scheduled_activities.get(sched_id, {})
            failure_msg = None
            if attrs and attrs.failure:
                failure_msg = attrs.failure.message
            duration_ms = None
            if sched.get("scheduled_time") and event.event_time:
                delta = event.event_time - sched["scheduled_time"]
                duration_ms = int(delta.total_seconds() * 1000)
            events.append({
                "event_type": "activity_failed",
                "timestamp": event_time,
                "activity_name": sched.get("name", "unknown"),
                "details": {"error": failure_msg},
                "duration_ms": duration_ms,
            })
        elif event_type == "EVENT_TYPE_ACTIVITY_TASK_TIMED_OUT":
            attrs = event.activity_task_timed_out_event_attributes
            sched_id = attrs.scheduled_event_id if attrs else None
            sched = scheduled_activities.get(sched_id, {})
            events.append({
                "event_type": "activity_timed_out",
                "timestamp": event_time,
                "activity_name": sched.get("name", "unknown"),
                "details": None,
                "duration_ms": None,
            })

    return {
        "workflow_id": workflow_id,
        "total_events": len(events),
        "events": events,
    }
