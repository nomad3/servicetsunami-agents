"""Luna Presence API — real-time state, mood, and shell tracking."""
import logging
from fastapi import APIRouter, Depends

from app.api.deps import get_current_user
from app.schemas.luna_presence import LunaPresenceUpdate, ShellRegisterRequest, ShellDeregisterRequest
from app.services import luna_presence_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/presence", tags=["presence"])


@router.get("/")
def get_presence(current_user=Depends(get_current_user)):
    return luna_presence_service.get_presence(current_user.tenant_id)


@router.put("/")
def update_presence(body: LunaPresenceUpdate, current_user=Depends(get_current_user)):
    return luna_presence_service.update_state(
        current_user.tenant_id,
        state=body.state, mood=body.mood, privacy=body.privacy,
        active_shell=body.active_shell, tool_status=body.tool_status,
        session_id=body.session_id,
    )


@router.post("/shell/register")
def register_shell(body: ShellRegisterRequest, current_user=Depends(get_current_user)):
    return luna_presence_service.register_shell(
        current_user.tenant_id, body.shell, capabilities=body.capabilities,
    )


@router.post("/shell/deregister")
def deregister_shell(body: ShellDeregisterRequest, current_user=Depends(get_current_user)):
    return luna_presence_service.deregister_shell(current_user.tenant_id, body.shell)
