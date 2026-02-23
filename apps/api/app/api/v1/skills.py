from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
import uuid

from app.api import deps
from app.models.user import User
from app.services.orchestration.skill_router import SkillRouter

router = APIRouter()


class SkillExecuteRequest(BaseModel):
    skill_name: str
    payload: dict
    task_id: Optional[uuid.UUID] = None
    agent_id: Optional[uuid.UUID] = None


@router.post("/execute")
def execute_skill(
    request: SkillExecuteRequest,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Execute a skill through the tenant's OpenClaw instance."""
    import logging
    logger = logging.getLogger(__name__)
    skill_router = SkillRouter(db=db, tenant_id=current_user.tenant_id)
    result = skill_router.execute_skill(
        skill_name=request.skill_name,
        payload=request.payload,
        task_id=request.task_id,
        agent_id=request.agent_id,
    )
    if result.get("status") == "error":
        error_detail = result.get("error", "Unknown error")
        logger.error("Skill execution failed for '%s': %s", request.skill_name, error_detail)
        raise HTTPException(status_code=502, detail=error_detail)
    return result


@router.get("/health")
def skill_health(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Check health of tenant's OpenClaw instance."""
    skill_router = SkillRouter(db=db, tenant_id=current_user.tenant_id)
    return skill_router.health_check()


@router.get("/diagnose")
def skill_diagnose(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Step-by-step diagnostic of OpenClaw WebSocket connection."""
    import asyncio
    import json as _json
    import logging
    import time as _time

    from app.core.config import settings
    from app.models.tenant_instance import TenantInstance
    from app.services.orchestration.skill_router import SkillRouter

    logger = logging.getLogger(__name__)
    tenant_id = current_user.tenant_id
    steps = {}

    # Step 1: Resolve instance
    instance = (
        db.query(TenantInstance)
        .filter(
            TenantInstance.tenant_id == tenant_id,
            TenantInstance.instance_type == "openclaw",
            TenantInstance.status == "running",
        )
        .first()
    )
    if not instance:
        return {"steps": {"resolve_instance": {"ok": False, "error": "No running instance"}}}

    steps["resolve_instance"] = {
        "ok": True,
        "instance_id": str(instance.id),
        "internal_url": instance.internal_url,
    }

    # Step 2: Check config
    token = settings.OPENCLAW_GATEWAY_TOKEN
    device_id = settings.OPENCLAW_DEVICE_ID
    private_key = settings.OPENCLAW_DEVICE_PRIVATE_KEY
    public_key = settings.OPENCLAW_DEVICE_PUBLIC_KEY
    steps["config_check"] = {
        "ok": bool(token and device_id and private_key and public_key),
        "token_length": len(token) if token else 0,
        "device_id_prefix": device_id[:12] + "..." if device_id and len(device_id) > 12 else "(empty)",
        "has_private_key": bool(private_key),
        "has_public_key": bool(public_key),
    }

    ws_url = instance.internal_url.replace("http://", "ws://").replace("https://", "wss://")
    steps["ws_url"] = ws_url

    # Step 3: WebSocket connect + challenge + Ed25519 auth
    async def _diagnose_ws():
        import websockets

        diag = {}

        try:
            async with websockets.connect(ws_url, open_timeout=10) as ws:
                diag["ws_connect"] = {"ok": True}

                # Challenge
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=10)
                    challenge = _json.loads(raw)
                    diag["challenge"] = {
                        "ok": True,
                        "event": challenge.get("event"),
                        "has_nonce": bool(challenge.get("payload", {}).get("nonce")),
                    }
                    nonce = challenge.get("payload", {}).get("nonce", "")
                except Exception as e:
                    diag["challenge"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
                    return diag

                # Auth with Ed25519 device signature
                try:
                    role = "operator"
                    scopes = ["operator.admin", "operator.approvals", "operator.pairing"]
                    signed_at_ms = int(_time.time() * 1000)

                    signature = SkillRouter._sign_device_payload(
                        private_key_pem=private_key,
                        device_id=device_id,
                        client_id="gateway-client",
                        client_mode="backend",
                        role=role,
                        scopes=scopes,
                        signed_at_ms=signed_at_ms,
                        token=token,
                        nonce=nonce,
                    )

                    connect_req = {
                        "type": "req",
                        "id": f"diag-{uuid.uuid4().hex[:8]}",
                        "method": "connect",
                        "params": {
                            "minProtocol": 3,
                            "maxProtocol": 3,
                            "client": {
                                "id": "gateway-client",
                                "version": "1.0.0",
                                "platform": "linux",
                                "mode": "backend",
                            },
                            "role": role,
                            "scopes": scopes,
                            "auth": {"token": token},
                            "device": {
                                "id": device_id,
                                "publicKey": public_key,
                                "signature": signature,
                                "signedAt": signed_at_ms,
                                "nonce": nonce,
                            },
                        },
                    }
                    await ws.send(_json.dumps(connect_req))
                    hello_raw = await asyncio.wait_for(ws.recv(), timeout=10)
                    hello = _json.loads(hello_raw)

                    features = hello.get("payload", {}).get("features", {})
                    diag["auth"] = {
                        "ok": hello.get("ok", False),
                        "response_type": hello.get("type"),
                        "error": hello.get("error") if not hello.get("ok") else None,
                        "available_methods": features.get("methods", []),
                        "available_events": features.get("events", []),
                    }
                except Exception as e:
                    diag["auth"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}

        except Exception as e:
            diag["ws_connect"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}

        return diag

    try:
        ws_diag = asyncio.run(_diagnose_ws())
        steps["websocket"] = ws_diag
    except Exception as e:
        steps["websocket"] = {"error": f"{type(e).__name__}: {e}"}

    return {"steps": steps}
