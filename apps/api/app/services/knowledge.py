"""Service for managing knowledge graph entities and relations"""
import json as _json
import logging
import uuid
from typing import List, Optional, Dict, Any

from sqlalchemy.orm import Session

from app.models.knowledge_entity import KnowledgeEntity
from app.models.knowledge_relation import KnowledgeRelation
from app.schemas.knowledge_entity import KnowledgeEntityCreate, KnowledgeEntityUpdate
from app.services import embedding_service
from app.core.config import settings

logger = logging.getLogger(__name__)


def _entity_embed_text(entity: KnowledgeEntity) -> str:
    """Build the text blob used for embedding an entity."""
    text = f"{entity.name} {entity.category or ''} {entity.description or ''}"
    if entity.attributes:
        props_str = _json.dumps(entity.attributes) if isinstance(entity.attributes, dict) else str(entity.attributes)
        text += f" {props_str[:500]}"
    return text.strip()


def _safe_embed_entity(db: Session, entity: KnowledgeEntity) -> None:
    """Embed an entity, silently catching errors so CRUD is never blocked."""
    try:
        embed_text = _entity_embed_text(entity)
        embedding_service.embed_and_store(
            db, entity.tenant_id, "entity", str(entity.id), embed_text,
        )
    except Exception:
        logger.exception("Failed to embed entity %s — skipping", entity.id)


# Entity operations
def create_entity(db: Session, entity_in: KnowledgeEntityCreate, tenant_id: uuid.UUID) -> KnowledgeEntity:
    """Create a knowledge entity."""
    entity = KnowledgeEntity(
        tenant_id=tenant_id,
        entity_type=entity_in.entity_type,
        category=entity_in.category,
        name=entity_in.name,
        description=entity_in.description,
        attributes=entity_in.attributes,
        confidence=entity_in.confidence or 1.0,
        source_agent_id=entity_in.source_agent_id,
        status=entity_in.status or "draft",
        collection_task_id=entity_in.collection_task_id,
        source_url=entity_in.source_url,
        enrichment_data=entity_in.enrichment_data,
    )
    db.add(entity)
    db.flush()

    # Embed entity for semantic search
    _safe_embed_entity(db, entity)

    db.commit()
    db.refresh(entity)
    return entity


def get_entity(db: Session, entity_id: uuid.UUID, tenant_id: uuid.UUID) -> Optional[KnowledgeEntity]:
    """Get entity by ID."""
    return db.query(KnowledgeEntity).filter(
        KnowledgeEntity.id == entity_id,
        KnowledgeEntity.tenant_id == tenant_id
    ).first()


def get_entities(
    db: Session,
    tenant_id: uuid.UUID,
    entity_type: str = None,
    skip: int = 0,
    limit: int = 100,
    status: str = None,
    task_id: uuid.UUID = None,
    category: str = None,
) -> List[KnowledgeEntity]:
    """List entities with optional filters."""
    query = db.query(KnowledgeEntity).filter(KnowledgeEntity.tenant_id == tenant_id)
    if entity_type:
        query = query.filter(KnowledgeEntity.entity_type == entity_type)
    if status:
        query = query.filter(KnowledgeEntity.status == status)
    if task_id:
        query = query.filter(KnowledgeEntity.collection_task_id == task_id)
    if category:
        query = query.filter(KnowledgeEntity.category == category)
    return query.order_by(KnowledgeEntity.created_at.desc()).offset(skip).limit(limit).all()


def search_entities(
    db: Session,
    tenant_id: uuid.UUID,
    name_query: str,
    entity_type: str = None,
    category: str = None,
    limit: int = 50,
) -> List[KnowledgeEntity]:
    """Search entities by name — uses vector similarity when available, ILIKE fallback."""

    # Vector search path (preferred when GOOGLE_API_KEY is configured)
    if name_query and settings.GOOGLE_API_KEY:
        try:
            results = embedding_service.search_similar(
                db, tenant_id, ["entity"], name_query, limit=limit,
            )
            if results:
                entity_ids = [r["content_id"] for r in results]
                entities = db.query(KnowledgeEntity).filter(
                    KnowledgeEntity.id.in_(entity_ids),
                    KnowledgeEntity.tenant_id == tenant_id,
                ).all()
                # Apply optional filters
                if entity_type:
                    entities = [e for e in entities if e.entity_type == entity_type]
                if category:
                    entities = [e for e in entities if e.category == category]
                # Preserve similarity ranking order
                id_order = {eid: i for i, eid in enumerate(entity_ids)}
                entities.sort(key=lambda e: id_order.get(str(e.id), 999))
                return entities
        except Exception:
            logger.exception("Vector search failed — falling back to ILIKE")

    # ILIKE fallback
    query = db.query(KnowledgeEntity).filter(
        KnowledgeEntity.tenant_id == tenant_id,
        KnowledgeEntity.name.ilike(f"%{name_query}%")
    )
    if entity_type:
        query = query.filter(KnowledgeEntity.entity_type == entity_type)
    if category:
        query = query.filter(KnowledgeEntity.category == category)
    return query.limit(limit).all()


def update_entity(
    db: Session,
    entity_id: uuid.UUID,
    tenant_id: uuid.UUID,
    entity_in: KnowledgeEntityUpdate
) -> Optional[KnowledgeEntity]:
    """Update an entity."""
    entity = get_entity(db, entity_id, tenant_id)
    if not entity:
        return None

    update_data = entity_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(entity, field, value)

    db.flush()

    # Re-embed entity with updated content
    _safe_embed_entity(db, entity)

    db.commit()
    db.refresh(entity)
    return entity


def delete_entity(db: Session, entity_id: uuid.UUID, tenant_id: uuid.UUID) -> bool:
    """Delete an entity and its relations."""
    entity = get_entity(db, entity_id, tenant_id)
    if not entity:
        return False

    # Delete related relations
    db.query(KnowledgeRelation).filter(
        (KnowledgeRelation.from_entity_id == entity_id) |
        (KnowledgeRelation.to_entity_id == entity_id)
    ).delete(synchronize_session=False)

    # Remove embedding
    try:
        embedding_service.delete_embedding(db, "entity", str(entity.id))
    except Exception:
        logger.exception("Failed to delete embedding for entity %s — continuing", entity.id)

    db.delete(entity)
    db.commit()
    return True


def bulk_create_entities(
    db: Session,
    entities_in: List[KnowledgeEntityCreate],
    tenant_id: uuid.UUID,
) -> Dict[str, Any]:
    """Bulk create entities with dedup."""
    created = []
    duplicates = 0

    for entity_in in entities_in:
        existing = db.query(KnowledgeEntity).filter(
            KnowledgeEntity.tenant_id == tenant_id,
            KnowledgeEntity.name == entity_in.name,
            KnowledgeEntity.entity_type == entity_in.entity_type,
        ).first()

        if existing:
            duplicates += 1
            continue

        entity = KnowledgeEntity(
            tenant_id=tenant_id,
            entity_type=entity_in.entity_type,
            category=entity_in.category,
            name=entity_in.name,
            attributes=entity_in.attributes,
            confidence=entity_in.confidence or 1.0,
            source_agent_id=entity_in.source_agent_id,
            status=entity_in.status or "draft",
            collection_task_id=entity_in.collection_task_id,
            source_url=entity_in.source_url,
            enrichment_data=entity_in.enrichment_data,
        )
        db.add(entity)
        db.flush()

        # Embed entity for semantic search
        _safe_embed_entity(db, entity)

        created.append(entity)

    db.commit()
    for e in created:
        db.refresh(e)

    return {"created": len(created), "updated": 0, "duplicates_skipped": duplicates, "entities": created}


def get_collection_summary(db: Session, task_id: uuid.UUID, tenant_id: uuid.UUID) -> Dict[str, Any]:
    """Get summary of entities collected by a task."""
    entities = db.query(KnowledgeEntity).filter(
        KnowledgeEntity.tenant_id == tenant_id,
        KnowledgeEntity.collection_task_id == task_id,
    ).all()

    by_status: Dict[str, int] = {}
    by_type: Dict[str, int] = {}
    by_category: Dict[str, int] = {}
    sources = set()

    for e in entities:
        by_status[e.status or "draft"] = by_status.get(e.status or "draft", 0) + 1
        by_type[e.entity_type] = by_type.get(e.entity_type, 0) + 1
        cat = e.category or "uncategorized"
        by_category[cat] = by_category.get(cat, 0) + 1
        if e.source_url:
            sources.add(e.source_url)

    return {
        "task_id": task_id,
        "total_entities": len(entities),
        "by_status": by_status,
        "by_type": by_type,
        "by_category": by_category,
        "sources": list(sources),
    }


def score_entity(db: Session, entity_id: uuid.UUID, tenant_id: uuid.UUID, rubric_id: str = None) -> Optional[dict]:
    """Score an entity using the LeadScoringTool with a configurable rubric."""
    from app.services.tool_executor import LeadScoringTool
    tool = LeadScoringTool(db, tenant_id, rubric_id=rubric_id)
    result = tool.execute(entity_id=str(entity_id))
    if result.success:
        return result.data
    return None


def update_entity_status(
    db: Session,
    entity_id: uuid.UUID,
    tenant_id: uuid.UUID,
    new_status: str,
) -> Optional[KnowledgeEntity]:
    """Update entity status (lifecycle transition)."""
    valid_statuses = {"draft", "verified", "enriched", "actioned", "archived"}
    if new_status not in valid_statuses:
        return None

    entity = get_entity(db, entity_id, tenant_id)
    if not entity:
        return None

    entity.status = new_status
    db.commit()
    db.refresh(entity)
    return entity


# Relation operations
def create_relation(db: Session, relation_in, tenant_id: uuid.UUID) -> KnowledgeRelation:
    """Create a relation between entities."""
    # Verify both entities exist and belong to tenant
    from_entity = get_entity(db, relation_in.from_entity_id, tenant_id)
    to_entity = get_entity(db, relation_in.to_entity_id, tenant_id)

    if not from_entity or not to_entity:
        raise ValueError("One or both entities not found")

    relation = KnowledgeRelation(
        tenant_id=tenant_id,
        from_entity_id=relation_in.from_entity_id,
        to_entity_id=relation_in.to_entity_id,
        relation_type=relation_in.relation_type,
        strength=relation_in.strength or 1.0,
        evidence=relation_in.evidence,
        discovered_by_agent_id=relation_in.discovered_by_agent_id
    )
    db.add(relation)
    db.commit()
    db.refresh(relation)
    return relation


def get_entity_relations(
    db: Session,
    entity_id: uuid.UUID,
    tenant_id: uuid.UUID,
    direction: str = "both"
) -> List[KnowledgeRelation]:
    """Get all relations for an entity."""
    query = db.query(KnowledgeRelation).filter(KnowledgeRelation.tenant_id == tenant_id)

    if direction == "outgoing":
        query = query.filter(KnowledgeRelation.from_entity_id == entity_id)
    elif direction == "incoming":
        query = query.filter(KnowledgeRelation.to_entity_id == entity_id)
    else:  # both
        query = query.filter(
            (KnowledgeRelation.from_entity_id == entity_id) |
            (KnowledgeRelation.to_entity_id == entity_id)
        )

    return query.all()


def get_all_relations(
    db: Session,
    tenant_id: uuid.UUID,
    relation_type: str = None,
    skip: int = 0,
    limit: int = 100,
) -> List[KnowledgeRelation]:
    """List all relations for a tenant with optional type filter."""
    from sqlalchemy.orm import joinedload

    query = db.query(KnowledgeRelation).filter(
        KnowledgeRelation.tenant_id == tenant_id
    ).options(
        joinedload(KnowledgeRelation.from_entity),
        joinedload(KnowledgeRelation.to_entity),
    )
    if relation_type:
        query = query.filter(KnowledgeRelation.relation_type == relation_type)
    return query.order_by(KnowledgeRelation.created_at.desc()).offset(skip).limit(limit).all()


def delete_relation(db: Session, relation_id: uuid.UUID, tenant_id: uuid.UUID) -> bool:
    """Delete a relation."""
    relation = db.query(KnowledgeRelation).filter(
        KnowledgeRelation.id == relation_id,
        KnowledgeRelation.tenant_id == tenant_id
    ).first()

    if not relation:
        return False

    db.delete(relation)
    db.commit()
    return True
