"""Robot interaction API — audio/vision/ambient capture."""
import hashlib
import logging
import uuid
from typing import Optional, Tuple

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user_optional, get_db
from app.models.device_registry import DeviceRegistry
from app.models.user import User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/robot", tags=["robot"])


def _resolve_tenant(
    x_device_token: Optional[str],
    db: Session,
    current_user: Optional[User] = None,
) -> Tuple[uuid.UUID, Optional[uuid.UUID]]:
    """Resolve tenant_id (and optional user_id) from JWT user or device token."""
    if current_user:
        return current_user.tenant_id, current_user.id
    if x_device_token:
        token_hash = hashlib.sha256(x_device_token.encode()).hexdigest()
        device = db.query(DeviceRegistry).filter(
            DeviceRegistry.device_token_hash == token_hash,
        ).first()
        if device:
            return device.tenant_id, None
    raise HTTPException(status_code=401, detail="Authentication required")


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
    x_device_token: Optional[str] = Header(None, alias="X-Device-Token"),
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Full interaction: audio/text -> Luna -> response + emotion + motion_hint."""
    if not body.text and not body.audio_b64 and not body.image_b64:
        raise HTTPException(status_code=400, detail="Provide text, audio_b64, or image_b64")

    tenant_id, user_id = _resolve_tenant(x_device_token, db, current_user)

    from app.services.chat import post_user_message
    from app.models.chat import ChatSession
    from app.models.agent_kit import AgentKit

    # Get or create robot session
    session = None
    if body.session_id:
        try:
            session = db.query(ChatSession).filter(
                ChatSession.id == uuid.UUID(body.session_id),
                ChatSession.tenant_id == tenant_id,
            ).first()
        except (ValueError, AttributeError):
            pass

    if not session:
        kit = db.query(AgentKit).filter(
            AgentKit.tenant_id == tenant_id,
        ).first()
        session = ChatSession(
            title="Robot Session",
            tenant_id=tenant_id,
            agent_kit_id=kit.id if kit else None,
            source="robot",
        )
        db.add(session)
        db.commit()
        db.refresh(session)

    # Process text input (or STT from audio — placeholder for now)
    message = body.text or "[Audio input — STT not yet implemented]"

    user_msg, assistant_msg = post_user_message(
        db, session=session, user_id=user_id, content=message,
    )

    # Derive emotion from the response itself (not global presence state)
    response_text = (assistant_msg.content or "") if assistant_msg else ""
    response_lower = response_text.lower()
    emotion = "calm"
    if any(w in response_lower for w in ["great", "awesome", "excellent", "happy", "love"]):
        emotion = "happy"
    elif any(w in response_lower for w in ["sorry", "unfortunately", "error", "failed", "issue"]):
        emotion = "empathetic"
    elif any(w in response_lower for w in ["interesting", "curious", "let me check", "looking"]):
        emotion = "focused"
    motion_hint = "speaking" if response_text else "idle"

    return {
        "text": assistant_msg.content if assistant_msg else "",
        "emotion": emotion,
        "motion_hint": motion_hint,
        "session_id": str(session.id),
    }


@router.post("/vision/analyze")
def vision_analyze(
    body: VisionAnalyzeRequest,
    x_device_token: Optional[str] = Header(None, alias="X-Device-Token"),
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Analyze an image from a camera/robot."""
    tenant_id, _user_id = _resolve_tenant(x_device_token, db, current_user)

    try:
        from app.services.knowledge import create_observation
        create_observation(
            db, tenant_id,
            observation_text=f"Camera frame captured. Context: {body.context[:200]}",
            observation_type="vision",
            source_type="camera",
            source_channel="camera",
        )
        db.commit()
    except Exception:
        logger.exception("Failed to store vision observation")

    return {
        "description": f"Image received ({len(body.image_b64)} bytes b64). Vision analysis pending — local vision model not yet configured.",
        "context": body.context,
    }


@router.post("/ambient/ingest")
def ambient_ingest(
    body: AmbientIngestRequest,
    x_device_token: Optional[str] = Header(None, alias="X-Device-Token"),
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Ingest ambient audio for knowledge extraction."""
    tenant_id, _user_id = _resolve_tenant(x_device_token, db, current_user)

    try:
        from app.services.knowledge import create_observation
        create_observation(
            db, tenant_id,
            observation_text=f"Ambient audio capture ({body.duration_seconds}s from {body.source}). STT pending.",
            observation_type="ambient",
            source_type="ambient_audio",
            source_channel="microphone",
        )
        db.commit()
    except Exception:
        logger.exception("Failed to store ambient observation")

    return {
        "status": "ingested",
        "duration_seconds": body.duration_seconds,
        "source": body.source,
        "message": "Audio captured. STT processing pending — local whisper model not yet configured.",
    }
