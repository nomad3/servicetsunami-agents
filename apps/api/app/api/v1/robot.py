"""Robot interaction API — audio/vision/ambient capture."""
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional

from app.api.deps import get_current_active_user, get_db
from app.models.user import User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/robot", tags=["robot"])


class InteractRequest(BaseModel):
    audio_b64: Optional[str] = None
    image_b64: Optional[str] = None
    text: Optional[str] = None
    session_id: Optional[str] = None


class VisionAnalyzeRequest(BaseModel):
    image_b64: str
    context: str = ""


class AmbientIngestRequest(BaseModel):
    audio_b64: str
    duration_seconds: float = 30.0
    source: str = "microphone"


@router.post("/interact")
def robot_interact(
    body: InteractRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Full interaction: audio/text -> Luna -> response + emotion + motion_hint."""
    from app.services.chat import post_user_message
    from app.models.chat import ChatSession
    from app.models.agent_kit import AgentKit

    # Get or create robot session
    session = None
    if body.session_id:
        try:
            session = db.query(ChatSession).filter(
                ChatSession.id == uuid.UUID(body.session_id),
                ChatSession.tenant_id == current_user.tenant_id,
            ).first()
        except (ValueError, AttributeError):
            pass

    if not session:
        kit = db.query(AgentKit).filter(
            AgentKit.tenant_id == current_user.tenant_id,
        ).first()
        session = ChatSession(
            title="Robot Session",
            tenant_id=current_user.tenant_id,
            agent_kit_id=kit.id if kit else None,
            source="robot",
        )
        db.add(session)
        db.commit()
        db.refresh(session)

    # Process text input (or STT from audio — placeholder for now)
    message = body.text or "[Audio input — STT not yet implemented]"

    user_msg, assistant_msg = post_user_message(
        db, session=session, user_id=current_user.id, content=message,
    )

    # Derive emotion and motion hint from presence state
    try:
        from app.services import luna_presence_service
        presence = luna_presence_service.get_presence(current_user.tenant_id)
        emotion = presence.get("mood", "calm")
        motion_hint = presence.get("state", "idle")
    except Exception:
        emotion = "calm"
        motion_hint = "idle"

    return {
        "text": assistant_msg.content if assistant_msg else "",
        "emotion": emotion,
        "motion_hint": motion_hint,
        "session_id": str(session.id),
    }


@router.post("/vision/analyze")
def vision_analyze(
    body: VisionAnalyzeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Analyze an image from a camera/robot."""
    try:
        from app.services.knowledge import create_observation
        create_observation(
            db, current_user.tenant_id,
            observation_text=f"Camera frame captured. Context: {body.context[:200]}",
            observation_type="vision",
            source_type="camera",
            source_channel="camera",
        )
        db.commit()
    except Exception:
        pass

    return {
        "description": f"Image received ({len(body.image_b64)} bytes b64). Vision analysis pending — local vision model not yet configured.",
        "context": body.context,
    }


@router.post("/ambient/ingest")
def ambient_ingest(
    body: AmbientIngestRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Ingest ambient audio for knowledge extraction."""
    try:
        from app.services.knowledge import create_observation
        create_observation(
            db, current_user.tenant_id,
            observation_text=f"Ambient audio capture ({body.duration_seconds}s from {body.source}). STT pending.",
            observation_type="ambient",
            source_type="ambient_audio",
            source_channel="microphone",
        )
        db.commit()
    except Exception:
        pass

    return {
        "status": "ingested",
        "duration_seconds": body.duration_seconds,
        "source": body.source,
        "message": "Audio captured. STT processing pending — local whisper model not yet configured.",
    }
