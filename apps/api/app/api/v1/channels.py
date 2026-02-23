"""WhatsApp channel management endpoints via OpenClaw gateway RPC."""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional, List

from app.api import deps
from app.models.user import User
from app.services.orchestration.skill_router import SkillRouter

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Request / Response Models ────────────────────────────────────────

class WhatsAppEnableRequest(BaseModel):
    dm_policy: str = "allowlist"
    allow_from: List[str] = []
    account_id: str = "default"


class WhatsAppSendRequest(BaseModel):
    to: str
    message: str
    account_id: str = "default"


class WhatsAppPairRequest(BaseModel):
    force: bool = False
    account_id: str = "default"


class WhatsAppLogoutRequest(BaseModel):
    account_id: str = "default"


# ── Helpers ──────────────────────────────────────────────────────────

def _gateway(db: Session, user: User, method: str, params: dict = None, timeout: int = 30):
    """Call gateway RPC and raise HTTPException on error."""
    router_svc = SkillRouter(db=db, tenant_id=user.tenant_id)
    result = router_svc.call_gateway_method(method, params or {}, timeout_seconds=timeout)
    if result.get("status") == "error":
        raise HTTPException(status_code=502, detail=result.get("error", "Gateway error"))
    return result.get("data", {})


# ── Endpoints ────────────────────────────────────────────────────────

@router.post("/whatsapp/enable")
def enable_whatsapp(
    request: WhatsAppEnableRequest,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Enable the WhatsApp channel on the tenant's OpenClaw instance."""
    config_patch = {
        "channels": {
            "whatsapp": {
                "enabled": True,
                "dmPolicy": request.dm_policy,
                "allowFrom": request.allow_from,
                "accounts": {
                    request.account_id: {"enabled": True},
                },
            },
        },
    }
    data = _gateway(db, current_user, "config.patch", {"raw": json.dumps(config_patch)})
    return {"status": "enabled", "data": data}


@router.post("/whatsapp/disable")
def disable_whatsapp(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Disable the WhatsApp channel."""
    config_patch = {"channels": {"whatsapp": {"enabled": False}}}
    data = _gateway(db, current_user, "config.patch", {"raw": json.dumps(config_patch)})
    return {"status": "disabled", "data": data}


@router.get("/whatsapp/status")
def whatsapp_status(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Get WhatsApp channel connection status."""
    data = _gateway(db, current_user, "channels.status", {})
    # Extract whatsapp-specific status from the response
    wa_accounts = data.get("channelAccounts", {}).get("whatsapp", [])
    if not wa_accounts:
        return {"enabled": False, "linked": False, "connected": False, "accounts": []}

    return {
        "enabled": True,
        "accounts": wa_accounts,
        "linked": any(a.get("linked") for a in wa_accounts),
        "connected": any(a.get("connected") for a in wa_accounts),
    }


@router.post("/whatsapp/pair")
def start_pairing(
    request: WhatsAppPairRequest,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Start WhatsApp QR pairing. Returns a QR data URL for scanning."""
    data = _gateway(
        db, current_user, "web.login.start",
        {"accountId": request.account_id, "force": request.force},
        timeout=30,
    )
    return {
        "qr_data_url": data.get("qrDataUrl"),
        "message": data.get("message", "Scan the QR code with WhatsApp"),
    }


@router.get("/whatsapp/pair/status")
def pairing_status(
    account_id: str = Query("default"),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Poll for pairing completion. Returns connected status."""
    data = _gateway(
        db, current_user, "web.login.wait",
        {"timeoutMs": 5000, "accountId": account_id},
        timeout=10,
    )
    return {
        "connected": data.get("connected", False),
        "message": data.get("message", ""),
    }


@router.post("/whatsapp/logout")
def logout_whatsapp(
    request: WhatsAppLogoutRequest,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Logout/unlink WhatsApp account."""
    data = _gateway(
        db, current_user, "channels.logout",
        {"channel": "whatsapp", "accountId": request.account_id},
    )
    return {"status": "logged_out", "data": data}


@router.post("/whatsapp/send")
def send_whatsapp(
    request: WhatsAppSendRequest,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Send a WhatsApp message through the channel."""
    data = _gateway(
        db, current_user, "chat.send",
        {"message": request.message, "sessionKey": "main"},
    )
    return {"status": "sent", "data": data}
