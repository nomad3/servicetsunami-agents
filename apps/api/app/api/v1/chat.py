from __future__ import annotations

import asyncio
from typing import List, Optional
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api import deps
from app.models.user import User
from app.schemas import chat as chat_schema
from app.schemas import knowledge_entity as ke_schema
from app.services import chat as chat_service
from app.services import knowledge as knowledge_service
from app.services.embedding_service import embed_and_store as _embed
from app.services.enhanced_chat import get_enhanced_chat_service

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
            agent_kit_id=payload.agent_kit_id,
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

        # First event: user message saved
        yield f"data: {_json.dumps({'type': 'user_saved', 'message': result['user_message']})}\n\n"

        # Stream the assistant response 2 words at a time
        full_text = result["content"]
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

    try:
        parts, attachment_meta = build_media_parts(
            media_bytes=file_bytes,
            mime_type=mime_type,
            caption=content,
            filename=file.filename or "",
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
        agent_kit_id=session_in.agent_kit_id,
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
