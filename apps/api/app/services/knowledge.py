"""Service for managing knowledge graph entities and relations"""
import json as _json
import logging
import re
import uuid
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any

from sqlalchemy.orm import Session
from sqlalchemy import text, func

from app.models.knowledge_entity import KnowledgeEntity
from app.models.knowledge_entity_history import KnowledgeEntityHistory
from app.models.knowledge_observation import KnowledgeObservation
from app.models.knowledge_relation import KnowledgeRelation
from app.models.embedding import Embedding
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
        # Store in shared table
        embedding_service.embed_and_store(
            db, entity.tenant_id, "entity", str(entity.id), embed_text,
        )
        # ALSO store directly on model for faster same-table search
        vec = embedding_service.embed_text(embed_text)
        if vec is not None:
            entity.embedding = vec
    except Exception:
        logger.exception("Failed to embed entity %s — skipping", entity.id)


# ---------------------------------------------------------------------------
# Entity History Tracking
# ---------------------------------------------------------------------------

def create_entity_history(
    db: Session,
    entity: KnowledgeEntity,
    change_reason: str = None,
    changed_by_platform: str = None,
) -> KnowledgeEntityHistory:
    """Snapshot current entity state into a history record before mutation.

    Auto-increments version by querying the max version for the entity_id.
    """
    # Get next version number
    max_version = db.query(func.max(KnowledgeEntityHistory.version)).filter(
        KnowledgeEntityHistory.entity_id == entity.id,
    ).scalar() or 0
    next_version = max_version + 1

    # Snapshot current properties + attributes
    properties_snapshot = entity.properties if isinstance(entity.properties, dict) else None
    attributes_snapshot = entity.attributes if isinstance(entity.attributes, dict) else None

    history = KnowledgeEntityHistory(
        entity_id=entity.id,
        tenant_id=entity.tenant_id,
        version=next_version,
        properties_snapshot=properties_snapshot,
        attributes_snapshot=attributes_snapshot,
        change_reason=change_reason,
        changed_by_platform=changed_by_platform,
    )
    db.add(history)
    db.flush()
    return history


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
    entity_in: KnowledgeEntityUpdate,
    change_reason: str = None,
    changed_by_platform: str = None,
) -> Optional[KnowledgeEntity]:
    """Update an entity (snapshots current state to history first)."""
    entity = get_entity(db, entity_id, tenant_id)
    if not entity:
        return None

    # Snapshot current state BEFORE applying changes
    try:
        create_entity_history(
            db, entity,
            change_reason=change_reason or "entity_updated",
            changed_by_platform=changed_by_platform,
        )
    except Exception:
        logger.exception("Failed to create entity history for %s — continuing with update", entity_id)

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

    try:
        from app.services.embedding_service import embed_and_store
        embed_text = f"{relation.relation_type}: {from_entity.name} → {to_entity.name}"
        if relation.evidence:
            embed_text += f" ({relation.evidence[:300]})"
        embed_and_store(
            db,
            tenant_id=tenant_id,
            content_type="relation",
            content_id=str(relation.id),
            text_content=embed_text,
        )
    except Exception:
        logger.debug("Relation embedding skipped", exc_info=True)

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


# ---------------------------------------------------------------------------
# Git History Context
# ---------------------------------------------------------------------------

def upsert_entity_by_name(
    db: Session,
    tenant_id: uuid.UUID,
    name: str,
    *,
    entity_type: str = "general",
    category: Optional[str] = None,
    description: Optional[str] = None,
) -> tuple[KnowledgeEntity, bool]:
    """Public wrapper around _find_or_create_entity. Returns (entity, created)."""
    existing = db.query(KnowledgeEntity).filter(
        KnowledgeEntity.tenant_id == tenant_id,
        KnowledgeEntity.name == name,
    ).first()
    if existing:
        return existing, False
    entity = _find_or_create_entity(
        db, tenant_id=tenant_id, name=name,
        entity_type=entity_type, category=category or "general",
        description=description,
    )
    return entity, True


def get_entity_by_name(
    db: Session, tenant_id: uuid.UUID, name: str,
) -> Optional[KnowledgeEntity]:
    return db.query(KnowledgeEntity).filter(
        KnowledgeEntity.tenant_id == tenant_id,
        KnowledgeEntity.name == name,
    ).first()


def _find_or_create_entity(
    db: Session,
    tenant_id: uuid.UUID,
    name: str,
    entity_type: str,
    category: str,
    description: str = None,
    properties: dict = None,
) -> KnowledgeEntity:
    """Find an existing entity by name+type or create a new one."""
    entity = db.query(KnowledgeEntity).filter(
        KnowledgeEntity.tenant_id == tenant_id,
        KnowledgeEntity.name == name,
        KnowledgeEntity.entity_type == entity_type,
    ).first()

    if entity:
        # Update properties if provided
        if properties:
            existing = entity.properties or {}
            existing.update(properties)
            entity.properties = existing
            entity.updated_at = datetime.utcnow()
            db.flush()
        return entity

    entity = KnowledgeEntity(
        tenant_id=tenant_id,
        entity_type=entity_type,
        category=category,
        name=name,
        description=description,
        properties=properties or {},
        confidence=0.9,
        status="verified",
        extraction_platform="git",
    )
    db.add(entity)
    db.flush()
    _safe_embed_entity(db, entity)
    return entity


def _find_or_create_relation(
    db: Session,
    tenant_id: uuid.UUID,
    from_entity_id: uuid.UUID,
    to_entity_id: uuid.UUID,
    relation_type: str,
) -> KnowledgeRelation:
    """Find or create a relation between two entities."""
    existing = db.query(KnowledgeRelation).filter(
        KnowledgeRelation.tenant_id == tenant_id,
        KnowledgeRelation.from_entity_id == from_entity_id,
        KnowledgeRelation.to_entity_id == to_entity_id,
        KnowledgeRelation.relation_type == relation_type,
    ).first()
    if existing:
        return existing

    relation = KnowledgeRelation(
        tenant_id=tenant_id,
        from_entity_id=from_entity_id,
        to_entity_id=to_entity_id,
        relation_type=relation_type,
        strength=1.0,
    )
    db.add(relation)
    db.flush()
    return relation


# ---------------------------------------------------------------------------
# Observation CRUD
# ---------------------------------------------------------------------------

def create_observation(
    db: Session,
    tenant_id: uuid.UUID,
    observation_text: str,
    observation_type: str = "fact",
    source_type: str = "conversation",
    source_platform: str = None,
    source_agent: str = None,
    entity_id: uuid.UUID = None,
    confidence: float = 1.0,
    source_channel: str = None,
    source_ref: str = None,
    sentiment: str = None,
) -> KnowledgeObservation:
    """Create a knowledge observation using the ORM model.

    Auto-embeds via embedding_service and stores the vector directly on the
    model's embedding column.  Also logs a memory_activity event.

    Returns:
        The created KnowledgeObservation instance.
    """
    observation = KnowledgeObservation(
        tenant_id=tenant_id,
        entity_id=entity_id,
        observation_text=observation_text,
        observation_type=observation_type,
        source_type=source_type,
        source_platform=source_platform,
        source_agent=source_agent,
        confidence=confidence,
        source_channel=source_channel,
        source_ref=source_ref,
        sentiment=sentiment,
    )

    # Generate and store embedding directly on the model
    try:
        vec = embedding_service.embed_text(observation_text)
        if vec is not None:
            observation.embedding = vec
    except Exception:
        logger.debug("Observation embedding generation skipped for new observation")

    db.add(observation)
    db.flush()

    # Also store in the shared vector_store for cross-content-type search
    try:
        embedding_service.embed_and_store(
            db, tenant_id, "observation", str(observation.id), observation_text,
        )
    except Exception:
        logger.debug("Observation vector_store embedding skipped for %s", observation.id)

    # Log memory activity
    try:
        from app.services.memory_activity import log_activity
        log_activity(
            db,
            tenant_id=tenant_id,
            event_type="observation_created",
            description=f"{observation_type}: {observation_text[:200]}",
            source=source_platform or source_type,
            event_metadata={
                "observation_id": str(observation.id),
                "observation_type": observation_type,
                "entity_id": str(entity_id) if entity_id else None,
            },
            entity_id=entity_id,
        )
    except Exception:
        logger.debug("Memory activity log skipped for observation %s", observation.id)

    db.commit()
    db.refresh(observation)
    return observation


def search_observations(
    db: Session,
    tenant_id: uuid.UUID,
    query_text: str,
    entity_id: uuid.UUID = None,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """Semantic search on observation embeddings using pgvector cosine distance.

    Optionally scoped to a specific entity.

    Returns:
        List of observation dicts with id, text, type, similarity score.
    """
    try:
        query_vec = embedding_service.embed_text(query_text)
    except Exception:
        logger.debug("Failed to generate query embedding for observation search")
        return []

    if query_vec is None:
        return []

    from pgvector.sqlalchemy import Vector

    # Build query using cosine distance operator
    cosine_dist = KnowledgeObservation.embedding.cosine_distance(query_vec)
    q = db.query(
        KnowledgeObservation,
        (1 - cosine_dist).label("similarity"),
    ).filter(
        KnowledgeObservation.tenant_id == tenant_id,
        KnowledgeObservation.embedding.isnot(None),
    )

    if entity_id is not None:
        q = q.filter(KnowledgeObservation.entity_id == entity_id)

    q = q.order_by(cosine_dist).limit(limit)

    results = []
    for obs, similarity in q.all():
        results.append({
            "id": str(obs.id),
            "observation_text": obs.observation_text,
            "observation_type": obs.observation_type,
            "source_type": obs.source_type,
            "source_platform": obs.source_platform,
            "entity_id": str(obs.entity_id) if obs.entity_id else None,
            "confidence": obs.confidence,
            "similarity": float(similarity) if similarity is not None else 0.0,
            "created_at": obs.created_at.isoformat() if obs.created_at else None,
        })
    return results


def _create_observation(
    db: Session,
    tenant_id: uuid.UUID,
    observation_text: str,
    observation_type: str,
    source_type: str = "git_history",
    entity_id: uuid.UUID = None,
) -> None:
    """Create a knowledge observation (legacy helper — delegates to create_observation)."""
    create_observation(
        db,
        tenant_id=tenant_id,
        observation_text=observation_text,
        observation_type=observation_type,
        source_type=source_type,
        source_platform="git",
        entity_id=entity_id,
    )


def store_git_context(
    db: Session,
    tenant_id: uuid.UUID,
    commits: List[Dict[str, Any]],
    repo_name: str,
) -> Dict[str, int]:
    """Store git commit history as knowledge entities and observations.

    For each commit:
    - Find or create contributor entity (category='contributor')
    - Find or create repository entity (category='repository')
    - Create contributes_to relation
    - Create git_commit observation linked to repository entity

    Args:
        commits: List of dicts with keys: hash, author, email, date, subject, files_changed
        repo_name: Repository name (e.g. 'nomad3/agentprovision-agents')

    Returns:
        Dict with counts: contributors_created, commits_stored, relations_created
    """
    stats = {"contributors_created": 0, "commits_stored": 0, "relations_created": 0}

    # Find or create repository entity
    repo_entity = _find_or_create_entity(
        db, tenant_id,
        name=repo_name,
        entity_type="repository",
        category="repository",
        description=f"Git repository: {repo_name}",
    )

    seen_authors = {}

    for commit in commits:
        author = commit.get("author", "Unknown")
        email = commit.get("email", "")
        subject = commit.get("subject", "")
        commit_hash = commit.get("hash", "")[:8]
        files_changed = commit.get("files_changed", 0)

        # Skip merge commits and trivial changes
        if subject.lower().startswith("merge") or not subject.strip():
            continue

        # Find or create contributor entity
        author_key = email or author
        if author_key not in seen_authors:
            contributor = _find_or_create_entity(
                db, tenant_id,
                name=author,
                entity_type="person",
                category="contributor",
                description=f"Git contributor: {author} ({email})",
                properties={"email": email, "last_active": commit.get("date", "")},
            )
            seen_authors[author_key] = contributor

            # Create contributes_to relation
            _find_or_create_relation(
                db, tenant_id,
                from_entity_id=contributor.id,
                to_entity_id=repo_entity.id,
                relation_type="contributes_to",
            )
            stats["relations_created"] += 1
            stats["contributors_created"] += 1
        else:
            contributor = seen_authors[author_key]

        # Create git_commit observation
        obs_text = f"{subject} ({files_changed} files changed) by {author} [{commit_hash}]"
        _create_observation(
            db, tenant_id, obs_text,
            observation_type="git_commit",
            entity_id=repo_entity.id,
        )
        stats["commits_stored"] += 1

    db.commit()
    return stats


def detect_file_hotspots(
    db: Session,
    tenant_id: uuid.UUID,
    file_changes: Dict[str, int],
    repo_name: str,
    threshold: int = 5,
) -> int:
    """Detect frequently-changed directories and store as file_hotspot observations.

    Args:
        file_changes: Dict mapping directory path to change count over rolling window
        repo_name: Repository name
        threshold: Minimum changes to qualify as a hotspot (default 5)

    Returns:
        Number of hotspot observations created
    """
    # Find repository entity
    repo_entity = db.query(KnowledgeEntity).filter(
        KnowledgeEntity.tenant_id == tenant_id,
        KnowledgeEntity.name == repo_name,
        KnowledgeEntity.entity_type == "repository",
    ).first()

    if not repo_entity:
        return 0

    hotspots = 0
    for directory, count in file_changes.items():
        if count >= threshold:
            obs_text = f"{directory} had {count} changes in 7 days — active development area"
            _create_observation(
                db, tenant_id, obs_text,
                observation_type="file_hotspot",
                entity_id=repo_entity.id,
            )
            hotspots += 1

    db.commit()
    return hotspots


def store_pr_outcome(
    db: Session,
    tenant_id: uuid.UUID,
    repo: str,
    pr_number: int,
    outcome: str,
    title: str = "",
    review_comments: List[str] = None,
) -> Dict[str, Any]:
    """Store a PR outcome as a git_pr observation and return RL reward signal.

    Args:
        outcome: One of 'merged', 'closed', 'reverted'

    Returns:
        Dict with observation_id and suggested rl_reward
    """
    reward_map = {
        "merged": 0.5,
        "merged_with_comments": 0.3,
        "closed": -0.3,
        "reverted": -0.5,
    }

    # Adjust outcome based on review comments
    effective_outcome = outcome
    if outcome == "merged" and review_comments:
        effective_outcome = "merged_with_comments"

    reward = reward_map.get(effective_outcome, 0.0)

    reviews_summary = ""
    if review_comments:
        reviews_summary = f" Reviews: {'; '.join(review_comments[:3])}"

    obs_text = f"PR #{pr_number} {outcome}: {title}.{reviews_summary}"

    repo_entity = db.query(KnowledgeEntity).filter(
        KnowledgeEntity.tenant_id == tenant_id,
        KnowledgeEntity.name == repo,
        KnowledgeEntity.entity_type == "repository",
    ).first()

    _create_observation(
        db, tenant_id, obs_text,
        observation_type="git_pr",
        source_type="git_pr",
        entity_id=repo_entity.id if repo_entity else None,
    )
    db.commit()

    return {"pr_number": pr_number, "outcome": outcome, "rl_reward": reward}


# ---------------------------------------------------------------------------
# Entity Quality Tracking
# ---------------------------------------------------------------------------

def increment_reference_count(
    db: Session,
    tenant_id: uuid.UUID,
    entity_names: List[str],
    response_text: str,
) -> int:
    """Scan response text for entity names and increment reference_count.

    Case-insensitive word-boundary matching is used to avoid false positives
    on very short entity names.

    Args:
        entity_names: List of entity names to look for.
        response_text: The agent response text to scan.

    Returns:
        Number of entities whose reference_count was incremented.
    """
    if not entity_names or not response_text:
        return 0

    response_lower = response_text.lower()
    incremented = 0

    for name in entity_names:
        if not name or len(name) < 2:
            continue
        # Case-insensitive check
        if name.lower() in response_lower:
            entity = db.query(KnowledgeEntity).filter(
                KnowledgeEntity.tenant_id == tenant_id,
                func.lower(KnowledgeEntity.name) == name.lower(),
            ).first()
            if entity:
                entity.reference_count = (entity.reference_count or 0) + 1
                incremented += 1

    if incremented:
        db.flush()

    return incremented


def update_feedback_score(
    db: Session,
    tenant_id: uuid.UUID,
    entity_id: uuid.UUID,
    feedback_type: str,
) -> Optional[KnowledgeEntity]:
    """Update an entity's feedback_score based on user feedback.

    memory_helpful  -> +0.1
    memory_irrelevant -> -0.1
    Clamped to [-1.0, 1.0].

    Returns:
        Updated entity, or None if not found.
    """
    feedback_deltas = {
        "memory_helpful": 0.1,
        "memory_irrelevant": -0.1,
    }
    delta = feedback_deltas.get(feedback_type)
    if delta is None:
        return None

    entity = get_entity(db, entity_id, tenant_id)
    if not entity:
        return None

    current = entity.feedback_score if entity.feedback_score is not None else 0.0
    entity.feedback_score = max(-1.0, min(1.0, current + delta))
    db.flush()
    return entity


def get_quality_stats(db: Session, tenant_id: uuid.UUID) -> Dict[str, Any]:
    """Compute entity quality statistics for the tenant.

    Returns:
        Dict with total_entities, embedding_coverage_pct, top_10_by_usefulness,
        bottom_10, and per_platform_extraction_stats.
    """
    total = db.query(func.count(KnowledgeEntity.id)).filter(
        KnowledgeEntity.tenant_id == tenant_id,
        KnowledgeEntity.deleted_at.is_(None),
    ).scalar() or 0

    if total == 0:
        return {
            "total_entities": 0,
            "embedding_coverage_pct": 0.0,
            "top_10_by_usefulness": [],
            "bottom_10": [],
            "per_platform_extraction_stats": [],
        }

    # Embedding coverage: count entities that have a corresponding embedding row
    embedded_count = db.query(func.count(Embedding.id)).filter(
        Embedding.tenant_id == tenant_id,
        Embedding.content_type == "entity",
    ).scalar() or 0

    embedding_coverage = round(embedded_count * 100.0 / total, 1) if total > 0 else 0.0

    # Top 10 by usefulness (reference_count + recall_count + feedback_score)
    top_entities = (
        db.query(KnowledgeEntity)
        .filter(
            KnowledgeEntity.tenant_id == tenant_id,
            KnowledgeEntity.deleted_at.is_(None),
        )
        .order_by(
            (
                func.coalesce(KnowledgeEntity.reference_count, 0)
                + func.coalesce(KnowledgeEntity.recall_count, 0)
            ).desc(),
            func.coalesce(KnowledgeEntity.feedback_score, 0).desc(),
        )
        .limit(10)
        .all()
    )

    # Bottom 10 by quality
    bottom_entities = (
        db.query(KnowledgeEntity)
        .filter(
            KnowledgeEntity.tenant_id == tenant_id,
            KnowledgeEntity.deleted_at.is_(None),
        )
        .order_by(
            func.coalesce(KnowledgeEntity.data_quality_score, 0.5).asc(),
            func.coalesce(KnowledgeEntity.feedback_score, 0).asc(),
        )
        .limit(10)
        .all()
    )

    def _entity_summary(e: KnowledgeEntity) -> Dict[str, Any]:
        return {
            "id": str(e.id),
            "name": e.name,
            "entity_type": e.entity_type,
            "category": e.category,
            "reference_count": e.reference_count or 0,
            "recall_count": e.recall_count or 0,
            "feedback_score": e.feedback_score or 0.0,
            "data_quality_score": e.data_quality_score,
        }

    # Per-platform extraction stats
    platform_stats = (
        db.query(
            KnowledgeEntity.extraction_platform,
            func.count(KnowledgeEntity.id).label("count"),
            func.avg(KnowledgeEntity.data_quality_score).label("avg_quality"),
        )
        .filter(
            KnowledgeEntity.tenant_id == tenant_id,
            KnowledgeEntity.deleted_at.is_(None),
            KnowledgeEntity.extraction_platform.isnot(None),
        )
        .group_by(KnowledgeEntity.extraction_platform)
        .order_by(func.count(KnowledgeEntity.id).desc())
        .all()
    )

    return {
        "total_entities": total,
        "embedding_coverage_pct": embedding_coverage,
        "top_10_by_usefulness": [_entity_summary(e) for e in top_entities],
        "bottom_10": [_entity_summary(e) for e in bottom_entities],
        "per_platform_extraction_stats": [
            {
                "platform": p or "unknown",
                "count": c,
                "avg_quality": round(float(q), 3) if q is not None else None,
            }
            for p, c, q in platform_stats
        ],
    }


# Module-level alias so callers can do: from app.services.knowledge import knowledge_service
import sys as _sys
knowledge_service = _sys.modules[__name__]
