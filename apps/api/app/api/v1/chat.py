from __future__ import annotations

import asyncio
import logging
from typing import List, Optional
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api import deps
from app.models.user import User
from app.schemas import chat as chat_schema
from app.schemas import knowledge_entity as ke_schema
from app.services import chat as chat_service
from app.services import chat_jobs as chat_jobs_service
from app.services import knowledge as knowledge_service
from app.services.embedding_service import embed_and_store as _embed
from app.services.enhanced_chat import get_enhanced_chat_service

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/episodes", response_model=List[dict])
def list_recent_episodes(
    *,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
    limit: int = 10,
):
    """Return recent conversation episodes for context continuity."""
    from app.models.conversation_episode import ConversationEpisode

    episodes = (
        db.query(ConversationEpisode)
        .filter(ConversationEpisode.tenant_id == current_user.tenant_id)
        .order_by(ConversationEpisode.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": str(e.id),
            "summary": e.summary,
            "mood": e.mood,
            "key_entities": e.key_entities or [],
            "source_channel": e.source_channel,
            "message_count": e.message_count,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in episodes
    ]


@router.get("/sessions", response_model=List[chat_schema.ChatSession])
def list_sessions(
    *,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    return chat_service.list_sessions(db, tenant_id=current_user.tenant_id)


@router.post(
    "/sessions",
    response_model=chat_schema.ChatSession,
    status_code=status.HTTP_201_CREATED,
)
def create_session(
    payload: chat_schema.ChatSessionCreate,
    *,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    try:
        session = chat_service.create_session(
            db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            dataset_id=payload.dataset_id,
            dataset_group_id=payload.dataset_group_id,
            agent_id=payload.agent_id,
            title=payload.title,
        )
        return session
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get(
    "/sessions/{session_id}",
    response_model=chat_schema.ChatSession,
)
def read_session(
    session_id: uuid.UUID,
    *,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    session = chat_service.get_session(db, session_id=session_id, tenant_id=current_user.tenant_id)
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat session not found")
    return session


@router.get(
    "/sessions/{session_id}/messages",
    response_model=List[chat_schema.ChatMessage],
)
def list_messages(
    session_id: uuid.UUID,
    *,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    session = chat_service.get_session(db, session_id=session_id, tenant_id=current_user.tenant_id)
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat session not found")
    return session.messages


@router.get(
    "/sessions/{session_id}/entities",
    response_model=List[ke_schema.KnowledgeEntity],
)
def get_session_entities(
    session_id: uuid.UUID,
    *,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Return knowledge entities for the tenant (scoped via session ownership).

    NOTE: Entities are not yet tagged with a source session_id in the schema,
    so this returns all tenant entities.  The per-message badge
    (context.entities_extracted) is the primary UX; this endpoint is
    preparatory for a future entities panel.
    """
    session = chat_service.get_session(db, session_id=session_id, tenant_id=current_user.tenant_id)
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat session not found")
    return knowledge_service.get_entities(db, tenant_id=current_user.tenant_id)


@router.post(
    "/sessions/{session_id}/messages",
    response_model=chat_schema.ChatTurn,
    status_code=status.HTTP_201_CREATED,
)
def post_message(
    session_id: uuid.UUID,
    payload: chat_schema.ChatMessageCreate,
    *,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    session = chat_service.get_session(db, session_id=session_id, tenant_id=current_user.tenant_id)
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat session not found")

    user_message, assistant_message = chat_service.post_user_message(
        db,
        session=session,
        user_id=current_user.id,
        content=payload.content,
    )
    return chat_schema.ChatTurn(
        user_message=chat_schema.ChatMessage.model_validate(user_message),
        assistant_message=chat_schema.ChatMessage.model_validate(assistant_message)
    )


@router.post(
    "/sessions/{session_id}/messages/stream",
    status_code=status.HTTP_200_OK,
)
def post_message_stream(
    session_id: uuid.UUID,
    payload: chat_schema.ChatMessageCreate,
    *,
    current_user: User = Depends(deps.get_current_active_user),
):
    """SSE endpoint: generate the full response then stream it back 2 words at a time."""
    import json as _json
    import time as _time
    import threading as _threading
    from app.db.session import SessionLocal

    # Run generation in a background thread so we can immediately stream
    # heartbeat comments — prevents Cloudflare 524 on slow/long responses.
    result: dict = {}
    done_event = _threading.Event()

    def _generate():
        gen_db = SessionLocal()
        try:
            session = chat_service.get_session(gen_db, session_id=session_id, tenant_id=current_user.tenant_id)
            if not session:
                result["error"] = "Chat session not found"
                return

            user_msg, assistant_msg = chat_service.post_user_message(
                gen_db,
                session=session,
                user_id=current_user.id,
                content=payload.content,
            )
            # Eagerly convert to schemas before closing session
            result["user_message"] = chat_schema.ChatMessage.model_validate(user_msg).model_dump(mode='json')
            result["assistant_message"] = chat_schema.ChatMessage.model_validate(assistant_msg).model_dump(mode='json')
            result["content"] = assistant_msg.content or ""
        except Exception as exc:
            logger.exception("Stream generation failed")
            result["error"] = str(exc)
        finally:
            gen_db.close()
            done_event.set()

    _threading.Thread(target=_generate, daemon=True).start()

    chunk_size = 2  # ~2 tokens per SSE event

    def _event_generator():
        # Send heartbeat comments immediately so Cloudflare sees data flowing.
        heartbeat_interval = 3  # seconds
        while not done_event.wait(timeout=heartbeat_interval):
            yield ": heartbeat\n\n"

        if "error" in result:
            yield f"data: {_json.dumps({'type': 'error', 'detail': result['error']})}\n\n"
            return

        # Defensive: the generation thread should have populated either "error"
        # or "user_message"/"assistant_message" before signalling done_event.
        # If it set done_event without populating either (e.g. an exception
        # outside the try/except boundary, or a thread death we didn't catch),
        # surface that explicitly instead of crashing the SSE response with a
        # KeyError that propagates to the client as a 500.
        if "user_message" not in result or "assistant_message" not in result:
            yield f"data: {_json.dumps({'type': 'error', 'detail': 'Generation thread exited without producing a response'})}\n\n"
            return

        # First event: user message saved
        yield f"data: {_json.dumps({'type': 'user_saved', 'message': result['user_message']})}\n\n"

        # Stream the assistant response 2 words at a time
        full_text = result.get("content", "")
        words = full_text.split(" ")
        for i in range(0, len(words), chunk_size):
            chunk = " ".join(words[i:i + chunk_size])
            if i + chunk_size < len(words):
                chunk += " "
            yield f"data: {_json.dumps({'type': 'token', 'text': chunk})}\n\n"
            _time.sleep(0.01)  # Faster streaming

        # Final event: complete message
        yield f"data: {_json.dumps({'type': 'done', 'message': result['assistant_message']})}\n\n"

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/sessions/{session_id}/messages/upload",
    response_model=chat_schema.ChatTurn,
    status_code=status.HTTP_201_CREATED,
)
async def post_message_with_file(
    session_id: uuid.UUID,
    content: str = Form(""),
    file: UploadFile = File(...),
    *,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Post a message with a file attachment (image, audio, or PDF)."""
    session = chat_service.get_session(
        db, session_id=session_id, tenant_id=current_user.tenant_id,
    )
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat session not found")

    from app.services.media_utils import build_media_parts, classify_media

    file_bytes = await file.read()
    mime_type = file.content_type or "application/octet-stream"

    media_type = classify_media(mime_type)
    if media_type == "unsupported":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type: {mime_type}",
        )

    # Async-safe transcription path: if the upload is audio, resolve the
    # transcript via the code-worker workflow BEFORE calling the (sync)
    # build_media_parts helper. transcribe_bytes_sync inside an async
    # handler blocks the event loop on its ThreadPoolExecutor bridge —
    # see transcription_client.py docstring. We pass an empty string
    # (NOT None) on failure so build_media_parts skips its sync dispatch
    # branch and falls straight to inline_data.
    precomputed_transcript: Optional[str] = None
    if media_type == "audio":
        precomputed_transcript = ""  # sentinel — skip sync dispatch even if async failed
        try:
            from app.services.transcription_client import (
                TranscriptionUnavailable,
                transcribe_async,
            )

            tr = await transcribe_async(file_bytes)
            if tr.status == "completed" and tr.transcript:
                precomputed_transcript = tr.transcript
        except TranscriptionUnavailable:
            logger.warning("Transcription service unavailable for chat upload; falling back to inline audio")
        except Exception:
            logger.exception("Inline transcription failed for chat upload; falling back to inline audio")

    try:
        parts, attachment_meta = build_media_parts(
            media_bytes=file_bytes,
            mime_type=mime_type,
            caption=content,
            filename=file.filename or "",
            precomputed_transcript=precomputed_transcript,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    user_msg, assistant_msg = await asyncio.to_thread(
        chat_service.post_user_message,
        db,
        session=session,
        user_id=current_user.id,
        content=content or f"[Sent {media_type}: {file.filename}]",
        media_parts=parts,
        attachment_meta=attachment_meta,
    )

    # Embed attachment text for semantic search
    try:
        embed_text = f"{file.filename or 'attachment'}: {(attachment_meta or {}).get('extracted_text', content)[:2000]}"
        _embed(
            db,
            tenant_id=current_user.tenant_id,
            content_type="attachment",
            content_id=str(user_msg.id),
            text_content=embed_text,
        )
    except Exception:
        pass  # Never break uploads for embedding failures

    return chat_schema.ChatTurn(
        user_message=chat_schema.ChatMessage.model_validate(user_msg),
        assistant_message=chat_schema.ChatMessage.model_validate(assistant_msg),
    )


@router.post("/sessions/enhanced", response_model=chat_schema.ChatSession)
def create_session_enhanced(
    *,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
    session_in: chat_schema.ChatSessionCreate,
    agent_group_id: Optional[uuid.UUID] = None,
):
    """Create chat session with optional agent group orchestration."""
    enhanced_service = get_enhanced_chat_service(db, current_user.tenant_id)
    return enhanced_service.create_session_with_orchestration(
        dataset_id=session_in.dataset_id,
        agent_id=session_in.agent_id,
        agent_group_id=agent_group_id,
        user_id=current_user.id,
        title=session_in.title,
    )


@router.post("/sessions/{session_id}/messages/enhanced", response_model=chat_schema.ChatTurn)
def post_message_enhanced(
    *,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
    session_id: uuid.UUID,
    message_in: chat_schema.ChatMessageCreate,
    agent_id: Optional[uuid.UUID] = None,
):
    """Post message with memory integration."""
    session = chat_service.get_session(
        db, session_id=session_id, tenant_id=current_user.tenant_id
    )
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    enhanced_service = get_enhanced_chat_service(db, current_user.tenant_id)
    user_msg, assistant_msg = enhanced_service.post_message_with_memory(
        session=session,
        user_id=current_user.id,
        content=message_in.content,
        agent_id=agent_id,
    )
    return chat_schema.ChatTurn(
        user_message=chat_schema.ChatMessage.model_validate(user_msg),
        assistant_message=chat_schema.ChatMessage.model_validate(assistant_msg)
    )


@router.get("/sessions/{session_id}/events")
def session_events_stream(
    session_id: uuid.UUID,
    *,
    current_user: User = Depends(deps.get_current_active_user),
):
    """Long-lived SSE stream for session-level events (collaboration_started, etc.)."""
    from app.db.session import SessionLocal
    db = SessionLocal()
    try:
        session = chat_service.get_session(db, session_id=session_id, tenant_id=current_user.tenant_id)
        if not session:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat session not found")
    finally:
        db.close()

    from app.services.collaboration_events import subscribe_session

    return StreamingResponse(
        subscribe_session(str(session_id)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/sessions/{session_id}/collaborations")
def list_session_collaborations(
    session_id: uuid.UUID,
    *,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """List all collaborations linked to this chat session."""
    from app.models.blackboard import Blackboard
    from app.models.collaboration import CollaborationSession
    from app.schemas.collaboration import CollaborationSessionInDB

    boards = db.query(Blackboard).filter(
        Blackboard.tenant_id == current_user.tenant_id,
        Blackboard.chat_session_id == session_id,
    ).all()

    board_ids = [b.id for b in boards]
    if not board_ids:
        return []

    sessions = db.query(CollaborationSession).filter(
        CollaborationSession.tenant_id == current_user.tenant_id,
        CollaborationSession.blackboard_id.in_(board_ids),
    ).order_by(CollaborationSession.created_at.desc()).all()

    return [CollaborationSessionInDB.model_validate(s) for s in sessions]


# ──────────────────────────────────────────────────────────────────────
# Async chat-result pattern — task #161
# Design: docs/plans/2026-05-17-async-chat-result-pattern-design.md
#
# POST /sessions/{sid}/messages/start    → { job_id }    (returns immediately)
# GET  /jobs/{job_id}/events?from=<seq>  → SSE (replay + tail)
# GET  /jobs/{job_id}                    → terminal status / result snapshot
# POST /jobs/{job_id}/cancel             → set cancel_requested flag
#
# Why a new path instead of replacing /messages/stream:
#   * Phase 1 of the rollout (per design doc §"Migration path") is
#     write-path-only — both endpoints coexist while we soak.
#   * The legacy /stream endpoint is what every existing client (web,
#     Tauri, Luna mobile) currently uses; cutting it would break
#     traffic before we have read-path parity confidence.
#   * The flag-gated cutover (chat_async_jobs) ramps tenants when
#     they're ready. Code deletion is Phase 4.
# ──────────────────────────────────────────────────────────────────────


class _JobStartResponse(BaseModel):
    """Tiny envelope so a future field add (e.g. queue depth) doesn't
    require a route version bump."""
    job_id: uuid.UUID


@router.post(
    "/sessions/{session_id}/messages/start",
    response_model=_JobStartResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def post_message_start(
    session_id: uuid.UUID,
    payload: chat_schema.ChatMessageCreate,
    *,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Create a chat_job + dispatch generation; return job_id immediately.

    Cloudflare's 524 ceiling kills any HTTP request that goes idle for
    >100 s. Returning a job_id in <200 ms (then having the client
    subscribe to /jobs/{job_id}/events) keeps the original request
    short while the work continues server-side.
    """
    session = chat_service.get_session(
        db, session_id=session_id, tenant_id=current_user.tenant_id
    )
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat session not found",
        )

    job = chat_jobs_service.create_job(
        db,
        session_id=session_id,
        tenant_id=current_user.tenant_id,
        user_id=current_user.id,
        content=payload.content,
    )

    # Snapshot primitives BEFORE the worker thread starts. `current_user`
    # came from `Depends(get_db)`-scoped helpers, and `payload` is the
    # request body; both become unsafe to touch once this handler
    # returns (request scope closes, ORM session is gone, DetachedInstanceError
    # under load). Mirrors the pattern used by `stream_chat_job_events`
    # above. The worker closes over THESE primitives, never the ORM.
    tenant_id = current_user.tenant_id
    user_id = current_user.id
    content = payload.content
    sid = session_id
    job_uuid = uuid.UUID(job["id"])

    # Worker dispatch — runs in a background thread so this request
    # returns immediately. Each job opens its own DB session; the
    # request-scoped `db` is not safe to share across threads.
    import threading

    def _run_job():
        import time as _time
        from app.db.session import SessionLocal

        wdb = SessionLocal()

        # Cancel-propagation contract (BLOCKER #2 from review):
        # cancel_job() only flips `cancel_requested`. The worker MUST
        # poll for that flag between phases and self-flip via
        # observe_cancel(). Granularity is "between each side-effect"
        # not "inside post_user_message" — post_user_message is a
        # single synchronous call we don't have hooks into. Phases
        # gated below:
        #   (i)   before start_job's lifecycle.started event
        #   (ii)  before post_user_message
        #   (iii) before the final chunk event
        #   (iv)  before finish_job
        def _bail_if_cancelled() -> bool:
            if chat_jobs_service.is_cancel_requested(wdb, job_id=job_uuid):
                chat_jobs_service.append_event(
                    wdb,
                    job_id=job_uuid,
                    kind="lifecycle",
                    payload={"event": "cancelled"},
                )
                chat_jobs_service.observe_cancel(wdb, job_id=job_uuid)
                return True
            return False

        try:
            chat_jobs_service.start_job(wdb, job_id=job_uuid)
            if _bail_if_cancelled():
                return
            chat_jobs_service.append_event(
                wdb,
                job_id=job_uuid,
                kind="lifecycle",
                payload={"event": "started"},
            )

            # Re-fetch the session in the worker's own DB scope so we
            # don't reuse a request-scoped ORM identity across threads.
            wsession = chat_service.get_session(
                wdb, session_id=sid, tenant_id=tenant_id
            )
            if not wsession:
                chat_jobs_service.append_event(
                    wdb,
                    job_id=job_uuid,
                    kind="lifecycle",
                    payload={"event": "error", "detail": "session vanished"},
                )
                chat_jobs_service.fail_job(
                    wdb, job_id=job_uuid, error="Chat session not found",
                )
                return

            if _bail_if_cancelled():
                return

            t0 = _time.perf_counter()
            user_msg, assistant_msg = chat_service.post_user_message(
                wdb,
                session=wsession,
                user_id=user_id,
                content=content,
            )

            if _bail_if_cancelled():
                return

            # Emit a single `chunk` event with the full text. The
            # client renders it the same way it would render a stream
            # of small chunks — the protocol is the same shape.
            #
            # Future: code-worker iterations will emit progressive
            # `chunk` events as the CLI streams them. For now the
            # CLI path returns whole-message; we wrap that in the
            # event-log shape so the client code is forward-compatible.
            text_out = assistant_msg.content or ""
            chat_jobs_service.append_event(
                wdb,
                job_id=job_uuid,
                kind="chunk",
                payload={"text": text_out},
            )

            chat_jobs_service.append_event(
                wdb,
                job_id=job_uuid,
                kind="lifecycle",
                payload={
                    "event": "done",
                    "user_message_id": str(user_msg.id),
                    "assistant_message_id": str(assistant_msg.id),
                    "elapsed_s": _time.perf_counter() - t0,
                },
            )
            if _bail_if_cancelled():
                return
            # finish_job is gated on status NOT IN terminal; even if the
            # cancel beat us here, observe_cancel(running -> cancelled)
            # makes this UPDATE a no-op. Race-free by SQL constraint.
            chat_jobs_service.finish_job(
                wdb,
                job_id=job_uuid,
                result_message_id=assistant_msg.id,
            )
        except Exception as exc:
            logger.exception("[chat-job %s] worker crashed", job_uuid)
            try:
                chat_jobs_service.append_event(
                    wdb,
                    job_id=job_uuid,
                    kind="lifecycle",
                    payload={"event": "error", "detail": str(exc)[:512]},
                )
            except Exception:
                pass
            try:
                chat_jobs_service.fail_job(
                    wdb,
                    job_id=job_uuid,
                    error=str(exc),
                )
            except Exception:
                pass
        finally:
            wdb.close()

    threading.Thread(target=_run_job, daemon=True).start()
    return _JobStartResponse(job_id=job_uuid)


@router.get("/jobs/{job_id}")
def get_chat_job(
    job_id: uuid.UUID,
    *,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Return the current job-state snapshot.

    Used by the client's polling-fallback path when SSE isn't viable
    (some corporate proxies kill text/event-stream even with the new
    short-lived design — the client falls back to repeated GETs).
    """
    job = chat_jobs_service.get_job(
        db, job_id=job_id, tenant_id=current_user.tenant_id
    )
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return job


@router.post("/jobs/{job_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
def cancel_chat_job(
    job_id: uuid.UUID,
    *,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Cooperative cancel — sets ``cancel_requested=TRUE``.

    Per design §"Cancel semantics" we lean cooperative + 30 s grace
    before SIGTERM. This endpoint only sets the flag; the worker
    observes it and flips to ``cancelled``. A queued (not-yet-picked-up)
    job flips immediately.
    """
    job = chat_jobs_service.get_job(
        db, job_id=job_id, tenant_id=current_user.tenant_id
    )
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    chat_jobs_service.cancel_job(db, job_id=job_id)
    return {"job_id": str(job_id), "cancel_requested": True}


@router.get("/jobs/{job_id}/events")
def stream_chat_job_events(
    job_id: uuid.UUID,
    from_seq: int = 0,
    *,
    current_user: User = Depends(deps.get_current_active_user),
):
    """SSE stream — replay events with seq > from_seq, then tail.

    Reconnect-safe by design: the client remembers the highest seq it
    rendered, and on reconnect passes ``?from_seq=<n>`` so the catch-up
    block doesn't re-emit history it's already painted.

    Tail strategy: short-poll the event log every 1 s. Cheap because
    the inner loop only fires SELECTs against (job_id, seq) PK
    (index-only scans) and the connection is short-lived — we close
    the moment the job's status row hits terminal.
    """
    import json as _json
    import time as _time

    from app.db.session import SessionLocal

    # Snapshot tenant id outside the generator — the dependency-injected
    # `current_user` may be garbage-collected by the time the generator
    # runs because the request body returns first.
    tenant_id = current_user.tenant_id

    # Pre-flight ownership check using a transient DB session — surface a
    # 404 *as a normal HTTP response* (not embedded in the SSE stream)
    # so the client's error handling matches its REST expectations.
    _pf_db = SessionLocal()
    try:
        job_pf = chat_jobs_service.get_job(_pf_db, job_id=job_id, tenant_id=tenant_id)
        if job_pf is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    finally:
        _pf_db.close()

    def _serialize_event(payload: dict) -> str:
        """Single source of truth for SSE frame shape.

        All four event types (event / terminal / timeout / truncated
        preamble) ship through this so future schema drift is one
        diff, not four. The trailing ``\n\n`` is the SSE record
        separator.
        """
        return f"data: {_json.dumps(payload)}\n\n"

    def _gen():
        last_seq = int(from_seq)
        idle_iters = 0
        # Hard ceiling so a runaway never holds the request forever; the
        # client reconnects with `from_seq=<last_seq>` on a clean close.
        # 600 iter * 1 s = 10 min — well below CF's 100 s idle ceiling
        # because each loop yields data or a heartbeat.
        max_iters = 600
        sse_db = SessionLocal()
        try:
            # Pre-flight preamble: if the very first read_events would
            # truncate (events buffered up == limit), tell the client
            # so it can immediately page via from_seq=<last_seq>.
            # (NIT #12 from review — cheap surface, no extra round trip.)
            initial_events = chat_jobs_service.read_events(
                sse_db, job_id=job_id, from_seq=last_seq
            )
            if len(initial_events) >= 2000:
                yield _serialize_event({"type": "truncated", "from_seq": last_seq})

            # Emit the pre-fetched batch via the normal event path so
            # we don't double-read.
            pending_initial = initial_events

            for _ in range(max_iters):
                events = pending_initial if pending_initial else chat_jobs_service.read_events(
                    sse_db, job_id=job_id, from_seq=last_seq
                )
                pending_initial = None
                if events:
                    idle_iters = 0
                    for ev in events:
                        last_seq = ev["seq"]
                        yield _serialize_event({
                            "type": "event",
                            "seq": ev["seq"],
                            "kind": ev["kind"],
                            "payload": ev["payload"],
                        })

                # After draining new events, check terminal status.
                job_now = chat_jobs_service.get_job(
                    sse_db, job_id=job_id, tenant_id=tenant_id
                )
                if job_now and job_now["status"] in chat_jobs_service.TERMINAL_STATUSES:
                    yield _serialize_event({
                        "type": "terminal",
                        "status": job_now["status"],
                        "result_message_id": job_now.get("result_message_id"),
                        "error": job_now.get("error"),
                        "last_seq": last_seq,
                    })
                    return

                # No new events this cycle — heartbeat keeps CF awake.
                if not events:
                    idle_iters += 1
                    yield ": heartbeat\n\n"

                _time.sleep(1.0)

            # max_iters exhausted: tell the client to reconnect with
            # ?from_seq=<last_seq>. Without this sentinel a polling
            # caller couldn't tell "server says reconnect" from "TCP
            # died" (IMPORTANT #3).
            yield _serialize_event({"type": "timeout", "last_seq": last_seq})
        finally:
            sse_db.close()

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
