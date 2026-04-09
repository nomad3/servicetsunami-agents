"""Embedding service — generate, store, and search vector embeddings.

Uses nomic-embed-text-v1.5 (768-dim) via sentence-transformers for local,
API-key-free embedding generation. All functions are module-level, matching
the service pattern used elsewhere.
"""
import logging
import os
import uuid
from typing import Dict, List, Optional

import numpy as np

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.embedding import Embedding

logger = logging.getLogger(__name__)

# Lazy-initialized sentence-transformers model
_model = None

_MODEL_NAME = "nomic-ai/nomic-embed-text-v1.5"
_DIMENSIONS = 768
_MAX_INPUT_CHARS = 8000

# Canonical intent definitions for tier routing
# Language-agnostic via nomic multilingual embeddings
INTENT_DEFINITIONS = [
    {"name": "greeting or small talk", "tier": "light", "tools": [], "mutation": False},
    {"name": "check calendar or schedule or upcoming events", "tier": "light", "tools": ["calendar"], "mutation": False},
    {"name": "read or search emails", "tier": "light", "tools": ["email"], "mutation": False},
    {"name": "what do we know about a person or company", "tier": "light", "tools": ["knowledge"], "mutation": False},
    {"name": "search files or documents in drive", "tier": "light", "tools": ["drive"], "mutation": False},
    {"name": "check status of a workflow or task", "tier": "light", "tools": ["workflows"], "mutation": False},
    {"name": "list or check jira issues or tickets", "tier": "light", "tools": ["jira"], "mutation": False},
    {"name": "list or check github issues or pull requests", "tier": "light", "tools": ["github"], "mutation": False},
    {"name": "check competitor status or report", "tier": "light", "tools": ["competitor"], "mutation": False},
    {"name": "check ad campaign metrics or performance", "tier": "light", "tools": ["ads"], "mutation": False},
    {"name": "show me pipeline or sales summary", "tier": "light", "tools": ["sales"], "mutation": False},
    {"name": "book appointment or create reservation or schedule meeting", "tier": "full", "tools": ["bookings"], "mutation": True},
    {"name": "send email or compose message", "tier": "full", "tools": ["email"], "mutation": True},
    {"name": "create or update jira issue or ticket", "tier": "full", "tools": ["jira"], "mutation": True},
    {"name": "process order refund or cancellation", "tier": "full", "tools": ["ecommerce"], "mutation": True},
    {"name": "analyze data or compare metrics or generate report", "tier": "full", "tools": ["data", "reports"], "mutation": False},
    {"name": "create or run a workflow", "tier": "full", "tools": ["workflows"], "mutation": True},
    {"name": "write code or fix bug or create pull request", "tier": "full", "tools": ["github", "shell"], "mutation": True},
    {"name": "manage competitors or add competitor", "tier": "full", "tools": ["competitor"], "mutation": True},
    {"name": "pause or modify ad campaign", "tier": "full", "tools": ["ads"], "mutation": True},
    {"name": "update deal or advance pipeline stage", "tier": "full", "tools": ["sales"], "mutation": True},
    {"name": "create entity or record observation in knowledge graph", "tier": "full", "tools": ["knowledge"], "mutation": True},
    {"name": "execute shell command or deploy changes", "tier": "full", "tools": ["shell"], "mutation": True},
    {"name": "forecast revenue or predict trends", "tier": "full", "tools": ["data", "reports"], "mutation": False},
    {"name": "generate proposal or draft outreach", "tier": "full", "tools": ["sales", "email"], "mutation": True},
    {"name": "connect or manage mcp servers", "tier": "full", "tools": ["mcp_servers"], "mutation": True},
    {"name": "register or manage webhooks", "tier": "full", "tools": ["webhooks"], "mutation": True},
    {"name": "start or stop inbox or competitor monitor", "tier": "full", "tools": ["monitor"], "mutation": True},
]

# In-memory intent embedding cache (populated at startup)
_intent_cache: list | None = None

_model_loading = False
_model_load_failures = 0
_MAX_LOAD_RETRIES = 3


def _expand_intents_with_translations() -> list:
    """Use local Ollama to translate intent definitions for multilingual matching.

    Reads INTENT_EXPANSION_LANGUAGES env var (comma-separated language names,
    e.g. "Spanish,Portuguese,French,German"). For each language, generates a
    translation of each intent definition name via local Qwen inference.
    All translations share the same tier/tools/mutation as their source intent.

    Returns list of additional intent dicts (empty if env var not set or Ollama unavailable).
    Set at startup — no runtime overhead on the hot path.
    """
    from app.services.local_inference import generate_sync  # deferred: avoids circular import

    languages_str = os.environ.get("INTENT_EXPANSION_LANGUAGES", "").strip()
    if not languages_str:
        return []

    languages = [lang.strip() for lang in languages_str.split(",") if lang.strip()]
    if not languages:
        return []

    logger.info("Expanding intent embeddings for languages: %s", languages)
    additional: list = []

    for intent_def in INTENT_DEFINITIONS:
        for language in languages:
            try:
                translation = generate_sync(
                    prompt=(
                        f'Translate this phrase to {language}: "{intent_def["name"]}"\n'
                        f"Return ONLY the translated phrase, nothing else."
                    ),
                    temperature=0.1,
                    max_tokens=60,
                    timeout=8.0,
                )
                if translation and translation.strip():
                    cleaned = translation.strip().strip('"').strip("'")
                    additional.append({**intent_def, "name": cleaned})
            except Exception as exc:
                logger.debug(
                    "Translation failed for '%s' → %s: %s",
                    intent_def["name"], language, exc,
                )

    logger.info(
        "Intent expansion: %d translations (%d intents × %d languages)",
        len(additional), len(INTENT_DEFINITIONS), len(languages),
    )
    return additional


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
                from sentence_transformers import SentenceTransformer, models
                # Attempt standard load
                try:
                    loaded[0] = SentenceTransformer(_MODEL_NAME, trust_remote_code=True)
                except TypeError as te:
                    if "word_embedding_dimension" in str(te):
                        logger.info("Nomic pooling bug detected — applying manual layer fix")
                        # Manual assembly to bypass the broken __init__ in some ST versions
                        word_embedding_model = models.Transformer(_MODEL_NAME, max_seq_length=2048)
                        pooling_model = models.Pooling(word_embedding_model.get_word_embedding_dimension())
                        loaded[0] = SentenceTransformer(modules=[word_embedding_model, pooling_model])
                    else:
                        raise
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


# ------------------------------------------------------------------
# Intent embedding cache for tier routing classification
# ------------------------------------------------------------------

def initialize_intent_embeddings():
    """Embed canonical intents at API startup. Call once from main.py.

    Builds the in-memory _intent_cache with embedded vectors for each intent.
    If INTENT_EXPANSION_LANGUAGES is set, also embeds Ollama-translated variants
    for multilingual matching — same quality, no hardcoded phrases.
    """
    global _intent_cache
    model = _get_model()
    if not model:
        logger.warning("Embedding model not available, intent matching disabled")
        return

    # Start with canonical English intents
    all_intents = list(INTENT_DEFINITIONS)

    # Optionally expand with translated variants
    try:
        additional = _expand_intents_with_translations()
        if additional:
            all_intents.extend(additional)
    except Exception as e:
        logger.warning("Intent expansion failed: %s — using English only", e)

    _intent_cache = []
    for intent_def in all_intents:
        try:
            prefixed = f"search_query: {intent_def['name']}"
            vec = model.encode(prefixed, normalize_embeddings=True)
            _intent_cache.append({**intent_def, "vector": vec})
        except Exception as e:
            logger.error("Failed to embed intent '%s': %s", intent_def["name"], e)

    logger.info(
        "Intent embedding cache: %d intents (%d English + %d translated)",
        len(_intent_cache), len(INTENT_DEFINITIONS), len(_intent_cache) - len(INTENT_DEFINITIONS),
    )


def match_intent(message: str) -> dict:
    """Embed message and cosine-match against cached intent vectors.

    Returns best matching intent dict with 'similarity' score, or None if
    no match above threshold (0.4) or cache not initialized.
    """
    if not _intent_cache:
        return None
    model = _get_model()
    if not model:
        return None
    try:
        prefixed = f"search_query: {message[:_MAX_INPUT_CHARS]}"
        msg_vec = model.encode(prefixed, normalize_embeddings=True)
        best_match = None
        best_score = 0.0
        for intent in _intent_cache:
            score = float(np.dot(msg_vec, intent["vector"]) / (
                np.linalg.norm(msg_vec) * np.linalg.norm(intent["vector"])
            ))
            if score > best_score:
                best_score = score
                best_match = intent
        if best_score >= 0.4 and best_match:
            return {
                "name": best_match["name"],
                "tier": best_match["tier"],
                "tools": best_match["tools"],
                "mutation": best_match["mutation"],
                "similarity": best_score,
            }
    except Exception as e:
        logger.error(f"Intent matching failed: {e}")
    return None


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
