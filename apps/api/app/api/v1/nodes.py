"""Network node API routes for STP registry operations."""
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.api import deps
from app.core.config import settings
from app.models.user import User
from app.schemas.network_node import NetworkNodeHeartbeat, NetworkNodeInDB, NetworkNodeRegister
from app.services import network_nodes as svc

router = APIRouter()


def _verify_internal_key(x_internal_key: Optional[str] = Header(None, alias="X-Internal-Key")):
    if x_internal_key not in (settings.API_INTERNAL_KEY, settings.MCP_API_KEY):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid internal key")


@router.post("/internal/register", response_model=NetworkNodeInDB, status_code=201)
def register_node_internal(
    item_in: NetworkNodeRegister,
    tenant_id: str = "",
    db: Session = Depends(deps.get_db),
    _auth=Depends(_verify_internal_key),
):
    """Register or refresh a node (internal service call)."""
    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id query param required")
    return svc.register_node(
        db,
        tenant_id=tenant_id,
        node_id=item_in.node_id,
        name=item_in.name,
        tailscale_ip=item_in.tailscale_ip,
        capabilities=item_in.capabilities,
        max_concurrent_tasks=item_in.max_concurrent_tasks,
        current_load=item_in.current_load,
        pricing_tier=item_in.pricing_tier,
        status=item_in.status,
    )


@router.post("/internal/{node_id}/heartbeat", response_model=NetworkNodeInDB)
def heartbeat_node_internal(
    node_id: UUID,
    item_in: NetworkNodeHeartbeat,
    tenant_id: str = "",
    db: Session = Depends(deps.get_db),
    _auth=Depends(_verify_internal_key),
):
    """Update node heartbeat state (internal service call)."""
    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id query param required")
    node = svc.heartbeat_node(db, tenant_id, node_id, item_in.model_dump(exclude_unset=True))
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    return node


@router.delete("/internal/{node_id}")
def delete_node_internal(
    node_id: UUID,
    tenant_id: str = "",
    db: Session = Depends(deps.get_db),
    _auth=Depends(_verify_internal_key),
):
    """Deregister a node (internal service call)."""
    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id query param required")
    if not svc.delete_node(db, tenant_id, node_id):
        raise HTTPException(status_code=404, detail="Node not found")
    return {"status": "deleted", "node_id": str(node_id)}


@router.get("", response_model=List[NetworkNodeInDB])
def list_network_nodes(
    status: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """List nodes registered for the current tenant."""
    return svc.list_nodes(db, current_user.tenant_id, status=status, skip=skip, limit=limit)


@router.get("/{node_id}", response_model=NetworkNodeInDB)
def get_network_node(
    node_id: UUID,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Get a single node by ID."""
    node = svc.get_node(db, current_user.tenant_id, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    return node


@router.delete("/{node_id}")
def delete_network_node(
    node_id: UUID,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Delete a node owned by the current tenant."""
    if not svc.delete_node(db, current_user.tenant_id, node_id):
        raise HTTPException(status_code=404, detail="Node not found")
    return {"status": "deleted", "node_id": str(node_id)}
