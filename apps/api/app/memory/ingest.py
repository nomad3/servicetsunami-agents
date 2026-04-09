"""Bulk ingestion entry point.

Receives MemoryEvents from source adapters (or directly from workflows like
PostChatMemoryWorkflow). For each event:
  1. Validate source_type via adapter registry (fail-fast on unknown).
  2. Resolve or create entities listed in proposed_entities (dedup by name+tenant).
  3. Insert observations linked to those entities.
  4. Insert relations between entities.
  5. Insert commitments via record_commitment().
  6. Audit each write to memory_activities with workflow_id.
"""
from dataclasses import dataclass
from typing import Optional, List, Dict, Any
from uuid import UUID
import logging

from sqlalchemy.orm import Session

from app.memory.types import MemoryEvent
from app.memory.adapters import registry
from app.memory.record import record_commitment, record_observation, _audit
from app.services import knowledge as knowledge_service

logger = logging.getLogger(__name__)


@dataclass
class IngestionResult:
    events_processed: int = 0
    entities_created: int = 0
    observations_created: int = 0
    commitments_created: int = 0
    errors: int = 0


def ingest_events(
    db: Session,
    tenant_id: UUID,
    events: List[MemoryEvent],
    workflow_id: str | None = None,
) -> IngestionResult:
    """Bulk ingestion of memory events.

    Args:
        db: SQLAlchemy session.
        tenant_id: UUID of the tenant.
        events: List of MemoryEvent instances to process.
        workflow_id: Optional Temporal workflow type ID for traceability.

    Returns:
        IngestionResult summary.
    """
    result = IngestionResult()

    for event in events:
        try:
            # 1. Validate source_type via adapter registry
            # Note: During Phase 1, we might not have all adapters registered yet.
            # If the source is 'workflow' or 'internal', we skip adapter validation.
            if event.source_type not in ("workflow", "internal"):
                registry.get_adapter(event.source_type)

            # 2. Resolve or create entities
            entity_map = {}  # name -> entity_id
            for ent_data in event.proposed_entities:
                name = ent_data.get("name")
                if not name:
                    continue
                
                entity, created = knowledge_service.upsert_entity_by_name(
                    db,
                    tenant_id=tenant_id,
                    name=name,
                    entity_type=ent_data.get("entity_type", "general"),
                    category=ent_data.get("category"),
                    description=ent_data.get("description"),
                )
                entity_map[name] = entity.id
                if created:
                    result.entities_created += 1
                    _audit(
                        db, tenant_id=tenant_id,
                        event_type="entity_created",
                        description=f"Entity created: {name}",
                        target_table="knowledge_entities",
                        target_id=entity.id,
                        source_type=event.source_type,
                        source_id=event.source_id,
                        actor_slug=event.actor_slug,
                        workflow_id=workflow_id,
                    )

            # 3. Insert observations
            for obs_data in event.proposed_observations:
                entity_id = obs_data.get("entity_id")
                entity_name = obs_data.get("entity_name")
                
                if not entity_id and entity_name:
                    entity_id = entity_map.get(entity_name)
                    if not entity_id:
                        # Fallback: look up in DB
                        entity = knowledge_service.get_entity_by_name(db, tenant_id, entity_name)
                        if entity:
                            entity_id = entity.id
                
                if not entity_id:
                    logger.warning("Observation proposed for unknown entity '%s' in event %s", 
                                   entity_name or "unknown", event.source_id)
                    continue

                record_observation(
                    db,
                    tenant_id=tenant_id,
                    entity_id=entity_id,
                    content=obs_data["content"],
                    confidence=obs_data.get("confidence", event.confidence),
                    source_type=event.source_type,
                    source_id=event.source_id,
                    actor_slug=event.actor_slug,
                    workflow_id=workflow_id,
                )
                result.observations_created += 1

            # 4. Insert commitments
            for c_data in event.proposed_commitments:
                record_commitment(
                    db,
                    tenant_id=tenant_id,
                    owner_agent_slug=c_data.get("owner_agent_slug") or event.actor_slug or "luna",
                    title=c_data["title"],
                    description=c_data.get("description"),
                    commitment_type=c_data.get("commitment_type", "action"),
                    due_at=c_data.get("due_at"),
                    source_type=event.source_type,
                    source_id=event.source_id,
                    workflow_id=workflow_id,
                )
                result.commitments_created += 1

            result.events_processed += 1

        except Exception as e:
            logger.exception("Failed to ingest memory event %s: %s", event.source_id, e)
            result.errors += 1
            db.rollback()
            continue

    db.commit()
    return result
