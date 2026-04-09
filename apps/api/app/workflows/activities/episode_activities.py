"""Activities for EpisodeWorkflow."""
import logging
from datetime import datetime
from uuid import UUID
from temporalio import activity

from app.db.session import SessionLocal
from app.models.chat import ChatMessage
from app.models.conversation_episode import ConversationEpisode
from app.services.local_inference import summarize_chat_window
from app.services.embedding_service import embed_text

logger = logging.getLogger(__name__)


@activity.defn
async def fetch_window_messages(
    chat_session_id: str,
    window_start_iso: str,
    window_end_iso: str,
) -> list[dict]:
    """Fetch all messages in a session between two timestamps."""
    db = SessionLocal()
    try:
        start_dt = datetime.fromisoformat(window_start_iso)
        end_dt = datetime.fromisoformat(window_end_iso)
        
        rows = db.query(ChatMessage).filter(
            ChatMessage.session_id == UUID(chat_session_id),
            ChatMessage.created_at >= start_dt,
            ChatMessage.created_at <= end_dt,
        ).order_by(ChatMessage.created_at.asc()).all()
        
        return [
            {
                "role": r.role,
                "content": r.content,
                "created_at": r.created_at.isoformat()
            }
            for r in rows
        ]
    except Exception as e:
        logger.exception("fetch_window_messages failed")
        raise
    finally:
        db.close()


@activity.defn
async def summarize_window(messages: list[dict]) -> dict:
    """Gemma4 summarization with structured output."""
    try:
        # Calls the helper in local_inference.py
        return summarize_chat_window(messages)
    except Exception as e:
        logger.exception("summarize_window failed")
        raise


@activity.defn
async def embed_and_store_episode(
    tenant_id: str,
    chat_session_id: str,
    window_start_iso: str,
    window_end_iso: str,
    trigger_reason: str,
    summary: dict,
) -> str:
    """Store the generated episode in the database."""
    db = SessionLocal()
    try:
        text_to_embed = summary.get("summary", "")
        if not text_to_embed:
            # Fallback if summary is empty
            text_to_embed = f"Conversation episode from {window_start_iso} to {window_end_iso}"
            
        emb = embed_text(text_to_embed, task_type="RETRIEVAL_DOCUMENT")
        
        ep = ConversationEpisode(
            tenant_id=UUID(tenant_id),
            session_id=UUID(chat_session_id),
            summary=summary.get("summary", "")[:2000],
            key_topics=summary.get("key_topics", [])[:10],
            key_entities=summary.get("key_entities", [])[:10],
            mood=summary.get("mood", "neutral"),
            message_count=len(summary.get("messages", [])),
            window_start=datetime.fromisoformat(window_start_iso),
            window_end=datetime.fromisoformat(window_end_iso),
            trigger_reason=trigger_reason,
            generated_by="gemma4",
            embedding=emb,
        )
        db.add(ep)
        db.commit()
        db.refresh(ep)
        return str(ep.id)
    except Exception as e:
        logger.exception("embed_and_store_episode failed")
        db.rollback()
        raise
    finally:
        db.close()


@activity.defn
async def find_idle_sessions(
    tenant_id: str,
    idle_minutes: int = 10,
) -> list[dict]:
    """Find chat sessions that have unsummarized messages and have been idle."""
    from app.db.session import SessionLocal
    from app.models.chat import ChatSession, ChatMessage
    from app.models.conversation_episode import ConversationEpisode
    from sqlalchemy import func
    from datetime import datetime, timedelta, timezone
    
    db = SessionLocal()
    try:
        # 1. Get sessions for this tenant
        sessions = db.query(ChatSession).filter(
            ChatSession.tenant_id == UUID(tenant_id)
        ).all()
        
        idle_threshold = datetime.now(timezone.utc) - timedelta(minutes=idle_minutes)
        results = []
        
        for session in sessions:
            # Check last message time
            last_msg = db.query(ChatMessage).filter(
                ChatMessage.session_id == session.id
            ).order_by(ChatMessage.created_at.desc()).first()
            
            if not last_msg:
                continue
                
            # Handle naive vs aware
            last_msg_at = last_msg.created_at
            if last_msg_at.tzinfo is None:
                last_msg_at = last_msg_at.replace(tzinfo=timezone.utc)
                
            if last_msg_at > idle_threshold:
                continue # Still active
                
            # Check if there are messages since last episode
            last_episode = db.query(ConversationEpisode).filter(
                ConversationEpisode.session_id == session.id
            ).order_by(ConversationEpisode.created_at.desc()).first()
            
            since = last_episode.created_at if last_episode else datetime(2020, 1, 1, tzinfo=timezone.utc)
            if since.tzinfo is None:
                since = since.replace(tzinfo=timezone.utc)
                
            new_msg_count = db.query(func.count(ChatMessage.id)).filter(
                ChatMessage.session_id == session.id,
                ChatMessage.created_at > since,
            ).scalar() or 0
            
            if new_msg_count >= 2:
                first_new = db.query(ChatMessage).filter(
                    ChatMessage.session_id == session.id,
                    ChatMessage.created_at > since,
                ).order_by(ChatMessage.created_at.asc()).first()
                
                results.append({
                    "id": str(session.id),
                    "window_start": first_new.created_at.isoformat(),
                    "window_end": datetime.now(timezone.utc).isoformat(),
                })
                
        return results
    finally:
        db.close()
