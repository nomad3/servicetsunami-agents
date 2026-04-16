"""Collaboration session API endpoints."""

import json
import logging
import time
import uuid
from typing import List, Optional

import redis as redis_lib

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.blackboard import Blackboard, BlackboardEntry
from app.models.user import User
from app.schemas.blackboard import BlackboardEntryInDB, BlackboardInDB
from app.schemas.collaboration import (
    AdvancePhaseRequest,
    CollaborationSessionCreate,
    CollaborationSessionInDB,
)
from app.services import collaboration_service

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("", response_model=List[CollaborationSessionInDB])
def list_sessions(
    session_status: Optional[str] = Query(default=None, alias="status"),
    blackboard_id: Optional[uuid.UUID] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List collaboration sessions."""
    return collaboration_service.list_sessions(
        db, current_user.tenant_id,
        status=session_status,
        blackboard_id=blackboard_id,
        limit=limit,
    )


@router.post("", response_model=CollaborationSessionInDB, status_code=status.HTTP_201_CREATED)
def create_session(
    session_in: CollaborationSessionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new collaboration session on an existing blackboard."""
    try:
        return collaboration_service.create_session(db, current_user.tenant_id, session_in)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/{session_id}", response_model=CollaborationSessionInDB)
def get_session(
    session_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get a collaboration session."""
    session = collaboration_service.get_session(db, current_user.tenant_id, session_id)
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return session


@router.post("/{session_id}/advance", response_model=dict)
def advance_phase(
    session_id: uuid.UUID,
    request: AdvancePhaseRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Contribute to the current phase and advance the collaboration.

    Each agent posts their contribution for their role's phase. The session
    advances through the pattern's phases and can loop for multiple rounds
    when disagreements arise.
    """
    try:
        result = collaboration_service.advance_phase(
            db, current_user.tenant_id, session_id,
            agent_slug=request.agent_slug,
            contribution=request.contribution,
            evidence=request.evidence,
            confidence=request.confidence,
            agrees_with_previous=request.agrees_with_previous,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    if not result:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Session not active or not found",
        )
    return result


@router.get("/{session_id}/stream")
def collaboration_stream(
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
):
    """SSE stream with Postgres catch-up then live Redis.

    Releases DB connection immediately after catch-up to avoid pool exhaustion.
    """
    from app.core.config import settings
    from app.db.session import SessionLocal
    from app.services.collaboration_events import subscribe_collaboration

    db = SessionLocal()
    try:
        collab = collaboration_service.get_session(db, current_user.tenant_id, session_id)
        if not collab:
            raise HTTPException(status_code=404, detail="Collaboration session not found")

        # Step 1: Query Postgres for entries already written.
        catch_up_entries = (
            db.query(BlackboardEntry)
            .filter(BlackboardEntry.blackboard_id == collab.blackboard_id)
            .order_by(BlackboardEntry.board_version.asc())
            .all()
        )
        seen_versions: set = set()
        catch_up_data = []
        for e in catch_up_entries:
            seen_versions.add(e.board_version)
            catch_up_data.append(json.dumps({
                "event_type": "blackboard_entry",
                "payload": {
                    "entry_id": str(e.id),
                    "entry_type": e.entry_type,
                    "author_slug": e.author_agent_slug,
                    "author_role": e.author_role,
                    "content_preview": (e.content or "")[:200],
                    "content_full": e.content,
                    "confidence": e.confidence,
                    "board_version": e.board_version,
                },
                "timestamp": e.created_at.timestamp() if e.created_at else 0,
            }))
    finally:
        db.close()

    channel = f"collaboration:{str(session_id)}"

    def _stream():
        # Yield historical entries from Postgres
        for event_data in catch_up_data:
            yield f"data: {event_data}\n\n"

        # Subscribe to live Redis stream
        pubsub = None
        try:
            r = redis_lib.Redis.from_url(settings.REDIS_URL, decode_responses=True)
            pubsub = r.pubsub()
            pubsub.subscribe(channel)
        except Exception as e:
            logger.warning("Redis subscribe failed for collab %s: %s", session_id, e)
            yield from subscribe_collaboration(str(session_id))
            return

        # Drain the pubsub, skipping duplicates by board_version
        last_heartbeat = time.time()
        heartbeat_interval = 15
        try:
            for message in pubsub.listen():
                if message["type"] == "message":
                    try:
                        data = json.loads(message["data"])
                        if data.get("event_type") == "blackboard_entry":
                            bv = data.get("payload", {}).get("board_version")
                            if bv is not None and bv in seen_versions:
                                continue  # already sent from catch-up
                            if bv is not None:
                                seen_versions.add(bv)
                        yield f"data: {message['data']}\n\n"
                        if data.get("event_type") == "collaboration_completed":
                            pubsub.unsubscribe(channel)
                            return
                    except Exception:
                        yield f"data: {message['data']}\n\n"

                if time.time() - last_heartbeat > heartbeat_interval:
                    yield ": heartbeat\n\n"
                    last_heartbeat = time.time()
        except Exception as e:
            logger.warning("Redis pubsub error for collab %s: %s", session_id, e)
            yield f"data: {json.dumps({'event_type': 'error', 'payload': {'detail': 'Stream connection lost'}})}\n\n"
        finally:
            try:
                pubsub.close()
            except Exception:
                pass

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/{session_id}/detail")
def collaboration_detail(
    session_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Full detail with all blackboard entries — powers replay UI."""
    collab = collaboration_service.get_session(db, current_user.tenant_id, session_id)
    if not collab:
        raise HTTPException(status_code=404, detail="Collaboration session not found")

    entries = (
        db.query(BlackboardEntry)
        .filter(BlackboardEntry.blackboard_id == collab.blackboard_id)
        .order_by(BlackboardEntry.board_version.asc())
        .all()
    )
    board = db.query(Blackboard).filter(Blackboard.id == collab.blackboard_id).first()

    return {
        "session": CollaborationSessionInDB.model_validate(collab),
        "blackboard": BlackboardInDB.model_validate(board) if board else None,
        "entries": [BlackboardEntryInDB.model_validate(e) for e in entries],
        "entry_count": len(entries),
        "phases_completed": collab.phase_index,
        "rounds_completed": collab.rounds_completed,
    }


class CollaborationTriggerRequest(BaseModel):
    chat_session_id: uuid.UUID
    task_description: str
    pattern: Optional[str] = None
    role_overrides: Optional[dict] = None


@router.post("/trigger", status_code=202)
def trigger_collaboration(
    request: CollaborationTriggerRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Manually trigger a CoalitionWorkflow. Returns 202 immediately."""
    from app.services.agent_router import dispatch_coalition

    dispatch_coalition(
        tenant_id=current_user.tenant_id,
        chat_session_id=str(request.chat_session_id),
        task_description=request.task_description,
    )

    return {
        "status": "dispatched",
        "chat_session_id": str(request.chat_session_id),
        "task_description": request.task_description,
        "message": "CoalitionWorkflow dispatched. Subscribe to GET /chat/sessions/{id}/events for collaboration_started.",
    }
