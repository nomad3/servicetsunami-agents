from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Tuple
import uuid

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.db.safe_ops import safe_rollback
from app.models.agent import Agent
from app.services._agent_ordering import agent_status_rank
from app.models.chat import ChatSession as ChatSessionModel, ChatMessage
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
    agent_id: uuid.UUID | None = None,
    dataset_id: uuid.UUID | None = None,
    dataset_group_id: uuid.UUID | None = None,
    title: str | None = None,
) -> ChatSessionModel:
    dataset = None
    dataset_group = None
    agent = None

    if dataset_id:
        dataset = dataset_service.get_dataset(db, dataset_id=dataset_id, tenant_id=tenant_id)
        if not dataset:
            raise ValueError("Dataset not found for tenant")

    if dataset_group_id:
        from app.services import dataset_groups as dataset_group_service  # Local import to avoid cycle

        dataset_group = dataset_group_service.get_dataset_group(db, group_id=dataset_group_id)
        if not dataset_group or dataset_group.tenant_id != tenant_id:
            raise ValueError("Dataset group not found for tenant")

    if agent_id:
        agent = db.query(Agent).filter(Agent.id == agent_id).first()
        if not agent or str(agent.tenant_id) != str(tenant_id):
            raise ValueError("Agent not found for tenant")
    else:
        # Auto-select: prefer Luna (any agent whose name starts with
        # "Luna"), then production > staging > draft > deprecated,
        # stable tiebreak by id.
        #
        # 2026-05-19 fix: the previous query used `Agent.name == "Luna"`
        # which only matched the literal bare name. Tenants with named
        # variants like "Luna Supervisor" or "Luna General Assistant"
        # never satisfied the predicate, so the selection fell through to
        # `Agent.id.asc()` and picked whichever production agent had the
        # lowest UUID — for at least one tenant that was "Root Cause
        # Analyst", which then appeared as the title of every fresh chat
        # session. Bumping to `ILIKE 'Luna%'` honours the original intent
        # without requiring tenants to keep an unnamed "Luna" row.
        agent = (
            db.query(Agent)
            .filter(Agent.tenant_id == tenant_id)
            .order_by(
                Agent.name.ilike("Luna%").desc(),
                agent_status_rank.asc(),
                Agent.id.asc(),
            )
            .first()
        )

    session_title = title
    if not session_title:
        parts = []
        if agent:
            parts.append(agent.name)
        if dataset:
            parts.append(f"on {dataset.name}")
        elif dataset_group:
            parts.append(f"on {dataset_group.name} (Group)")
        # 2026-05-19 fix: append a HH:MM disambiguator when the caller
        # didn't pass a title. Previously every fresh session for the
        # same agent had the same title, so a day's worth of dispatches
        # collapsed into a wall of identical "Root Cause Analyst" rows
        # in the dashboard sessions list. The timestamp suffix is cheap
        # and reversible; callers that want a meaningful title still get
        # full control via the `title` parameter.
        from datetime import datetime
        base_title = " ".join(parts) if parts else "New Session"
        session_title = f"{base_title} · {datetime.utcnow().strftime('%H:%M')}"

    session = ChatSessionModel(
        title=session_title,
        dataset_id=dataset.id if dataset else None,
        dataset_group_id=dataset_group.id if dataset_group else None,
        agent_id=agent.id if agent else None,
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


def _extract_int(context: Dict[str, Any] | None, key: str) -> int | None:
    """Pull a key from the context dict and coerce to int, or return
    None if the key is missing or unparseable. Used for the per-
    message cost/token split columns added in migration 129 — same
    failure mode as `_extract_tokens_used`: None means "not measured",
    NOT zero."""
    if not isinstance(context, dict):
        return None
    value = context.get(key)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _extract_float(context: Dict[str, Any] | None, key: str) -> float | None:
    """Same as `_extract_int` but for floats (cost_usd)."""
    if not isinstance(context, dict):
        return None
    value = context.get(key)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_str(context: Dict[str, Any] | None, key: str, max_len: int) -> str | None:
    """Same shape as the others but for strings (model id). Truncates
    to `max_len` because the DB column is VARCHAR(64)."""
    if not isinstance(context, dict):
        return None
    value = context.get(key)
    if not isinstance(value, str) or not value:
        return None
    return value[:max_len]


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
    # Per-message cost/token split (migration 129). Only assistant
    # turns carry these fields — user messages by definition have no
    # generation cost. None ⇒ "not measured", preserved end-to-end.
    if role == "assistant":
        input_tokens = _extract_int(normalized_context, "input_tokens")
        output_tokens = _extract_int(normalized_context, "output_tokens")
        cost_usd = _extract_float(normalized_context, "cost_usd")
        model = _extract_str(normalized_context, "model", max_len=64)
    else:
        input_tokens = output_tokens = cost_usd = model = None
    message = ChatMessage(
        session_id=session.id,
        role=role,
        content=content,
        context=normalized_context or None,
        tokens_used=tokens_used,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
        model=model,
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
    # [chat-trace] start a wall-clock anchor used by checkpoints downstream.
    # Goal: surface where the hot path stalls when Luna goes silent without
    # raising. Without this, the gap between WhatsApp `Inbound DM` and
    # `Dispatching ChatCliWorkflow` is opaque and the only fix is restart.
    _trace_t0 = time.perf_counter()
    logger.info(
        "[chat-trace] enter post_user_message: tenant=%s session=%s sender=%s",
        str(session.tenant_id)[:8], str(session.id)[:8], sender_phone or "web",
    )

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

    # Publish chat_message events to the channel-agnostic v2 stream so
    # the dashboard's AgentActivityPanel + future viewports see the
    # turn live. Fail-soft: if publish raises, the chat turn itself
    # still succeeds — events are observational, not load-bearing.
    # `publish_session_event` opens its own DB session and persists
    # to `session_events` before publishing to Redis.
    try:
        from app.services.collaboration_events import publish_session_event
        publish_session_event(
            str(session.id),
            "chat_message",
            {
                "role": "user",
                "text": content,
                "message_id": str(user_message.id),
            },
            tenant_id=str(session.tenant_id) if session.tenant_id else None,
        )
        if assistant_message and assistant_message.content:
            ctx = assistant_message.context or {}
            publish_session_event(
                str(session.id),
                "chat_message",
                {
                    "role": "alpha",
                    "text": assistant_message.content,
                    "message_id": str(assistant_message.id),
                    "served_by": ctx.get("served_by"),
                    "tokens": ctx.get("token_count"),
                    "cost_usd": ctx.get("cost_usd"),
                },
                tenant_id=str(session.tenant_id) if session.tenant_id else None,
            )
    except Exception:
        logger.exception("post_user_message: publish_session_event failed (non-fatal)")

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
    safe_rollback(db)
    # Capture identity attributes as plain Python values BEFORE we make any
    # downstream DB calls. SQLAlchemy expires `session` attributes after a
    # commit/rollback inside the dispatch path; subsequent attribute reads
    # then trigger an auto-refresh SELECT against `chat_sessions`. If the
    # underlying psycopg2 transaction is poisoned at that moment the SELECT
    # raises `InFailedSqlTransaction` from inside a logger.info() call,
    # which surfaces to users as a generic Luna error even though the
    # response itself was already produced. Capturing the values up front
    # decouples log/string operations from the live ORM identity map.
    _session_id_str = str(session.id)
    _session_id_short = _session_id_str[:8]
    _session_tenant_id = session.tenant_id
    # Also snapshot memory_context — see the same auto-refresh hazard.
    # This snapshot is reused (a) inside the route_and_execute else-branch
    # below, and (b) in the post-route recalled_entity_names merge. We
    # capture once here so both code paths (coalition + route) can share it
    # without re-triggering a SELECT against chat_sessions.
    _session_memory_snapshot = dict(session.memory_context or {})
    # [chat-trace] anchor for this function — `_trace_t0` from
    # `post_user_message` is in a different scope and not visible here.
    # Reset locally so the elapsed= readings in the route_and_execute
    # bracket below have a defined reference.
    _trace_t0 = time.perf_counter()
    from app.services.agent_router import route_and_execute
    from app.services.skill_manager import skill_manager

    # Derive ordered list of skill slugs that should compose this turn's
    # CLAUDE.md. The first entry is the agent's identity skill; the rest
    # (if any) are additional capability bundles concatenated after it.
    #
    # Frontmatter shape we accept on agent.config:
    #   skills: [primary, capability_a, capability_b]   # preferred (PR2+)
    #   skill_slug: "primary"                           # legacy single-slot
    # If neither is set, we fall back to the tenant's primary agent slug.
    agent_skill_slugs: list[str] = []
    primary_slug = resolve_primary_agent_slug(db, session.tenant_id)
    if session.agent:
        agent_config = session.agent.config or {}
        skills_list = agent_config.get("skills")
        if isinstance(skills_list, list) and skills_list:
            agent_skill_slugs = [str(s) for s in skills_list if s]
        elif agent_config.get("skill_slug"):
            agent_skill_slugs = [str(agent_config["skill_slug"])]
        else:
            # Try to find a matching skill by agent name (case-insensitive).
            # Existing fallback for agents created before either field was set.
            agent_name_lower = (session.agent.name or "").lower()
            for skill in skill_manager.list_skills():
                if skill.slug == agent_name_lower or agent_name_lower.startswith(skill.slug):
                    agent_skill_slugs = [skill.slug]
                    break

    if not agent_skill_slugs:
        agent_skill_slugs = [primary_slug]

    agent_slug = agent_skill_slugs[0]  # identity = first skill in the list

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

    # @coalition prefix: dispatch CoalitionWorkflow directly (fire-and-forget)
    if cli_message.strip().lower().startswith("@coalition"):
        task_description = cli_message.strip()[len("@coalition"):].strip()
        # Strip optional sub-command prefixes like "investigate:", "analyze:", etc.
        for _pfx in ("investigate:", "analyze:", "research:", "plan:", "review:"):
            if task_description.lower().startswith(_pfx):
                task_description = task_description[len(_pfx):].strip()
                break
        if not task_description:
            task_description = cli_message
        try:
            from app.services.agent_router import dispatch_coalition
            dispatch_coalition(_session_tenant_id, _session_id_str, task_description)
            logger.info("@coalition dispatched for session %s: %s", _session_id_str, task_description[:80])
        except Exception as _e:
            logger.warning("@coalition dispatch failed: %s", _e)
        response_text = "Multi-agent coalition assembled. Watch the **Collaboration Panel** for live updates as each agent works through the investigation phases."
        context = {"agent_tier": "coalition", "coalition_dispatched": True}
    else:
        # Routing and execution. Use the pre-captured `_session_id_short` /
        # `_session_id_str` / `_session_tenant_id` locals — see the comment
        # block at the top of this function. Reading `session.id` here would
        # auto-refresh against the live ORM identity map and raise from
        # inside the `logger.info()` call when the txn is poisoned.
        # session.memory_context was already snapshotted at function entry
        # (`_session_memory_snapshot`) — see the comment block at the top.
        logger.info(
            "[chat-trace] route_and_execute: enter session=%s elapsed=%.0fms",
            _session_id_short, (time.perf_counter() - _trace_t0) * 1000,
        )
        try:
            response_text, context = route_and_execute(
                db,
                tenant_id=_session_tenant_id,
                user_id=user_id,
                message=cli_message,
                channel="whatsapp" if sender_phone else "web",
                sender_phone=sender_phone,
                agent_slug=agent_slug,
                agent_skill_slugs=agent_skill_slugs,
                conversation_summary=summary,
                image_b64=image_b64,
                image_mime=image_mime,
                db_session_memory={
                    **_session_memory_snapshot,
                    "chat_session_id": _session_id_str,
                },
            )
            logger.info(
                "[chat-trace] route_and_execute: return session=%s elapsed=%.0fms response=%s",
                _session_id_short, (time.perf_counter() - _trace_t0) * 1000,
                "ok" if response_text else "none",
            )
        except Exception as e:
            # Roll back BEFORE we continue — route_and_execute holds the
            # request's db session for the entire dispatch path
            # (cli_session_manager → memory recall → agent_router) and any
            # broken-state propagation here would poison the db.commit()
            # calls below (assistant message append, ExecutionTrace write,
            # session memory_context update). This is the consumer-side
            # guard for the cascade that PR #349 diagnosed.
            safe_rollback(db)
            logger.error(
                "[chat-trace] route_and_execute: raised session=%s elapsed=%.0fms err=%s",
                _session_id_short, (time.perf_counter() - _trace_t0) * 1000, e,
                exc_info=True,
            )
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
        # PR #361 follow-up: same auto-refresh trap as the success-log line.
        # Reading `session.memory_context` here triggers a SELECT-refresh
        # against chat_sessions; if the txn was poisoned upstream that
        # SELECT raises InFailedSqlTransaction and Luna's reply gets
        # swallowed by the outer "Failed to process through agent pipeline"
        # handler in whatsapp_service.py. Use the snapshot we captured
        # before route_and_execute (line ~406) instead, and isolate the
        # commit in its own try/safe_rollback so a poisoned txn here can't
        # silence the response.
        try:
            _mem_dirty = False
            _mem = dict(_session_memory_snapshot)
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
        except Exception as _mem_e:
            safe_rollback(db)
            logger.warning(
                "[chat-trace] session.memory_context update failed for session=%s — continuing with response: %s",
                _session_id_short, _mem_e,
            )

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
                # Failure-only for the Gemini path until we wire stdout-side
                # tool-event capture (FastMCP-side or --debug). Empty list does
                # NOT mean "no tools used" — it means "no tool errors observed".
                "tools_called": meta.get("tools_called") or [],
                # Phase A.1 stage breakdown — populated by cli_session_manager
                # via metadata['timings']. Each value is ms-since-prev-mark.
                "timings": meta.get("timings") or {},
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

    # Dispatch post-chat memory activities (Temporal) — runs for all tenants.
    try:
        from app.memory.dispatch import dispatch_post_chat_memory
        dispatch_post_chat_memory(
            tenant_id=session.tenant_id,
            session_id=session.id,
            user_message_id=user_message_id,
            assistant_message_id=assistant_msg.id,
        )
    except Exception as e:
        logger.error("Failed to dispatch PostChatMemoryWorkflow: %s", e)

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
            chat_session_id=str(session.id),
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
