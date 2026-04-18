import logging
import uuid
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app import schemas
from app.api import deps
from app.core.config import settings
from app.models.agent import Agent
from app.models.agent_audit_log import AgentAuditLog
from app.models.agent_integration_config import AgentIntegrationConfig
from app.models.agent_performance_snapshot import AgentPerformanceSnapshot
from app.models.agent_version import AgentVersion
from app.models.integration_config import IntegrationConfig
from app.models.user import User
from app.schemas.audit import AuditLogEntry
from app.services import agents as agent_service
from app.services.agent_importer import parse_agent_definition
from app.services.agent_registry import registry

logger = logging.getLogger(__name__)

router = APIRouter()

# Status transition order for promote
_PROMOTE_TRANSITIONS = {
    "draft": "staging",
    "staging": "production",
}

@router.get("", response_model=List[schemas.agent.Agent])
def read_agents(
    db: Session = Depends(deps.get_db),
    skip: int = 0,
    limit: int = 100,
    current_user: User = Depends(deps.get_current_active_user),
):
    """
    Retrieve agents for the current tenant.
    """
    agents = agent_service.get_agents_by_tenant(
        db, tenant_id=current_user.tenant_id, skip=skip, limit=limit
    )
    return agents


@router.post("", response_model=schemas.agent.Agent, status_code=status.HTTP_201_CREATED)
def create_agent(
    *,
    db: Session = Depends(deps.get_db),
    item_in: schemas.agent.AgentCreate,
    current_user: User = Depends(deps.get_current_active_user),
):
    """
    Create new agent for the current tenant.
    """
    item = agent_service.create_tenant_agent(db=db, item_in=item_in, tenant_id=current_user.tenant_id)
    return item

@router.get("/discover")
def discover_agents(
    capability: str,
    max_latency_ms: Optional[int] = None,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    agents = registry.find_by_capability(capability, current_user.tenant_id, db)
    return [
        {
            "id": str(a.id),
            "name": a.name,
            "description": a.description,
            "status": a.status,
            "capabilities": a.capabilities,
        }
        for a in agents
    ]


@router.post("/import", response_model=schemas.agent.Agent, status_code=status.HTTP_201_CREATED)
def import_agent(
    *,
    db: Session = Depends(deps.get_db),
    body: schemas.agent.AgentImportRequest,
    current_user: User = Depends(deps.get_current_active_user),
):
    parsed = parse_agent_definition(body.content, body.filename)
    item_in = schemas.agent.AgentCreate(
        name=parsed.get("name", "Imported Agent"),
        description=parsed.get("description"),
        persona_prompt=parsed.get("persona_prompt"),
        capabilities=parsed.get("capabilities") or [],
        config=parsed.get("config"),
        status="draft",
    )
    item = agent_service.create_tenant_agent(db=db, item_in=item_in, tenant_id=current_user.tenant_id)
    return item


@router.get("/{agent_id}", response_model=schemas.agent.Agent)
def read_agent_by_id(
    agent_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """
    Retrieve a specific agent by ID for the current tenant.
    """
    agent = agent_service.get_agent(db, agent_id=agent_id)
    if not agent or str(agent.tenant_id) != str(current_user.tenant_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    return agent

@router.put("/{agent_id}", response_model=schemas.agent.Agent)
def update_agent(
    *,
    db: Session = Depends(deps.get_db),
    agent_id: uuid.UUID,
    item_in: schemas.agent.AgentCreate,
    current_user: User = Depends(deps.get_current_active_user),
):
    """
    Update an existing agent for the current tenant.
    """
    agent = agent_service.get_agent(db, agent_id=agent_id)
    if not agent or str(agent.tenant_id) != str(current_user.tenant_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    item = agent_service.update_agent(db=db, db_obj=agent, obj_in=item_in)
    return item

@router.delete("/{agent_id}", status_code=status.HTTP_200_OK)
def delete_agent(
    *,
    db: Session = Depends(deps.get_db),
    agent_id: uuid.UUID,
    current_user: User = Depends(deps.get_current_active_user),
):
    """
    Delete an agent for the current tenant.
    """
    agent = agent_service.get_agent(db, agent_id=agent_id)
    if not agent or str(agent.tenant_id) != str(current_user.tenant_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    agent_service.delete_agent(db=db, agent_id=agent_id)
    return {"deleted": True}


@router.post("/{agent_id}/promote", response_model=schemas.agent.Agent)
def promote_agent(
    *,
    db: Session = Depends(deps.get_db),
    body: schemas.agent.AgentPromoteRequest,
    current_user: User = Depends(deps.get_current_active_user),
    agent: Agent = Depends(deps.require_agent_permission("promote")),
):
    next_status = _PROMOTE_TRANSITIONS.get(agent.status)
    if next_status is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot promote agent with status '{agent.status}'",
        )

    # Snapshot current config before transitioning
    config_snapshot = {
        "name": agent.name,
        "description": agent.description,
        "status": agent.status,
        "persona_prompt": agent.persona_prompt,
        "capabilities": agent.capabilities,
        "tool_groups": agent.tool_groups,
        "config": agent.config,
    }

    version_record = AgentVersion(
        agent_id=agent.id,
        tenant_id=agent.tenant_id,
        version=agent.version,
        config_snapshot=config_snapshot,
        promoted_by=current_user.id,
        promoted_at=datetime.utcnow(),
        status=next_status,
        notes=body.notes,
    )
    db.add(version_record)

    agent.status = next_status
    agent.version = agent.version + 1
    db.commit()
    db.refresh(agent)
    return agent


@router.post("/{agent_id}/deprecate", response_model=schemas.agent.Agent)
def deprecate_agent(
    *,
    db: Session = Depends(deps.get_db),
    body: schemas.agent.AgentDeprecateRequest,
    current_user: User = Depends(deps.get_current_active_user),
    agent: Agent = Depends(deps.require_agent_permission("deprecate")),
):
    if agent.status == "deprecated":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Agent is already deprecated",
        )

    if body.successor_agent_id:
        successor = db.query(Agent).filter(Agent.id == body.successor_agent_id).first()
        if not successor or str(successor.tenant_id) != str(agent.tenant_id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Successor agent not found",
            )
        agent.successor_agent_id = successor.id

    agent.status = "deprecated"
    db.commit()
    db.refresh(agent)
    return agent


@router.get("/{agent_id}/versions", response_model=List[schemas.agent.AgentVersionResponse])
def list_agent_versions(
    agent_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if not agent or str(agent.tenant_id) != str(current_user.tenant_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")

    versions = (
        db.query(AgentVersion)
        .filter(AgentVersion.agent_id == agent_id)
        .order_by(AgentVersion.version.desc())
        .all()
    )
    return versions


@router.post("/{agent_id}/versions/{version_num}/rollback", response_model=schemas.agent.Agent)
def rollback_agent_version(
    version_num: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
    agent: Agent = Depends(deps.require_agent_permission("promote")),
):
    target = (
        db.query(AgentVersion)
        .filter(AgentVersion.agent_id == agent.id, AgentVersion.version == version_num)
        .first()
    )
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Version not found")

    snapshot = target.config_snapshot
    agent.name = snapshot.get("name", agent.name)
    agent.description = snapshot.get("description", agent.description)
    agent.status = "production"
    for field in ("persona_prompt", "capabilities", "tool_groups", "config"):
        if field in snapshot and hasattr(agent, field):
            setattr(agent, field, snapshot[field])

    live_version = (
        db.query(AgentVersion)
        .filter(AgentVersion.agent_id == agent.id, AgentVersion.version == agent.version)
        .first()
    )
    if live_version:
        live_version.status = "rolled_back"

    new_version_num = agent.version + 1
    rollback_record = AgentVersion(
        agent_id=agent.id,
        tenant_id=agent.tenant_id,
        version=new_version_num,
        config_snapshot=snapshot,
        status="production",
        notes=f"Rollback to version {version_num}",
        promoted_by=current_user.id,
        promoted_at=datetime.utcnow(),
    )
    db.add(rollback_record)
    agent.version = new_version_num
    db.commit()
    db.refresh(agent)
    return agent


@router.get("/{agent_id}/versions/{version_num}/diff")
def diff_agent_version(
    agent_id: uuid.UUID,
    version_num: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if not agent or str(agent.tenant_id) != str(current_user.tenant_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")

    target = (
        db.query(AgentVersion)
        .filter(AgentVersion.agent_id == agent_id, AgentVersion.version == version_num)
        .first()
    )
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Version not found")

    snapshot = target.config_snapshot
    current_values = {
        "name": agent.name,
        "description": agent.description,
        "status": agent.status,
        "persona_prompt": agent.persona_prompt,
        "capabilities": agent.capabilities,
        "tool_groups": agent.tool_groups,
        "config": agent.config,
    }

    changed = []
    unchanged = []
    for field, old_val in snapshot.items():
        new_val = current_values.get(field)
        if old_val != new_val:
            changed.append({"field": field, "old": old_val, "new": new_val})
        else:
            unchanged.append(field)

    return {"changed": changed, "unchanged": unchanged}


@router.post("/{agent_id}/heartbeat")
def agent_heartbeat(
    agent_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if not agent or str(agent.tenant_id) != str(current_user.tenant_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")

    try:
        import redis as redis_lib
        r = redis_lib.from_url(settings.REDIS_URL)
        r.set(f"agent:available:{agent_id}", "1", ex=90)
    except Exception as exc:
        logger.warning("Heartbeat Redis write failed for agent %s: %s", agent_id, exc)

    # Touch updated_at if the column exists (Agent model may not have it)
    if hasattr(agent, "updated_at"):
        agent.updated_at = datetime.utcnow()
        db.commit()

    return {"status": "ok", "agent_id": str(agent_id)}


@router.get("/{agent_id}/audit-log", response_model=List[AuditLogEntry])
def get_agent_audit_log(
    agent_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
    from_dt: Optional[datetime] = None,
    to_dt: Optional[datetime] = None,
    status: Optional[str] = None,
    limit: int = 50,
):
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if not agent or str(agent.tenant_id) != str(current_user.tenant_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")

    q = (
        db.query(AgentAuditLog)
        .filter(
            AgentAuditLog.agent_id == agent_id,
            AgentAuditLog.tenant_id == current_user.tenant_id,
        )
    )
    if from_dt:
        q = q.filter(AgentAuditLog.created_at >= from_dt)
    if to_dt:
        q = q.filter(AgentAuditLog.created_at <= to_dt)
    if status:
        q = q.filter(AgentAuditLog.status == status)

    return q.order_by(AgentAuditLog.created_at.desc()).limit(limit).all()


@router.get("/{agent_id}/performance")
def get_agent_performance(
    agent_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
    window: str = "24h",
):
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if not agent or str(agent.tenant_id) != str(current_user.tenant_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")

    if window == "7d":
        start_dt = datetime.utcnow() - timedelta(days=7)
    elif window == "30d":
        start_dt = datetime.utcnow() - timedelta(days=30)
    else:
        start_dt = datetime.utcnow() - timedelta(hours=24)

    snapshots = (
        db.query(AgentPerformanceSnapshot)
        .filter(
            AgentPerformanceSnapshot.agent_id == agent_id,
            AgentPerformanceSnapshot.window_start >= start_dt,
        )
        .all()
    )

    invocation_count = sum(s.invocation_count for s in snapshots)
    success_count = sum(s.success_count for s in snapshots)
    error_count = sum(s.error_count for s in snapshots)
    timeout_count = sum(s.timeout_count for s in snapshots)
    success_rate = (success_count / invocation_count) if invocation_count > 0 else 0.0
    total_tokens = sum(s.total_tokens for s in snapshots)
    total_cost_usd = sum(s.total_cost_usd for s in snapshots)

    p50_vals = [s.latency_p50_ms for s in snapshots if s.latency_p50_ms is not None]
    p95_vals = [s.latency_p95_ms for s in snapshots if s.latency_p95_ms is not None]
    p99_vals = [s.latency_p99_ms for s in snapshots if s.latency_p99_ms is not None]
    qs_vals = [s.avg_quality_score for s in snapshots if s.avg_quality_score is not None]

    latency_p50_ms = (sum(p50_vals) / len(p50_vals)) if p50_vals else None
    latency_p95_ms = (sum(p95_vals) / len(p95_vals)) if p95_vals else None
    latency_p99_ms = (sum(p99_vals) / len(p99_vals)) if p99_vals else None
    avg_quality_score = (sum(qs_vals) / len(qs_vals)) if qs_vals else None

    return {
        "agent_id": str(agent_id),
        "window": window,
        "invocation_count": invocation_count,
        "success_rate": success_rate,
        "latency_p50_ms": latency_p50_ms,
        "latency_p95_ms": latency_p95_ms,
        "latency_p99_ms": latency_p99_ms,
        "avg_quality_score": avg_quality_score,
        "total_tokens": total_tokens,
        "total_cost_usd": total_cost_usd,
        "snapshot_count": len(snapshots),
    }


# --- Per-agent integration assignment ---

@router.get("/{agent_id}/integrations")
def list_agent_integrations(
    agent_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if not agent or str(agent.tenant_id) != str(current_user.tenant_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    rows = (
        db.query(AgentIntegrationConfig)
        .filter(
            AgentIntegrationConfig.agent_id == agent_id,
            AgentIntegrationConfig.tenant_id == current_user.tenant_id,
        )
        .all()
    )
    return [str(r.integration_config_id) for r in rows]


@router.post("/{agent_id}/integrations")
def assign_integration(
    agent_id: uuid.UUID,
    body: dict,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if not agent or str(agent.tenant_id) != str(current_user.tenant_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")

    cfg_id = body.get("integration_config_id")
    if not cfg_id:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="integration_config_id required")

    try:
        cfg_id = uuid.UUID(str(cfg_id))
    except ValueError:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid integration_config_id")

    cfg = (
        db.query(IntegrationConfig)
        .filter(IntegrationConfig.id == cfg_id, IntegrationConfig.tenant_id == current_user.tenant_id)
        .first()
    )
    if not cfg:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Integration config not found")

    # Idempotent: return 200 if already assigned
    existing = (
        db.query(AgentIntegrationConfig)
        .filter(
            AgentIntegrationConfig.agent_id == agent_id,
            AgentIntegrationConfig.integration_config_id == cfg_id,
        )
        .first()
    )
    if not existing:
        row = AgentIntegrationConfig(
            agent_id=agent_id,
            integration_config_id=cfg_id,
            tenant_id=current_user.tenant_id,
        )
        db.add(row)
        db.commit()

    return {"agent_id": str(agent_id), "integration_config_id": str(cfg_id)}


@router.delete("/{agent_id}/integrations/{cfg_id}")
def remove_integration(
    agent_id: uuid.UUID,
    cfg_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if not agent or str(agent.tenant_id) != str(current_user.tenant_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")

    row = (
        db.query(AgentIntegrationConfig)
        .filter(
            AgentIntegrationConfig.agent_id == agent_id,
            AgentIntegrationConfig.integration_config_id == cfg_id,
            AgentIntegrationConfig.tenant_id == current_user.tenant_id,
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assignment not found")

    db.delete(row)
    db.commit()
    return {"deleted": True}
