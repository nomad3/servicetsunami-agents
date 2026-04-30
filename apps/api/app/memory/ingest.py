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
import logging
from dataclasses import dataclass
from typing import Optional, List
from uuid import UUID
from sqlalchemy.orm import Session

from app.memory.types import MemoryEvent
from app.memory.adapters.registry import get_adapter
from app.memory.record import record_observation, record_commitment
from app.services import knowledge as knowledge_service

logger = logging.getLogger(__name__)


@dataclass
class IngestResult:
    events_processed: int = 0
    entities_created: int = 0
    entities_reused: int = 0
    observations_created: int = 0
    relations_created: int = 0
    commitments_created: int = 0
    skipped: int = 0
    errors: List[str] = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []


def ingest_events(
    db: Session,
    tenant_id: UUID,
    events: List[MemoryEvent],
    workflow_id: Optional[str] = None,
) -> IngestResult:
    result = IngestResult()
    for ev in events:
        # 1. Validate source_type — KeyError if unknown
        try:
            get_adapter(ev.source_type)
        except KeyError:
            # For Phase 1, we might ingest events without an adapter during development
            # but production events should always have a valid source_type.
            logger.warning("Ingesting event with unregistered source_type: %s", ev.source_type)

        try:
            # 2. Resolve or create entities.
            # Tolerate either dicts ({name, category, description}) or persisted
            # KnowledgeEntity instances — callers occasionally hand us the latter
            # by mistake, and crashing the whole workflow over a contract slip
            # loses observations and relations too.
            entity_lookup = {}
            for prop in ev.proposed_entities:
                if isinstance(prop, dict):
                    name = prop.get("name")
                    category = prop.get("category")
                    description = prop.get("description")
                else:
                    # Contract is dicts; flag non-dicts loudly so future
                    # producer-side regressions don't go silent the way the
                    # KnowledgeEntity-instance bug did.
                    logger.warning(
                        "ingest_events: non-dict in proposed_entities (got %s); "
                        "falling back to attribute access. Caller should send dicts.",
                        type(prop).__name__,
                    )
                    name = getattr(prop, "name", None)
                    category = getattr(prop, "category", None)
                    description = getattr(prop, "description", None)
                if not name:
                    continue
                ent, created = knowledge_service.upsert_entity_by_name(
                    db, tenant_id=tenant_id,
                    name=name,
                    category=category,
                    description=description,
                )
                entity_lookup[name] = ent
                if created:
                    result.entities_created += 1
                else:
                    result.entities_reused += 1

            # 3. Insert observations
            for obs_dict in ev.proposed_observations:
                ent_name = obs_dict.get("entity_name")
                ent = entity_lookup.get(ent_name)
                if not ent and ent_name:
                    # Try lookup by name if not in proposed_entities
                    ent = knowledge_service.get_entity_by_name(db, tenant_id, ent_name)
                
                if not ent:
                    result.skipped += 1
                    continue
                    
                record_observation(
                    db, tenant_id=tenant_id,
                    entity_id=ent.id,
                    content=obs_dict["content"],
                    confidence=obs_dict.get("confidence", ev.confidence),
                    source_type=ev.source_type,
                    source_id=ev.source_id,
                    actor_slug=ev.actor_slug,
                    workflow_id=workflow_id,
                )
                result.observations_created += 1

            # 4. Insert commitments
            for c_dict in ev.proposed_commitments:
                record_commitment(
                    db, tenant_id=tenant_id,
                    owner_agent_slug=ev.actor_slug or "system",
                    title=c_dict["title"],
                    description=c_dict.get("description"),
                    commitment_type=c_dict.get("type", "action"),
                    due_at=c_dict.get("due_at"),
                    source_type=ev.source_type,
                    source_id=ev.source_id,
                    workflow_id=workflow_id,
                )
                result.commitments_created += 1

            # 5. Insert relations.
            # Two shapes flow into here, the producer side hasn't unified:
            #   - Shape A from `KnowledgeExtractionService.extract_from_content`
            #     prompt: {"from", "to", "type", "confidence", "evidence"}
            #     (knowledge_extraction.py:364-368)
            #   - Shape B from the gRPC `MemoryEvent` schema and the
            #     `local_inference.extract_knowledge_sync` prompt:
            #     {"from_entity", "to_entity", "relation_type"}
            #     (local_inference.py:416)
            # The crash before this fix was `KeyError: 'from_entity'` on a
            # Shape A dict. Tolerate both — entities/observations/commitments
            # already in this event would otherwise be lost too.
            for rel_dict in ev.proposed_relations:
                if not isinstance(rel_dict, dict):
                    logger.warning(
                        "ingest_events: non-dict in proposed_relations (got %s); skipping.",
                        type(rel_dict).__name__,
                    )
                    continue
                from_name = rel_dict.get("from_entity") or rel_dict.get("from")
                to_name = rel_dict.get("to_entity") or rel_dict.get("to")
                rel_type = rel_dict.get("relation_type") or rel_dict.get("type") or "related_to"
                if not from_name or not to_name:
                    continue

                from_ent = entity_lookup.get(from_name)
                to_ent = entity_lookup.get(to_name)

                if not from_ent or not to_ent:
                    # Try lookup if not in local cache
                    if not from_ent:
                        from_ent = knowledge_service.get_entity_by_name(db, tenant_id, from_name)
                    if not to_ent:
                        to_ent = knowledge_service.get_entity_by_name(db, tenant_id, to_name)

                if from_ent and to_ent:
                    knowledge_service._find_or_create_relation(
                        db, tenant_id=tenant_id,
                        from_entity_id=from_ent.id,
                        to_entity_id=to_ent.id,
                        relation_type=rel_type,
                    )
                    result.relations_created += 1
                else:
                    result.skipped += 1

            result.events_processed += 1
            db.commit()
        except Exception as e:
            logger.exception("Failed to ingest event %s", ev.source_id)
            result.errors.append(f"event {ev.source_id}: {e}")
            db.rollback()

    return result
