"""Embedding service — generate, store, and search vector embeddings.

Uses nomic-embed-text-v1.5 (768-dim) via sentence-transformers for local,
API-key-free embedding generation. All functions are module-level, matching
the service pattern used elsewhere.
"""
import logging
import uuid
from typing import Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.embedding import Embedding

logger = logging.getLogger(__name__)

# Lazy-initialized sentence-transformers model
_model = None

_MODEL_NAME = "nomic-ai/nomic-embed-text-v1.5"
_DIMENSIONS = 768
_MAX_INPUT_CHARS = 8000


_model_loading = False
_model_load_failures = 0
_MAX_LOAD_RETRIES = 3


def _get_model():
    """Lazy-init the sentence-transformers model with retry and timeout protection."""
    global _model, _model_loading, _model_load_failures
    if _model is not None:
        return _model
    if _model_loading:
        return None  # Another thread is loading — skip
    if _model_load_failures >= _MAX_LOAD_RETRIES:
        return None  # Too many failures — stop trying until restart

    _model_loading = True
    try:
        import signal
        import threading
        from sentence_transformers import SentenceTransformer

        # Load model with a timeout (120s) to prevent hanging on HF download
        loaded = [None]
        error = [None]

        def _load():
            try:
                loaded[0] = SentenceTransformer(_MODEL_NAME, trust_remote_code=True)
            except Exception as e:
                error[0] = e

        t = threading.Thread(target=_load, daemon=True)
        t.start()
        t.join(timeout=120)

        if t.is_alive():
            logger.warning("Embedding model load timed out (120s) — API continues without embeddings")
            _model_load_failures += 1
            return None

        if error[0]:
            logger.warning("Embedding model load failed: %s (attempt %d/%d)",
                          error[0], _model_load_failures + 1, _MAX_LOAD_RETRIES)
            _model_load_failures += 1
            return None

        _model = loaded[0]
        _model_load_failures = 0
        logger.info("Loaded embedding model: %s", _MODEL_NAME)
        return _model
    except Exception:
        logger.exception("Failed to load embedding model %s", _MODEL_NAME)
        _model_load_failures += 1
        return None
    finally:
        _model_loading = False


# ------------------------------------------------------------------
# Core: embed text
# ------------------------------------------------------------------

def embed_text(
    text_content: str,
    task_type: str = "RETRIEVAL_DOCUMENT",
) -> Optional[List[float]]:
    """Generate a 768-dim embedding for *text_content*.

    Returns None when the model fails to load or on error.
    The task_type prefix follows nomic-embed conventions:
    - "search_document: " for documents
    - "search_query: " for queries
    """
    model = _get_model()
    if model is None:
        logger.debug("embed_text skipped — model not loaded")
        return None

    try:
        truncated = text_content[:_MAX_INPUT_CHARS]

        # Nomic-embed uses task-specific prefixes
        if task_type == "RETRIEVAL_QUERY":
            prefixed = f"search_query: {truncated}"
        else:
            prefixed = f"search_document: {truncated}"

        embedding = model.encode(prefixed, normalize_embeddings=True)
        return embedding.tolist()
    except Exception:
        logger.exception("embed_text failed")
        return None


# ------------------------------------------------------------------
# Store / delete
# ------------------------------------------------------------------

def embed_and_store(
    db: Session,
    tenant_id: uuid.UUID,
    content_type: str,
    content_id: str,
    text_content: str,
    task_type: str = "RETRIEVAL_DOCUMENT",
) -> Optional[Embedding]:
    """Embed *text_content* and upsert the row in the embeddings table."""
    vector = embed_text(text_content, task_type=task_type)
    if vector is None:
        return None

    # Remove previous embedding for the same content
    db.query(Embedding).filter(
        Embedding.content_type == content_type,
        Embedding.content_id == content_id,
    ).delete(synchronize_session="fetch")

    row = Embedding(
        tenant_id=tenant_id,
        content_type=content_type,
        content_id=content_id,
        embedding=vector,
        text_content=text_content[:_MAX_INPUT_CHARS],
        task_type=task_type,
        model=_MODEL_NAME,
    )
    db.add(row)
    db.flush()
    return row


def delete_embedding(db: Session, content_type: str, content_id: str) -> None:
    """Delete embedding(s) matching *content_type* + *content_id*."""
    db.query(Embedding).filter(
        Embedding.content_type == content_type,
        Embedding.content_id == content_id,
    ).delete(synchronize_session="fetch")
    db.flush()


# ------------------------------------------------------------------
# Search / recall
# ------------------------------------------------------------------

def search_similar(
    db: Session,
    tenant_id: uuid.UUID,
    content_types: Optional[List[str]],
    query_text: str,
    limit: int = 10,
) -> List[Dict]:
    """Return the *limit* most similar embeddings to *query_text*.

    Uses pgvector cosine distance operator (<=>).
    Filters by tenant_id (includes NULL for global content) and optional
    content_types.
    """
    vector = embed_text(query_text, task_type="RETRIEVAL_QUERY")
    if vector is None:
        return []

    vector_literal = "[" + ",".join(str(v) for v in vector) + "]"

    # Build optional content_type filter
    type_clause = ""
    params: dict = {
        "tenant_id": str(tenant_id),
        "lim": limit,
    }
    if content_types:
        type_clause = "AND content_type = ANY(:ctypes)"
        params["ctypes"] = content_types

    # Inline the vector literal to avoid SQLAlchemy confusing ':vector::vector'
    # (colon-based named param vs PostgreSQL type cast).
    sql = text(f"""
        SELECT
            id,
            tenant_id,
            content_type,
            content_id,
            text_content,
            1 - (embedding <=> CAST('{vector_literal}' AS vector)) AS similarity
        FROM embeddings
        WHERE (tenant_id = CAST(:tenant_id AS uuid) OR tenant_id IS NULL)
          {type_clause}
        ORDER BY embedding <=> CAST('{vector_literal}' AS vector)
        LIMIT :lim
    """)

    rows = db.execute(sql, params).mappings().all()
    return [
        {
            "id": str(r["id"]),
            "tenant_id": str(r["tenant_id"]) if r["tenant_id"] else None,
            "content_type": r["content_type"],
            "content_id": r["content_id"],
            "text_content": r["text_content"],
            "similarity": float(r["similarity"]),
        }
        for r in rows
    ]


def recall(
    db: Session,
    tenant_id: uuid.UUID,
    query: str,
    limit: int = 20,
) -> List[Dict]:
    """Broad recall across all content types — convenience wrapper."""
    return search_similar(db, tenant_id, content_types=None, query_text=query, limit=limit)


# ------------------------------------------------------------------
# Targeted semantic search for memory recall engine
# ------------------------------------------------------------------

def search_entities_semantic(
    db: Session,
    tenant_id: uuid.UUID,
    query_embedding: List[float],
    limit: int = 30,
) -> List[Dict]:
    """Search entities via cosine similarity on embeddings table (content_type='entity').

    Joins with knowledge_entities to return entity metadata.
    Returns list of dicts with id, name, entity_type, category, description, similarity.
    """
    vector_literal = "[" + ",".join(str(v) for v in query_embedding) + "]"

    sql = text(f"""
        SELECT
            ke.id,
            ke.name,
            ke.entity_type,
            ke.category,
            ke.description,
            1 - (e.embedding <=> CAST('{vector_literal}' AS vector)) AS similarity
        FROM embeddings e
        JOIN knowledge_entities ke
            ON CAST(ke.id AS text) = e.content_id
            AND ke.tenant_id = e.tenant_id
        WHERE e.tenant_id = CAST(:tenant_id AS uuid)
          AND e.content_type = 'entity'
          AND ke.deleted_at IS NULL
        ORDER BY e.embedding <=> CAST('{vector_literal}' AS vector)
        LIMIT :lim
    """)

    rows = db.execute(sql, {"tenant_id": str(tenant_id), "lim": limit}).mappings().all()
    return [
        {
            "id": str(r["id"]),
            "name": r["name"],
            "entity_type": r["entity_type"],
            "category": r["category"] or "",
            "description": r["description"] or "",
            "similarity": float(r["similarity"]),
        }
        for r in rows
    ]


def search_memories_semantic(
    db: Session,
    tenant_id: uuid.UUID,
    query_embedding: List[float],
    limit: int = 15,
) -> List[Dict]:
    """Search agent_memories by content_embedding (pgvector cosine) directly.

    Returns list of dicts with id, agent_id, memory_type, content, importance, similarity.
    """
    vector_literal = "[" + ",".join(str(v) for v in query_embedding) + "]"

    # Fetch 3× candidates by raw cosine, then apply time-decay in SQL so
    # stale memories don't push out fresher ones during top-K selection.
    # decay_adjusted = similarity * GREATEST(0.1, 1 - decay_rate * days_since_access)
    sql = text(f"""
        WITH candidates AS (
            SELECT
                id, agent_id, memory_type, content, importance,
                decay_rate, last_accessed_at,
                1 - (content_embedding <=> CAST('{vector_literal}' AS vector)) AS raw_similarity
            FROM agent_memories
            WHERE tenant_id = CAST(:tenant_id AS uuid)
              AND content_embedding IS NOT NULL
            ORDER BY content_embedding <=> CAST('{vector_literal}' AS vector)
            LIMIT :candidate_lim
        )
        SELECT *,
            raw_similarity * GREATEST(0.1,
                1.0 - COALESCE(decay_rate, 0.01)
                    * EXTRACT(EPOCH FROM (NOW() - COALESCE(last_accessed_at, NOW()))) / 86400.0
            ) AS similarity
        FROM candidates
        ORDER BY similarity DESC
        LIMIT :lim
    """)

    rows = db.execute(sql, {"tenant_id": str(tenant_id), "lim": limit, "candidate_lim": limit * 3}).mappings().all()
    return [
        {
            "id": str(r["id"]),
            "agent_id": str(r["agent_id"]),
            "memory_type": r["memory_type"],
            "content": r["content"],
            "importance": float(r["importance"]) if r["importance"] else 0.5,
            "decay_rate": float(r["decay_rate"]) if r["decay_rate"] is not None else 0.01,
            "last_accessed_at": r["last_accessed_at"].isoformat() if r["last_accessed_at"] else None,
            "similarity": float(r["similarity"]),
        }
        for r in rows
    ]
