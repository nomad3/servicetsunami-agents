"""Memory recall engine for Luna.

Queries relevant entities, memories, and relations based on user message
keywords and returns structured context for automatic recall.
"""
import logging
import re
from datetime import datetime
from typing import Any, Dict, List
import uuid

from sqlalchemy.orm import Session
from sqlalchemy import or_, text

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
    """Build a memory context payload for Luna's automatic recall.

    Queries entities, memories, and relations matching the user's message
    keywords and returns a structured dict for context injection.
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


# ---------------------------------------------------------------------------
# Git Context Recall
# ---------------------------------------------------------------------------

# Keywords that suggest a code-related query
_CODE_KEYWORDS = {
    "code", "commit", "change", "changed", "file", "files", "bug", "fix",
    "deploy", "pr", "pull", "request", "branch", "merge", "release",
    "refactor", "implement", "build", "test", "error", "crash", "update",
    "migration", "schema", "api", "endpoint", "service", "component",
    "feature", "hotfix", "revert", "push", "git",
}


def _is_code_related(message: str) -> bool:
    """Heuristic: check if a user message is likely code-related."""
    words = set(re.findall(r'[\w]+', message.lower()))
    return bool(words & _CODE_KEYWORDS)


def get_recent_git_context(
    db: Session,
    tenant_id: uuid.UUID,
    query_text: str,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """Retrieve recent git context (commits, PRs, hotspots) relevant to the query.

    Searches knowledge_observations where observation_type is git_commit,
    git_pr, or file_hotspot using text matching on the query keywords.

    Returns a list of observation dicts sorted by relevance.
    """
    keywords = extract_keywords(query_text)
    if not keywords:
        return []

    # Build ILIKE conditions for matching observations
    conditions = []
    params = {"tid": str(tenant_id), "lim": limit}
    for i, kw in enumerate(keywords[:5]):
        conditions.append(f"observation_text ILIKE :kw{i}")
        params[f"kw{i}"] = f"%{kw}%"

    if not conditions:
        return []

    where_clause = " OR ".join(conditions)
    sql = text(f"""
        SELECT id, observation_text, observation_type, source_type, created_at
        FROM knowledge_observations
        WHERE tenant_id = CAST(:tid AS uuid)
          AND observation_type IN ('git_commit', 'git_pr', 'file_hotspot')
          AND ({where_clause})
        ORDER BY created_at DESC
        LIMIT :lim
    """)

    rows = db.execute(sql, params).fetchall()
    return [
        {
            "text": row.observation_text,
            "type": row.observation_type,
            "date": row.created_at.isoformat() if row.created_at else "",
        }
        for row in rows
    ]


def build_memory_context_with_git(
    db: Session,
    tenant_id: uuid.UUID,
    user_message: str,
) -> Dict[str, Any]:
    """Extended memory context that includes git history when relevant.

    Calls build_memory_context() first, then appends git context if the
    message appears code-related.
    """
    context = build_memory_context(db, tenant_id, user_message)

    if _is_code_related(user_message):
        git_context = get_recent_git_context(db, tenant_id, user_message, limit=5)
        if git_context:
            context["git_context"] = git_context

    return context
