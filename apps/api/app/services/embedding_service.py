"""Embedding service — generate, store, and search vector embeddings.

Uses Rust gRPC service (fastembed/ONNX) for local, API-key-free embedding 
generation. All functions are module-level, matching the service pattern 
used elsewhere.
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

_grpc_channel = None
_grpc_stub = None

_DIMENSIONS = 768
_MAX_INPUT_CHARS = 8000
_MODEL_NAME = "nomic-ai/nomic-embed-text-v1.5"

try:
    import grpc as _grpc
except ImportError:
    _grpc = None  # type: ignore[assignment]


def _get_grpc_stub():
    """Lazy-init the gRPC client stub for Rust embedding-service."""
    global _grpc_channel, _grpc_stub
    if _grpc_stub is not None:
        return _grpc_stub

    url = os.environ.get("EMBEDDING_SERVICE_URL")
    if not url:
        return None

    if _grpc is None:
        return None

    try:
        try:
            from app.generated import embedding_pb2_grpc
        except ImportError:
            logger.warning("gRPC generated code not found. Rust embedding disabled.")
            return None

        options = [
            ('grpc.keepalive_time_ms', 30000),
            ('grpc.keepalive_timeout_ms', 5000),
            ('grpc.keepalive_permit_without_calls', 1),
            ('grpc.max_receive_message_length', 16 * 1024 * 1024),
        ]
        _grpc_channel = _grpc.insecure_channel(url, options=options)
        _grpc_stub = embedding_pb2_grpc.EmbeddingServiceStub(_grpc_channel)
        logger.info("Connected to Rust embedding-service at %s", url)
        return _grpc_stub
    except Exception as e:
        logger.warning("Failed to connect to Rust embedding-service at %s: %s", url, e)
        return None


# Canonical intent definitions for tier routing
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


# ------------------------------------------------------------------
# Core: embed text
# ------------------------------------------------------------------

# Module-level model cache for Python fallback
_local_model = None


def embed_text(
    text_content: str,
    task_type: str = "RETRIEVAL_DOCUMENT",
) -> Optional[List[float]]:
    """Generate a 768-dim embedding for *text_content* via Rust gRPC (hot path) or local Python (fallback)."""
    global _local_model
    
    # 1. Try Rust gRPC (Fastest)
    stub = _get_grpc_stub()
    if stub:
        try:
            from app.generated import embedding_pb2
            # Map Python task types to Proto task types
            rust_task = "search_document"
            if task_type == "RETRIEVAL_QUERY":
                rust_task = "search_query"
            
            req = embedding_pb2.EmbedRequest(
                text=text_content[:_MAX_INPUT_CHARS],
                task_type=rust_task
            )
            resp = stub.Embed(req, timeout=5.0)  # Shorter timeout for faster fallback
            return list(resp.vector)
        except Exception as e:
            global _grpc_stub, _grpc_channel
            _grpc_stub = None
            _grpc_channel = None
            logger.debug("Rust embedding failed, falling back to Python: %s", e)

    # 2. Local Python Fallback (Reliability)
    try:
        if _local_model is None:
            from sentence_transformers import SentenceTransformer
            _local_model = SentenceTransformer(
                "nomic-ai/nomic-embed-text-v1.5", trust_remote_code=True
            )
        
        # nomic-embed-text-v1.5 requires prefix
        prefix = "search_document: "
        if task_type == "RETRIEVAL_QUERY":
            prefix = "search_query: "
            
        vector = _local_model.encode(prefix + text_content[:_MAX_INPUT_CHARS], normalize_embeddings=True)
        return vector.tolist()
    except Exception as e:
        logger.error("All embedding paths failed: %s", e)
        return None


def embed_batch(
    texts: List[str],
    task_type: str = "RETRIEVAL_DOCUMENT",
) -> List[Optional[List[float]]]:
    """Generate embeddings for a list of strings in bulk via Rust gRPC (hot path) or local Python (fallback)."""
    if not texts:
        return []

    # 1. Try Rust gRPC
    stub = _get_grpc_stub()
    if stub:
        try:
            from app.generated import embedding_pb2
            rust_task = "search_document"
            if task_type == "RETRIEVAL_QUERY":
                rust_task = "search_query"
            
            req = embedding_pb2.EmbedBatchRequest(
                texts=[t[:_MAX_INPUT_CHARS] for t in texts],
                task_type=rust_task
            )
            resp = stub.EmbedBatch(req, timeout=30.0)
            return [list(r.vector) for r in resp.results]
        except Exception as e:
            global _grpc_stub, _grpc_channel
            _grpc_stub = None
            _grpc_channel = None
            logger.debug("Rust batch embedding failed, falling back to Python: %s", e)

    # 2. Local Python Fallback
    try:
        global _local_model
        if _local_model is None:
            from sentence_transformers import SentenceTransformer
            _local_model = SentenceTransformer(
                "nomic-ai/nomic-embed-text-v1.5", trust_remote_code=True
            )
        
        prefix = "search_document: "
        if task_type == "RETRIEVAL_QUERY":
            prefix = "search_query: "
            
        prefixed_texts = [prefix + t[:_MAX_INPUT_CHARS] for t in texts]
        vectors = _local_model.encode(prefixed_texts, normalize_embeddings=True)
        return [v.tolist() for v in vectors]
    except Exception as e:
        logger.error("All batch embedding paths failed: %s", e)
        return [None] * len(texts)


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
    """Return the *limit* most similar embeddings to *query_text*."""
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

    sql = text(f"""
        SELECT
            id,
            tenant_id,
            content_type,
            content_id,
            text_content,
            created_at,
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
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
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
    """Search knowledge_entities via cosine similarity.

    Tries `content_type='entity'` first. Falls back to searching observations
    (which are always embedded) and mapping back to their parent entities.
    """
    vector_literal = "[" + ",".join(str(v) for v in query_embedding) + "]"

    sql = text(f"""
        SELECT DISTINCT ON (ke.id)
            ke.id,
            ke.name,
            ke.entity_type,
            ke.category,
            ke.description,
            1 - (e.embedding <=> CAST('{vector_literal}' AS vector)) AS similarity
        FROM embeddings e
        JOIN knowledge_observations ko
            ON CAST(ko.id AS text) = e.content_id
            AND ko.tenant_id = e.tenant_id
        JOIN knowledge_entities ke
            ON ko.entity_id = ke.id
        WHERE e.tenant_id = CAST(:tenant_id AS uuid)
          AND e.content_type = 'observation'
          AND ke.deleted_at IS NULL
        ORDER BY ke.id, e.embedding <=> CAST('{vector_literal}' AS vector)
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
    """Embed canonical intents at API startup via Rust gRPC. Call once from main.py."""
    global _intent_cache
    
    stub = _get_grpc_stub()
    if not stub:
        logger.warning("Embedding service not available for intent matching")
        return

    try:
        from app.generated import embedding_pb2
        
        all_intents = list(INTENT_DEFINITIONS)
        req = embedding_pb2.EmbedBatchRequest(
            texts=[f"search_query: {i['name']}" for i in all_intents],
            task_type="search_query"
        )
        resp = stub.EmbedBatch(req, timeout=60.0)
        
        _intent_cache = []
        for i, r in enumerate(resp.results):
            _intent_cache.append({**all_intents[i], "vector": np.array(r.vector)})
            
        logger.info("Intent embedding cache initialized via Rust (%d intents)", len(_intent_cache))
    except Exception as e:
        logger.error("Failed to initialize intent embeddings: %s", e)


def _expand_intents_with_translations() -> list:
    """Use local inference to translate intent definitions into other languages.
    
    Controlled by INTENT_EXPANSION_LANGUAGES env var (e.g. 'Spanish,Portuguese').
    Used during startup or testing to expand the intent embedding cache.
    """
    langs_str = os.environ.get("INTENT_EXPANSION_LANGUAGES", "")
    if not langs_str:
        return []
    
    languages = [l.strip() for l in langs_str.split(",") if l.strip()]
    if not languages:
        return []
        
    from app.services.local_inference import generate_sync
    
    expanded = []
    for lang in languages:
        logger.info("Expanding intents for language: %s", lang)
        for intent in INTENT_DEFINITIONS:
            prompt = f"Translate this intent into {lang}: '{intent['name']}'. Respond with ONLY the translation."
            translation = generate_sync(prompt, temperature=0.0, max_tokens=50)
            if translation:
                expanded.append({
                    **intent,
                    "name": translation.strip().strip("'\""),
                })
    return expanded


def match_intent(message: str) -> dict:
    """Embed message and cosine-match against cached intent vectors."""
    if not _intent_cache:
        return None
    
    msg_vec = embed_text(message, task_type="RETRIEVAL_QUERY")
    if msg_vec is None:
        return None
        
    try:
        msg_vec = np.array(msg_vec)
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
    """Search agent_memories by content_embedding (pgvector cosine) directly."""
    vector_literal = "[" + ",".join(str(v) for v in query_embedding) + "]"

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
