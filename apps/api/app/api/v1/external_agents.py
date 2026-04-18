import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.api import deps
from app.models.external_agent import ExternalAgent
from app.models.user import User
from app.schemas.external_agent import ExternalAgentCreate, ExternalAgentInDB, ExternalAgentUpdate

router = APIRouter()


def _get_agent_or_404(db: Session, agent_id: uuid.UUID, tenant_id: uuid.UUID) -> ExternalAgent:
    agent = db.query(ExternalAgent).filter(ExternalAgent.id == agent_id).first()
    if not agent or str(agent.tenant_id) != str(tenant_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="External agent not found")
    return agent


@router.get("", response_model=List[ExternalAgentInDB])
def list_external_agents(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    return (
        db.query(ExternalAgent)
        .filter(ExternalAgent.tenant_id == current_user.tenant_id)
        .all()
    )


@router.post("", response_model=ExternalAgentInDB, status_code=status.HTTP_201_CREATED)
def register_external_agent(
    body: ExternalAgentCreate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    agent = ExternalAgent(
        tenant_id=current_user.tenant_id,
        name=body.name,
        description=body.description,
        avatar_url=body.avatar_url,
        protocol=body.protocol,
        endpoint_url=body.endpoint_url,
        auth_type=body.auth_type,
        credential_id=body.credential_id,
        capabilities=body.capabilities,
        health_check_path=body.health_check_path,
        metadata_=body.metadata or {},
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)
    return agent


@router.get("/{agent_id}", response_model=ExternalAgentInDB)
def get_external_agent(
    agent_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    return _get_agent_or_404(db, agent_id, current_user.tenant_id)


@router.put("/{agent_id}", response_model=ExternalAgentInDB)
def update_external_agent(
    agent_id: uuid.UUID,
    body: ExternalAgentUpdate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    agent = _get_agent_or_404(db, agent_id, current_user.tenant_id)
    update_data = body.model_dump(exclude_unset=True)
    # metadata field in schema maps to metadata_ column
    if "metadata" in update_data:
        agent.metadata_ = update_data.pop("metadata")
    for field, value in update_data.items():
        setattr(agent, field, value)
    agent.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(agent)
    return agent


@router.delete("/{agent_id}", status_code=status.HTTP_200_OK)
def delete_external_agent(
    agent_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    agent = _get_agent_or_404(db, agent_id, current_user.tenant_id)
    db.delete(agent)
    db.commit()
    return {"deleted": True}


@router.post("/{agent_id}/health-check")
def health_check(
    agent_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    agent = _get_agent_or_404(db, agent_id, current_user.tenant_id)
    try:
        import httpx
        url = agent.endpoint_url.rstrip("/") + agent.health_check_path
        resp = httpx.get(url, timeout=5.0)
        if resp.status_code == 200:
            agent.status = "online"
            agent.last_seen_at = datetime.utcnow()
        else:
            agent.status = "offline"
    except Exception:
        agent.status = "offline"
    db.commit()
    db.refresh(agent)
    return {"status": agent.status, "last_seen_at": str(agent.last_seen_at)}


@router.post("/{agent_id}/test-task")
def test_task(
    agent_id: uuid.UUID,
    body: Dict[str, Any],
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    agent = _get_agent_or_404(db, agent_id, current_user.tenant_id)
    return {
        "result": "Test task received. External agent dispatch adapter not yet connected.",
        "agent_id": str(agent_id),
        "protocol": agent.protocol,
    }


@router.post("/callback/{agent_id}")
def webhook_callback(
    agent_id: uuid.UUID,
    request: Request,
    db: Session = Depends(deps.get_db),
):
    # Verify the agent exists; full adapter implementation is in Task 18
    agent = db.query(ExternalAgent).filter(ExternalAgent.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="External agent not found")
    return {"received": True}
