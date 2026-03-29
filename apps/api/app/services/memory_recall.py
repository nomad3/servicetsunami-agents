"""Memory recall engine for Luna.

Hybrid recall: semantic search (pgvector cosine) + keyword boost + RL logging.
Falls back to ILIKE keyword matching when the embedding model is unavailable.
"""
import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
import uuid

from sqlalchemy.orm import Session
from sqlalchemy import or_, text

from app.models.knowledge_entity import KnowledgeEntity
from app.models.knowledge_relation import KnowledgeRelation
from app.models.agent_memory import AgentMemory
from app.models.world_state import WorldStateAssertion
from app.services import embedding_service
from app.services.rl_experience_service import log_experience

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


def _build_memory_context_keyword_fallback(
    db: Session,
    tenant_id: uuid.UUID,
    keywords: List[str],
) -> Dict[str, Any]:
    """ILIKE-based fallback when the embedding model is not available."""
    # Query matching entities (top 10 by name/description match)
    entity_filters = []
    for kw in keywords:
        entity_filters.append(KnowledgeEntity.name.ilike(f"%{kw}%"))
        entity_filters.append(KnowledgeEntity.description.ilike(f"%{kw}%"))

    entities = (
        db.query(KnowledgeEntity)
        .filter(
            KnowledgeEntity.tenant_id == tenant_id,
            KnowledgeEntity.deleted_at.is_(None),
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

    return context


def _fetch_top_observations_semantic(
    db: Session,
    tenant_id: uuid.UUID,
    entity_id: str,
    query_embedding: List[float],
    limit: int = 3,
) -> List[Dict[str, Any]]:
    """Fetch top observations for an entity, ranked by semantic similarity to query."""
    vector_literal = "[" + ",".join(str(v) for v in query_embedding) + "]"

    sql = text(f"""
        SELECT observation_text, observation_type, source_type, created_at,
               source_channel, source_ref,
               1 - (embedding <=> CAST('{vector_literal}' AS vector)) AS similarity
        FROM knowledge_observations
        WHERE entity_id = CAST(:entity_id AS uuid)
          AND tenant_id = CAST(:tenant_id AS uuid)
          AND embedding IS NOT NULL
        ORDER BY embedding <=> CAST('{vector_literal}' AS vector)
        LIMIT :lim
    """)
    rows = db.execute(sql, {
        "entity_id": entity_id,
        "tenant_id": str(tenant_id),
        "lim": limit,
    }).fetchall()

    if rows:
        return [
            {
                "text": row.observation_text,
                "type": row.observation_type,
                "source": row.source_type or "",
                "date": row.created_at.isoformat() if row.created_at else "",
                "source_channel": row.source_channel or "",
                "source_ref": row.source_ref or "",
            }
            for row in rows
        ]

    # Fallback: fetch most recent observations if none have embeddings
    sql_fallback = text("""
        SELECT observation_text, observation_type, source_type, created_at,
               source_channel, source_ref
        FROM knowledge_observations
        WHERE entity_id = CAST(:entity_id AS uuid)
          AND tenant_id = CAST(:tenant_id AS uuid)
        ORDER BY created_at DESC
        LIMIT :lim
    """)
    rows = db.execute(sql_fallback, {
        "entity_id": entity_id,
        "tenant_id": str(tenant_id),
        "lim": limit,
    }).fetchall()
    return [
        {
            "text": row.observation_text,
            "type": row.observation_type,
            "source": row.source_type or "",
            "date": row.created_at.isoformat() if row.created_at else "",
            "source_channel": row.source_channel or "",
            "source_ref": row.source_ref or "",
        }
        for row in rows
    ]


def _fetch_user_entity(db: Session, tenant_id: uuid.UUID) -> Optional[Dict]:
    """Return the user/owner entity for this tenant, if one exists."""
    entity = (
        db.query(KnowledgeEntity)
        .filter(
            KnowledgeEntity.tenant_id == tenant_id,
            KnowledgeEntity.category == "user",
        )
        .order_by(KnowledgeEntity.created_at.desc())
        .first()
    )
    if entity is None:
        return None
    return {
        "id": str(entity.id),
        "name": entity.name,
        "entity_type": entity.entity_type,
        "category": entity.category,
        "description": entity.description or "",
        "similarity": 1.0,  # pinned — always relevant
        "pinned": True,
    }


def build_memory_context(
    db: Session,
    tenant_id: uuid.UUID,
    user_message: str,
    session_entity_names: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Build a memory context payload for Luna's automatic recall.

    Hybrid recall engine:
    1. Embed user message
    2. Semantic search: top 30 entities + top 15 memories via pgvector cosine
    3. Keyword boost: entities whose name exactly matches a query word get +0.3
    4. Combine, sort by final score
    5. Fetch top 3 observations per recalled entity (semantic)
    6. Return top 10 entities + top 5 memories + observations + relations
    7. Update recall counters on recalled entities
    8. Log RL experience for the memory_recall decision point

    Falls back to ILIKE keyword matching when embedding model is unavailable.

    The user/owner entity (category="user") is always injected at the top of
    relevant_entities regardless of query — so questions like "who am I?" always
    get answered from the knowledge graph.
    """
    # Always fetch the user/owner entity — pinned into context regardless of query
    user_entity = _fetch_user_entity(db, tenant_id)

    keywords = extract_keywords(user_message)
    if not keywords:
        # No searchable keywords — return minimal context with just the user profile
        if user_entity:
            return {
                "recalled_entity_names": [user_entity["name"]],
                "relevant_entities": [user_entity],
                "relevant_memories": [],
                "relevant_relations": [],
                "entity_observations": {},
            }
        return {}

    # --- Step 1: Embed user message ---
    query_embedding = embedding_service.embed_text(user_message, task_type="RETRIEVAL_QUERY")

    # Fallback to keyword-based recall if embedding model is not loaded
    if query_embedding is None:
        logger.info("Embedding model unavailable — falling back to keyword recall")
        return _build_memory_context_keyword_fallback(db, tenant_id, keywords)

    # --- Step 2: Semantic search ---
    semantic_entities = embedding_service.search_entities_semantic(
        db, tenant_id, query_embedding, limit=30
    )
    semantic_memories = embedding_service.search_memories_semantic(
        db, tenant_id, query_embedding, limit=15
    )

    # Note: time-based decay is applied in SQL (search_memories_semantic)
    # during top-K selection, so no Python-side decay needed here.

    # --- Step 3: Keyword boost ---
    # Entities whose name exactly matches a word in the query get +0.3 similarity
    query_words_lower = set(re.findall(r'[\w]+', user_message.lower()))
    for ent in semantic_entities:
        entity_name_words = set(re.findall(r'[\w]+', ent["name"].lower()))
        if entity_name_words & query_words_lower:
            ent["similarity"] = min(ent["similarity"] + 0.3, 1.0)

    # Session entity boost: entities mentioned earlier in this conversation get +0.2
    if session_entity_names:
        session_names_lower = {n.lower() for n in session_entity_names}
        for ent in semantic_entities:
            if ent["name"].lower() in session_names_lower:
                ent["similarity"] = min(ent["similarity"] + 0.2, 1.0)

    # --- Step 4: Sort by final score ---
    semantic_entities.sort(key=lambda x: x["similarity"], reverse=True)
    semantic_memories.sort(key=lambda x: x["similarity"], reverse=True)

    # --- Step 5: Take top N ---
    top_entities = semantic_entities[:10]
    top_memories = semantic_memories[:5]

    # Always pin user entity at the top (deduplicate if already recalled)
    if user_entity:
        already_in = any(e["id"] == user_entity["id"] for e in top_entities)
        if not already_in:
            top_entities = [user_entity] + top_entities[:9]

    if not top_entities and not top_memories:
        return {}

    # --- Step 6: Fetch top 3 observations per recalled entity ---
    entity_observations: Dict[str, List[Dict]] = {}
    for ent in top_entities:
        obs = _fetch_top_observations_semantic(
            db, tenant_id, ent["id"], query_embedding, limit=3
        )
        if obs:
            entity_observations[ent["name"]] = obs

    # --- Fetch relations for recalled entities ---
    entity_ids = [uuid.UUID(e["id"]) for e in top_entities]
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
            .limit(15)
            .all()
        )

    # Build entity name lookup for relations
    entity_map = {uuid.UUID(e["id"]): e["name"] for e in top_entities}
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

    # --- Step 7: Build context ---
    recalled_entity_names = [e["name"] for e in top_entities]
    context: Dict[str, Any] = {
        "recalled_entity_names": recalled_entity_names,
        "relevant_entities": [
            {
                "name": e["name"],
                "type": e["entity_type"],
                "category": e["category"],
                "description": e["description"],
                "similarity": round(e["similarity"], 4),
            }
            for e in top_entities
        ],
        "relevant_memories": [
            {
                "type": m["memory_type"],
                "content": m["content"],
                "similarity": round(m["similarity"], 4),
            }
            for m in top_memories
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

    if entity_observations:
        context["entity_observations"] = entity_observations

    # --- Check for disputed assertions on recalled entities ---
    if entity_ids:
        try:
            disputed = db.query(WorldStateAssertion).filter(
                WorldStateAssertion.tenant_id == tenant_id,
                WorldStateAssertion.subject_entity_id.in_(entity_ids),
                WorldStateAssertion.status == "disputed",
            ).limit(5).all()

            if disputed:
                context["contradictions"] = [
                    {
                        "entity": d.subject_slug,
                        "attribute": d.attribute_path,
                        "current": d.previous_value_json,
                        "conflicting": d.value_json,
                        "reason": d.dispute_reason,
                    }
                    for d in disputed
                ]
        except Exception:
            logger.debug("Failed to fetch disputed assertions", exc_info=True)

    # --- Step 7b: Recall recent episodes ---
    try:
        from sqlalchemy import text as sa_text

        vector_literal = "[" + ",".join(str(v) for v in query_embedding) + "]"
        episode_sql = sa_text(f"""
            SELECT id, summary, key_topics, key_entities, mood, source_channel, created_at,
                   1 - (embedding <=> CAST('{vector_literal}' AS vector)) AS similarity
            FROM conversation_episodes
            WHERE tenant_id = CAST(:tid AS uuid)
              AND embedding IS NOT NULL
            ORDER BY embedding <=> CAST('{vector_literal}' AS vector)
            LIMIT 5
        """)
        episode_rows = db.execute(episode_sql, {"tid": str(tenant_id)}).mappings().all()
        episodes = [
            {
                "summary": r["summary"],
                "mood": r["mood"],
                "source": r["source_channel"],
                "date": r["created_at"].strftime("%b %d") if r["created_at"] else "",
                "similarity": round(float(r["similarity"]), 4),
            }
            for r in episode_rows
            if float(r["similarity"]) > 0.3
        ]
        if episodes:
            context["recent_episodes"] = episodes[:3]
    except Exception:
        logger.debug("Episode recall failed", exc_info=True)

    # --- Step 8: Update recall counters on recalled entities ---
    if entity_ids:
        now = datetime.utcnow()
        db.execute(
            text("""
                UPDATE knowledge_entities
                SET recall_count = COALESCE(recall_count, 0) + 1,
                    last_recalled_at = :now
                WHERE id = ANY(CAST(:ids AS uuid[]))
                  AND tenant_id = CAST(:tenant_id AS uuid)
            """),
            {
                "now": now,
                "ids": [str(eid) for eid in entity_ids],
                "tenant_id": str(tenant_id),
            },
        )

    # Update access counts for recalled memories
    if top_memories:
        now = datetime.utcnow()
        memory_ids = [m["id"] for m in top_memories]
        db.execute(
            text("""
                UPDATE agent_memories
                SET access_count = COALESCE(access_count, 0) + 1,
                    last_accessed_at = :now
                WHERE id = ANY(CAST(:ids AS uuid[]))
            """),
            {"now": now, "ids": memory_ids},
        )

    db.commit()

    # --- Step 9: Log RL experience for memory_recall decision ---
    try:
        trajectory_id = uuid.uuid4()
        log_experience(
            db=db,
            tenant_id=tenant_id,
            trajectory_id=trajectory_id,
            step_index=0,
            decision_point="memory_recall",
            state={
                "query": user_message[:500],
                "keywords": keywords,
                "num_entity_candidates": len(semantic_entities),
                "num_memory_candidates": len(semantic_memories),
            },
            action={
                "recalled_entities": [e["name"] for e in top_entities],
                "recalled_memories_count": len(top_memories),
                "top_entity_score": top_entities[0]["similarity"] if top_entities else 0,
                "top_memory_score": top_memories[0]["similarity"] if top_memories else 0,
            },
            alternatives=[{"method": "keyword_only"}, {"method": "semantic_only"}],
            explanation={"method": "hybrid_semantic_keyword_boost"},
            state_text=user_message[:500],
        )
    except Exception:
        logger.debug("Failed to log RL experience for memory_recall", exc_info=True)

    logger.info(
        "Hybrid recall for tenant %s: %d entities, %d memories, %d relations, %d observations (keywords: %s)",
        tenant_id, len(top_entities), len(top_memories), len(relations),
        sum(len(v) for v in entity_observations.values()), keywords[:5],
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


def find_meeting_context(
    db: Session,
    tenant_id: uuid.UUID,
    attendee_emails: List[str],
) -> List[Dict[str, Any]]:
    """Find entities by email in attributes (JSONB containment) and return with top 3 observations.

    Searches knowledge_entities whose `attributes` JSONB field contains any of the
    provided email addresses. Returns entity info plus their most recent observations.
    """
    if not attendee_emails:
        return []

    results = []
    for email_addr in attendee_emails:
        email_addr = email_addr.strip().lower()
        if not email_addr or "@" not in email_addr:
            continue

        # Search entities whose attributes contain this email (text search on JSONB)
        sql = text("""
            SELECT id, name, entity_type, category, description, attributes
            FROM knowledge_entities
            WHERE tenant_id = CAST(:tid AS uuid)
              AND deleted_at IS NULL
              AND (
                  CAST(attributes AS text) ILIKE :email_pattern
                  OR LOWER(name) = :email_lower
              )
            LIMIT 5
        """)
        rows = db.execute(sql, {
            "tid": str(tenant_id),
            "email_pattern": f"%{email_addr}%",
            "email_lower": email_addr,
        }).fetchall()

        for row in rows:
            entity_id = row.id
            # Fetch top 3 observations for this entity
            obs_sql = text("""
                SELECT observation_text, observation_type, created_at
                FROM knowledge_observations
                WHERE entity_id = CAST(:eid AS uuid)
                  AND tenant_id = CAST(:tid AS uuid)
                ORDER BY created_at DESC
                LIMIT 3
            """)
            observations = db.execute(obs_sql, {
                "eid": str(entity_id),
                "tid": str(tenant_id),
            }).fetchall()

            results.append({
                "entity_id": str(entity_id),
                "name": row.name,
                "entity_type": row.entity_type,
                "category": row.category or "",
                "description": row.description or "",
                "email": email_addr,
                "observations": [
                    {
                        "text": obs.observation_text,
                        "type": obs.observation_type,
                        "date": obs.created_at.isoformat() if obs.created_at else "",
                    }
                    for obs in observations
                ],
            })

    return results


def find_stale_leads(
    db: Session,
    tenant_id: uuid.UUID,
    stale_days: int = 7,
) -> List[Dict[str, Any]]:
    """Query entities where category='lead' with no activity in N days.

    Returns list of stale lead dicts with entity info and days since last activity.
    """
    cutoff = datetime.utcnow() - timedelta(days=stale_days)

    stale_leads = (
        db.query(KnowledgeEntity)
        .filter(
            KnowledgeEntity.tenant_id == tenant_id,
            KnowledgeEntity.category == "lead",
            KnowledgeEntity.deleted_at.is_(None),
            KnowledgeEntity.status.notin_(["archived"]),
            or_(
                KnowledgeEntity.updated_at < cutoff,
                KnowledgeEntity.updated_at.is_(None),
            ),
        )
        .order_by(KnowledgeEntity.updated_at.asc().nullsfirst())
        .limit(10)
        .all()
    )

    results = []
    now = datetime.utcnow()
    for lead in stale_leads:
        last_activity = lead.updated_at or lead.created_at
        days_stale = (now - last_activity).days if last_activity else stale_days

        results.append({
            "entity_id": str(lead.id),
            "name": lead.name,
            "entity_type": lead.entity_type,
            "description": lead.description or "",
            "score": lead.score,
            "days_stale": days_stale,
            "last_activity": last_activity.isoformat() if last_activity else None,
        })

    return results


def find_related_context(
    db: Session,
    tenant_id: uuid.UUID,
    entity_ids: List[uuid.UUID],
    limit: int = 3,
) -> List[Dict[str, Any]]:
    """Find 2-hop graph neighbors via knowledge_relations.

    Starting from entity_ids, traverses relations to find directly connected
    entities (1-hop) and their connections (2-hop). Returns unique neighbor
    entities with relation info.
    """
    if not entity_ids:
        return []

    id_strs = [str(eid) for eid in entity_ids]
    placeholders = ", ".join(f"CAST(:id{i} AS uuid)" for i in range(len(id_strs)))
    params = {f"id{i}": id_str for i, id_str in enumerate(id_strs)}
    params["tid"] = str(tenant_id)
    params["lim"] = limit * 3  # fetch more for filtering

    sql = text(f"""
        WITH hop1 AS (
            SELECT DISTINCT
                CASE
                    WHEN from_entity_id IN ({placeholders}) THEN to_entity_id
                    ELSE from_entity_id
                END AS neighbor_id,
                relation_type
            FROM knowledge_relations
            WHERE tenant_id = CAST(:tid AS uuid)
              AND (from_entity_id IN ({placeholders}) OR to_entity_id IN ({placeholders}))
        ),
        hop2 AS (
            SELECT DISTINCT
                CASE
                    WHEN kr.from_entity_id = h1.neighbor_id THEN kr.to_entity_id
                    ELSE kr.from_entity_id
                END AS neighbor_id,
                kr.relation_type
            FROM knowledge_relations kr
            JOIN hop1 h1 ON (kr.from_entity_id = h1.neighbor_id OR kr.to_entity_id = h1.neighbor_id)
            WHERE kr.tenant_id = CAST(:tid AS uuid)
        ),
        all_neighbors AS (
            SELECT neighbor_id, relation_type FROM hop1
            UNION
            SELECT neighbor_id, relation_type FROM hop2
        )
        SELECT DISTINCT ke.id, ke.name, ke.entity_type, ke.category, ke.description,
               an.relation_type
        FROM all_neighbors an
        JOIN knowledge_entities ke ON ke.id = an.neighbor_id
        WHERE ke.tenant_id = CAST(:tid AS uuid)
          AND ke.deleted_at IS NULL
          AND ke.id NOT IN ({placeholders})
        LIMIT :lim
    """)

    rows = db.execute(sql, params).fetchall()

    return [
        {
            "entity_id": str(row.id),
            "name": row.name,
            "entity_type": row.entity_type,
            "category": row.category or "",
            "description": row.description or "",
            "relation_type": row.relation_type,
        }
        for row in rows[:limit]
    ]


def build_memory_context_with_git(
    db: Session,
    tenant_id: uuid.UUID,
    user_message: str,
    session_entity_names: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Extended memory context that includes git history when relevant.

    Calls build_memory_context() first, then appends git context if the
    message appears code-related.
    """
    context = build_memory_context(db, tenant_id, user_message, session_entity_names=session_entity_names)

    if _is_code_related(user_message):
        git_context = get_recent_git_context(db, tenant_id, user_message, limit=5)
        if git_context:
            context["git_context"] = git_context

    return context
