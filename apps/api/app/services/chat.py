from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Tuple
import uuid

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.models.chat import ChatSession as ChatSessionModel, ChatMessage
from app.services import agent_kits as agent_kit_service
from app.services import datasets as dataset_service
from app.services.agent_identity import resolve_primary_agent_slug
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


def _detect_emotion(text: str) -> str:
    """Detect emotion from response text using keyword heuristics.

    Returns one of: idle, thinking, happy, responding, alert, sleep, listening
    """
    if not text or len(text.strip()) < 20:
        return "idle"

    lower = text.lower()

    # Check happy keywords
    happy_keywords = [
        "congratulations", "great", "awesome", "excellent", "well done",
        "glad", "happy", "love", "perfect", "wonderful", "fantastic",
    ]
    if any(kw in lower for kw in happy_keywords):
        return "happy"

    # Check alert keywords
    alert_keywords = [
        "warning", "error", "critical", "urgent", "danger",
        "important", "caution", "immediately", "alert", "failed",
    ]
    if any(kw in lower for kw in alert_keywords):
        return "alert"

    # Check thinking keywords
    thinking_keywords = [
        "analyzing", "investigating", "looking into", "let me think",
        "considering", "evaluating", "researching", "hmm", "perhaps", "might",
    ]
    if any(kw in lower for kw in thinking_keywords):
        return "thinking"

    # Check responding keywords (normal informational responses)
    responding_keywords = [
        "here is", "here's", "i found", "the answer", "result",
    ]
    if any(kw in lower for kw in responding_keywords):
        return "responding"

    # Default to responding for any substantive response
    return "responding"


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
    # Gap 2: Detect if user is acting on a previous suggestion (non-blocking).
    # Gap 3: Detect if user message resolves an open commitment (non-blocking).
    # Capture primitives before thread — ORM objects are not safe to access
    # across thread boundaries after the request session may have closed.
    try:
        import threading
        _sig_tenant_id = session.tenant_id
        _sig_session_id = session.id

        def _detect_signals_and_resolve():
            from app.db.session import SessionLocal as _SL
            from app.services.behavioral_signals import detect_acted_on_signals
            edb = _SL()
            try:
                detect_acted_on_signals(
                    db=edb,
                    tenant_id=_sig_tenant_id,
                    user_message=content,
                    session_id=_sig_session_id,
                )
            except Exception:
                edb.rollback()
            finally:
                edb.close()

        threading.Thread(target=_detect_signals_and_resolve, daemon=True).start()
    except Exception:
        pass

    # Gap 3: Commitment resolution already runs inside _detect_signals_and_resolve above

    user_context = {"attachment": attachment_meta} if attachment_meta else None
    user_message = _append_message(
        db, session=session, role="user", content=content, context=user_context,
    )
    assistant_message = _generate_agentic_response(
        db,
        session=session,
        user_id=user_id,
        user_message=content,
        user_message_id=user_message.id,
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
    user_message_id: uuid.UUID,
    sender_phone: str | None = None,
    media_parts: list | None = None,
) -> ChatMessage:
    """Route user message through the CLI orchestrator (Claude Code CLI)."""
    # Ensure clean DB session — previous requests may have left a poisoned transaction
    try:
        db.rollback()
    except Exception:
        pass
    from app.services.agent_router import route_and_execute
    from app.services.skill_manager import skill_manager

    # Derive agent_slug from session's agent kit config or skill lookup
    agent_slug = None
    primary_slug = resolve_primary_agent_slug(db, session.tenant_id)
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
    
    if not agent_slug:
        agent_slug = primary_slug

    # Strategy: fit as many recent messages as possible into a 65KB budget
    recent_msgs = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == session.id)
        .order_by(ChatMessage.created_at.desc())
        .limit(30)
        .all()
    )
    max_total = 65000
    kept = []
    total_chars = 0
    for m in recent_msgs:
        role = "User" if m.role == "user" else "Assistant"
        line = f"[{role}]: {m.content}"
        if total_chars + len(line) > max_total and kept:
            break
        kept.append(line)
        total_chars += len(line)
    summary = "\n\n".join(reversed(kept))

    # If media_parts present, extract image data for CLI
    cli_message = user_message
    image_b64 = ""
    image_mime = ""
    if media_parts:
        for part in media_parts:
            if isinstance(part, dict):
                inline = part.get("inline_data")
                if inline and inline.get("data"):
                    part_mime = inline.get("mime_type", "image/jpeg")
                    # Only extract image/* types — audio/* and video/* are not supported in CLI path
                    if part_mime.startswith("image/"):
                        image_b64 = inline["data"]  # Already base64
                        image_mime = part_mime
                        ext = image_mime.split("/")[-1].replace("jpeg", "jpg")
                        cli_message += f"\n\n[User attached an image (user_image.{ext}) in the working directory. Use your Read tool to view it and analyze its contents.]"
                    # audio/* and video/* are skipped silently — not supported in Gemini API function_response.parts
                elif part.get("text"):
                    cli_message += f"\n\n{part['text']}"

    # Routing and execution
    try:
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
    except Exception as e:
        logger.error("Routing failed: %s", e, exc_info=True)
        response_text = None
        context = {"error": str(e)}

    # Gap 4: Score confidence and apply hedging when uncertain.
    _agent_tier_early = context.get("agent_tier", "full") if context else "full"
    if _agent_tier_early == "full":
        try:
            from app.services.confidence_scorer import score_and_maybe_hedge
            _tool_calls_made = bool(context and context.get("tool_calls_made"))
            response_text, _confidence = score_and_maybe_hedge(
                response_text=response_text,
                user_message=user_message,
                tool_calls_made=_tool_calls_made,
            )
            if context is None:
                context = {}
            context["confidence_score"] = _confidence
        except Exception:
            pass

    # Extract tier metadata from router trace for downstream RL logging
    agent_tier = context.get("agent_tier", "full") if context else "full"
    tool_groups = context.get("tool_groups", []) if context else []

    if context and isinstance(context, dict):
        _mem_dirty = False
        _mem = dict(session.memory_context or {})
        recalled_entity_names = context.get("recalled_entity_names")
        if recalled_entity_names:
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
        if context is None:
            context = {}
        context["emotion"] = "alert"
        return _append_message(
            db, session=session, role="assistant",
            content=error_msg, context=context,
        )

    # Detect emotion from response content
    emotion = _detect_emotion(response_text)
    if context is None:
        context = {}
    context["emotion"] = emotion

    assistant_msg = _append_message(
        db, session=session, role="assistant",
        content=response_text, context=context,
    )

    # Create execution trace for audit trail
    try:
        from app.models.execution_trace import ExecutionTrace
        meta = context if isinstance(context, dict) else {}
        input_tokens = int(meta.get("input_tokens", 0) or 0)
        output_tokens = int(meta.get("output_tokens", 0) or 0)
        duration_ms = int(meta.get("duration_ms", 0) or 0)
        cost_usd = meta.get("cost_usd")
        trace = ExecutionTrace(
            tenant_id=session.tenant_id,
            session_id=session.id,
            step_type="chat_response",
            step_order=0,
            details={
                "agent_slug": agent_slug or primary_slug,
                "platform": meta.get("platform", "unknown"),
                "channel": "whatsapp" if sender_phone else "web",
                "user_message": user_message[:500],
                "response_preview": response_text[:500] if response_text else "",
            },
            duration_ms=duration_ms if duration_ms else None,
            input_tokens=input_tokens if input_tokens else None,
            output_tokens=output_tokens if output_tokens else None,
            cost_usd=cost_usd,
        )
        db.add(trace)
        db.commit()
    except Exception:
        db.rollback()

    # Phase 1.6: Dispatch post-chat memory activities (Temporal)
    from app.memory.feature_flag import is_v2_enabled
    if is_v2_enabled(session.tenant_id):
        try:
            from app.memory.dispatch import dispatch_post_chat_memory
            dispatch_post_chat_memory(
                tenant_id=session.tenant_id,
                session_id=session.id,
                user_message_id=user_message_id,
                assistant_message_id=assistant_msg.id,
            )
        except Exception as e:
            logger.warning("Failed to dispatch PostChatMemoryWorkflow: %s", e)

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
            tokens_used=int(meta.get("input_tokens", 0) or 0) + int(meta.get("output_tokens", 0) or 0),
            cost_usd=meta.get("cost_usd"),
            trajectory_id=meta.get("routing_trajectory_id"),
            tool_groups=tool_groups,
        )
    except Exception:
        pass

    # ── Post-response confidence scoring ──
    try:
        from app.services.confidence_scorer import score_response_confidence
        confidence = score_response_confidence(response_text, question=user_message)
        if context and isinstance(context, dict):
            context["response_confidence"] = round(confidence, 3)
        logger.debug(f"Response confidence: {confidence:.2f} for tenant {str(session.tenant_id)[:8]}")
    except Exception:
        pass

    return assistant_msg
