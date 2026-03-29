from pydantic import BaseModel
from typing import Optional, List, Dict
from datetime import datetime


class LunaPresenceSnapshot(BaseModel):
    state: str = "idle"  # idle, listening, thinking, responding, focused, alert, sleep, handoff, private_mode, error
    mood: str = "calm"  # calm, warm, playful, serious, empathetic, neutral
    privacy: str = "open"  # open, passive, muted, camera_off, mic_off, private
    active_shell: Optional[str] = None  # whatsapp, web, desktop, mobile, necklace, glasses, camera
    connected_shells: List[str] = []
    tool_status: str = "idle"  # idle, running, waiting, error
    attention_target: Optional[str] = None
    timestamp: Optional[str] = None
    session_id: Optional[str] = None


class LunaPresenceUpdate(BaseModel):
    state: Optional[str] = None
    mood: Optional[str] = None
    privacy: Optional[str] = None
    active_shell: Optional[str] = None
    tool_status: Optional[str] = None
    attention_target: Optional[str] = None
    session_id: Optional[str] = None


class ShellRegisterRequest(BaseModel):
    shell: str
    capabilities: Optional[Dict[str, bool]] = None


class ShellDeregisterRequest(BaseModel):
    shell: str
