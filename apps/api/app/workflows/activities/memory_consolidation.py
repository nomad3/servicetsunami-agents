"""Temporal activities for memory consolidation workflow.

Handles entity deduplication, memory decay, entity lifecycle promotions,
memory-entity synchronization, and consolidation audit logging.
"""
import json
import logging
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List

from sqlalchemy import text, func, or_
from temporalio import activity

from app.db.session import SessionLocal
from app.models.knowledge_entity import KnowledgeEntity
from app.models.knowledge_relation import KnowledgeRelation
from app.models.knowledge_observation import KnowledgeObservation
from app.models.agent_memory import AgentMemory
from app.services.memory_activity import log_activity

logger = logging.getLogger(__name__)


@activity.defn
async def find_duplicate_entities(tenant_id: str) -> Dict[str, Any]:
    """Find entities with same entity_type and high name similarity.

    Uses embedding cosine similarity > 0.92 for entities that have embeddings,
    and falls back to exact name matching (case-insensitive) for others.
    Returns clusters of duplicate entity IDs.
    """
    db = SessionLocal()
    try:
        tid = uuid.UUID(tenant_id)
        clusters: List[List[str]] = []
        seen_ids = set()

        # Strategy 1: Find entities with identical names (case-insensitive) + same type
        sql = text("""
            SELECT a.id AS a_id, b.id AS b_id
            FROM knowledge_entities a
            JOIN knowledge_entities b
                ON a.tenant_id = b.tenant_id
                AND a.entity_type = b.entity_type
                AND LOWER(TRIM(a.name)) = LOWER(TRIM(b.name))
                AND a.id < b.id
            WHERE a.tenant_id = CAST(:tid AS uuid)
              AND a.deleted_at IS NULL
              AND b.deleted_at IS NULL
            LIMIT 200
        """)
        rows = db.execute(sql, {"tid": str(tid)}).fetchall()

        # Build clusters from pairs
        pair_map: Dict[str, set] = {}
        for row in rows:
            a_id = str(row.a_id)
            b_id = str(row.b_id)
            # Find existing cluster
            found_cluster = None
            for key, cluster in pair_map.items():
                if a_id in cluster or b_id in cluster:
                    found_cluster = key
                    break
            if found_cluster:
                pair_map[found_cluster].add(a_id)
                pair_map[found_cluster].add(b_id)
            else:
                pair_map[a_id] = {a_id, b_id}

        for cluster_set in pair_map.values():
            cluster_list = list(cluster_set)
            if any(cid in seen_ids for cid in cluster_list):
                continue
            clusters.append(cluster_list)
            seen_ids.update(cluster_list)

        # Strategy 2: Embedding cosine similarity > 0.92
        emb_sql = text("""
            SELECT a.content_id AS a_id, b.content_id AS b_id,
                   1 - (a.embedding <=> b.embedding) AS similarity
            FROM embeddings a
            JOIN embeddings b
                ON a.tenant_id = b.tenant_id
                AND a.content_type = b.content_type
                AND a.content_id < b.content_id
            WHERE a.tenant_id = CAST(:tid AS uuid)
              AND a.content_type = 'entity'
              AND 1 - (a.embedding <=> b.embedding) > 0.92
            LIMIT 200
        """)
        try:
            emb_rows = db.execute(emb_sql, {"tid": str(tid)}).fetchall()
            for row in emb_rows:
                a_id = str(row.a_id)
                b_id = str(row.b_id)
                if a_id in seen_ids and b_id in seen_ids:
                    continue
                # Check they are not already in a cluster together
                found = False
                for cluster in clusters:
                    if a_id in cluster or b_id in cluster:
                        if a_id not in cluster:
                            cluster.append(a_id)
                        if b_id not in cluster:
                            cluster.append(b_id)
                        seen_ids.add(a_id)
                        seen_ids.add(b_id)
                        found = True
                        break
                if not found:
                    clusters.append([a_id, b_id])
                    seen_ids.update([a_id, b_id])
        except Exception as e:
            logger.debug("Embedding similarity search failed (non-fatal): %s", e)

        logger.info(
            "Found %d duplicate clusters for tenant %s",
            len(clusters), tenant_id[:8],
        )
        return {"clusters": clusters, "count": len(clusters)}

    except Exception as e:
        logger.exception("find_duplicate_entities failed: %s", e)
        return {"clusters": [], "count": 0, "error": str(e)}
    finally:
        db.close()


@activity.defn
async def auto_merge_duplicates(tenant_id: str, clusters_json: str) -> Dict[str, Any]:
    """Merge duplicate entity clusters.

    For each cluster: keep the entity with the highest recall_count,
    transfer relations and observations to it, then soft-delete duplicates.
    """
    db = SessionLocal()
    try:
        tid = uuid.UUID(tenant_id)
        clusters = json.loads(clusters_json)
        merged_count = 0

        for cluster in clusters:
            if len(cluster) < 2:
                continue

            entity_ids = [uuid.UUID(eid) for eid in cluster]

            # Find the primary entity (highest recall_count)
            entities = (
                db.query(KnowledgeEntity)
                .filter(
                    KnowledgeEntity.id.in_(entity_ids),
                    KnowledgeEntity.tenant_id == tid,
                    KnowledgeEntity.deleted_at.is_(None),
                )
                .order_by(KnowledgeEntity.recall_count.desc().nullslast())
                .all()
            )

            if len(entities) < 2:
                continue

            primary = entities[0]
            duplicates = entities[1:]

            for dup in duplicates:
                # Transfer relations: update from_entity_id
                db.query(KnowledgeRelation).filter(
                    KnowledgeRelation.from_entity_id == dup.id,
                    KnowledgeRelation.tenant_id == tid,
                ).update(
                    {"from_entity_id": primary.id},
                    synchronize_session="fetch",
                )

                # Transfer relations: update to_entity_id
                db.query(KnowledgeRelation).filter(
                    KnowledgeRelation.to_entity_id == dup.id,
                    KnowledgeRelation.tenant_id == tid,
                ).update(
                    {"to_entity_id": primary.id},
                    synchronize_session="fetch",
                )

                # Transfer observations
                db.query(KnowledgeObservation).filter(
                    KnowledgeObservation.entity_id == dup.id,
                    KnowledgeObservation.tenant_id == tid,
                ).update(
                    {"entity_id": primary.id},
                    synchronize_session="fetch",
                )

                # Accumulate counts
                primary.recall_count = (primary.recall_count or 0) + (dup.recall_count or 0)
                primary.reference_count = (primary.reference_count or 0) + (dup.reference_count or 0)

                # Soft-delete duplicate
                dup.deleted_at = datetime.utcnow()
                merged_count += 1

            db.commit()

        logger.info("Merged %d duplicate entities for tenant %s", merged_count, tenant_id[:8])
        return {"merged": merged_count, "notified": 0}

    except Exception as e:
        logger.exception("auto_merge_duplicates failed: %s", e)
        db.rollback()
        return {"merged": 0, "notified": 0, "error": str(e)}
    finally:
        db.close()


@activity.defn
async def apply_memory_decay(tenant_id: str) -> Dict[str, Any]:
    """Apply time-based decay to agent memories.

    For memories with importance < 0.9 and last_accessed > 30 days:
    - Calculate effective = importance * (0.95 ^ days_since_access)
    - Archive when effective < 0.05 and access_count < 2 (set expires_at = now())
    """
    db = SessionLocal()
    try:
        tid = uuid.UUID(tenant_id)
        cutoff = datetime.utcnow() - timedelta(days=30)

        memories = (
            db.query(AgentMemory)
            .filter(
                AgentMemory.tenant_id == tid,
                AgentMemory.importance < 0.9,
                AgentMemory.expires_at.is_(None),
                or_(
                    AgentMemory.last_accessed_at < cutoff,
                    AgentMemory.last_accessed_at.is_(None),
                ),
            )
            .limit(500)
            .all()
        )

        decayed = 0
        archived = 0
        now = datetime.utcnow()

        for mem in memories:
            last_access = mem.last_accessed_at or mem.created_at
            days_since = max(1, (now - last_access).days)
            effective = (mem.importance or 0.5) * (0.95 ** days_since)

            if effective < 0.05 and (mem.access_count or 0) < 2:
                mem.expires_at = now
                archived += 1
            decayed += 1

        db.commit()

        logger.info(
            "Memory decay for tenant %s: %d evaluated, %d archived",
            tenant_id[:8], decayed, archived,
        )
        return {"decayed": decayed, "archived": archived}

    except Exception as e:
        logger.exception("apply_memory_decay failed: %s", e)
        db.rollback()
        return {"decayed": 0, "archived": 0, "error": str(e)}
    finally:
        db.close()


@activity.defn
async def promote_entities(tenant_id: str) -> Dict[str, Any]:
    """Promote entities through lifecycle stages.

    - draft -> verified: confidence > 0.7, age > 7 days
    - verified -> enriched: 3+ relations OR recall_count > 5
    - any -> archived: 90 days no activity, zero recalls/references
    """
    db = SessionLocal()
    try:
        tid = uuid.UUID(tenant_id)
        now = datetime.utcnow()
        promotions = {"draft_to_verified": 0, "verified_to_enriched": 0, "archived": 0}

        # draft -> verified: confidence > 0.7 and older than 7 days
        seven_days_ago = now - timedelta(days=7)
        draft_entities = (
            db.query(KnowledgeEntity)
            .filter(
                KnowledgeEntity.tenant_id == tid,
                KnowledgeEntity.status == "draft",
                KnowledgeEntity.confidence > 0.7,
                KnowledgeEntity.created_at < seven_days_ago,
                KnowledgeEntity.deleted_at.is_(None),
            )
            .limit(200)
            .all()
        )
        for ent in draft_entities:
            ent.status = "verified"
            ent.updated_at = now
            promotions["draft_to_verified"] += 1

        # verified -> enriched: 3+ relations OR recall_count > 5
        verified_entities = (
            db.query(KnowledgeEntity)
            .filter(
                KnowledgeEntity.tenant_id == tid,
                KnowledgeEntity.status == "verified",
                KnowledgeEntity.deleted_at.is_(None),
            )
            .limit(200)
            .all()
        )
        for ent in verified_entities:
            if (ent.recall_count or 0) > 5:
                ent.status = "enriched"
                ent.updated_at = now
                promotions["verified_to_enriched"] += 1
                continue
            # Check relation count
            rel_count = db.query(func.count(KnowledgeRelation.id)).filter(
                or_(
                    KnowledgeRelation.from_entity_id == ent.id,
                    KnowledgeRelation.to_entity_id == ent.id,
                ),
                KnowledgeRelation.tenant_id == tid,
            ).scalar() or 0
            if rel_count >= 3:
                ent.status = "enriched"
                ent.updated_at = now
                promotions["verified_to_enriched"] += 1

        # any -> archived: 90 days no activity, zero recalls/references
        ninety_days_ago = now - timedelta(days=90)
        stale_entities = (
            db.query(KnowledgeEntity)
            .filter(
                KnowledgeEntity.tenant_id == tid,
                KnowledgeEntity.status.notin_(["archived"]),
                KnowledgeEntity.deleted_at.is_(None),
                KnowledgeEntity.updated_at < ninety_days_ago,
                or_(
                    KnowledgeEntity.recall_count.is_(None),
                    KnowledgeEntity.recall_count == 0,
                ),
                or_(
                    KnowledgeEntity.reference_count.is_(None),
                    KnowledgeEntity.reference_count == 0,
                ),
            )
            .limit(200)
            .all()
        )
        for ent in stale_entities:
            ent.status = "archived"
            ent.updated_at = now
            promotions["archived"] += 1

        db.commit()

        logger.info("Entity promotions for tenant %s: %s", tenant_id[:8], promotions)
        return promotions

    except Exception as e:
        logger.exception("promote_entities failed: %s", e)
        db.rollback()
        return {"draft_to_verified": 0, "verified_to_enriched": 0, "archived": 0, "error": str(e)}
    finally:
        db.close()


@activity.defn
async def sync_memories_and_entities(tenant_id: str) -> Dict[str, Any]:
    """Sync high-importance facts from AgentMemory to KnowledgeEntity.

    High-importance facts (importance >= 0.8, type='fact') in AgentMemory that
    don't have a corresponding KnowledgeEntity (by name match) get a new entity
    created.
    """
    db = SessionLocal()
    try:
        tid = uuid.UUID(tenant_id)
        synced = 0

        # Find high-importance fact memories
        fact_memories = (
            db.query(AgentMemory)
            .filter(
                AgentMemory.tenant_id == tid,
                AgentMemory.memory_type == "fact",
                AgentMemory.importance >= 0.8,
                AgentMemory.expires_at.is_(None),
            )
            .limit(100)
            .all()
        )

        for mem in fact_memories:
            # Use the first line or first 100 chars as the entity name
            content = mem.content.strip()
            name = content.split("\n")[0][:100].strip()
            if not name:
                continue

            # Check if entity with this name already exists
            existing = (
                db.query(KnowledgeEntity.id)
                .filter(
                    KnowledgeEntity.tenant_id == tid,
                    func.lower(KnowledgeEntity.name) == func.lower(name),
                    KnowledgeEntity.deleted_at.is_(None),
                )
                .first()
            )
            if existing:
                continue

            # Create new entity from memory
            entity = KnowledgeEntity(
                tenant_id=tid,
                entity_type="concept",
                category="fact",
                name=name,
                description=content[:500] if len(content) > 100 else None,
                confidence=mem.importance,
                status="draft",
                source_url=f"memory:{mem.id}",
            )
            db.add(entity)
            synced += 1

        db.commit()

        logger.info("Synced %d memories to entities for tenant %s", synced, tenant_id[:8])
        return {"synced": synced}

    except Exception as e:
        logger.exception("sync_memories_and_entities failed: %s", e)
        db.rollback()
        return {"synced": 0, "error": str(e)}
    finally:
        db.close()


@activity.defn
async def log_consolidation_results(tenant_id: str, results_json: str) -> Dict[str, Any]:
    """Log consolidation summary as a memory_activity record."""
    db = SessionLocal()
    try:
        tid = uuid.UUID(tenant_id)
        results = json.loads(results_json)

        # Build summary description
        parts = []
        if "duplicates" in results:
            parts.append(f"{results['duplicates'].get('count', 0)} dup clusters found")
        if "merge" in results:
            parts.append(f"{results['merge'].get('merged', 0)} merged")
        if "decay" in results:
            parts.append(f"{results['decay'].get('archived', 0)} memories archived")
        if "promotions" in results:
            promo = results["promotions"]
            total_promoted = promo.get("draft_to_verified", 0) + promo.get("verified_to_enriched", 0)
            parts.append(f"{total_promoted} promoted, {promo.get('archived', 0)} entities archived")
        if "sync" in results:
            parts.append(f"{results['sync'].get('synced', 0)} synced")

        description = f"Memory consolidation: {', '.join(parts)}" if parts else "Memory consolidation cycle completed"

        log_activity(
            db,
            tenant_id=tid,
            event_type="memory_consolidation",
            description=description,
            source="consolidation_workflow",
            event_metadata=results,
        )
        return {"status": "logged"}

    except Exception as e:
        logger.exception("log_consolidation_results failed: %s", e)
        return {"status": "error", "error": str(e)}
    finally:
        db.close()
