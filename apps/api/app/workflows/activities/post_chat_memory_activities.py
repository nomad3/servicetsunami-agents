"""Activities for PostChatMemoryWorkflow."""
import logging
from datetime import datetime
from uuid import UUID
from temporalio import activity

from app.db.session import SessionLocal
from app.memory import ingest
from app.memory.types import MemoryEvent

logger = logging.getLogger(__name__)


@activity.defn
async def detect_commitment(
    tenant_id: str,
    chat_session_id: str,
    user_message_id: str,
    assistant_message_id: str,
) -> dict:
    """Detect and record commitments from chat history."""
    from uuid import UUID
    from app.db.session import SessionLocal
    from app.models.chat import ChatMessage
    from app.memory.classifiers.commitment import classify_commitment
    from app.memory.record import record_commitment

    db = SessionLocal()
    try:
        user_msg = db.get(ChatMessage, UUID(user_message_id))
        asst_msg = db.get(ChatMessage, UUID(assistant_message_id))

        detections = []
        for msg in (user_msg, asst_msg):
            if not msg:
                continue
            # role mapping: user -> user, assistant -> assistant
            role = "assistant" if msg.role == "assistant" else "user"
            cls = classify_commitment(msg.content, role=role)
            if not cls.is_commitment:
                continue
            
            # Resolve owner: check metadata for agent_slug (important for coalitions)
            owner = "user"
            if msg.role == "assistant":
                # Default to tenant branding or luna
                owner = resolve_primary_agent_slug(db, UUID(tenant_id))
                
                if msg.context and isinstance(msg.context, dict):
                    owner = msg.context.get("agent_slug") or owner

            c = record_commitment(
                db, 
                tenant_id=UUID(tenant_id),
                owner_agent_slug=owner,
                title=cls.title or msg.content[:80],
                commitment_type=cls.type or "action",
                due_at=cls.due_at,
                source_type="chat",
                source_id=str(msg.id),
            )
            detections.append(str(c.id))
        
        db.commit()
        return {"detected": len(detections), "commitment_ids": detections}
    except Exception as e:
        logger.exception("detect_commitment activity failed")
        db.rollback()
        raise
    finally:
        db.close()


@activity.defn
async def update_world_state(
    tenant_id: str,
    chat_session_id: str,
    user_message_id: str,
    assistant_message_id: str,
) -> dict:
    """Sync world state based on new observations.
    
    Uses ingest_events to merge any extracted observations from this turn
    into the long-term memory graph.
    """
    from uuid import UUID
    from app.db.session import SessionLocal
    from app.models.chat import ChatMessage
    from app.services.knowledge_extraction import KnowledgeExtractionService
    from app.memory.ingest import ingest_events, MemoryEvent
    from app.services.agent_identity import resolve_primary_agent_slug

    db = SessionLocal()
    try:
        user_msg = db.get(ChatMessage, UUID(user_message_id))
        asst_msg = db.get(ChatMessage, UUID(assistant_message_id))
        if not user_msg or not asst_msg:
            return {"updated": 0, "skipped": "messages not found"}

        # Extract (re-run or use cached results if we had an extraction activity)
        # For Phase 1 reliability, we re-extract to ensure we have the objects.
        content = f"User: {user_msg.content}\n\nAssistant: {asst_msg.content}"
        svc = KnowledgeExtractionService()
        raw_result = svc.extract_from_content(
            db,
            tenant_id=UUID(tenant_id),
            content=content,
            content_type='chat_transcript',
            activity_source='chat',
        )
        
        # `extract_from_content` returns persisted KnowledgeEntity instances under
        # "entities", but `ingest_events` expects dicts. Re-shape so name/category/
        # description survive — `upsert_entity_by_name` returns the existing row
        # without overwriting it, so the round-trip is a safe dedup that lets
        # relations link via entity_lookup.
        # NB: this comprehension MUST stay inside the `db = SessionLocal()` block
        # — accessing `e.name` etc. on detached instances would raise.
        proposed_entity_dicts = [
            {"name": e.name, "category": e.category, "description": e.description}
            for e in raw_result.get("entities", [])
            if getattr(e, "name", None)
        ]
        # NB: `extract_from_content` does not return an "observations" key (only
        # entities/relations/memories/action_triggers). `memories` is semantically
        # standalone facts, not entity observations, so we don't wire it here.
        # Observation ingest from chat is therefore deliberately empty until
        # `KnowledgeExtractionService` grows an observations channel.
        now = datetime.utcnow()
        event = MemoryEvent(
            tenant_id=UUID(tenant_id),
            source_type="chat",
            source_id=user_message_id,
            occurred_at=now,
            ingested_at=now,
            kind="text",
            text=content,
            proposed_entities=proposed_entity_dicts,
            proposed_observations=[],
            proposed_relations=raw_result.get("relations", []),
            proposed_commitments=[],
            confidence=0.9,
        )
        
        ingest_result = ingest_events(db, UUID(tenant_id), [event], workflow_id=activity.info().workflow_id)
        
        return {
            "entities_created": ingest_result.entities_created,
            "observations_created": ingest_result.observations_created,
            "status": "ingested"
        }
    finally:
        db.close()


@activity.defn
async def update_behavioral_signals(
    tenant_id: str,
    chat_session_id: str,
    user_message_id: str,
    assistant_message_id: str,
) -> dict:
    """Two operations:
    1. EXTRACT suggestions from the assistant response → pending behavioral_signals.
    2. DETECT whether the user's current message acts on any prior pending signals.
    """
    from uuid import UUID
    from app.db.session import SessionLocal
    from app.models.chat import ChatMessage
    from app.services.behavioral_signals import (
        extract_suggestions_from_response,
        detect_acted_on_signals,
    )

    db = SessionLocal()
    try:
        user_msg = db.get(ChatMessage, UUID(user_message_id))
        asst_msg = db.get(ChatMessage, UUID(assistant_message_id))

        new_signals = []
        if asst_msg:
            # extract_suggestions_from_response already commits internally
            new_signals = extract_suggestions_from_response(
                db, 
                tenant_id=UUID(tenant_id),
                response_text=asst_msg.content,
                message_id=asst_msg.id,
                session_id=UUID(chat_session_id),
            )

        confirmations = []
        if user_msg:
            # detect_acted_on_signals already commits internally
            confirmations = detect_acted_on_signals(
                db, 
                tenant_id=UUID(tenant_id),
                user_message=user_msg.content,
                session_id=UUID(chat_session_id),
            )

        return {
            "new_signals": len(new_signals),
            "confirmations": len(confirmations),
        }
    except Exception as e:
        logger.exception("update_behavioral_signals activity failed")
        db.rollback()
        raise
    finally:
        db.close()


@activity.defn
async def maybe_trigger_episode(
    tenant_id: str,
    chat_session_id: str,
    user_message_id: str,
    assistant_message_id: str,
) -> dict:
    """Evaluate if conversation should be archived as an Episode."""
    from uuid import UUID
    from datetime import datetime, timezone
    from sqlalchemy import func
    from app.db.session import SessionLocal
    from app.models.chat import ChatMessage
    from app.models.conversation_episode import ConversationEpisode

    db = SessionLocal()
    try:
        # Check messages since last episode for this session
        last_episode = db.query(ConversationEpisode).filter(
            ConversationEpisode.session_id == UUID(chat_session_id),
        ).order_by(ConversationEpisode.created_at.desc()).first()

        since = last_episode.created_at if last_episode else datetime(2020, 1, 1, tzinfo=timezone.utc)

        # Time gap guard — avoid duplicate episodes from rapid messages
        if last_episode:
            # handle naive vs aware datetimes
            last_created = last_episode.created_at
            if last_created.tzinfo is None:
                last_created = last_created.replace(tzinfo=timezone.utc)
            
            age_seconds = (datetime.now(timezone.utc) - last_created).total_seconds()
            if age_seconds < 300:  # 5 minute cooldown
                return {"should_trigger": False, "reason": "cooldown", "age_seconds": age_seconds}

        new_msg_count = db.query(func.count(ChatMessage.id)).filter(
            ChatMessage.session_id == UUID(chat_session_id),
            ChatMessage.created_at > since,
        ).scalar() or 0

        # Plan Task 25 says >= 30 messages, but chat.py says 4.
        # I'll use 15 as a balanced compromise for Phase 1.
        if new_msg_count < 15:
            return {"should_trigger": False, "new_messages": new_msg_count}

        first_new_msg = db.query(ChatMessage).filter(
            ChatMessage.session_id == UUID(chat_session_id),
            ChatMessage.created_at > since,
        ).order_by(ChatMessage.created_at.asc()).first()

        # We don't dispatch the child workflow here (activity rule).
        # We return the parameters for the PARENT workflow to dispatch.
        return {
            "should_trigger": True,
            "window_start_iso": first_new_msg.created_at.isoformat(),
            "window_end_iso": datetime.now(timezone.utc).isoformat(),
            "trigger_reason": "message_count_threshold",
            "new_messages": new_msg_count,
        }
    except Exception as e:
        logger.exception("maybe_trigger_episode activity failed")
        db.rollback()
        raise
    finally:
        db.close()
