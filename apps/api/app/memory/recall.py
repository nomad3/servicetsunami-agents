"""Memory recall — pre-loads context for the chat hot path.

This module is the entry point for all recall operations. The hot path
calls `recall()` ONCE per chat turn before dispatching to the CLI;
no in-prompt "recall tool" exists in this design.

Signature takes a RecallRequest dataclass (mirrors the gRPC IDL exactly)
so Phase 2 cutover to a Rust gRPC client is a no-op for callers.

This is the Phase 1.3 port of the legacy `services.memory_recall.build_memory_context()`
function (319 lines). The legacy function stays alive at
`apps/api/app/services/memory_recall.py:342` until Plan Task 30 (Phase 1.7)
flips the chat hot path to this new entry point via the USE_MEMORY_V2 flag.

Behavioral additions over the legacy function:
  - Hard 1500ms timeout via deadline-checkpoint pattern (no thread pool —
    SQLAlchemy sessions aren't thread-safe). Each `_query.py` helper checks
    the deadline before its DB call and bails early.
  - Token budget enforcement: estimate `len(content) // 4`, drop lowest-priority
    items until total ≤ `request.total_token_budget`.
  - Returns a typed `RecallResponse` (dataclass) instead of a dict.
  - Visibility filter wired through (currently a stub — Task 11 replaces it).
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import or_, text
from sqlalchemy.orm import Session

from app.db.safe_ops import safe_rollback
# Direct-submodule import — `from app.memory import _query` would re-enter
# the package __init__ which in turn imports this module (recall), creating
# a load-time cycle. `import app.memory._query as _query` bypasses __init__.
import app.memory._query as _query
from app.memory.types import (
    CommitmentSummary,
    ContradictionSummary,
    ConversationSummary,
    EntitySummary,
    EpisodeSummary,
    ObservationSummary,
    RecallMetadata,
    RecallRequest,
    RecallResponse,
    RelationSummary,
)
from app.models.knowledge_entity import KnowledgeEntity
from app.models.knowledge_observation import KnowledgeObservation
from app.services import embedding_service
from app.services.rl_experience_service import log_experience

logger = logging.getLogger(__name__)

# Hard timeout: 1500ms total (soft target is 500ms — see plan Phase 1.3).
_HARD_TIMEOUT_SECONDS = 1.5

_grpc_channel = None
_grpc_stub = None

try:
    import grpc as _grpc
except ImportError:
    _grpc = None  # type: ignore[assignment]


def _get_grpc_stub():
    """Lazy-init the gRPC client stub for Rust memory-core."""
    global _grpc_channel, _grpc_stub
    if _grpc_stub is not None:
        return _grpc_stub

    url = os.environ.get("MEMORY_CORE_URL")
    if not url:
        return None

    if _grpc is None:
        return None

    try:
        try:
            from app.generated import memory_pb2_grpc
        except ImportError:
            logger.warning("gRPC generated code not found for memory-core. Rust memory disabled.")
            return None

        options = [
            ('grpc.keepalive_time_ms', 30000),
            ('grpc.keepalive_timeout_ms', 5000),
            ('grpc.keepalive_permit_without_calls', 1),
            ('grpc.max_receive_message_length', 16 * 1024 * 1024),
        ]
        _grpc_channel = _grpc.insecure_channel(url, options=options)
        _grpc_stub = memory_pb2_grpc.MemoryCoreStub(_grpc_channel)
        logger.info("Connected to Rust memory-core at %s", url)
        return _grpc_stub
    except Exception as e:
        logger.warning("Failed to connect to Rust memory-core at %s: %s", url, e)
        return None


def _recall_rust(request: RecallRequest) -> Optional[RecallResponse]:
    """Run recall via Rust gRPC. Returns None on any failure."""
    stub = _get_grpc_stub()
    if not stub:
        return None
    try:
        from app.generated import memory_pb2

        req = memory_pb2.RecallRequest(
            tenant_id=str(request.tenant_id),
            agent_slug=request.agent_slug,
            query=request.query,
            user_id=str(request.user_id) if request.user_id else "",
            chat_session_id=str(request.chat_session_id) if request.chat_session_id else "",
            top_k_per_type=request.top_k_per_type,
            total_token_budget=request.total_token_budget,
        )

        t0 = time.perf_counter()
        resp = stub.Recall(req, timeout=5.0)
        elapsed = (time.perf_counter() - t0) * 1000

        entities = [
            EntitySummary(
                id=uuid.UUID(e.id),
                name=e.name,
                category=e.category or None,
                description=e.description or None,
                similarity=e.similarity,
                confidence=1.0,
                source_type=e.entity_type or None,
            )
            for e in resp.entities
        ]

        observations = [
            ObservationSummary(
                id=uuid.UUID(o.id),
                entity_id=uuid.UUID(o.entity_id),
                content=o.content,
                similarity=o.similarity,
                confidence=1.0,
                created_at=datetime.now(timezone.utc),
            )
            for o in resp.observations
        ]

        relations = [
            RelationSummary(
                id=uuid.UUID(r.from_entity),  # use from_entity as pseudo-id
                from_entity=r.from_entity,
                to_entity=r.to_entity,
                relation_type=r.relation_type,
                confidence=1.0,
            )
            for r in resp.relations
        ]

        episodes = [
            EpisodeSummary(
                id=uuid.UUID(e.id),
                session_id=None,
                summary=e.summary,
                key_topics=[],
                key_entities=[],
                similarity=e.similarity,
                created_at=datetime.fromtimestamp(
                    e.created_at.seconds, tz=timezone.utc
                ) if e.created_at else datetime.now(timezone.utc),
            )
            for e in resp.episodes
        ]

        commitments = [
            CommitmentSummary(
                id=uuid.UUID(c.id),
                title=c.title,
                state=c.status,  # proto field is 'status', maps to 'state'
                due_at=datetime.fromtimestamp(
                    c.due_at.seconds, tz=timezone.utc
                ) if c.due_at and c.due_at.seconds else None,
                priority="normal",
                similarity=0.0,
            )
            for c in resp.commitments
        ]

        past_conversations = [
            ConversationSummary(
                id=uuid.UUID(cv.session_id) if cv.session_id else uuid.uuid4(),
                role=cv.role,
                content=cv.content,
                created_at=datetime.fromtimestamp(
                    cv.created_at.seconds, tz=timezone.utc
                ) if cv.created_at and cv.created_at.seconds else datetime.now(timezone.utc),
                similarity=cv.similarity,
            )
            for cv in resp.past_conversations
        ]

        return RecallResponse(
            entities=entities,
            observations=observations,
            relations=relations,
            episodes=episodes,
            commitments=commitments,
            past_conversations=past_conversations,
            goals=[],  # Rust doesn't query goals yet
            contradictions=[],  # Rust doesn't query contradictions yet
            total_tokens_estimate=resp.metadata.total_tokens_estimate if resp.metadata else 0,
            metadata=RecallMetadata(
                elapsed_ms=elapsed,
                degraded=resp.metadata.degraded if resp.metadata else False,
            ),
        )
    except Exception as e:
        # Force reconnect on next call (service may have restarted)
        global _grpc_stub, _grpc_channel
        _grpc_stub = None
        _grpc_channel = None
        logger.warning("Rust recall failed (will reconnect next call): %s", e)
        return None


def _compare_and_log(
    request: RecallRequest,
    python_result: RecallResponse,
    rust_result: RecallResponse,
) -> None:
    """Compare Python and Rust recall results and log divergences.

    This is observational only — it never affects the returned result.
    """
    from app.memory.validation_metrics import metrics

    py_entity_ids = [e.id for e in python_result.entities]
    rust_entity_ids = [e.id for e in rust_result.entities]

    # Top-3 exact match
    py_top3 = py_entity_ids[:3]
    rust_top3 = rust_entity_ids[:3]
    top3_match = py_top3 == rust_top3

    # Top-10 Jaccard similarity
    py_top10 = set(py_entity_ids[:10])
    rust_top10 = set(rust_entity_ids[:10])
    if py_top10 or rust_top10:
        intersection = py_top10 & rust_top10
        union = py_top10 | rust_top10
        jaccard = len(intersection) / len(union) if union else 1.0
    else:
        jaccard = 1.0  # both empty = perfect agreement

    matched = top3_match and jaccard >= 0.9
    metrics.record_read(matched)

    if not top3_match or jaccard < 0.9:
        logger.warning(
            "dual-read DIVERGENCE (tenant=%s, agent=%s): "
            "top3_match=%s, jaccard=%.3f, py_top3=%s, rust_top3=%s, "
            "py_top10_size=%d, rust_top10_size=%d",
            request.tenant_id, request.agent_slug,
            top3_match, jaccard,
            [str(i) for i in py_top3], [str(i) for i in rust_top3],
            len(py_top10), len(rust_top10),
        )
    else:
        logger.debug(
            "dual-read OK (tenant=%s, agent=%s): "
            "top3_match=%s, jaccard=%.3f",
            request.tenant_id, request.agent_slug,
            top3_match, jaccard,
        )


def recall(db: Session, request: RecallRequest) -> RecallResponse:
    """Pre-load memory context for a chat turn."""
    use_rust = os.environ.get("USE_RUST_MEMORY", "false").lower() == "true"
    dual_read = os.environ.get("MEMORY_DUAL_READ", "false").lower() == "true"

    if use_rust and not dual_read:
        rust_result = _recall_rust(request)
        if rust_result is not None:
            return rust_result
        logger.warning("Rust recall failed, falling back to Python")

    metadata = RecallMetadata(elapsed_ms=0.0)
    
    # --- Step 0: Context Enrichment (Who is the user?) ---
    user_name = None
    if request.user_id:
        try:
            from app.models.user import User
            user = db.query(User).filter(User.id == request.user_id).first()
            if user:
                user_name = user.full_name
        except Exception:
            safe_rollback(db)
    elif request.chat_session_id:
        # Fallback for sessions where user_id wasn't passed but we might find it 
        # (Though chat_sessions doesn't have user_id, some implementations might have it in metadata)
        pass

    query_with_context = request.query
    query_lower = request.query.lower()
    if user_name and any(kw in query_lower for kw in ["who", "me", "my", " i "]) and any(kw in query_lower for kw in ["am", "is", "memory", "know", "recall", "about"]):
        # Inject identity hints for semantic search if asking about themselves
        query_with_context = f"{request.query} (User identity: {user_name})"
        logger.info("recall: enriched query with identity hint: %s", user_name)

    # --- Step 1: Embed the query ---
    try:
        query_embedding = embedding_service.embed_text(
            query_with_context, task_type="RETRIEVAL_QUERY"
        )
    except Exception:
        logger.exception("embedding_service.embed_text failed")
        query_embedding = None

    t0 = time.perf_counter()
    deadline = t0 + _HARD_TIMEOUT_SECONDS

    if query_embedding is None:
        return _recall_keyword_only(db, request, t0, metadata)

    # --- Step 2: Run all the searches with deadline checkpoints ---
    entities: list[EntitySummary] = []
    observations: list[ObservationSummary] = []
    relations: list[RelationSummary] = []
    episodes: list[EpisodeSummary] = []
    commitments = []
    goals = []
    contradictions: list[ContradictionSummary] = []

    try:
        entities = _query.search_entities(
            db, request.tenant_id, query_embedding,
            top_k=max(request.top_k_per_type * 2, 10),
            agent_slug=request.agent_slug,
            deadline=deadline,
        )
        logger.info("recall: found %d raw entities", len(entities))
    except Exception:
        logger.exception("search_entities failed")

    if _query._check_deadline(deadline):
        metadata.degraded = True
    else:
        entity_ids = [e.id for e in entities[:request.top_k_per_type]]

        try:
            observations = _query.search_observations(
                db, request.tenant_id, entity_ids, query_embedding,
                top_k=request.top_k_per_type * 3,  # ~3 obs per entity
                deadline=deadline,
            )
        except Exception:
            logger.exception("search_observations failed")

        try:
            episodes = _query.search_episodes(
                db, request.tenant_id, query_embedding,
                top_k=request.top_k_per_type,
                deadline=deadline,
            )
        except Exception:
            logger.exception("search_episodes failed")

        try:
            commitments = _query.search_commitments(
                db, request.tenant_id, request.agent_slug,
                top_k=request.top_k_per_type,
                query_embedding=query_embedding,
                deadline=deadline,
            )
        except Exception:
            logger.exception("search_commitments failed")

        try:
            goals = _query.search_goals(
                db, request.tenant_id, request.agent_slug,
                top_k=request.top_k_per_type,
                query_embedding=query_embedding,
                deadline=deadline,
            )
        except Exception:
            logger.exception("search_goals failed")

        try:
            relations = _query.search_relations(
                db, request.tenant_id, entity_ids, deadline=deadline,
            )
        except Exception:
            logger.exception("search_relations failed")

        try:
            contradictions = _query.search_world_state_contradictions(
                db, request.tenant_id, entity_ids,
                top_k=5, deadline=deadline,
            )
        except Exception:
            logger.exception("search_world_state_contradictions failed")

        if _query._check_deadline(deadline):
            metadata.degraded = True

    # --- Step 3: Trim entities to top_k_per_type AFTER observations are fetched ---
    entities = entities[:request.top_k_per_type]

    # --- Step 4: Side effects (KEEP — chat hot path depends on this) ---
    if entities:
        try:
            now = datetime.utcnow()
            db.execute(
                text("""
                    UPDATE knowledge_entities
                    SET recall_count = COALESCE(recall_count, 0) + 1,
                        last_recalled_at = :now
                    WHERE id = ANY(CAST(:ids AS uuid[]))
                      AND tenant_id = CAST(:tid AS uuid)
                """),
                {
                    "now": now,
                    "ids": [str(e.id) for e in entities],
                    "tid": str(request.tenant_id),
                },
            )
            db.commit()
        except Exception:
            logger.exception("recall_count UPDATE failed")
            db.rollback()

    # --- Step 5: Build response and enforce token budget ---
    response = RecallResponse(
        entities=entities,
        observations=observations,
        relations=relations,
        commitments=commitments,
        goals=goals,
        past_conversations=[],
        episodes=episodes,
        contradictions=contradictions,
        metadata=metadata,
    )
    _enforce_token_budget(response, request.total_token_budget)

    # --- Step 6: RL logging (KEEP — separate concern, fire-and-forget) ---
    try:
        log_experience(
            db=db,
            tenant_id=request.tenant_id,
            trajectory_id=uuid.uuid4(),
            step_index=0,
            decision_point="memory_recall",
            state={
                "query": request.query[:500],
                "agent_slug": request.agent_slug,
                "num_entity_candidates": len(entities),
                "num_observation_candidates": len(observations),
            },
            action={
                "recalled_entities": [e.name for e in entities],
                "recalled_observations_count": len(observations),
                "top_entity_score": entities[0].similarity if entities else 0,
                "used_keyword_fallback": metadata.used_keyword_fallback,
                "degraded": metadata.degraded,
                "truncated_for_budget": metadata.truncated_for_budget,
            },
            alternatives=[{"method": "keyword_only"}, {"method": "semantic_only"}],
            explanation={"method": "memory_first_v2_pre_recall"},
            state_text=request.query[:500],
        )
    except Exception:
        logger.debug("Failed to log RL experience for memory_recall", exc_info=True)

    metadata.elapsed_ms = (time.perf_counter() - t0) * 1000
    logger.info(
        "memory.recall(tenant=%s, agent=%s): %d ents, %d obs, %d eps, "
        "%d cmts, %d goals, %d rels, %d contradictions in %.0fms (degraded=%s, "
        "fallback=%s, truncated=%s)",
        request.tenant_id, request.agent_slug,
        len(response.entities), len(response.observations), len(response.episodes),
        len(response.commitments), len(response.goals), len(response.relations),
        len(response.contradictions),
        metadata.elapsed_ms, metadata.degraded, metadata.used_keyword_fallback,
        metadata.truncated_for_budget,
    )

    # --- Step 7: Dual-read shadow comparison (observational only) ---
    if dual_read:
        try:
            rust_result = _recall_rust(request)
            if rust_result is not None:
                _compare_and_log(request, response, rust_result)
            else:
                logger.warning("dual-read: Rust recall returned None (tenant=%s)", request.tenant_id)
        except Exception:
            logger.warning("dual-read: comparison failed", exc_info=True)

    return response


# ---------------------------------------------------------------------------
# Keyword fallback (when embed_text fails / returns None)
# ---------------------------------------------------------------------------

def _recall_keyword_only(
    db: Session,
    request: RecallRequest,
    t0: float,
    metadata: RecallMetadata,
) -> RecallResponse:
    """ILIKE-based fallback when the embedding model is unavailable."""
    metadata.used_keyword_fallback = True
    keywords = _extract_keywords(request.query)

    if not keywords:
        metadata.elapsed_ms = (time.perf_counter() - t0) * 1000
        return RecallResponse(metadata=metadata)

    # Match entities by name OR description ILIKE
    entity_filters = []
    for kw in keywords:
        entity_filters.append(KnowledgeEntity.name.ilike(f"%{kw}%"))
        entity_filters.append(KnowledgeEntity.description.ilike(f"%{kw}%"))

    raw_entities = (
        db.query(KnowledgeEntity)
        .filter(
            KnowledgeEntity.tenant_id == request.tenant_id,
            KnowledgeEntity.deleted_at.is_(None),
            or_(*entity_filters),
        )
        .order_by(KnowledgeEntity.confidence.desc().nullslast())
        .limit(request.top_k_per_type)
        .all()
    )
    entities = [
        EntitySummary(
            id=e.id,
            name=e.name,
            category=e.category,
            description=e.description or "",
            confidence=float(e.confidence or 1.0),
            similarity=0.5,  # keyword match — no semantic score
            source_type=e.entity_type,
        )
        for e in raw_entities
    ]

    # Observations matching the query keywords
    obs_filters = [KnowledgeObservation.observation_text.ilike(f"%{kw}%") for kw in keywords]
    raw_obs = (
        db.query(KnowledgeObservation)
        .filter(
            KnowledgeObservation.tenant_id == request.tenant_id,
            or_(*obs_filters),
        )
        .order_by(KnowledgeObservation.created_at.desc())
        .limit(request.top_k_per_type * 2)
        .all()
    )
    observations = [
        ObservationSummary(
            id=o.id,
            entity_id=o.entity_id,
            content=o.observation_text,
            confidence=float(o.confidence or 1.0),
            similarity=0.5,
            created_at=o.created_at or datetime.utcnow(),
        )
        for o in raw_obs
    ]

    response = RecallResponse(
        entities=entities,
        observations=observations,
        metadata=metadata,
    )
    _enforce_token_budget(response, request.total_token_budget)
    metadata.elapsed_ms = (time.perf_counter() - t0) * 1000
    return response


# ---------------------------------------------------------------------------
# Token budget enforcement
# ---------------------------------------------------------------------------

def _estimate_tokens(text_str: str) -> int:
    """Cheap token estimate: 4 chars ≈ 1 token (matches plan spec)."""
    return len(text_str) // 4


def _summary_text(item) -> str:
    """Pull the text content out of a *Summary dataclass for token estimation."""
    for attr in ("content", "description", "summary", "title", "subject", "winning_value"):
        v = getattr(item, attr, None)
        if v:
            return str(v)
    return str(getattr(item, "name", "") or "")


def _enforce_token_budget(response: RecallResponse, budget: int) -> None:
    """Walk result lists in priority order, drop lowest-priority items until total ≤ budget.

    Priority (highest to lowest = least likely to drop):
      commitments → contradictions → entities → observations → episodes →
      past_conversations → goals → relations
    """
    # Compute the running total
    def _total_tokens() -> int:
        total = 0
        for lst in (
            response.commitments, response.contradictions, response.entities,
            response.observations, response.episodes, response.past_conversations,
            response.goals, response.relations,
        ):
            for it in lst:
                total += _estimate_tokens(_summary_text(it))
        return total

    initial_total = _total_tokens()
    response.total_tokens_estimate = initial_total

    if initial_total <= budget:
        return

    # Drop from LOWEST priority first
    drop_order = [
        ("relations", response.relations),
        ("goals", response.goals),
        ("past_conversations", response.past_conversations),
        ("episodes", response.episodes),
        ("observations", response.observations),
        ("entities", response.entities),
        ("contradictions", response.contradictions),
        ("commitments", response.commitments),
    ]

    for _name, lst in drop_order:
        while lst and _total_tokens() > budget:
            lst.pop()
        if _total_tokens() <= budget:
            break

    response.total_tokens_estimate = _total_tokens()
    if response.metadata is not None:
        response.metadata.truncated_for_budget = True


# ---------------------------------------------------------------------------
# Keyword extraction (mirrors legacy memory_recall.extract_keywords)
# ---------------------------------------------------------------------------

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
    "el", "la", "los", "las", "un", "una", "de", "del", "en",
    "que", "por", "para", "con", "como", "pero", "si", "mas",
    "hola", "gracias",
}


def _extract_keywords(message: str) -> list[str]:
    """Extract meaningful keywords from a user message (≥3 chars, not stop)."""
    import re
    words = re.findall(r"[\w]+", message.lower())
    keywords = [
        w for w in words
        if w not in _STOP_WORDS
        and len(w) >= 3
        and not w.isdigit()
    ]
    seen = set()
    out: list[str] = []
    for kw in keywords:
        if kw not in seen:
            seen.add(kw)
            out.append(kw)
    return out[:10]
