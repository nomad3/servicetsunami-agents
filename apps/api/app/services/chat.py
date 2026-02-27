from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Tuple
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
from app.services import agent_kits as agent_kit_service
from app.services import datasets as dataset_service
from app.services.adk_client import ADKNotConfiguredError, get_adk_client
from app.services.knowledge_extraction import knowledge_extraction_service

logger = logging.getLogger(__name__)

ADK_UNCONFIGURED_MESSAGE = (
    "Agentic responses require the ADK service. Please configure ADK_BASE_URL."
)
ADK_FAILURE_MESSAGE = "The ADK service is temporarily unavailable. Please retry in a moment."

# Rough per-token pricing (USD per token) for cost estimation.
# Gemini 2.5 Flash: ~$0.15/1M input, ~$0.60/1M output
_COST_PER_INPUT_TOKEN = 0.15 / 1_000_000
_COST_PER_OUTPUT_TOKEN = 0.60 / 1_000_000


def _estimate_cost(total_tokens: int, context: Dict[str, Any] | None = None) -> float:
    """Estimate USD cost from token counts in the ADK response context."""
    if not total_tokens:
        return 0.0
    ctx = context or {}
    prompt = ctx.get("prompt_tokens", 0)
    completion = ctx.get("completion_tokens", 0)
    if prompt or completion:
        return round(prompt * _COST_PER_INPUT_TOKEN + completion * _COST_PER_OUTPUT_TOKEN, 6)
    # Fallback: use blended rate
    return round(total_tokens * _COST_PER_INPUT_TOKEN, 6)


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
    return message


def post_user_message(
    db: Session,
    *,
    session: ChatSessionModel,
    user_id: uuid.UUID,
    content: str,
) -> Tuple[ChatMessage, ChatMessage]:
    user_message = _append_message(db, session=session, role="user", content=content)
    assistant_message = _generate_agentic_response(
        db,
        session=session,
        user_id=user_id,
        user_message=content,
    )
    return user_message, assistant_message


def _generate_agentic_response(
    db: Session,
    *,
    session: ChatSessionModel,
    user_id: uuid.UUID,
    user_message: str,
) -> ChatMessage:
    if not settings.ADK_BASE_URL:
        logger.error(f"ADK_BASE_URL is missing in settings: {settings.ADK_BASE_URL}")
        return _append_message(
            db,
            session=session,
            role="assistant",
            content=ADK_UNCONFIGURED_MESSAGE,
            context={"error": "adk_not_configured"},
        )

    try:
        client = get_adk_client()
    except ADKNotConfiguredError as e:
        logger.error(f"get_adk_client raised ADKNotConfiguredError: {e}")
        return _append_message(
            db,
            session=session,
            role="assistant",
            content=ADK_UNCONFIGURED_MESSAGE,
            context={"error": "adk_not_configured"},
        )

    agent_kit = session.agent_kit
    dataset = session.dataset
    dataset_group = session.dataset_group

    if not agent_kit:
        return _append_message(
            db,
            session=session,
            role="assistant",
            content="No agent kit is attached to this session yet.",
            context=None,
        )

    # --- Chat-to-Workflow bridge: create audit task before ADK call ---
    bridge_start = time.time()
    bridge_task_id, bridge_agent_id = _bridge_chat_to_workflow(
        db, session=session, user_message=user_message,
    )

    # Resolve ADK session ID: for WhatsApp/external sessions the external_id
    # is the channel session key (e.g. "whatsapp:56954791985") and the ADK
    # session ID is stored in memory_context to avoid overwriting the lookup key.
    _mem = session.memory_context or {}
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

    try:
        events = client.run(user_id=user_id, session_id=str(adk_session_id), message=user_message)
        response_text, context = _extract_adk_response(events)
        _run_entity_extraction(db, session, context)

        # --- Bridge: mark task completed ---
        _tokens = context.get("tokens_used", 0) if context else 0
        _cost = _estimate_cost(_tokens, context)
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
        return assistant_msg

    except Exception as exc:
        # ADK sessions are in-memory; if the pod restarted the session is gone.
        # Detect 404 "Session not found" and transparently re-create.
        is_session_lost = "404" in str(exc) or "Session not found" in str(exc)
        if is_session_lost:
            logger.warning("ADK session %s lost (pod restart?), re-creating.", adk_session_id)
            try:
                adk_state = _build_adk_state(
                    tenant_id=session.tenant_id,
                    agent_kit=agent_kit,
                    dataset=dataset,
                    dataset_group=dataset_group,
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
                events = client.run(user_id=user_id, session_id=str(adk_session_id), message=user_message)
                response_text, context = _extract_adk_response(events)
                _run_entity_extraction(db, session, context)

                # --- Bridge: mark task completed after retry ---
                _tokens = context.get("tokens_used", 0) if context else 0
                _cost = _estimate_cost(_tokens, context)
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

            except Exception as retry_exc:
                logger.exception("ADK retry after session re-creation also failed: %s", retry_exc)
                if bridge_task_id:
                    _bridge_complete_task(
                        db, task_id=bridge_task_id, tenant_id=session.tenant_id,
                        agent_id=bridge_agent_id, success=False,
                        duration_ms=int((time.time() - bridge_start) * 1000),
                        error=str(retry_exc),
                    )
                return _append_message(
                    db,
                    session=session,
                    role="assistant",
                    content=ADK_FAILURE_MESSAGE,
                    context={"error": str(retry_exc)},
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
        return _append_message(
            db,
            session=session,
            role="assistant",
            content=ADK_FAILURE_MESSAGE,
            context={"error": str(exc)},
        )


def _run_entity_extraction(
    db: Session,
    session: ChatSessionModel,
    context: Dict[str, Any] | None,
) -> None:
    """Run entity extraction on the session and store count in context.

    Wrapped in try/except so extraction failures never break chat.
    """
    try:
        extracted = knowledge_extraction_service.extract_from_session(
            db, session.id, session.tenant_id
        )
        entities_extracted = len(extracted)
        if entities_extracted > 0 and context is not None:
            context["entities_extracted"] = entities_extracted
            logger.info("Extracted %d entities from session %s", entities_extracted, session.id)
    except Exception:
        logger.warning("Entity extraction failed for session %s", session.id, exc_info=True)


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

    if not assistant_text:
        assistant_text = "Agent run completed without a response."

    context = {
        "adk_events": events,
        "tokens_used": total_tokens,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
    }
    return assistant_text, context
