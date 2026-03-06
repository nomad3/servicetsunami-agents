"""API routes for agent memories"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
import uuid

from app.api.deps import get_db, get_current_user
from app.models.user import User
from app.models.agent_memory import AgentMemory
from app.schemas.agent_memory import AgentMemoryInDB, AgentMemoryCreate, AgentMemoryUpdate
from app.schemas.memory_activity import MemoryActivityInDB
from app.services import memories as service

router = APIRouter()


@router.post("", response_model=AgentMemoryInDB, status_code=201)
def create_memory(
    memory_in: AgentMemoryCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Store a new memory for an agent."""
    try:
        return service.create_memory(db, memory_in, current_user.tenant_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/agent/{agent_id}", response_model=List[AgentMemoryInDB])
def get_agent_memories(
    agent_id: uuid.UUID,
    memory_type: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get all memories for an agent."""
    return service.get_agent_memories(
        db, agent_id, current_user.tenant_id, memory_type, skip, limit
    )


# ── Tenant-scoped memory views (for Memory page) ──────────────────


@router.get("/tenant", response_model=List[AgentMemoryInDB])
def get_tenant_memories(
    memory_type: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get all memories for the current tenant, optionally filtered by type."""
    query = db.query(AgentMemory).filter(
        AgentMemory.tenant_id == current_user.tenant_id
    )
    if memory_type:
        query = query.filter(AgentMemory.memory_type == memory_type)
    return query.order_by(AgentMemory.created_at.desc()).offset(skip).limit(limit).all()


@router.get("/activity", response_model=List[MemoryActivityInDB])
def get_activity_feed(
    source: Optional[str] = None,
    event_type: Optional[str] = None,
    skip: int = 0,
    limit: int = 20,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get the memory activity feed for the current tenant."""
    from app.services.memory_activity import get_recent_activity
    return get_recent_activity(
        db, current_user.tenant_id,
        limit=limit, source=source, event_type=event_type, skip=skip,
    )


@router.get("/stats")
def get_memory_stats_endpoint(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get memory overview stats for the current tenant."""
    from app.services.memory_activity import get_memory_stats
    return get_memory_stats(db, current_user.tenant_id)


@router.get("/{memory_id}", response_model=AgentMemoryInDB)
def get_memory(
    memory_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Recall a specific memory."""
    memory = service.get_memory(db, memory_id, current_user.tenant_id)
    if not memory:
        raise HTTPException(status_code=404, detail="Memory not found")
    return memory


@router.patch("/{memory_id}", response_model=AgentMemoryInDB)
def update_memory(
    memory_id: uuid.UUID,
    memory_in: AgentMemoryUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Update a memory's importance or content."""
    memory = service.update_memory(db, memory_id, current_user.tenant_id, memory_in)
    if not memory:
        raise HTTPException(status_code=404, detail="Memory not found")
    return memory


@router.delete("/{memory_id}", status_code=204)
def delete_memory(
    memory_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Forget a memory."""
    if not service.delete_memory(db, memory_id, current_user.tenant_id):
        raise HTTPException(status_code=404, detail="Memory not found")
