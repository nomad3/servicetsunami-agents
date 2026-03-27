"""Luna Presence Service — in-memory presence state per tenant.

Presence is ephemeral (not persisted to DB). Tracks Luna's current
state, mood, privacy, active shell, and connected shells.
"""
import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, Optional, List
import threading

logger = logging.getLogger(__name__)

# In-memory presence state per tenant
_presence_store: Dict[str, dict] = {}
_lock = threading.Lock()

VALID_STATES = {"idle", "listening", "thinking", "responding", "focused", "alert", "sleep", "handoff", "private_mode", "error"}
VALID_MOODS = {"calm", "warm", "playful", "serious", "empathetic", "neutral"}
VALID_PRIVACY = {"open", "passive", "muted", "camera_off", "mic_off", "private"}
VALID_TOOL_STATUS = {"idle", "running", "waiting", "error"}


def _default_presence() -> dict:
    return {
        "state": "idle",
        "mood": "calm",
        "privacy": "open",
        "active_shell": None,
        "connected_shells": [],
        "tool_status": "idle",
        "attention_target": None,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "session_id": None,
    }


def get_presence(tenant_id) -> dict:
    tid = str(tenant_id)
    with _lock:
        if tid not in _presence_store:
            _presence_store[tid] = _default_presence()
        snap = dict(_presence_store[tid])
    snap["timestamp"] = datetime.now(timezone.utc).isoformat()
    return snap


def update_state(tenant_id, state: Optional[str] = None, mood: Optional[str] = None,
                 privacy: Optional[str] = None, active_shell: Optional[str] = None,
                 tool_status: Optional[str] = None, attention_target: Optional[str] = None,
                 session_id: Optional[str] = None) -> dict:
    tid = str(tenant_id)
    with _lock:
        if tid not in _presence_store:
            _presence_store[tid] = _default_presence()
        p = _presence_store[tid]
        if state and state in VALID_STATES:
            p["state"] = state
        if mood and mood in VALID_MOODS:
            p["mood"] = mood
        if privacy and privacy in VALID_PRIVACY:
            p["privacy"] = privacy
        if active_shell is not None:
            p["active_shell"] = active_shell
        if tool_status and tool_status in VALID_TOOL_STATUS:
            p["tool_status"] = tool_status
        if attention_target is not None:
            p["attention_target"] = attention_target
        if session_id is not None:
            p["session_id"] = session_id
        p["timestamp"] = datetime.now(timezone.utc).isoformat()
        return dict(p)


def register_shell(tenant_id, shell_name: str) -> dict:
    tid = str(tenant_id)
    with _lock:
        if tid not in _presence_store:
            _presence_store[tid] = _default_presence()
        shells = _presence_store[tid]["connected_shells"]
        if shell_name not in shells:
            shells.append(shell_name)
        _presence_store[tid]["active_shell"] = shell_name
        _presence_store[tid]["timestamp"] = datetime.now(timezone.utc).isoformat()
        return dict(_presence_store[tid])


def deregister_shell(tenant_id, shell_name: str) -> dict:
    tid = str(tenant_id)
    with _lock:
        if tid not in _presence_store:
            _presence_store[tid] = _default_presence()
        shells = _presence_store[tid]["connected_shells"]
        if shell_name in shells:
            shells.remove(shell_name)
        if _presence_store[tid]["active_shell"] == shell_name:
            _presence_store[tid]["active_shell"] = shells[0] if shells else None
        _presence_store[tid]["timestamp"] = datetime.now(timezone.utc).isoformat()
        return dict(_presence_store[tid])
