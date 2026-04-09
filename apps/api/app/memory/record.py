"""Sync memory writes — small, fast, request-thread.

Phase 1: thin wrappers over existing services (knowledge, commitment_service,
goal_service) that ALSO write to memory_activities for audit traceability.
Phase 2: Rust memory-core gRPC service replaces the wrappers.
"""
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID
from sqlalchemy.orm import Session
from sqlalchemy import text as sql_text

from app.services import knowledge as knowledge_service
from app.services import commitment_service, goal_service
from app.schemas.commitment_record import CommitmentRecordCreate, CommitmentType, CommitmentPriority, CommitmentSourceType
from app.schemas.goal_record import GoalRecordCreate
from app.models.knowledge_observation import KnowledgeObservation
from app.models.commitment_record import CommitmentRecord
from app.models.goal_record import GoalRecord
from app.models.memory_activity import MemoryActivity


def _audit(
    db: Session, *,
    tenant_id: UUID,
    event_type: str,
    description: str,
    target_table: str,  # logical table name — stored in metadata
    target_id: UUID,
    source_type: Optional[str] = None,
    source_id: Optional[str] = None,
    actor_slug: Optional[str] = None,
    workflow_id: Optional[str] = None,
    workflow_run_id: Optional[str] = None,
    entity_id: Optional[UUID] = None,
    memory_id: Optional[UUID] = None,
):
    """Write a MemoryActivity audit row using ONLY columns that exist."""
    db.add(MemoryActivity(
        tenant_id=tenant_id,
        event_type=event_type,
        description=description,
        source=source_type,  # the existing column is just `source`
        event_metadata={
            "target_table": target_table,
            "target_id": str(target_id),
            "source_id": source_id,
            "actor_slug": actor_slug,
        },
        workflow_id=workflow_id,
        workflow_run_id=workflow_run_id,
        entity_id=entity_id,
        memory_id=memory_id,
        created_at=datetime.utcnow(),
    ))


def _find_existing_by_source(
    db: Session, tenant_id: UUID, target_table: str,
    source_type: str, source_id: str,
):
    """Look up an existing memory_activities row for dedup. Returns the
    target_id if found, else None."""
    row = db.execute(sql_text("""
        SELECT (metadata->>'target_id') AS target_id
        FROM memory_activities
        WHERE tenant_id = :t
          AND metadata->>'source_type' = :st
          AND metadata->>'source_id' = :sid
          AND metadata->>'target_table' = :tt
        ORDER BY created_at DESC LIMIT 1
    """), {"t": str(tenant_id), "st": source_type, "sid": source_id, "tt": target_table}).first()
    return row.target_id if row else None


def record_observation(
    db: Session, tenant_id: UUID, *,
    entity_id: UUID, content: str, confidence: float = 0.7,
    source_type: Optional[str] = None, source_id: Optional[str] = None,
    actor_slug: Optional[str] = None, workflow_id: Optional[str] = None,
) -> KnowledgeObservation:
    # Dedup by source_type + source_id (stored in metadata)
    if source_type and source_id:
        existing_id = _find_existing_by_source(
            db, tenant_id, "knowledge_observations", source_type, source_id
        )
        if existing_id:
            existing = db.get(KnowledgeObservation, UUID(existing_id))
            if existing:
                return existing

    obs = knowledge_service.create_observation(
        db, tenant_id=tenant_id,
        observation_text=content,
        observation_type="fact",
        source_type=source_type or "memory_record",
        entity_id=entity_id,
        confidence=confidence,
    )
    _audit(db, tenant_id=tenant_id,
           event_type="observation_created",
           description=f"Observation on entity {entity_id}: {content[:80]}",
           target_table="knowledge_observations", target_id=obs.id,
           source_type=source_type, source_id=source_id,
           actor_slug=actor_slug, workflow_id=workflow_id,
           entity_id=entity_id)
    db.commit()
    return obs


def record_commitment(
    db: Session, tenant_id: UUID, *,
    owner_agent_slug: str, title: str, description: Optional[str] = None,
    commitment_type: str = "action", due_at: Optional[datetime] = None,
    source_type: Optional[str] = None, source_id: Optional[str] = None,
    workflow_id: Optional[str] = None,
) -> CommitmentRecord:
    if source_type and source_id:
        existing_id = _find_existing_by_source(
            db, tenant_id, "commitment_records", source_type, source_id
        )
        if existing_id:
            existing = db.get(CommitmentRecord, UUID(existing_id))
            if existing:
                return existing

    # Build the Pydantic schema the service expects.
    commitment_in = CommitmentRecordCreate(
        owner_agent_slug=owner_agent_slug,
        title=title,
        description=description,
        commitment_type=CommitmentType(commitment_type),
        due_at=due_at,
        source_type=CommitmentSourceType.TOOL_CALL,
        source_ref={"memory_source_type": source_type, "memory_source_id": source_id} if source_type else {},
    )
    c = commitment_service.create_commitment(db, tenant_id=tenant_id, commitment_in=commitment_in)

    _audit(db, tenant_id=tenant_id,
           event_type="commitment_created",
           description=f"Commitment: {title[:80]}",
           target_table="commitment_records", target_id=c.id,
           source_type=source_type, source_id=source_id,
           actor_slug=owner_agent_slug, workflow_id=workflow_id)
    db.commit()
    return c


def record_goal(
    db: Session, tenant_id: UUID, *,
    owner_agent_slug: str, title: str,
    source_type: Optional[str] = None, source_id: Optional[str] = None,
    **kwargs,
) -> GoalRecord:
    goal_in = GoalRecordCreate(owner_agent_slug=owner_agent_slug, title=title, **kwargs)
    g = goal_service.create_goal(db, tenant_id=tenant_id, goal_in=goal_in)
    _audit(db, tenant_id=tenant_id,
           event_type="goal_created",
           description=f"Goal: {title[:80]}",
           target_table="goal_records", target_id=g.id,
           source_type=source_type, source_id=source_id,
           actor_slug=owner_agent_slug)
    db.commit()
    return g
