"""Device registry and robot interaction API."""
import hashlib
import logging
import secrets
from datetime import datetime
from typing import List, Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user, get_db
from app.models.device_registry import DeviceRegistry
from app.models.user import User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/devices", tags=["devices"])


def get_device_by_token(
    x_device_token: str = Header(..., alias="X-Device-Token"),
    db: Session = Depends(get_db),
) -> DeviceRegistry:
    """Authenticate a device by its token. Used for heartbeat and device-originated requests."""
    token_hash = hashlib.sha256(x_device_token.encode()).hexdigest()
    device = db.query(DeviceRegistry).filter(
        DeviceRegistry.device_token_hash == token_hash,
    ).first()
    if not device:
        raise HTTPException(status_code=401, detail="Invalid device token")
    return device


class DeviceRegisterRequest(BaseModel):
    device_name: str
    device_type: Literal["camera", "robot", "necklace", "glasses", "sensor"]
    capabilities: List[str] = []
    config: dict = {}


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
    device: DeviceRegistry = Depends(get_device_by_token),
):
    if device.device_id != device_id:
        raise HTTPException(status_code=403, detail="Token does not match device")
    device.status = "online"
    device.last_heartbeat = datetime.utcnow()
    db.commit()
    return {"status": "ok", "device_id": device_id}
