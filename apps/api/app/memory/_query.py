"""Internal pgvector query helpers used by `memory.recall()`.

Each helper is small (≤30 lines), takes a `deadline` (a `time.perf_counter()`
absolute timestamp), and bails early — returning an empty list — if the
deadline has already been exceeded BEFORE the DB call. This is the
deadline-checkpoint pattern: SQLAlchemy sessions are not thread-safe, so
we never use a thread pool; we just stop issuing new queries once the
budget is gone.

These helpers know nothing about the orchestrator (`recall.py`) — they
return strongly-typed `*Summary` dataclasses from `app.memory.types`.
"""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import or_, text
from sqlalchemy.orm import Session

from app.memory.types import (
    CommitmentSummary,
    ContradictionSummary,
    EntitySummary,
    EpisodeSummary,
    GoalSummary,
    ObservationSummary,
    RelationSummary,
)
from app.memory.visibility import apply_visibility
from app.models.commitment_record import CommitmentRecord
from app.models.goal_record import GoalRecord
from app.models.knowledge_entity import KnowledgeEntity
from app.models.knowledge_relation import KnowledgeRelation
from app.models.world_state import WorldStateAssertion

logger = logging.getLogger(__name__)


def _check_deadline(deadline: Optional[float]) -> bool:
    """Return True if the deadline (a `time.perf_counter()` value) has passed."""
    if deadline is None:
        return False
    return time.perf_counter() > deadline


def search_entities(
    db: Session,
    tenant_id: uuid.UUID,
    query_embedding: list[float],
    top_k: int,
    agent_slug: str,
    deadline: Optional[float] = None,
) -> list[EntitySummary]:
    """Top-K entities by pgvector cosine similarity, scoped by visibility.

    Now uses KnowledgeEntity.embedding column directly for performance.
    Visibility filter (design doc §7) is inlined into the WHERE clause
    because this query uses raw SQL, not the ORM.
    """
    if _check_deadline(deadline):
        return []
    vec = "[" + ",".join(str(v) for v in query_embedding) + "]"
    sql = text(f"""
        SELECT id, name, entity_type, category, description,
               confidence,
               1 - (embedding <=> CAST('{vec}' AS vector)) AS similarity
        FROM knowledge_entities
        WHERE tenant_id = CAST(:tid AS uuid)
          AND embedding IS NOT NULL
          AND deleted_at IS NULL
          AND (
              visibility = 'tenant_wide'
              OR (visibility = 'agent_scoped' AND owner_agent_slug = :agent)
              OR (visibility = 'agent_group'  AND :agent = ANY(visible_to))
          )
        ORDER BY embedding <=> CAST('{vec}' AS vector)
        LIMIT :lim
    """)
    rows = db.execute(
        sql,
        {"tid": str(tenant_id), "lim": top_k, "agent": agent_slug},
    ).mappings().all()
    return [
        EntitySummary(
            id=r["id"],
            name=r["name"],
            category=r["category"],
            description=r["description"] or "",
            confidence=float(r["confidence"] or 1.0),
            similarity=float(r["similarity"]),
            source_type=r["entity_type"],
        )
        for r in rows
    ]


def search_observations(
    db: Session,
    tenant_id: uuid.UUID,
    entity_ids: list[uuid.UUID],
    query_embedding: list[float],
    top_k: int,
    deadline: Optional[float] = None,
) -> list[ObservationSummary]:
    """Top-K observations across the given entity_ids by cosine similarity."""
    if _check_deadline(deadline) or not entity_ids:
        return []
    vec = "[" + ",".join(str(v) for v in query_embedding) + "]"
    sql = text(f"""
        SELECT id, entity_id, observation_text, confidence, created_at,
               1 - (embedding <=> CAST('{vec}' AS vector)) AS similarity
        FROM knowledge_observations
        WHERE tenant_id = CAST(:tid AS uuid)
          AND entity_id = ANY(CAST(:ids AS uuid[]))
          AND embedding IS NOT NULL
        ORDER BY embedding <=> CAST('{vec}' AS vector)
        LIMIT :lim
    """)
    rows = db.execute(
        sql,
        {"tid": str(tenant_id), "ids": [str(eid) for eid in entity_ids], "lim": top_k},
    ).mappings().all()
    return [
        ObservationSummary(
            id=r["id"],
            entity_id=r["entity_id"],
            content=r["observation_text"],
            confidence=float(r["confidence"] or 1.0),
            similarity=float(r["similarity"]),
            created_at=r["created_at"] or datetime.utcnow(),
        )
        for r in rows
    ]


def search_episodes(
    db: Session,
    tenant_id: uuid.UUID,
    query_embedding: list[float],
    top_k: int,
    deadline: Optional[float] = None,
) -> list[EpisodeSummary]:
    """Top-K conversation episodes by cosine similarity (>0.3 threshold)."""
    if _check_deadline(deadline):
        return []
    vec = "[" + ",".join(str(v) for v in query_embedding) + "]"
    sql = text(f"""
        SELECT id, session_id, summary, key_topics, key_entities, created_at,
               1 - (embedding <=> CAST('{vec}' AS vector)) AS similarity
        FROM conversation_episodes
        WHERE tenant_id = CAST(:tid AS uuid)
          AND embedding IS NOT NULL
        ORDER BY embedding <=> CAST('{vec}' AS vector)
        LIMIT :lim
    """)
    rows = db.execute(sql, {"tid": str(tenant_id), "lim": top_k}).mappings().all()
    out: list[EpisodeSummary] = []
    for r in rows:
        sim = float(r["similarity"])
        if sim < 0.3:
            continue
        out.append(EpisodeSummary(
            id=r["id"],
            session_id=r["session_id"],
            summary=r["summary"] or "",
            key_topics=r["key_topics"] or [],
            key_entities=r["key_entities"] or [],
            created_at=r["created_at"] or datetime.utcnow(),
            similarity=sim,
        ))
    return out


def search_commitments(
    db: Session,
    tenant_id: uuid.UUID,
    agent_slug: str,
    top_k: int,
    query_embedding: Optional[list[float]] = None,
    deadline: Optional[float] = None,
) -> list[CommitmentSummary]:
    """Top-K open commitments. Uses semantic search if embedding provided, fallback to recency."""
    if _check_deadline(deadline):
        return []

    if query_embedding:
        vec = "[" + ",".join(str(v) for v in query_embedding) + "]"
        sql = text(f"""
            SELECT id, title, state, due_at, priority,
                   1 - (embedding <=> CAST('{vec}' AS vector)) AS similarity
            FROM commitment_records
            WHERE tenant_id = CAST(:tid AS uuid)
              AND state = 'open'
              AND embedding IS NOT NULL
              AND (
                  visibility = 'tenant_wide'
                  OR (visibility = 'agent_scoped' AND owner_agent_slug = :agent)
                  OR (visibility = 'agent_group'  AND :agent = ANY(visible_to))
              )
            ORDER BY embedding <=> CAST('{vec}' AS vector)
            LIMIT :lim
        """)
        rows = db.execute(
            sql,
            {"tid": str(tenant_id), "lim": top_k, "agent": agent_slug},
        ).mappings().all()
        return [
            CommitmentSummary(
                id=r["id"],
                title=r["title"],
                state=r["state"],
                due_at=r["due_at"],
                priority=r["priority"] or "normal",
                similarity=float(r["similarity"]),
            )
            for r in rows
        ]

    # Fallback to recency/due_at if no embedding
    q = (
        db.query(CommitmentRecord)
        .filter(
            CommitmentRecord.tenant_id == tenant_id,
            CommitmentRecord.state == "open",
        )
    )
    q = apply_visibility(q, CommitmentRecord, agent_slug)
    rows = q.order_by(CommitmentRecord.due_at.asc().nullslast()).limit(top_k).all()
    return [
        CommitmentSummary(
            id=r.id,
            title=r.title,
            state=r.state,
            due_at=r.due_at,
            priority=r.priority or "normal",
            similarity=1.0,
        )
        for r in rows
    ]


def search_goals(
    db: Session,
    tenant_id: uuid.UUID,
    agent_slug: str,
    top_k: int,
    query_embedding: Optional[list[float]] = None,
    deadline: Optional[float] = None,
) -> list[GoalSummary]:
    """Top-K active goals. Uses semantic search if embedding provided, fallback to recency."""
    if _check_deadline(deadline):
        return []

    if query_embedding:
        vec = "[" + ",".join(str(v) for v in query_embedding) + "]"
        sql = text(f"""
            SELECT id, title, state, progress_pct, priority,
                   1 - (embedding <=> CAST('{vec}' AS vector)) AS similarity
            FROM goal_records
            WHERE tenant_id = CAST(:tid AS uuid)
              AND state IN ('active', 'proposed', 'in_progress')
              AND embedding IS NOT NULL
              AND (
                  visibility = 'tenant_wide'
                  OR (visibility = 'agent_scoped' AND owner_agent_slug = :agent)
                  OR (visibility = 'agent_group'  AND :agent = ANY(visible_to))
              )
            ORDER BY embedding <=> CAST('{vec}' AS vector)
            LIMIT :lim
        """)
        rows = db.execute(
            sql,
            {"tid": str(tenant_id), "lim": top_k, "agent": agent_slug},
        ).mappings().all()
        return [
            GoalSummary(
                id=r["id"],
                title=r["title"],
                state=r["state"],
                progress_pct=int(r["progress_pct"] or 0),
                priority=r["priority"] or "normal",
                similarity=float(r["similarity"]),
            )
            for r in rows
        ]

    # Fallback to recency/updated_at if no embedding
    q = (
        db.query(GoalRecord)
        .filter(
            GoalRecord.tenant_id == tenant_id,
            GoalRecord.state.in_(["active", "proposed", "in_progress"]),
        )
    )
    q = apply_visibility(q, GoalRecord, agent_slug)
    rows = q.order_by(GoalRecord.updated_at.desc()).limit(top_k).all()
    return [
        GoalSummary(
            id=r.id,
            title=r.title,
            state=r.state,
            progress_pct=int(r.progress_pct or 0),
            priority=r.priority or "normal",
            similarity=1.0,
        )
        for r in rows
    ]


def search_relations(
    db: Session,
    tenant_id: uuid.UUID,
    entity_ids: list[uuid.UUID],
    deadline: Optional[float] = None,
) -> list[RelationSummary]:
    """Relations connecting any pair of the given entity_ids (or one + foreign)."""
    if _check_deadline(deadline) or not entity_ids:
        return []
    rels = (
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
    if not rels:
        return []
    # Resolve entity names for the relation endpoints
    rel_entity_ids = set()
    for r in rels:
        rel_entity_ids.add(r.from_entity_id)
        rel_entity_ids.add(r.to_entity_id)
    name_rows = (
        db.query(KnowledgeEntity.id, KnowledgeEntity.name)
        .filter(KnowledgeEntity.id.in_(list(rel_entity_ids)))
        .all()
    )
    name_map = {eid: ename for eid, ename in name_rows}
    return [
        RelationSummary(
            id=r.id,
            from_entity=name_map.get(r.from_entity_id, str(r.from_entity_id)),
            to_entity=name_map.get(r.to_entity_id, str(r.to_entity_id)),
            relation_type=r.relation_type,
            confidence=float(r.strength or 1.0),
        )
        for r in rels
    ]


def search_world_state_contradictions(
    db: Session,
    tenant_id: uuid.UUID,
    entity_ids: list[uuid.UUID],
    top_k: int = 5,
    deadline: Optional[float] = None,
) -> list[ContradictionSummary]:
    """Disputed world-state assertions on the given entities."""
    if _check_deadline(deadline) or not entity_ids:
        return []
    rows = (
        db.query(WorldStateAssertion)
        .filter(
            WorldStateAssertion.tenant_id == tenant_id,
            WorldStateAssertion.subject_entity_id.in_(entity_ids),
            WorldStateAssertion.status == "disputed",
        )
        .limit(top_k)
        .all()
    )
    return [
        ContradictionSummary(
            assertion_id=d.id,
            subject=d.subject_slug,
            predicate=d.attribute_path,
            winning_value=str(d.value_json) if d.value_json is not None else "",
            losing_value=str(d.previous_value_json) if d.previous_value_json is not None else "",
            losing_source=d.dispute_reason or "",
        )
        for d in rows
    ]
