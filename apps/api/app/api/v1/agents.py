import logging
import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app import schemas
from app.api import deps
from app.core.config import settings
from app.models.agent import Agent
from app.models.agent_audit_log import AgentAuditLog
from app.models.agent_version import AgentVersion
from app.models.user import User
from app.schemas.audit import AuditLogEntry
from app.services import agents as agent_service

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
