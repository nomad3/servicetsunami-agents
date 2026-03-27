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
        db_session_memory=session.memory_context,
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

    # Post-response knowledge extraction (async, non-blocking)
    # Extracts entities, relations, and memories from the conversation turn
    # so Luna learns and remembers across sessions.
    try:
        import threading

        def _extract_knowledge():
            from app.db.session import SessionLocal as _SL
            from app.services.knowledge_extraction import KnowledgeExtractionService

            edb = _SL()
            try:
                extraction_service = KnowledgeExtractionService()
                conversation_text = (
                    f"User: {user_message}\n\nAssistant: {response_text}"
                )
                extraction_service.extract_from_content(
                    edb,
                    tenant_id=session.tenant_id,
                    content=conversation_text,
                    content_type="chat_transcript",
                )
                edb.commit()
            except Exception:
                edb.rollback()
            finally:
                edb.close()

        threading.Thread(target=_extract_knowledge, daemon=True).start()
    except Exception:
        pass  # Never block response delivery for extraction

    # Recall feedback: track which recalled entities were actually used in the response
    # Capture primitives before spawning the thread — ORM objects are not thread-safe.
    _feedback_tenant_id = session.tenant_id
    _feedback_recalled = (context or {}).get("recalled_entity_names", [])
    _feedback_response = response_text
    try:
        import threading

        def _recall_feedback():
            from app.db.session import SessionLocal as _SL
            from app.models.memory_activity import MemoryActivity

            if not _feedback_recalled or not _feedback_response:
                return

            response_lower = _feedback_response.lower()
            edb = _SL()
            try:
                for name in _feedback_recalled:
                    used = name.lower() in response_lower
                    activity = MemoryActivity(
                        tenant_id=_feedback_tenant_id,
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
        pass  # Never block response delivery for recall feedback

    return assistant_msg
