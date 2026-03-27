"""Luna Presence API — real-time state, mood, and shell tracking."""
import asyncio
import json
import logging
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.api.v1.deps import get_current_user
from app.services import luna_presence_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/presence", tags=["presence"])


@router.get("/")
def get_presence(current_user=Depends(get_current_user)):
    return luna_presence_service.get_presence(current_user.tenant_id)


@router.put("/")
def update_presence(
    state: str = None,
    mood: str = None,
    privacy: str = None,
    active_shell: str = None,
    tool_status: str = None,
    current_user=Depends(get_current_user),
):
    return luna_presence_service.update_state(
        current_user.tenant_id,
        state=state, mood=mood, privacy=privacy,
        active_shell=active_shell, tool_status=tool_status,
    )


@router.get("/stream")
async def presence_stream(request: Request, current_user=Depends(get_current_user)):
    """SSE endpoint for real-time presence updates. Polls every 2s."""
    tenant_id = str(current_user.tenant_id)
    last_timestamp = None

    async def event_generator():
        nonlocal last_timestamp
        while True:
            if await request.is_disconnected():
                break
            snap = luna_presence_service.get_presence(tenant_id)
            ts = snap.get("timestamp")
            if ts != last_timestamp:
                last_timestamp = ts
                yield f"data: {json.dumps(snap)}\n\n"
            await asyncio.sleep(2)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/shell/register")
def register_shell(shell_name: str, current_user=Depends(get_current_user)):
    return luna_presence_service.register_shell(current_user.tenant_id, shell_name)


@router.post("/shell/deregister")
def deregister_shell(shell_name: str, current_user=Depends(get_current_user)):
    return luna_presence_service.deregister_shell(current_user.tenant_id, shell_name)
