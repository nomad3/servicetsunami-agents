from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple
import uuid

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.models.chat import ChatSession as ChatSessionModel, ChatMessage
from app.services import agent_kits as agent_kit_service
from app.services import datasets as dataset_service
from app.services.embedding_service import embed_and_store as _embed

logger = logging.getLogger(__name__)


def _extract_tokens_used(context: Dict[str, Any] | None) -> int | None:
    """Normalize token usage from heterogeneous CLI metadata payloads."""
    if not isinstance(context, dict):
        return None

    explicit_total = context.get("tokens_used")
    if explicit_total is not None:
        try:
            return int(explicit_total)
        except (TypeError, ValueError):
            return None

    input_tokens = context.get("input_tokens")
    output_tokens = context.get("output_tokens")
    try:
        if input_tokens is None and output_tokens is None:
            return None
        return int(input_tokens or 0) + int(output_tokens or 0)
    except (TypeError, ValueError):
        return None


def list_sessions(db: Session, *, tenant_id: uuid.UUID) -> List[ChatSessionModel]:
    return (
        db.query(ChatSessionModel)
        .filter(ChatSessionModel.tenant_id == tenant_id)
        .order_by(ChatSessionModel.created_at.desc())
        .all()
    )


def get_session(db: Session, *, session_id: uuid.UUID, tenant_id: uuid.UUID) -> ChatSessionModel | None:
    session = db.query(ChatSessionModel).filter(ChatSessionModel.id == session_id).first()
    if session and str(session.tenant_id) == str(tenant_id):
        return session
    return None


def create_session(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    agent_kit_id: uuid.UUID | None = None,
    dataset_id: uuid.UUID | None = None,
    dataset_group_id: uuid.UUID | None = None,
    title: str | None = None,
) -> ChatSessionModel:
    dataset = None
    dataset_group = None
    agent_kit = None

    if dataset_id:
        dataset = dataset_service.get_dataset(db, dataset_id=dataset_id, tenant_id=tenant_id)
        if not dataset:
            raise ValueError("Dataset not found for tenant")

    if dataset_group_id:
        from app.services import dataset_groups as dataset_group_service  # Local import to avoid cycle

        dataset_group = dataset_group_service.get_dataset_group(db, group_id=dataset_group_id)
        if not dataset_group or dataset_group.tenant_id != tenant_id:
            raise ValueError("Dataset group not found for tenant")

    if agent_kit_id:
        agent_kit = agent_kit_service.get_agent_kit(db, agent_kit_id=agent_kit_id)
        if not agent_kit or str(agent_kit.tenant_id) != str(tenant_id):
            raise ValueError("Agent kit not found for tenant")
    else:
        # Auto-select the tenant's first kit when none is specified
        tenant_kits = agent_kit_service.get_agent_kits_by_tenant(db, tenant_id=tenant_id)
        if tenant_kits:
            agent_kit = tenant_kits[0]

    session_title = title
    if not session_title:
        parts = []
        if agent_kit:
            parts.append(agent_kit.name)
        if dataset:
            parts.append(f"on {dataset.name}")
        elif dataset_group:
            parts.append(f"on {dataset_group.name} (Group)")
        session_title = " ".join(parts) if parts else "New Session"

    session = ChatSessionModel(
        title=session_title,
        dataset_id=dataset.id if dataset else None,
        dataset_group_id=dataset_group.id if dataset_group else None,
        agent_kit_id=agent_kit.id if agent_kit else None,
        tenant_id=tenant_id,
        source="native",
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def _append_message(
    db: Session,
    *,
    session: ChatSessionModel,
    role: str,
    content: str,
    context: Dict[str, Any] | None = None,
) -> ChatMessage:
    normalized_context = dict(context or {})
    tokens_used = _extract_tokens_used(normalized_context) if role == "assistant" else None
    if role == "assistant" and tokens_used is not None and normalized_context.get("tokens_used") is None:
        normalized_context["tokens_used"] = tokens_used
    message = ChatMessage(
        session_id=session.id,
        role=role,
        content=content,
        context=normalized_context or None,
        tokens_used=tokens_used,
    )
    db.add(message)
    db.commit()
    db.refresh(message)
    try:
        _embed(
            db,
            tenant_id=session.tenant_id,
            content_type="chat_message",
            content_id=str(message.id),
            text_content=f"[{role}]: {content[:2000]}",
        )
    except Exception:
        logger.debug("Chat message embedding skipped", exc_info=True)
    return message


def post_user_message(
    db: Session,
    *,
    session: ChatSessionModel,
    user_id: uuid.UUID,
    content: str,
    sender_phone: str | None = None,
    media_parts: list | None = None,
    attachment_meta: dict | None = None,
) -> Tuple[ChatMessage, ChatMessage]:
    user_context = {"attachment": attachment_meta} if attachment_meta else None
    user_message = _append_message(
        db, session=session, role="user", content=content, context=user_context,
    )
    assistant_message = _generate_agentic_response(
        db,
        session=session,
        user_id=user_id,
        user_message=content,
        sender_phone=sender_phone,
        media_parts=media_parts,
    )
    return user_message, assistant_message


def _generate_agentic_response(
    db: Session,
    *,
    session: ChatSessionModel,
    user_id: uuid.UUID,
    user_message: str,
    sender_phone: str | None = None,
    media_parts: list | None = None,
) -> ChatMessage:
    """Route user message through the CLI orchestrator (Claude Code CLI)."""
    from app.services.agent_router import route_and_execute
    from app.services.skill_manager import skill_manager

    # Derive agent_slug from session's agent kit config or skill lookup
    agent_slug = None
    if session.agent_kit:
        kit_config = session.agent_kit.config or {}
        # Check if kit config specifies a skill slug directly
        agent_slug = kit_config.get("skill_slug")
        if not agent_slug:
            # Try to find a matching skill by kit name (case-insensitive)
            kit_name_lower = session.agent_kit.name.lower()
            for skill in skill_manager.list_skills():
                if skill.slug == kit_name_lower or kit_name_lower.startswith(skill.slug):
                    agent_slug = skill.slug
                    break

    # Build recent conversation history for CLI context
    recent_msgs = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == session.id)
        .order_by(ChatMessage.created_at.desc())
        .limit(6)  # Last 6 messages (3 turns) — just for immediate context
        .all()  # Full history available via MCP tools (search_knowledge, find_entities)
    )
    history_lines = []
    for m in reversed(recent_msgs):
        role = "User" if m.role == "user" else "Assistant"
        content = m.content[:300]
        history_lines.append(f"[{role}]: {content}")
        # Note if message had an attachment
        if m.context and isinstance(m.context, dict):
            attachment = m.context.get("attachment")
            if attachment:
                history_lines.append(f"  (attached: {attachment.get('type', 'file')} — {attachment.get('name', 'unnamed')})")
    summary = "\n\n".join(history_lines)

    # If media_parts present, extract image data for CLI
    cli_message = user_message
    image_b64 = ""
    image_mime = ""
    if media_parts:
        for part in media_parts:
            if isinstance(part, dict):
                inline = part.get("inline_data")
                if inline and inline.get("data"):
                    image_b64 = inline["data"]  # Already base64
                    image_mime = inline.get("mime_type", "image/jpeg")
                    ext = image_mime.split("/")[-1].replace("jpeg", "jpg")
                    cli_message += f"\n\n[User attached an image (user_image.{ext}) in the working directory. Use your Read tool to view it and analyze its contents.]"
                elif part.get("text"):
                    cli_message += f"\n\n{part['text']}"

    response_text, context = route_and_execute(
        db,
        tenant_id=session.tenant_id,
        user_id=user_id,
        message=cli_message,
        channel="whatsapp" if sender_phone else "web",
        sender_phone=sender_phone,
        agent_slug=agent_slug,
        conversation_summary=summary,
        image_b64=image_b64,
        image_mime=image_mime,
        db_session_memory={
            **(session.memory_context or {}),
            "chat_session_id": str(session.id),
        },
    )

    # Save CLI session ID and recalled entity names for cross-turn continuity
    if context and isinstance(context, dict):
        _mem_dirty = False
        _mem = dict(session.memory_context or {})

        cli_session_id = context.get("claude_session_id") or context.get("claude_cli_session_id")
        if cli_session_id:
            # Key matches what cli_session_manager reads: f"{platform}_cli_session_id"
            platform_key = context.get("platform", "claude_code")
            _mem[f"{platform_key}_cli_session_id"] = cli_session_id
            _mem_dirty = True

        # Persist recalled entity names so the next turn can boost them
        recalled_entity_names = context.get("recalled_entity_names")
        if recalled_entity_names:
            # Merge with existing session entities, keeping last 50 unique names
            existing = _mem.get("recalled_entity_names", [])
            merged = list(dict.fromkeys(existing + recalled_entity_names))[:50]
            _mem["recalled_entity_names"] = merged
            _mem_dirty = True

        if _mem_dirty:
            session.memory_context = _mem
            flag_modified(session, "memory_context")
            db.commit()

    if response_text is None:
        error_msg = (context or {}).get("error", "Agent failed to respond. Please try again.")
        return _append_message(
            db, session=session, role="assistant",
            content=error_msg, context=context,
        )

    assistant_msg = _append_message(
        db, session=session, role="assistant",
        content=response_text, context=context,
    )

    # Update presence: idle (response delivered), scoped to this session
    try:
        from app.services import luna_presence_service
        luna_presence_service.update_state(
            session.tenant_id, state="idle",
            session_id=str(session.id),
        )
    except Exception:
        pass

    # Auto-quality scoring (async, non-blocking — runs after response is saved)
    try:
        from app.services.auto_quality_scorer import score_and_log_async
        meta = context if isinstance(context, dict) else {}
        score_and_log_async(
            tenant_id=session.tenant_id,
            user_message=user_message,
            agent_response=response_text,
            platform=meta.get("platform", "claude_code"),
            agent_slug=agent_slug or "luna",
            channel="whatsapp" if sender_phone else "web",
            tokens_used=meta.get("input_tokens", 0) + meta.get("output_tokens", 0),
            cost_usd=meta.get("cost_usd", 0.0),
            rollout_experiment_id=meta.get("rollout_experiment_id"),
            rollout_arm=meta.get("rollout_arm"),
            routing_trajectory_id=meta.get("routing_trajectory_id"),
        )
    except Exception:
        pass  # Never block response delivery for scoring

    # ── Post-response memory writes ──
    # Capture ALL primitives before spawning threads — ORM objects are
    # not safe to dereference after the request session commits.
    _tenant_id = session.tenant_id  # uuid, not ORM lazy
    _session_id = session.id
    _channel = "whatsapp" if sender_phone else "web"
    _user_message = user_message
    _response_text = response_text
    _recalled_names = (context or {}).get("recalled_entity_names", [])

    # 1. Knowledge extraction — runs in a daemon thread with its own
    #    SessionLocal. Uses only captured primitives, never the request
    #    session. extract_from_content manages its own commits internally.
    try:
        import threading

        def _extract_knowledge():
            import logging as _log
            from app.db.session import SessionLocal as _SL
            from app.services.knowledge_extraction import KnowledgeExtractionService

            edb = _SL()
            try:
                extraction_service = KnowledgeExtractionService()
                conversation_text = f"User: {_user_message}\n\nAssistant: {_response_text}"
                extraction_service.extract_from_content(
                    edb,
                    tenant_id=_tenant_id,
                    content=conversation_text,
                    content_type="chat_transcript",
                )
            except Exception:
                _log.getLogger(__name__).warning(
                    "Post-response knowledge extraction failed for tenant %s",
                    str(_tenant_id)[:8], exc_info=True,
                )
                edb.rollback()
            finally:
                edb.close()

        threading.Thread(target=_extract_knowledge, daemon=True).start()
    except Exception:
        pass

    # 2. Recall feedback — lightweight, safe in a thread since we only
    #    use captured primitives.
    try:
        import threading

        def _recall_feedback():
            from app.db.session import SessionLocal as _SL
            from app.models.memory_activity import MemoryActivity

            if not _recalled_names or not _response_text:
                return

            response_lower = _response_text.lower()
            edb = _SL()
            try:
                for name in _recalled_names:
                    used = name.lower() in response_lower
                    activity = MemoryActivity(
                        tenant_id=_tenant_id,
                        event_type="recall_feedback",
                        description=f"Entity '{name}' recalled and {'used' if used else 'unused'} in response",
                        source="chat",
                        event_metadata={"entity_name": name, "used": used},
                    )
                    edb.add(activity)
                edb.commit()
            except Exception:
                edb.rollback()
            finally:
                edb.close()

        threading.Thread(target=_recall_feedback, daemon=True).start()
    except Exception:
        pass

    # 3. Episode generation — create a conversation episode summary
    #    when enough new messages have accumulated.
    try:
        import threading

        def _maybe_create_episode():
            from datetime import datetime as _dt
            from app.db.session import SessionLocal as _SL
            from app.models.conversation_episode import ConversationEpisode
            from app.models.chat import ChatMessage as _CM
            from sqlalchemy import func

            edb = _SL()
            try:
                # Check messages since last episode for this session
                last_episode = edb.query(ConversationEpisode).filter(
                    ConversationEpisode.session_id == _session_id,
                ).order_by(ConversationEpisode.created_at.desc()).first()

                since = last_episode.created_at if last_episode else _dt(2020, 1, 1)
                new_msg_count = edb.query(func.count(_CM.id)).filter(
                    _CM.session_id == _session_id,
                    _CM.created_at > since,
                ).scalar()

                if new_msg_count < 4:
                    return  # Not enough new messages

                # Fetch the new messages for summarization
                new_msgs = edb.query(_CM).filter(
                    _CM.session_id == _session_id,
                    _CM.created_at > since,
                ).order_by(_CM.created_at).limit(20).all()

                conversation_text = "\n".join(
                    f"{'User' if m.role == 'user' else 'Luna'}: {m.content[:300]}"
                    for m in new_msgs
                )

                # Summarize using local Qwen
                from app.services.local_inference import generate_sync
                prompt = (
                    "Summarize this conversation in 2-3 sentences. Include key topics "
                    "discussed, any decisions made, and the user's emotional tone "
                    "(excited, frustrated, neutral, curious, etc).\n\n"
                    f"Conversation:\n{conversation_text[:3000]}\n\nSummary:"
                )

                summary = generate_sync(prompt, model="qwen2.5-coder:1.5b", max_tokens=200, timeout=30)
                if not summary or len(summary) < 10:
                    return

                # Extract key entities from summary (capitalized multi-char words)
                import re
                _skip = {
                    'the', 'and', 'but', 'for', 'was', 'are', 'has', 'had',
                    'not', 'this', 'that', 'they', 'user', 'luna', 'with',
                }
                entities = []
                for word in summary.split():
                    cleaned = re.sub(r'[^\w]', '', word)
                    if cleaned and cleaned[0].isupper() and len(cleaned) > 2 and cleaned.lower() not in _skip:
                        entities.append(cleaned)
                entities = list(set(entities))[:10]

                # Detect mood from summary keywords
                mood = "neutral"
                summary_lower = summary.lower()
                if any(w in summary_lower for w in ["excited", "enthusiastic", "happy", "great"]):
                    mood = "positive"
                elif any(w in summary_lower for w in ["frustrated", "annoyed", "confused", "problem"]):
                    mood = "frustrated"
                elif any(w in summary_lower for w in ["curious", "interested", "exploring"]):
                    mood = "curious"

                # Generate embedding for semantic search
                from app.services.embedding_service import embed_text
                embedding = embed_text(summary, task_type="RETRIEVAL_DOCUMENT")

                episode = ConversationEpisode(
                    tenant_id=_tenant_id,
                    session_id=_session_id,
                    summary=summary.strip(),
                    key_topics=[],
                    key_entities=entities,
                    mood=mood,
                    message_count=new_msg_count,
                    source_channel=_channel,
                    embedding=embedding,
                )
                edb.add(episode)
                edb.commit()

                import logging as _log
                _log.getLogger(__name__).info(
                    "Created episode for session %s: %d msgs, mood=%s",
                    str(_session_id)[:8], new_msg_count, mood,
                )
            except Exception:
                import logging as _log
                _log.getLogger(__name__).debug("Episode generation failed", exc_info=True)
                edb.rollback()
            finally:
                edb.close()

        threading.Thread(target=_maybe_create_episode, daemon=True).start()
    except Exception:
        pass

    return assistant_msg
