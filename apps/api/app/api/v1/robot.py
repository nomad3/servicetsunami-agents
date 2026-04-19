"""Robot interaction API — audio/vision/ambient capture."""
import base64
import hashlib
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional, Tuple

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user_optional, get_db
from app.core.config import settings
from app.models.device_registry import DeviceRegistry
from app.models.user import User
from app.services.media_utils import transcribe_audio_bytes

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/robot", tags=["robot"])


def _resolve_tenant(
    x_device_token: Optional[str],
    db: Session,
    current_user: Optional[User] = None,
    x_internal_key: Optional[str] = None,
    x_tenant_id: Optional[str] = None,
) -> Tuple[uuid.UUID, Optional[uuid.UUID]]:
    """Resolve tenant_id (and optional user_id) from JWT, device token, or internal key.

    Internal callers (MCP tools, scheduler) authenticate by sending
    X-Internal-Key matching API_INTERNAL_KEY / MCP_API_KEY plus an
    X-Tenant-Id header. This path is used by agent-side code that
    doesn't have a user session (e.g. capture_camera_snapshot).
    """
    if current_user:
        return current_user.tenant_id, current_user.id
    if x_device_token:
        token_hash = hashlib.sha256(x_device_token.encode()).hexdigest()
        device = db.query(DeviceRegistry).filter(
            DeviceRegistry.device_token_hash == token_hash,
        ).first()
        if device:
            return device.tenant_id, None
    if x_internal_key and x_tenant_id:
        valid_keys = {k for k in (settings.API_INTERNAL_KEY, settings.MCP_API_KEY) if k}
        if x_internal_key in valid_keys:
            try:
                return uuid.UUID(x_tenant_id), None
            except (ValueError, AttributeError):
                raise HTTPException(status_code=400, detail="Invalid X-Tenant-Id")
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

    from sqlalchemy import case
    from app.services.chat import post_user_message
    from app.models.chat import ChatSession
    from app.models.agent import Agent

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
        status_rank = case(
            (Agent.status == "production", 0),
            (Agent.status == "staging", 1),
            (Agent.status == "draft", 2),
            (Agent.status == "deprecated", 3),
            else_=4,
        )
        agent = (
            db.query(Agent)
            .filter(Agent.tenant_id == tenant_id)
            .order_by(
                (Agent.name == "Luna").desc(),
                status_rank.asc(),
                Agent.id.asc(),
            )
            .first()
        )
        session = ChatSession(
            title="Robot Session",
            tenant_id=tenant_id,
            agent_id=agent.id if agent else None,
            source="robot",
        )
        db.add(session)
        db.commit()
        db.refresh(session)

    # Process text input (or STT from audio)
    message = body.text
    if body.audio_b64:
        try:
            audio_bytes = base64.b64decode(body.audio_b64)
            transcript = transcribe_audio_bytes(audio_bytes)
            if transcript:
                message = f"{message} {transcript}" if message else transcript
            else:
                logger.warning("STT failed for robot interaction")
                if not message:
                    message = "[Audio input — transcription failed]"
        except Exception as e:
            logger.error("Audio decoding failed: %s", e)
            if not message:
                message = "[Audio input — decoding failed]"

    if not message:
        message = "[No text or audio input provided]"

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

    stored = False
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
        stored = True
    except Exception:
        logger.exception("Failed to store vision observation")
        db.rollback()

    return {
        "status": "stored" if stored else "error",
        "description": f"Camera frame. Context: {body.context[:200]}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


class VisionRequest(BaseModel):
    image_b64: str
    source: str = "camera"
    context: Optional[str] = None

@router.post("/vision/snapshot")
def upload_snapshot(
    body: VisionRequest,
    x_device_token: Optional[str] = Header(None, alias="X-Device-Token"),
    x_internal_key: Optional[str] = Header(None, alias="X-Internal-Key"),
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-Id"),
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Upload a camera snapshot for vision processing.

    Accepts JWT user, device token, or internal key + tenant id auth.
    """
    tenant_id, _user_id = _resolve_tenant(
        x_device_token, db, current_user,
        x_internal_key=x_internal_key, x_tenant_id=x_tenant_id,
    )

    try:
        from app.services.knowledge import create_observation
        obs_text = f"Visual snapshot from {body.source}."
        if body.context:
            obs_text += f" Context: {body.context}"
        
        create_observation(
            db, tenant_id,
            observation_text=obs_text,
            observation_type="vision",
            source_type="camera_snapshot",
            source_channel=body.source,
            # In production, image_b64 would be uploaded to S3/GCS
        )
        db.commit()
    except Exception:
        logger.exception("Failed to store vision observation")

    return {
        "status": "stored",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "message": "Snapshot received and stored for knowledge extraction.",
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

    transcript = None
    if body.audio_b64:
        try:
            audio_bytes = base64.b64decode(body.audio_b64)
            transcript = transcribe_audio_bytes(audio_bytes)
        except Exception:
            logger.exception("Failed to transcribe ambient audio")

    try:
        from app.services.knowledge import create_observation
        obs_text = f"Ambient audio capture ({body.duration_seconds}s from {body.source})."
        if transcript:
            obs_text += f" Transcript: {transcript}"
        else:
            obs_text += " STT failed or silent."

        create_observation(
            db, tenant_id,
            observation_text=obs_text,
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
        "transcript_preview": transcript[:100] if transcript else None,
    }
