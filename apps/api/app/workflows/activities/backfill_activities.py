"""Activities for BackfillEmbeddingsWorkflow."""
import logging
from uuid import UUID
from temporalio import activity

from app.db.session import SessionLocal
from app.services.embedding_service import embed_and_store
from sqlalchemy import text as sql_text

logger = logging.getLogger(__name__)


@activity.defn
async def find_unembedded_chat_messages(tenant_id: str, batch_size: int) -> list[dict]:
    """Find chat messages that don't have embeddings yet."""
    db = SessionLocal()
    try:
        # Join with chat_sessions to filter by tenant_id
        rows = db.execute(sql_text("""
            SELECT cm.id, cm.role, cm.content
            FROM chat_messages cm
            JOIN chat_sessions cs ON cs.id = cm.session_id
            WHERE cs.tenant_id = CAST(:t AS uuid)
              AND char_length(cm.content) > 5
              AND NOT EXISTS (
                SELECT 1 FROM embeddings e
                WHERE e.content_type = 'chat_message'
                  AND e.content_id = cm.id::text
              )
            LIMIT :n
        """), {"t": tenant_id, "n": batch_size}).fetchall()
        
        return [
            {
                "id": str(r.id),
                "role": r.role,
                "content": r.content,
                "tenant_id": tenant_id
            }
            for r in rows
        ]
    except Exception as e:
        logger.exception("find_unembedded_chat_messages failed")
        raise
    finally:
        db.close()


@activity.defn
async def embed_message_batch(messages: list[dict]) -> int:
    """Generate and store embeddings for a batch of messages."""
    db = SessionLocal()
    try:
        count = 0
        for m in messages:
            text_to_embed = f"[{m['role']}] {m['content'][:2000]}"
            # Task 33 specifically mentions passing the real tenant_id
            embed_and_store(
                db,
                tenant_id=UUID(m["tenant_id"]),
                content_type="chat_message",
                content_id=m["id"],
                text_content=text_to_embed,
            )
            count += 1
        db.commit()
        return count
    except Exception as e:
        logger.exception("embed_message_batch failed")
        db.rollback()
        raise
    finally:
        db.close()
