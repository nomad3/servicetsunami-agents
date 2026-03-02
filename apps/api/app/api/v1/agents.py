from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app import schemas
from app.api import deps
from app.services import agents as agent_service
from app.models.user import User
import uuid

router = APIRouter()

@router.get("/", response_model=List[schemas.agent.Agent])
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


@router.post("/", response_model=schemas.agent.Agent, status_code=status.HTTP_201_CREATED)
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
