"""Temporal activities for embedding backfill workflow.

Finds entities, memories, and observations missing embeddings and generates
them in batches of 100 using the embedding service.
"""
import logging
import uuid
from typing import Any, Dict

from temporalio import activity

from app.db.session import SessionLocal
from app.services.embedding_service import embed_text, embed_and_store

logger = logging.getLogger(__name__)

BATCH_SIZE = 100


@activity.defn
async def backfill_entity_embeddings(tenant_id: str) -> Dict[str, Any]:
    """Find entities not in the embeddings table and create embeddings for them.

    Looks for knowledge_entities whose ID does not appear in the embeddings
    table with content_type='entity'. Processes in batches of 100.
    """
    from sqlalchemy import text

    db = SessionLocal()
    try:
        tid = uuid.UUID(tenant_id)
        total_embedded = 0

        while True:
            # Find entities missing from embeddings table
            sql = text("""
                SELECT ke.id, ke.name, ke.entity_type, ke.description, ke.category
                FROM knowledge_entities ke
                LEFT JOIN embeddings e
                    ON CAST(ke.id AS text) = e.content_id
                    AND e.content_type = 'entity'
                WHERE ke.tenant_id = CAST(:tid AS uuid)
                  AND ke.deleted_at IS NULL
                  AND e.id IS NULL
                LIMIT :batch
            """)
            rows = db.execute(sql, {"tid": str(tid), "batch": BATCH_SIZE}).fetchall()

            if not rows:
                break

            for row in rows:
                text_content = f"{row.name} ({row.entity_type})"
                if row.category:
                    text_content += f" [{row.category}]"
                if row.description:
                    text_content += f": {row.description}"

                embed_and_store(
                    db=db,
                    tenant_id=tid,
                    content_type="entity",
                    content_id=str(row.id),
                    text_content=text_content,
                )
                total_embedded += 1

            db.commit()
            activity.heartbeat(f"Entities embedded so far: {total_embedded}")

        logger.info("Backfilled %d entity embeddings for tenant %s", total_embedded, tenant_id[:8])
        return {"embedded": total_embedded}

    except Exception as e:
        logger.exception("backfill_entity_embeddings failed: %s", e)
        db.rollback()
        return {"embedded": 0, "error": str(e)}
    finally:
        db.close()


@activity.defn
async def backfill_memory_embeddings(tenant_id: str) -> Dict[str, Any]:
    """Find agent_memories where content_embedding IS NULL and generate embeddings.

    Updates the content_embedding column (pgvector Vector(768)) directly on the
    agent_memories table. Processes in batches of 100.
    """
    from sqlalchemy import text

    db = SessionLocal()
    try:
        tid = uuid.UUID(tenant_id)
        total_embedded = 0

        while True:
            sql = text("""
                SELECT id, content, memory_type
                FROM agent_memories
                WHERE tenant_id = CAST(:tid AS uuid)
                  AND content_embedding IS NULL
                  AND content IS NOT NULL
                LIMIT :batch
            """)
            rows = db.execute(sql, {"tid": str(tid), "batch": BATCH_SIZE}).fetchall()

            if not rows:
                break

            for row in rows:
                text_content = f"{row.memory_type}: {row.content}"
                vector = embed_text(text_content)
                if vector is not None:
                    vector_literal = "[" + ",".join(str(v) for v in vector) + "]"
                    update_sql = text("""
                        UPDATE agent_memories
                        SET content_embedding = CAST(:vec AS vector)
                        WHERE id = CAST(:mid AS uuid)
                    """)
                    db.execute(update_sql, {"vec": vector_literal, "mid": str(row.id)})
                    total_embedded += 1

            db.commit()
            activity.heartbeat(f"Memories embedded so far: {total_embedded}")

        logger.info("Backfilled %d memory embeddings for tenant %s", total_embedded, tenant_id[:8])
        return {"embedded": total_embedded}

    except Exception as e:
        logger.exception("backfill_memory_embeddings failed: %s", e)
        db.rollback()
        return {"embedded": 0, "error": str(e)}
    finally:
        db.close()


@activity.defn
async def backfill_observation_embeddings(tenant_id: str) -> Dict[str, Any]:
    """Find knowledge_observations where embedding IS NULL and generate embeddings.

    Updates the embedding column (pgvector Vector(768)) directly on the
    knowledge_observations table. Processes in batches of 100.
    """
    from sqlalchemy import text

    db = SessionLocal()
    try:
        tid = uuid.UUID(tenant_id)
        total_embedded = 0

        while True:
            sql = text("""
                SELECT id, observation_text, observation_type
                FROM knowledge_observations
                WHERE tenant_id = CAST(:tid AS uuid)
                  AND embedding IS NULL
                  AND observation_text IS NOT NULL
                LIMIT :batch
            """)
            rows = db.execute(sql, {"tid": str(tid), "batch": BATCH_SIZE}).fetchall()

            if not rows:
                break

            for row in rows:
                text_content = f"{row.observation_type}: {row.observation_text}"
                vector = embed_text(text_content)
                if vector is not None:
                    vector_literal = "[" + ",".join(str(v) for v in vector) + "]"
                    update_sql = text("""
                        UPDATE knowledge_observations
                        SET embedding = CAST(:vec AS vector)
                        WHERE id = CAST(:oid AS uuid)
                    """)
                    db.execute(update_sql, {"vec": vector_literal, "oid": str(row.id)})
                    total_embedded += 1

            db.commit()
            activity.heartbeat(f"Observations embedded so far: {total_embedded}")

        logger.info("Backfilled %d observation embeddings for tenant %s", total_embedded, tenant_id[:8])
        return {"embedded": total_embedded}

    except Exception as e:
        logger.exception("backfill_observation_embeddings failed: %s", e)
        db.rollback()
        return {"embedded": 0, "error": str(e)}
    finally:
        db.close()
