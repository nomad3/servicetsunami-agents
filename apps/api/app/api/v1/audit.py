import csv
import io
from datetime import datetime
from typing import List, Optional
import uuid

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api import deps
from app.models.agent_audit_log import AgentAuditLog
from app.models.user import User
from app.schemas.audit import AuditLogEntry

router = APIRouter()


@router.get("/agents", response_model=List[AuditLogEntry])
def list_agent_audit_logs(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.require_superuser),
    from_dt: Optional[datetime] = None,
    to_dt: Optional[datetime] = None,
    agent_id: Optional[uuid.UUID] = None,
    invoked_by: Optional[uuid.UUID] = None,
    limit: int = 100,
):
    q = db.query(AgentAuditLog).filter(AgentAuditLog.tenant_id == current_user.tenant_id)

    if agent_id:
        q = q.filter(AgentAuditLog.agent_id == agent_id)
    if invoked_by:
        q = q.filter(AgentAuditLog.invoked_by_user_id == invoked_by)
    if from_dt:
        q = q.filter(AgentAuditLog.created_at >= from_dt)
    if to_dt:
        q = q.filter(AgentAuditLog.created_at <= to_dt)

    return q.order_by(AgentAuditLog.created_at.desc()).limit(limit).all()


@router.get("/agents/export")
def export_agent_audit_logs(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.require_superuser),
    from_dt: Optional[datetime] = None,
    to_dt: Optional[datetime] = None,
    format: str = "csv",
):
    q = db.query(AgentAuditLog).filter(AgentAuditLog.tenant_id == current_user.tenant_id)

    if from_dt:
        q = q.filter(AgentAuditLog.created_at >= from_dt)
    if to_dt:
        q = q.filter(AgentAuditLog.created_at <= to_dt)

    rows = q.order_by(AgentAuditLog.created_at.desc()).limit(50_000).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "id", "created_at", "agent_id", "invoked_by_user_id", "invocation_type",
        "input_summary", "output_summary", "input_tokens", "output_tokens",
        "cost_usd", "latency_ms", "status", "error_message", "quality_score",
    ])
    for r in rows:
        writer.writerow([
            r.id, r.created_at, r.agent_id, r.invoked_by_user_id, r.invocation_type,
            r.input_summary, r.output_summary, r.input_tokens, r.output_tokens,
            r.cost_usd, r.latency_ms, r.status, r.error_message, r.quality_score,
        ])

    output.seek(0)

    def _stream():
        yield output.read()

    return StreamingResponse(
        _stream(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=audit_log.csv"},
    )
