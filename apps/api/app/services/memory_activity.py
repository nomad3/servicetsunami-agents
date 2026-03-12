"""Service for Luna's memory activity log.

Provides logging and querying of memory events: entity extraction,
memory creation, action triggers, recalls, etc.
"""
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
import uuid

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.memory_activity import MemoryActivity
from app.models.knowledge_entity import KnowledgeEntity
from app.models.knowledge_relation import KnowledgeRelation
from app.models.agent_memory import AgentMemory
from app.services import embedding_service

logger = logging.getLogger(__name__)


def log_activity(
    db: Session,
    tenant_id: uuid.UUID,
    event_type: str,
    description: str,
    source: Optional[str] = None,
    event_metadata: Optional[Dict[str, Any]] = None,
    entity_id: Optional[uuid.UUID] = None,
    memory_id: Optional[uuid.UUID] = None,
    workflow_run_id: Optional[str] = None,
) -> MemoryActivity:
    """Log a memory activity event."""
    activity = MemoryActivity(
        tenant_id=tenant_id,
        event_type=event_type,
        description=description,
        source=source,
        event_metadata=event_metadata,
        entity_id=entity_id,
        memory_id=memory_id,
        workflow_run_id=workflow_run_id,
    )
    db.add(activity)
    db.flush()

    # Embed activity for semantic memory recall
    try:
        embed_text = f"{event_type}: {description or ''}"
        if event_metadata:
            import json as _json
            meta_str = _json.dumps(event_metadata) if isinstance(event_metadata, dict) else str(event_metadata)
            embed_text += f" {meta_str[:500]}"
        embedding_service.embed_and_store(
            db, tenant_id, "memory_activity", str(activity.id), embed_text.strip()
        )
    except Exception:
        pass  # Don't break activity logging if embedding fails

    db.commit()
    db.refresh(activity)
    return activity


def get_recent_activity(
    db: Session,
    tenant_id: uuid.UUID,
    limit: int = 20,
    source: Optional[str] = None,
    event_type: Optional[str] = None,
    skip: int = 0,
) -> List[MemoryActivity]:
    """Get recent activity events for a tenant."""
    query = db.query(MemoryActivity).filter(
        MemoryActivity.tenant_id == tenant_id
    )
    if source:
        query = query.filter(MemoryActivity.source == source)
    if event_type:
        query = query.filter(MemoryActivity.event_type == event_type)
    return query.order_by(MemoryActivity.created_at.desc()).offset(skip).limit(limit).all()


def get_memory_stats(
    db: Session,
    tenant_id: uuid.UUID,
) -> Dict[str, int]:
    """Get memory overview stats for a tenant."""
    total_entities = db.query(func.count(KnowledgeEntity.id)).filter(
        KnowledgeEntity.tenant_id == tenant_id
    ).scalar() or 0

    total_memories = db.query(func.count(AgentMemory.id)).filter(
        AgentMemory.tenant_id == tenant_id
    ).scalar() or 0

    total_relations = db.query(func.count(KnowledgeRelation.id)).filter(
        KnowledgeRelation.tenant_id == tenant_id
    ).scalar() or 0

    # Count activities from today
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    learned_today = db.query(func.count(MemoryActivity.id)).filter(
        MemoryActivity.tenant_id == tenant_id,
        MemoryActivity.created_at >= today_start,
        MemoryActivity.event_type.in_(["entity_created", "relation_created", "memory_created"]),
    ).scalar() or 0

    # Pending actions (triggered but not completed)
    pending_actions = db.query(func.count(MemoryActivity.id)).filter(
        MemoryActivity.tenant_id == tenant_id,
        MemoryActivity.event_type == "action_triggered",
    ).scalar() or 0
    completed_actions = db.query(func.count(MemoryActivity.id)).filter(
        MemoryActivity.tenant_id == tenant_id,
        MemoryActivity.event_type.in_(["action_completed", "action_failed"]),
    ).scalar() or 0

    return {
        "total_entities": total_entities,
        "total_memories": total_memories,
        "total_relations": total_relations,
        "learned_today": learned_today,
        "pending_actions": max(0, pending_actions - completed_actions),
    }


def search_memory(db: Session, tenant_id, query: str, content_types: list = None, limit: int = 20):
    """Semantic search across memory — skills, entities, activities, chat."""
    return embedding_service.search_similar(db, str(tenant_id), content_types, query, limit)
