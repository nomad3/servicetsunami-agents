"""Memory recall engine for Luna.

Queries relevant entities, memories, and relations based on user message
keywords and injects them into the ADK state_delta for automatic context.
"""
import logging
import re
from datetime import datetime
from typing import Any, Dict, List
import uuid

from sqlalchemy.orm import Session
from sqlalchemy import or_

from app.models.knowledge_entity import KnowledgeEntity
from app.models.knowledge_relation import KnowledgeRelation
from app.models.agent_memory import AgentMemory

logger = logging.getLogger(__name__)

# Minimum keyword length to avoid noise
_MIN_KEYWORD_LENGTH = 3

# Stop words to exclude from keyword extraction
_STOP_WORDS = {
    "the", "is", "at", "which", "on", "a", "an", "and", "or", "but",
    "in", "with", "to", "for", "of", "not", "no", "can", "has", "have",
    "had", "was", "were", "be", "been", "do", "does", "did", "will",
    "would", "could", "should", "may", "might", "shall", "must",
    "that", "this", "these", "those", "it", "its", "my", "your",
    "his", "her", "our", "their", "what", "how", "who", "when", "where",
    "why", "about", "from", "into", "through", "during", "before",
    "after", "above", "below", "between", "just", "also", "very",
    "too", "than", "then", "here", "there", "all", "any", "each",
    "every", "both", "few", "more", "most", "other", "some", "such",
    "only", "same", "so", "still", "now", "please", "thanks", "thank",
    "hey", "hi", "hello", "yes", "no", "okay", "ok", "sure",
    # Spanish common words
    "el", "la", "los", "las", "un", "una", "de", "del", "en",
    "que", "por", "para", "con", "como", "pero", "si", "mas",
    "hola", "gracias", "por favor",
}


def extract_keywords(message: str) -> List[str]:
    """Extract meaningful keywords from a user message."""
    # Split on non-alphanumeric (keep accented chars)
    words = re.findall(r'[\w]+', message.lower())
    # Filter: no stop words, minimum length, no pure numbers
    keywords = [
        w for w in words
        if w not in _STOP_WORDS
        and len(w) >= _MIN_KEYWORD_LENGTH
        and not w.isdigit()
    ]
    # Deduplicate while preserving order
    seen = set()
    unique = []
    for kw in keywords:
        if kw not in seen:
            seen.add(kw)
            unique.append(kw)
    return unique[:10]  # Cap at 10 keywords


def build_memory_context(
    db: Session,
    tenant_id: uuid.UUID,
    user_message: str,
) -> Dict[str, Any]:
    """Build a memory context payload for injection into ADK state_delta.

    Queries entities, memories, and relations matching the user's message
    keywords and returns a structured dict for Luna's automatic recall.
    """
    keywords = extract_keywords(user_message)
    if not keywords:
        return {}

    # Query matching entities (top 10 by name/description match)
    entity_filters = []
    for kw in keywords:
        entity_filters.append(KnowledgeEntity.name.ilike(f"%{kw}%"))
        entity_filters.append(KnowledgeEntity.description.ilike(f"%{kw}%"))

    entities = (
        db.query(KnowledgeEntity)
        .filter(
            KnowledgeEntity.tenant_id == tenant_id,
            or_(*entity_filters),
        )
        .order_by(KnowledgeEntity.confidence.desc().nullslast())
        .limit(10)
        .all()
    )

    # Query matching agent memories (top 5 by content match)
    memory_filters = [AgentMemory.content.ilike(f"%{kw}%") for kw in keywords]
    memories = (
        db.query(AgentMemory)
        .filter(
            AgentMemory.tenant_id == tenant_id,
            or_(*memory_filters),
        )
        .order_by(AgentMemory.importance.desc().nullslast())
        .limit(5)
        .all()
    )

    # Query relations for matched entities
    entity_ids = [e.id for e in entities]
    relations = []
    if entity_ids:
        relations = (
            db.query(KnowledgeRelation)
            .filter(
                KnowledgeRelation.tenant_id == tenant_id,
                or_(
                    KnowledgeRelation.from_entity_id.in_(entity_ids),
                    KnowledgeRelation.to_entity_id.in_(entity_ids),
                ),
            )
            .limit(10)
            .all()
        )

    if not entities and not memories:
        return {}

    # Build entity name lookup for relations
    entity_map = {e.id: e.name for e in entities}
    # Also fetch any entity names referenced in relations but not in our matched set
    rel_entity_ids = set()
    for r in relations:
        rel_entity_ids.add(r.from_entity_id)
        rel_entity_ids.add(r.to_entity_id)
    missing_ids = rel_entity_ids - set(entity_ids)
    if missing_ids:
        extra = db.query(KnowledgeEntity.id, KnowledgeEntity.name).filter(
            KnowledgeEntity.id.in_(list(missing_ids))
        ).all()
        for eid, ename in extra:
            entity_map[eid] = ename

    context = {
        "relevant_entities": [
            {
                "name": e.name,
                "type": e.entity_type,
                "category": e.category or "",
                "description": e.description or "",
            }
            for e in entities
        ],
        "relevant_memories": [
            {
                "type": m.memory_type,
                "content": m.content,
            }
            for m in memories
        ],
        "relevant_relations": [
            {
                "from": entity_map.get(r.from_entity_id, str(r.from_entity_id)),
                "to": entity_map.get(r.to_entity_id, str(r.to_entity_id)),
                "type": r.relation_type,
            }
            for r in relations
        ],
    }

    # Update access counts for recalled memories
    for m in memories:
        m.access_count = (m.access_count or 0) + 1
        m.last_accessed_at = datetime.utcnow()
    if memories:
        db.commit()

    logger.info(
        "Recall for tenant %s: %d entities, %d memories, %d relations (keywords: %s)",
        tenant_id, len(entities), len(memories), len(relations), keywords[:5],
    )

    return context
