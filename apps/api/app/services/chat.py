from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
import uuid

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.core.config import settings
from app.models.agent import Agent
from app.models.agent_kit import AgentKit
from app.models.agent_task import AgentTask
from app.models.chat import ChatSession as ChatSessionModel, ChatMessage
from app.models.dataset import Dataset
from app.models.execution_trace import ExecutionTrace
from app.models.tenant_features import TenantFeatures
from app.models.integration_config import IntegrationConfig
from app.services import agent_kits as agent_kit_service
from app.services import datasets as dataset_service
from app.services.adk_client import ADKError, ADKNotConfiguredError, get_adk_client
from app.services.knowledge_extraction import knowledge_extraction_service
from app.services.embedding_service import embed_and_store as _embed
from app.services.memory_recall import build_memory_context
from app.services.orchestration.credential_vault import retrieve_credentials_for_skill
from app.services import rl_experience_service

logger = logging.getLogger(__name__)

ADK_UNCONFIGURED_MESSAGE = (
    "Agentic responses require the ADK service. Please configure ADK_BASE_URL."
)
ADK_FAILURE_MESSAGE = "The ADK service is temporarily unavailable. Please retry in a moment."

# Per-token pricing (USD per token) by provider for cost estimation.
_MODEL_PRICING = {
    # Anthropic
    "anthropic_llm": {"input": 3.00 / 1_000_000, "output": 15.00 / 1_000_000},   # Claude Sonnet 4.5
    # Gemini (default)
    "gemini_llm": {"input": 0.15 / 1_000_000, "output": 0.60 / 1_000_000},       # Gemini 2.5 Flash
}
_DEFAULT_PRICING = _MODEL_PRICING["gemini_llm"]


def _estimate_cost(total_tokens: int, context: Dict[str, Any] | None = None, provider: str = None) -> float:
    """Estimate USD cost from token counts in the ADK response context."""
    if not total_tokens:
        return 0.0
    pricing = _MODEL_PRICING.get(provider, _DEFAULT_PRICING) if provider else _DEFAULT_PRICING
    ctx = context or {}
    prompt = ctx.get("prompt_tokens", 0)
    completion = ctx.get("completion_tokens", 0)
    if prompt or completion:
        return round(prompt * pricing["input"] + completion * pricing["output"], 6)
    # Fallback: use blended input rate
    return round(total_tokens * pricing["input"], 6)


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

    adk_session_id = None
    if settings.ADK_BASE_URL:
        try:
            adk_state = _build_adk_state(
                tenant_id=tenant_id,
                agent_kit=agent_kit,
                dataset=dataset,
                dataset_group=dataset_group,
            )
            adk_session = get_adk_client().create_session(user_id=user_id, state=adk_state)
            adk_session_id = adk_session.get("id")
        except ADKNotConfiguredError:
            # Misconfiguration – fall back to native session metadata
            logger.warning("ADK_BASE_URL configured improperly; proceeding without ADK session.")
        except Exception as exc:  # pragma: no cover - network failure path
            logger.exception("Unable to create ADK session: %s", exc)
            raise RuntimeError("Unable to create ADK session") from exc

    session = ChatSessionModel(
        title=session_title,
        dataset_id=dataset.id if dataset else None,
        dataset_group_id=dataset_group.id if dataset_group else None,
        agent_kit_id=agent_kit.id if agent_kit else None,
        tenant_id=tenant_id,
        source="adk" if adk_session_id else "native",
        external_id=adk_session_id,
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
    tokens_used = (context or {}).get("tokens_used") if role == "assistant" else None
    message = ChatMessage(
        session_id=session.id,
        role=role,
        content=content,
        context=context,
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


def _get_tenant_llm_config(db: Session, tenant_id) -> Optional[dict]:
    """Read tenant's active LLM provider and return config for ADK state_delta.

    Returns dict with {provider, model, api_key} or None if using default Gemini.
    """
    import uuid as _uuid

    # Read tenant's active provider from tenant_features
    features = db.query(TenantFeatures).filter(
        TenantFeatures.tenant_id == tenant_id
    ).first()

    if not features or not features.active_llm_provider:
        return None  # Use default Gemini

    provider = features.active_llm_provider
    if provider == "gemini_llm":
        return None  # Default Gemini — no override needed

    # Look up integration config for this provider
    config = db.query(IntegrationConfig).filter(
        IntegrationConfig.tenant_id == tenant_id,
        IntegrationConfig.integration_name == provider,
        IntegrationConfig.enabled == True
    ).first()

    if not config:
        return {"error": f"Provider '{provider}' is not configured. Go to LLM Settings to set it up."}

    # Retrieve decrypted credentials from vault
    try:
        creds = retrieve_credentials_for_skill(db, config.id, tenant_id)
    except Exception:
        return {"error": "Failed to retrieve credentials. Please re-save your API key in LLM Settings."}

    if not creds or "api_key" not in creds or "model" not in creds:
        return {"error": "Missing API key or model ID. Please configure them in LLM Settings."}

    return {
        "provider": provider,
        "model": creds["model"],
        "api_key": creds["api_key"],
    }


def _generate_agentic_response(
    db: Session,
    *,
    session: ChatSessionModel,
    user_id: uuid.UUID,
    user_message: str,
    sender_phone: str | None = None,
    media_parts: list | None = None,
) -> ChatMessage:
    # --- CLI Orchestrator path (feature flag) — checked FIRST, before ADK ---
    features = db.query(TenantFeatures).filter(
        TenantFeatures.tenant_id == session.tenant_id
    ).first()

    if features and getattr(features, 'cli_orchestrator_enabled', False):
        # CLI path — handles both text and media (images saved as files)
        from app.services.agent_router import route_and_execute

        # Derive agent_slug from session's agent kit (not hardcoded to Luna)
        agent_slug = None
        if session.agent_kit:
            # Use agent kit name as slug (lowercase, underscored)
            import re
            agent_slug = re.sub(r'[^a-z0-9]+', '_', session.agent_kit.name.lower()).strip('_')

        # Build full conversation history (not just summary) for CLI context
        recent_msgs = (
            db.query(ChatMessage)
            .filter(ChatMessage.session_id == session.id)
            .order_by(ChatMessage.created_at.desc())
            .limit(30)  # Last 30 messages (15 turns)
            .all()
        )
        history_lines = []
        for m in reversed(recent_msgs):
            role = "User" if m.role == "user" else "Assistant"
            content = m.content[:500]  # 500 chars per message, 30 msgs = ~15K max
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

        # Save Claude CLI session ID for continuity across messages
        if context and isinstance(context, dict):
            cli_session_id = context.get("claude_session_id") or context.get("claude_cli_session_id")
            if cli_session_id:
                _mem = dict(session.memory_context or {})
                _mem["claude_cli_session_id"] = cli_session_id
                session.memory_context = _mem
                flag_modified(session, "memory_context")
                db.commit()

        if response_text is None:
            error_msg = (context or {}).get("error", "Agent failed to respond. Please try again.")
            return _append_message(
                db, session=session, role="assistant",
                content=error_msg, context=context,
            )

        return _append_message(
            db, session=session, role="assistant",
            content=response_text, context=context,
        )

    # --- Existing ADK path (fallback when CLI orchestrator is disabled) ---
    if not settings.ADK_BASE_URL:
        logger.error(f"ADK_BASE_URL is missing in settings: {settings.ADK_BASE_URL}")
        return _append_message(
            db, session=session, role="assistant",
            content=ADK_UNCONFIGURED_MESSAGE,
            context={"error": "adk_not_configured"},
        )

    try:
        client = get_adk_client()
    except ADKNotConfiguredError as e:
        logger.error(f"get_adk_client raised ADKNotConfiguredError: {e}")
        return _append_message(
            db, session=session, role="assistant",
            content=ADK_UNCONFIGURED_MESSAGE,
            context={"error": "adk_not_configured"},
        )

    agent_kit = session.agent_kit
    dataset = session.dataset
    dataset_group = session.dataset_group

    if not agent_kit:
        return _append_message(
            db, session=session, role="assistant",
            content="No agent kit is attached to this session yet.",
            context=None,
        )

    # --- Chat-to-Workflow bridge: create audit task before ADK call ---
    bridge_start = time.time()
    bridge_task_id, bridge_agent_id = _bridge_chat_to_workflow(
        db, session=session, user_message=user_message,
    )

    # --- Context window guard: rotate ADK session if approaching limit ---
    # ADK keeps full conversation in memory. If cumulative prompt tokens
    # approach the model limit, create a fresh session with a summary.
    _MAX_SESSION_TOKENS = 120_000  # Rotate well before the 200K hard limit
    _mem = session.memory_context or {}
    _cumulative_tokens = _mem.get("cumulative_prompt_tokens", 0)

    if _cumulative_tokens > _MAX_SESSION_TOKENS:
        logger.info(
            "Session %s has %d cumulative tokens (limit %d), rotating ADK session",
            session.id, _cumulative_tokens, _MAX_SESSION_TOKENS,
        )
        # Build a brief summary from recent messages for continuity
        recent_msgs = (
            db.query(ChatMessage)
            .filter(ChatMessage.session_id == session.id)
            .order_by(ChatMessage.created_at.desc())
            .limit(6)
            .all()
        )
        summary_lines = []
        for m in reversed(recent_msgs):
            role = "User" if m.role == "user" else "Luna"
            summary_lines.append(f"{role}: {m.content[:200]}")
        conversation_summary = "\n".join(summary_lines)

        # Force new ADK session creation
        _mem.pop("adk_session_id", None)
        _mem["cumulative_prompt_tokens"] = 0
        _mem["conversation_summary"] = conversation_summary
        session.memory_context = _mem
        flag_modified(session, "memory_context")
        if session.source not in ("whatsapp",):
            session.external_id = None
        db.commit()
        db.refresh(session)
        _mem = session.memory_context or {}

    # Resolve ADK session ID: for WhatsApp/external sessions the external_id
    # is the channel session key (e.g. "whatsapp:56954791985") and the ADK
    # session ID is stored in memory_context to avoid overwriting the lookup key.
    adk_session_id = _mem.get("adk_session_id") or (
        session.external_id if session.source not in ("whatsapp",) else None
    )
    if not adk_session_id:
        try:
            adk_state = _build_adk_state(
                tenant_id=session.tenant_id,
                agent_kit=agent_kit,
                dataset=dataset,
                dataset_group=dataset_group,
                sender_phone=sender_phone,
            )
            adk_session = client.create_session(user_id=user_id, state=adk_state)
            adk_session_id = adk_session.get("id")
            if session.source in ("whatsapp",):
                # Store ADK session ID in memory_context, keep external_id for channel lookup
                _mem["adk_session_id"] = adk_session_id
                session.memory_context = dict(_mem)  # new dict so SQLAlchemy detects change
                flag_modified(session, "memory_context")
            else:
                session.external_id = adk_session_id
                session.source = "adk"
            db.commit()
            db.refresh(session)
        except Exception as exc:  # pragma: no cover - network failure path
            logger.exception("Unable to create ADK session for chat: %s", exc)
            if bridge_task_id:
                _bridge_complete_task(
                    db, task_id=bridge_task_id, tenant_id=session.tenant_id,
                    agent_id=bridge_agent_id, success=False,
                    duration_ms=int((time.time() - bridge_start) * 1000),
                    error=f"ADK session creation failed: {exc}",
                )
            return _append_message(
                db,
                session=session,
                role="assistant",
                content=ADK_FAILURE_MESSAGE,
                context={"error": str(exc)},
            )

    # Build memory context for automatic recall
    memory_context = {}
    try:
        memory_context = build_memory_context(db, session.tenant_id, user_message)
    except Exception:
        logger.warning("Memory recall failed", exc_info=True)

    state_delta = {"tenant_id": str(session.tenant_id)}
    if sender_phone:
        state_delta["whatsapp_phone"] = sender_phone
    if memory_context:
        state_delta["memory_context"] = memory_context

    # Include tenant's LLM provider config for ADK model override
    llm_config = _get_tenant_llm_config(db, session.tenant_id)
    if llm_config and "error" in llm_config:
        # Tenant selected a provider but credentials are missing/broken
        return _append_message(
            db,
            session=session,
            role="assistant",
            content=llm_config["error"],
            context={"error": "llm_config_missing"},
        )
    if llm_config:
        state_delta["llm_config"] = llm_config

    # Inject conversation summary when session was rotated (context window guard)
    _conv_summary = (session.memory_context or {}).get("conversation_summary")
    if _conv_summary:
        state_delta["conversation_summary"] = _conv_summary
        # Clear it after use so it's not sent again
        try:
            _mem_clear = dict(session.memory_context or {})
            _mem_clear.pop("conversation_summary", None)
            session.memory_context = _mem_clear
            flag_modified(session, "memory_context")
            db.commit()
        except Exception:
            pass

    if memory_context:
        try:
            from app.services.memory_activity import log_activity
            entity_count = len(memory_context.get("relevant_entities", []))
            memory_count = len(memory_context.get("relevant_memories", []))
            log_activity(
                db, session.tenant_id,
                event_type="recall_used",
                description=f"Recalled {entity_count} entities + {memory_count} memories",
                source="chat",
                event_metadata={"keywords": user_message[:100]},
            )
        except Exception:
            pass  # Never break chat for logging

    try:
        if media_parts:
            events = client.run(
                user_id=user_id,
                session_id=str(adk_session_id),
                parts=media_parts,
                state_delta=state_delta,
            )
        else:
            events = client.run(
                user_id=user_id,
                session_id=str(adk_session_id),
                message=user_message,
                state_delta=state_delta,
            )
        response_text, context = _extract_adk_response(events)
        _run_entity_extraction(db, session, context)

        # --- Track cumulative tokens for context window guard ---
        _tokens = context.get("tokens_used", 0) if context else 0
        prompt_tokens = context.get("prompt_tokens", 0) if context else 0
        # Use prompt_tokens if available, otherwise estimate from total (80% is typically prompt)
        _token_increment = prompt_tokens if prompt_tokens > 0 else int(_tokens * 0.8)
        try:
            _mem_update = dict(session.memory_context or {})
            _prev = _mem_update.get("cumulative_prompt_tokens", 0)
            _mem_update["cumulative_prompt_tokens"] = _prev + _token_increment
            session.memory_context = _mem_update
            flag_modified(session, "memory_context")
            db.commit()
            if _token_increment > 0:
                logger.debug(
                    "Session %s: +%d tokens (cumulative: %d)",
                    session.id, _token_increment, _mem_update["cumulative_prompt_tokens"],
                )
        except Exception:
            logger.debug("Token tracking commit failed", exc_info=True)

        # --- Bridge: mark task completed ---
        _cost = _estimate_cost(_tokens, context, provider=llm_config.get("provider") if llm_config else None)
        if bridge_task_id:
            duration_ms = int((time.time() - bridge_start) * 1000)
            _bridge_complete_task(
                db, task_id=bridge_task_id, tenant_id=session.tenant_id,
                agent_id=bridge_agent_id, success=True, duration_ms=duration_ms,
                tokens_used=_tokens, cost=_cost,
                details={
                    "response_preview": response_text[:300] if response_text else "",
                    "events_count": len(events),
                    "entities_extracted": context.get("entities_extracted", 0) if context else 0,
                },
            )

        assistant_msg = _append_message(
            db, session=session, role="assistant",
            content=response_text, context=context,
        )
        if bridge_task_id:
            assistant_msg.task_id = bridge_task_id
            assistant_msg.agent_id = bridge_agent_id
            db.commit()

        # Log RL experiences from this interaction (best-effort)
        try:
            _log_rl_experiences(
                db, session.tenant_id, session.id, assistant_msg.id,
                user_message, response_text, context,
            )
        except Exception:
            logger.debug("RL experience logging failed", exc_info=True)

        return assistant_msg

    except Exception as exc:
        # Context window overflow — force-rotate and retry with fresh session
        exc_str = str(exc)
        is_context_overflow = (
            "too long" in exc_str or "ContextWindow" in exc_str
            or "prompt is too long" in exc_str or "CONTEXT_OVERFLOW" in exc_str
            or (isinstance(exc, ADKError) and "CONTEXT_OVERFLOW" in exc.detail)
        )
        if is_context_overflow:
            logger.warning("Context window overflow on session %s, force-rotating", session.id)
            try:
                # Force rotation
                _mem_rot = dict(session.memory_context or {})
                _mem_rot.pop("adk_session_id", None)
                _mem_rot["cumulative_prompt_tokens"] = 0

                recent_msgs = (
                    db.query(ChatMessage)
                    .filter(ChatMessage.session_id == session.id)
                    .order_by(ChatMessage.created_at.desc())
                    .limit(4)
                    .all()
                )
                summary = "\n".join(
                    f"{'User' if m.role == 'user' else 'Luna'}: {m.content[:150]}"
                    for m in reversed(recent_msgs)
                )
                _mem_rot["conversation_summary"] = summary

                session.memory_context = _mem_rot
                flag_modified(session, "memory_context")
                if session.source not in ("whatsapp",):
                    session.external_id = None
                db.commit()
                db.refresh(session)

                # Create fresh ADK session
                adk_state = _build_adk_state(
                    tenant_id=session.tenant_id, agent_kit=agent_kit,
                    dataset=dataset, dataset_group=dataset_group,
                    sender_phone=sender_phone,
                )
                new_session = client.create_session(user_id=user_id, state=adk_state)
                new_adk_id = new_session.get("id")

                _mem2 = dict(session.memory_context or {})
                _mem2["adk_session_id"] = new_adk_id
                session.memory_context = _mem2
                flag_modified(session, "memory_context")
                db.commit()

                # Retry with fresh session
                retry_delta = {"tenant_id": str(session.tenant_id)}
                if llm_config:
                    retry_delta["llm_config"] = llm_config
                if summary:
                    retry_delta["conversation_summary"] = summary

                events = client.run(
                    user_id=user_id, session_id=new_adk_id,
                    message=user_message, state_delta=retry_delta,
                )
                response_text, context = _extract_adk_response(events)
                return _append_message(
                    db, session=session, role="assistant",
                    content=response_text, context=context,
                )
            except Exception as retry_exc:
                logger.exception("Context overflow retry also failed")
                return _append_message(
                    db, session=session, role="assistant",
                    content="I had to reset my memory due to a long conversation. Please try your message again.",
                    context={"error": "context_overflow_retry_failed"},
                )

        # ADK sessions are in-memory; if the pod restarted the session is gone.
        # Detect 404 "Session not found" and transparently re-create.
        is_session_lost = (
            (isinstance(exc, ADKError) and (exc.status_code == 404 or "NOT_FOUND" in exc.detail or "Session not found" in exc.detail))
            or "404" in str(exc)
            or "Session not found" in str(exc)
        )
        if is_session_lost:
            logger.warning("ADK session %s lost (pod restart?), re-creating.", adk_session_id)
            try:
                adk_state = _build_adk_state(
                    tenant_id=session.tenant_id,
                    agent_kit=agent_kit,
                    dataset=dataset,
                    dataset_group=dataset_group,
                    sender_phone=sender_phone,
                )
                new_adk_session = client.create_session(user_id=user_id, state=adk_state)
                adk_session_id = new_adk_session.get("id")
                if session.source in ("whatsapp",):
                    _mem = dict(session.memory_context or {})
                    _mem["adk_session_id"] = adk_session_id
                    session.memory_context = _mem
                    flag_modified(session, "memory_context")
                else:
                    session.external_id = adk_session_id
                db.commit()
                db.refresh(session)

                # Build memory context for automatic recall (retry path)
                retry_memory_context = {}
                try:
                    retry_memory_context = build_memory_context(db, session.tenant_id, user_message)
                except Exception:
                    logger.warning("Memory recall failed (retry path)", exc_info=True)

                retry_state_delta = {"tenant_id": str(session.tenant_id)}
                if sender_phone:
                    retry_state_delta["whatsapp_phone"] = sender_phone
                if retry_memory_context:
                    retry_state_delta["memory_context"] = retry_memory_context

                # Include tenant's LLM provider config for ADK model override (retry path)
                if llm_config:
                    retry_state_delta["llm_config"] = llm_config

                if retry_memory_context:
                    try:
                        from app.services.memory_activity import log_activity
                        entity_count = len(retry_memory_context.get("relevant_entities", []))
                        memory_count = len(retry_memory_context.get("relevant_memories", []))
                        log_activity(
                            db, session.tenant_id,
                            event_type="recall_used",
                            description=f"Recalled {entity_count} entities + {memory_count} memories",
                            source="chat",
                            event_metadata={"keywords": user_message[:100]},
                        )
                    except Exception:
                        pass  # Never break chat for logging

                if media_parts:
                    events = client.run(
                        user_id=user_id,
                        session_id=str(adk_session_id),
                        parts=media_parts,
                        state_delta=retry_state_delta,
                    )
                else:
                    events = client.run(
                        user_id=user_id,
                        session_id=str(adk_session_id),
                        message=user_message,
                        state_delta=retry_state_delta,
                    )
                response_text, context = _extract_adk_response(events)
                _run_entity_extraction(db, session, context)

                # --- Bridge: mark task completed after retry ---
                _tokens = context.get("tokens_used", 0) if context else 0
                _cost = _estimate_cost(_tokens, context, provider=llm_config.get("provider") if llm_config else None)
                if bridge_task_id:
                    duration_ms = int((time.time() - bridge_start) * 1000)
                    _bridge_complete_task(
                        db, task_id=bridge_task_id, tenant_id=session.tenant_id,
                        agent_id=bridge_agent_id, success=True, duration_ms=duration_ms,
                        tokens_used=_tokens, cost=_cost,
                        details={
                            "response_preview": response_text[:300] if response_text else "",
                            "events_count": len(events),
                            "session_recreated": True,
                        },
                    )

                assistant_msg = _append_message(
                    db, session=session, role="assistant",
                    content=response_text, context=context,
                )
                if bridge_task_id:
                    assistant_msg.task_id = bridge_task_id
                    assistant_msg.agent_id = bridge_agent_id
                    db.commit()
                return assistant_msg

            except ADKError as retry_adk_exc:
                logger.error("ADK retry failed: %s", retry_adk_exc.detail)
                if bridge_task_id:
                    _bridge_complete_task(
                        db, task_id=bridge_task_id, tenant_id=session.tenant_id,
                        agent_id=bridge_agent_id, success=False,
                        duration_ms=int((time.time() - bridge_start) * 1000),
                        error=retry_adk_exc.detail,
                    )
                return _append_message(
                    db,
                    session=session,
                    role="assistant",
                    content=retry_adk_exc.user_message,
                    context={"error": retry_adk_exc.detail, "error_code": retry_adk_exc.status_code},
                )

            except Exception as retry_exc:
                logger.exception("ADK retry after session re-creation also failed: %s", retry_exc)
                if bridge_task_id:
                    _bridge_complete_task(
                        db, task_id=bridge_task_id, tenant_id=session.tenant_id,
                        agent_id=bridge_agent_id, success=False,
                        duration_ms=int((time.time() - bridge_start) * 1000),
                        error=str(retry_exc),
                    )
                retry_detail = str(retry_exc)
                if "Connection refused" in retry_detail or "ConnectError" in retry_detail:
                    retry_msg = "Sorry, I can't process your message right now. The AI service is temporarily restarting. Please try again in a couple of minutes."
                else:
                    retry_msg = "Sorry, something went wrong processing your message. Please try again in a moment."
                return _append_message(
                    db,
                    session=session,
                    role="assistant",
                    content=retry_msg,
                    context={"error": retry_detail},
                )

        logger.exception("ADK run failed: %s", exc)
        # --- Bridge: mark task failed ---
        if bridge_task_id:
            _bridge_complete_task(
                db, task_id=bridge_task_id, tenant_id=session.tenant_id,
                agent_id=bridge_agent_id, success=False,
                duration_ms=int((time.time() - bridge_start) * 1000),
                error=str(exc),
            )
        # Use ADKError's user-friendly message when available
        if isinstance(exc, ADKError):
            user_msg = exc.user_message
            error_detail = exc.detail
            error_context = {"error": error_detail, "error_code": exc.status_code}
        else:
            error_detail = str(exc)
            if "Connection refused" in error_detail or "ConnectError" in error_detail:
                user_msg = "Sorry, I can't process your message right now. The AI service is temporarily restarting. Please try again in a couple of minutes."
            else:
                user_msg = "Sorry, something went wrong processing your message. Please try again in a moment."
            error_context = {"error": error_detail}
        return _append_message(
            db,
            session=session,
            role="assistant",
            content=user_msg,
            context=error_context,
        )


def _log_tool_usage(
    db: Session,
    tenant_id: uuid.UUID,
    events: List[Dict[str, Any]] | None,
) -> None:
    """Extract tool calls from ADK events and log as activity entries."""
    if not events:
        return

    # Tools worth logging — action tools that change state or fetch external data
    ACTION_TOOLS = {
        "send_email", "create_calendar_event", "create_entity", "update_entity",
        "merge_entities", "create_relation", "record_observation",
        "search_emails", "read_email", "list_calendar_events",
        "schedule_followup", "qualify_lead", "get_pipeline_summary",
        "start_inbox_monitor", "stop_inbox_monitor",
        "draft_outreach", "generate_proposal", "update_pipeline_stage",
        "query_sql", "generate_insights", "query_data_source",
        "scrape_webpage", "search_and_scrape",
        "execute_shell",
    }

    tools_used = []
    for event in events:
        content = event.get("content") or {}
        parts = content.get("parts", []) if isinstance(content, dict) else []
        for part in parts:
            if isinstance(part, dict) and "functionCall" in part:
                fn = part["functionCall"]
                tool_name = fn.get("name", "")
                if tool_name in ACTION_TOOLS:
                    agent_name = event.get("author", "unknown")
                    tools_used.append({"tool": tool_name, "agent": agent_name})

    if not tools_used:
        return

    try:
        from app.services.memory_activity import log_activity

        # Deduplicate by tool name for this batch
        seen = set()
        for usage in tools_used:
            key = usage["tool"]
            if key in seen:
                continue
            seen.add(key)
            log_activity(
                db, tenant_id,
                event_type="tool_used",
                description=f'{usage["agent"]} used {usage["tool"]}',
                source="chat",
                event_metadata={"tool_name": usage["tool"], "agent_name": usage["agent"]},
            )
    except Exception:
        logger.debug("Failed to log tool usage activity", exc_info=True)


def _run_entity_extraction(
    db: Session,
    session: ChatSessionModel,
    context: Dict[str, Any] | None,
) -> None:
    """Run entity extraction on the session and store count in context.

    Wrapped in try/except so extraction failures never break chat.
    """
    try:
        # Log tool usage from ADK events
        adk_events = context.get("adk_events") if context else None
        _log_tool_usage(db, session.tenant_id, adk_events)

        result = knowledge_extraction_service.extract_from_session(
            db, session.id, session.tenant_id
        )
        entities_extracted = len(result.get("entities", []))
        if entities_extracted > 0 and context is not None:
            context["entities_extracted"] = entities_extracted
            logger.info("Extracted %d entities from session %s", entities_extracted, session.id)

        # Track relations and memories in context
        relations_data = result.get("relations", [])
        memories_data = result.get("memories", [])
        if context is not None:
            if relations_data:
                context["relations_created"] = len(relations_data)
            if memories_data:
                context["memories_created"] = len(memories_data)

        # Dispatch action triggers (reminders, follow-ups)
        triggers = result.get("action_triggers", [])
        if triggers:
            _dispatch_action_triggers(db, session.tenant_id, triggers)

    except Exception:
        logger.warning("Entity extraction failed for session %s", session.id, exc_info=True)


def _log_rl_experiences(
    db: Session,
    tenant_id: uuid.UUID,
    session_id: uuid.UUID,
    message_id: uuid.UUID,
    user_message: str,
    response_text: str,
    context: Dict[str, Any] | None,
) -> None:
    """Log RL experiences from a chat interaction.

    Extracts decision points from ADK events:
    - agent_selection: which agent handled the request
    - tool_selection: which tools were called
    - response_generation: the overall response (linked to user feedback)
    """
    adk_events = (context or {}).get("adk_events", [])
    if not adk_events:
        return

    trajectory_id = message_id  # Use message ID as trajectory
    step = 0

    # Extract agents that handled this request
    agents_involved = []
    tools_called = []
    model_used = None

    for event in adk_events:
        author = event.get("author", "")
        content = event.get("content", {})
        parts = content.get("parts", []) if isinstance(content, dict) else []

        if author and author not in agents_involved:
            agents_involved.append(author)

        if not model_used and event.get("modelVersion"):
            model_used = event.get("modelVersion")

        for part in parts:
            if isinstance(part, dict):
                fc = part.get("functionCall") or part.get("function_call")
                if fc and fc.get("name"):
                    tool_name = fc["name"]
                    if tool_name != "transfer_to_agent" and tool_name not in tools_called:
                        tools_called.append(tool_name)

    # Log agent_selection experience
    if len(agents_involved) > 1:
        # First agent is root supervisor, last is the one that actually responded
        responding_agent = agents_involved[-1]
        rl_experience_service.log_experience(
            db=db,
            tenant_id=tenant_id,
            trajectory_id=trajectory_id,
            step_index=step,
            decision_point="agent_selection",
            state={"user_message": user_message[:500], "agents_available": agents_involved},
            action={"selected_agent": responding_agent},
            state_text=f"User asked: {user_message[:200]} → Routed to {responding_agent}",
        )
        step += 1

    # Log tool_selection experiences
    if tools_called:
        rl_experience_service.log_experience(
            db=db,
            tenant_id=tenant_id,
            trajectory_id=trajectory_id,
            step_index=step,
            decision_point="tool_selection",
            state={"user_message": user_message[:500], "agent": agents_involved[-1] if agents_involved else "unknown"},
            action={"tools_used": tools_called},
            state_text=f"Agent used tools: {', '.join(tools_called)} for: {user_message[:200]}",
        )
        step += 1

    # Log response_generation experience (always — this is what feedback buttons rate)
    tokens = (context or {}).get("tokens_used", 0)
    rl_experience_service.log_experience(
        db=db,
        tenant_id=tenant_id,
        trajectory_id=trajectory_id,
        step_index=step,
        decision_point="response_generation",
        state={
            "user_message": user_message[:500],
            "agent": agents_involved[-1] if agents_involved else "unknown",
            "model": model_used,
            "tools_used": tools_called,
            "tokens": tokens,
        },
        action={
            "response_length": len(response_text),
            "response_preview": response_text[:200],
        },
        state_text=f"Generated {len(response_text)} char response using {model_used or 'unknown'} for: {user_message[:200]}",
    )


def _dispatch_action_triggers(
    db: Session,
    tenant_id: uuid.UUID,
    triggers: list[dict],
) -> None:
    """Dispatch action triggers from extraction as Temporal workflows."""
    from app.services.memory_activity import log_activity

    for trigger in triggers:
        trigger_type = trigger.get("type", "")
        description = trigger.get("description", "")
        delay_hours = trigger.get("delay_hours", 0)
        entity_name = trigger.get("entity_name", "")

        if not description:
            continue

        try:
            if trigger_type in ("reminder", "follow_up"):
                _start_followup_workflow(
                    tenant_id=str(tenant_id),
                    entity_name=entity_name,
                    action=trigger_type,
                    delay_hours=delay_hours,
                    message=description,
                )

            log_activity(
                db, tenant_id,
                event_type="action_triggered",
                description=f"Scheduled: {description}",
                source="chat",
                event_metadata={"trigger_type": trigger_type, "delay_hours": delay_hours, "entity_name": entity_name},
            )
            logger.info("Dispatched action trigger: %s (%s)", description, trigger_type)
        except Exception:
            logger.warning("Failed to dispatch trigger: %s", description, exc_info=True)


def _start_followup_workflow(
    tenant_id: str,
    entity_name: str,
    action: str,
    delay_hours: int,
    message: str,
) -> None:
    """Start a FollowUpWorkflow via Temporal (best-effort)."""
    try:
        from temporalio.client import Client as TemporalClient
        import asyncio

        async def _start():
            client = await TemporalClient.connect(settings.TEMPORAL_ADDRESS)
            from app.workflows.follow_up import FollowUpInput, FollowUpWorkflow
            await client.start_workflow(
                FollowUpWorkflow.run,
                FollowUpInput(
                    entity_id=entity_name,  # Will be resolved by workflow
                    tenant_id=tenant_id,
                    action=action,
                    delay_hours=delay_hours or 24,
                    message=message,
                ),
                id=f"followup-{tenant_id[:8]}-{entity_name[:20]}-{int(time.time())}",
                task_queue="servicetsunami-orchestration",
            )

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_start())
        except RuntimeError:
            asyncio.run(_start())
    except Exception:
        logger.warning("Could not start FollowUp workflow (Temporal may be unavailable)", exc_info=True)


# ---------------------------------------------------------------------------
# Chat-to-Workflow bridge
# ---------------------------------------------------------------------------

def _resolve_agent_for_session(
    db: Session,
    *,
    session: ChatSessionModel,
) -> uuid.UUID | None:
    """Resolve an Agent ID from the session's agent_kit or tenant."""
    tenant_id = session.tenant_id
    agent_kit = session.agent_kit

    # Try to match from agent_kit.default_agents JSON
    if agent_kit and agent_kit.default_agents:
        default_agents = agent_kit.default_agents
        if isinstance(default_agents, list) and len(default_agents) > 0:
            first_agent = default_agents[0]
            first_name = first_agent.get("name") if isinstance(first_agent, dict) else None
            if first_name:
                agent = (
                    db.query(Agent)
                    .filter(Agent.tenant_id == tenant_id, Agent.name == first_name)
                    .first()
                )
                if agent:
                    return agent.id

    # Fallback: first agent in tenant
    agent = db.query(Agent).filter(Agent.tenant_id == tenant_id).first()
    return agent.id if agent else None


def _bridge_chat_to_workflow(
    db: Session,
    *,
    session: ChatSessionModel,
    user_message: str,
) -> Tuple[uuid.UUID | None, uuid.UUID | None]:
    """Create AgentTask + initial ExecutionTrace for chat audit trail.

    Returns (task_id, agent_id) or (None, None) on failure.
    """
    try:
        agent_id = _resolve_agent_for_session(db, session=session)
        if not agent_id:
            logger.debug("No agent found for tenant %s; skipping chat bridge", session.tenant_id)
            return None, None

        objective = user_message[:200] if len(user_message) > 200 else user_message
        now = datetime.utcnow()

        task = AgentTask(
            id=uuid.uuid4(),
            assigned_agent_id=agent_id,
            human_requested=True,
            status="executing",
            priority="normal",
            task_type="chat",
            objective=objective,
            context={
                "chat_session_id": str(session.id),
                "agent_kit_id": str(session.agent_kit_id) if session.agent_kit_id else None,
                "source": "chat_bridge",
            },
            started_at=now,
            created_at=now,
        )
        db.add(task)
        db.flush()

        # Link task to session
        if not session.root_task_id:
            session.root_task_id = task.id

        trace = ExecutionTrace(
            id=uuid.uuid4(),
            task_id=task.id,
            tenant_id=session.tenant_id,
            step_type="dispatched",
            step_order=1,
            agent_id=agent_id,
            details={
                "source": "chat_bridge",
                "chat_session_id": str(session.id),
                "message_preview": objective[:100],
            },
            created_at=now,
        )
        db.add(trace)

        try:
            _embed(
                db,
                tenant_id=session.tenant_id,
                content_type="agent_task",
                content_id=str(task.id),
                text_content=f"Task: {objective} | session:{session.id}",
            )
        except Exception:
            logger.debug("Task embedding skipped", exc_info=True)

        db.commit()

        return task.id, agent_id
    except Exception:
        logger.warning("Chat-to-workflow bridge failed", exc_info=True)
        db.rollback()
        return None, None


def _bridge_complete_task(
    db: Session,
    *,
    task_id: uuid.UUID,
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID,
    success: bool,
    duration_ms: int,
    details: dict | None = None,
    error: str | None = None,
    tokens_used: int = 0,
    cost: float = 0.0,
) -> None:
    """Update the bridged task and create final ExecutionTrace records."""
    try:
        task = db.query(AgentTask).filter(AgentTask.id == task_id).first()
        if not task:
            return

        now = datetime.utcnow()
        task.completed_at = now
        task.tokens_used = tokens_used
        task.cost = cost

        if success:
            task.status = "completed"
            if details:
                task.output = details
        else:
            task.status = "failed"
            task.error = error or "ADK execution failed"

        # "executing" trace — records the ADK call
        db.add(ExecutionTrace(
            id=uuid.uuid4(),
            task_id=task_id,
            tenant_id=tenant_id,
            step_type="executing",
            step_order=2,
            agent_id=agent_id,
            details={"source": "adk", "duration_ms": duration_ms},
            duration_ms=duration_ms,
            created_at=now,
        ))

        # Final trace
        db.add(ExecutionTrace(
            id=uuid.uuid4(),
            task_id=task_id,
            tenant_id=tenant_id,
            step_type="completed" if success else "failed",
            step_order=3,
            agent_id=agent_id,
            details=details if success else {"error": error},
            duration_ms=duration_ms,
            created_at=now,
        ))

        db.commit()
    except Exception:
        logger.warning("Chat bridge task completion failed", exc_info=True)
        db.rollback()


def _build_adk_state(
    *,
    tenant_id: uuid.UUID,
    agent_kit: AgentKit | None,
    dataset: Dataset | None,
    dataset_group: Any | None,
    sender_phone: str | None = None,
) -> Dict[str, Any]:
    datasets: List[Dataset] = []
    if dataset:
        datasets = [dataset]
    elif dataset_group:
        datasets = list(dataset_group.datasets or [])

    dataset_payloads = [
        {
            "id": str(ds.id),
            "name": ds.name,
            "description": ds.description,
            "schema": ds.schema,
            "metadata": ds.metadata_,
            "source_type": ds.source_type,
        }
        for ds in datasets
    ]

    payload: Dict[str, Any] = {
        "tenant_id": str(tenant_id),
        "datasets": dataset_payloads,
        "mcp": {
            "enabled": settings.MCP_ENABLED,
            "server_url": settings.MCP_SERVER_URL,
            "auto_sync": settings.DATABRICKS_AUTO_SYNC,
        },
    }

    if dataset_group:
        payload["dataset_group"] = {
            "id": str(dataset_group.id),
            "name": dataset_group.name,
            "dataset_ids": [str(ds.id) for ds in dataset_group.datasets or []],
        }

    if agent_kit:
        payload["agent_kit"] = {
            "id": str(agent_kit.id),
            "name": agent_kit.name,
            "description": agent_kit.description,
            "config": agent_kit.config,
        }

    if sender_phone:
        payload["whatsapp_phone"] = sender_phone

    return payload


def _extract_adk_response(events: List[Dict[str, Any]]) -> Tuple[str, Dict[str, Any]]:
    assistant_text = ""
    total_tokens = 0
    prompt_tokens = 0
    completion_tokens = 0

    for event in events:
        usage = event.get("usageMetadata") or {}
        total_tokens += usage.get("totalTokenCount", 0)
        prompt_tokens += usage.get("promptTokenCount", 0)
        completion_tokens += usage.get("candidatesTokenCount", 0)

    # Look for text response from agent (reverse order to get latest)
    for event in reversed(events):
        author = event.get("author")
        if author and author.lower() != "user":
            content = event.get("content") or {}
            parts = content.get("parts", []) if isinstance(content, dict) else []
            text_parts = []
            for part in parts:
                if isinstance(part, dict) and part.get("text"):
                    text_parts.append(part["text"])
            if text_parts:
                assistant_text = "\n".join(text_parts).strip()
                break

    # Fallback: extract error messages from tool/function responses
    if not assistant_text:
        for event in reversed(events):
            content = event.get("content") or {}
            parts = content.get("parts", []) if isinstance(content, dict) else []
            for part in parts:
                if isinstance(part, dict):
                    fn_resp = part.get("functionResponse") or part.get("function_response") or {}
                    resp_data = fn_resp.get("response") or {}
                    if isinstance(resp_data, dict) and resp_data.get("error"):
                        assistant_text = resp_data["error"]
                        break
            if assistant_text:
                break

    if not assistant_text:
        assistant_text = "Agent run completed without a response."

    context = {
        "adk_events": events,
        "tokens_used": total_tokens,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
    }
    return assistant_text, context
