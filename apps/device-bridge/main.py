import asyncio
import base64
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Dict, Optional
from urllib.parse import quote, urlparse, urlunparse

import httpx
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaPlayer
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator

# Configuration
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LUNA_API_URL = os.getenv("LUNA_API_URL", "http://api:8000/api/v1")
DEVICE_BRIDGE_TOKEN = os.getenv("DEVICE_BRIDGE_TOKEN", "")
DEVICE_ID = os.getenv("DEVICE_ID", f"bridge-{uuid.uuid4().hex[:8]}")
BRIDGE_NAME = os.getenv("BRIDGE_NAME", "Luna Device Bridge")

# CORS allowlist — comma-separated origins (e.g. "https://agentprovision.com,http://localhost:3000")
BRIDGE_CORS_ORIGINS = [
    o.strip() for o in os.getenv("BRIDGE_CORS_ORIGINS", "http://localhost:3000").split(",") if o.strip()
]

logging.basicConfig(level=LOG_LEVEL)
logger = logging.getLogger("device-bridge")

app = FastAPI(title="Luna Device Bridge")

app.add_middleware(
    CORSMiddleware,
    allow_origins=BRIDGE_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type", "X-Bridge-Token"],
)


def verify_bridge_token(
    authorization: Optional[str] = Header(None),
    x_bridge_token: Optional[str] = Header(None, alias="X-Bridge-Token"),
) -> None:
    """Reject requests without the configured bridge token.

    Accepts either an `Authorization: Bearer <token>` header or an
    `X-Bridge-Token: <token>` header. Rejects if DEVICE_BRIDGE_TOKEN is
    unset so a misconfigured bridge cannot be reached.
    """
    if not DEVICE_BRIDGE_TOKEN:
        raise HTTPException(status_code=503, detail="Bridge not configured (DEVICE_BRIDGE_TOKEN unset)")
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(None, 1)[1].strip()
    elif x_bridge_token:
        token = x_bridge_token.strip()
    if token != DEVICE_BRIDGE_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid bridge token")


# Global State
pcs = set()
cameras: Dict[str, Dict] = {}  # device_id -> {config, status}


def _build_authenticated_rtsp(rtsp_url: str, username: Optional[str], password: Optional[str]) -> str:
    """Safely embed RTSP credentials, URL-encoding each component.

    Raises HTTPException if the scheme is not rtsp/rtsps.
    """
    parsed = urlparse(rtsp_url)
    if parsed.scheme not in ("rtsp", "rtsps"):
        raise HTTPException(
            status_code=422,
            detail=f"Only rtsp/rtsps URLs are allowed (got: {parsed.scheme or 'none'})",
        )
    if not parsed.hostname:
        raise HTTPException(status_code=422, detail="RTSP URL must include a host")
    if username and password:
        userinfo = f"{quote(username, safe='')}:{quote(password, safe='')}@"
        # Reconstruct netloc without preserving any existing credentials in the URL.
        host = parsed.hostname
        port = f":{parsed.port}" if parsed.port else ""
        new_netloc = f"{userinfo}{host}{port}"
        parsed = parsed._replace(netloc=new_netloc)
    return urlunparse(parsed)


class ConnectRequest(BaseModel):
    device_id: str
    sdp: str
    type: str


class CameraConfig(BaseModel):
    device_id: str
    name: str
    rtsp_url: str
    username: Optional[str] = None
    password: Optional[str] = None

    @field_validator("rtsp_url")
    @classmethod
    def _scheme(cls, v: str) -> str:
        scheme = urlparse(v).scheme
        if scheme not in ("rtsp", "rtsps"):
            raise ValueError(f"rtsp_url must use rtsp or rtsps scheme (got: {scheme or 'none'})")
        return v


@app.on_event("startup")
async def startup_event():
    logger.info("Starting Luna Device Bridge: %s", DEVICE_ID)
    if not DEVICE_BRIDGE_TOKEN:
        logger.warning("DEVICE_BRIDGE_TOKEN is not set — all requests will be rejected")
    asyncio.create_task(heartbeat_loop())


async def heartbeat_loop():
    """Register and maintain connection with Luna API."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        while True:
            try:
                url = f"{LUNA_API_URL}/devices/{DEVICE_ID}/heartbeat"
                headers = {"X-Device-Token": DEVICE_BRIDGE_TOKEN} if DEVICE_BRIDGE_TOKEN else {}
                resp = await client.post(url, headers=headers)
                if resp.status_code == 404:
                    logger.info("Bridge not registered, awaiting manual registration via UI")
                logger.debug("Heartbeat sent: %s", resp.status_code)
            except Exception as e:
                logger.error("Heartbeat failed: %s", e)
            await asyncio.sleep(30)


@app.post("/cameras", dependencies=[Depends(verify_bridge_token)])
async def add_camera(config: CameraConfig):
    """Add a new EZVIZ or RTSP camera to the bridge."""
    cameras[config.device_id] = {"config": config, "status": "idle"}
    logger.info("Camera added: %s (%s)", config.name, config.device_id)
    return {"status": "added", "device_id": config.device_id}


@app.post("/cameras/{device_id}/snapshot", dependencies=[Depends(verify_bridge_token)])
async def capture_snapshot(device_id: str):
    """Capture a single frame from the camera's RTSP stream."""
    if device_id not in cameras:
        raise HTTPException(status_code=404, detail="Camera not found")

    config = cameras[device_id]["config"]
    rtsp_url = _build_authenticated_rtsp(config.rtsp_url, config.username, config.password)

    try:
        import cv2  # type: ignore
    except ImportError:
        raise HTTPException(
            status_code=501,
            detail="cv2 (opencv-python) not installed on bridge — snapshots unsupported",
        )

    cap = cv2.VideoCapture(rtsp_url)
    try:
        if not cap.isOpened():
            raise HTTPException(status_code=502, detail="Failed to open RTSP stream")
        success, frame = cap.read()
        if not success or frame is None:
            raise HTTPException(status_code=502, detail="Failed to read frame from RTSP stream")
        ok, buffer = cv2.imencode(".jpg", frame)
        if not ok:
            raise HTTPException(status_code=500, detail="Failed to encode frame as JPEG")
        img_b64 = base64.b64encode(buffer).decode("utf-8")
    finally:
        cap.release()

    return {
        "image_b64": img_b64,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "device_id": device_id,
    }


@app.post("/bridge/connect", dependencies=[Depends(verify_bridge_token)])
async def connect(request: ConnectRequest):
    """Establish WebRTC connection for a camera stream."""
    if request.device_id not in cameras:
        raise HTTPException(status_code=404, detail="Camera not found on this bridge")

    config = cameras[request.device_id]["config"]
    offer = RTCSessionDescription(sdp=request.sdp, type=request.type)
    pc = RTCPeerConnection()
    pcs.add(pc)

    @pc.on("connectionstatechange")
    async def on_connectionstatechange():
        logger.info("Connection state is %s", pc.connectionState)
        if pc.connectionState in ("failed", "closed"):
            await pc.close()
            pcs.discard(pc)

    player = None
    try:
        rtsp_url = _build_authenticated_rtsp(config.rtsp_url, config.username, config.password)
        player = MediaPlayer(rtsp_url)
        if player.video:
            pc.addTrack(player.video)
        await pc.setRemoteDescription(offer)
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
        return {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}
    except HTTPException:
        await pc.close()
        pcs.discard(pc)
        raise
    except Exception as e:
        logger.error("Failed to connect to RTSP: %s", e)
        if player is not None and getattr(player, "video", None):
            try:
                player.video.stop()
            except Exception:
                pass
        await pc.close()
        pcs.discard(pc)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/status")
async def status():
    """Public status endpoint — does not leak camera config, useful for readiness checks."""
    return {
        "bridge_id": DEVICE_ID,
        "bridge_name": BRIDGE_NAME,
        "camera_count": len(cameras),
        "active_connections": len(pcs),
        "configured": bool(DEVICE_BRIDGE_TOKEN),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8088)
