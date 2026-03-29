"""Device registry and robot interaction API."""
import hashlib
import logging
import secrets
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import List

from app.api.deps import get_current_active_user, get_db
from app.models.device_registry import DeviceRegistry
from app.models.user import User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/devices", tags=["devices"])


class DeviceRegisterRequest(BaseModel):
    device_name: str
    device_type: str  # camera, robot, necklace, glasses, sensor
    capabilities: List[str] = []
    config: dict = {}


class DeviceCommandRequest(BaseModel):
    command: str  # e.g. "capture_frame", "play_audio", "set_led"
    payload: dict = {}


@router.get("/")
def list_devices(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    devices = db.query(DeviceRegistry).filter(
        DeviceRegistry.tenant_id == current_user.tenant_id,
    ).order_by(DeviceRegistry.created_at.desc()).all()
    return [
        {
            "id": str(d.id),
            "device_id": d.device_id,
            "device_name": d.device_name,
            "device_type": d.device_type,
            "status": d.status,
            "capabilities": d.capabilities or [],
            "last_heartbeat": d.last_heartbeat.isoformat() if d.last_heartbeat else None,
            "created_at": d.created_at.isoformat() if d.created_at else None,
        }
        for d in devices
    ]


@router.post("/")
def register_device(
    body: DeviceRegisterRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    device_id = f"{current_user.tenant_id}-{body.device_type}-{secrets.token_hex(4)}"

    device = DeviceRegistry(
        tenant_id=current_user.tenant_id,
        device_id=device_id,
        device_name=body.device_name,
        device_type=body.device_type,
        status="offline",
        device_token_hash=token_hash,
        capabilities=body.capabilities,
        config=body.config,
    )
    db.add(device)
    db.commit()
    db.refresh(device)

    return {
        "id": str(device.id),
        "device_id": device.device_id,
        "device_token": token,  # Only returned once!
        "message": "Save this token — it won't be shown again.",
    }


@router.delete("/{device_id}")
def remove_device(
    device_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    device = db.query(DeviceRegistry).filter(
        DeviceRegistry.device_id == device_id,
        DeviceRegistry.tenant_id == current_user.tenant_id,
    ).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    db.delete(device)
    db.commit()
    return {"status": "removed", "device_id": device_id}


@router.post("/{device_id}/heartbeat")
def device_heartbeat(
    device_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    device = db.query(DeviceRegistry).filter(
        DeviceRegistry.device_id == device_id,
        DeviceRegistry.tenant_id == current_user.tenant_id,
    ).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    device.status = "online"
    device.last_heartbeat = datetime.utcnow()
    db.commit()
    return {"status": "ok", "device_id": device_id}
