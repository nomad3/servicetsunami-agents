"""Service layer for STP network nodes."""
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.network_node import NetworkNode


def register_node(
    db: Session,
    tenant_id: uuid.UUID,
    *,
    node_id: Optional[uuid.UUID] = None,
    name: str,
    tailscale_ip: Optional[str] = None,
    capabilities: Optional[Dict[str, Any]] = None,
    max_concurrent_tasks: int = 3,
    current_load: float = 0.0,
    pricing_tier: str = "standard",
    status: str = "online",
) -> NetworkNode:
    """Create or refresh a node registration."""
    node = None
    if node_id:
        node = get_node(db, tenant_id, node_id)
    if node is None and tailscale_ip:
        node = db.query(NetworkNode).filter(
            NetworkNode.tenant_id == tenant_id,
            NetworkNode.tailscale_ip == tailscale_ip,
        ).first()
    if node is None:
        node = NetworkNode(
            id=node_id or uuid.uuid4(),
            tenant_id=tenant_id,
            name=name,
        )
        db.add(node)

    node.name = name
    node.tailscale_ip = tailscale_ip
    node.capabilities = capabilities
    node.max_concurrent_tasks = max_concurrent_tasks
    node.current_load = current_load
    node.pricing_tier = pricing_tier
    node.status = status
    node.last_heartbeat = datetime.utcnow()
    db.commit()
    db.refresh(node)
    return node


def list_nodes(
    db: Session,
    tenant_id: uuid.UUID,
    *,
    status: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
) -> List[NetworkNode]:
    query = db.query(NetworkNode).filter(NetworkNode.tenant_id == tenant_id)
    if status:
        query = query.filter(NetworkNode.status == status)
    return query.order_by(NetworkNode.last_heartbeat.desc()).offset(skip).limit(limit).all()


def get_node(db: Session, tenant_id: uuid.UUID, node_id: uuid.UUID) -> Optional[NetworkNode]:
    return db.query(NetworkNode).filter(
        NetworkNode.id == node_id,
        NetworkNode.tenant_id == tenant_id,
    ).first()


def heartbeat_node(
    db: Session,
    tenant_id: uuid.UUID,
    node_id: uuid.UUID,
    updates: Dict[str, Any],
) -> Optional[NetworkNode]:
    node = get_node(db, tenant_id, node_id)
    if not node:
        return None
    for key, value in updates.items():
        if value is not None and hasattr(node, key):
            setattr(node, key, value)
    node.last_heartbeat = datetime.utcnow()
    node.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(node)
    return node


def delete_node(db: Session, tenant_id: uuid.UUID, node_id: uuid.UUID) -> bool:
    node = get_node(db, tenant_id, node_id)
    if not node:
        return False
    db.delete(node)
    db.commit()
    return True
