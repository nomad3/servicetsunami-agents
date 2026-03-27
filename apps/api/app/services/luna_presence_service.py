"""Luna Presence Service — in-memory presence state per tenant.

Presence is ephemeral (not persisted to DB). Tracks Luna's current
state, mood, privacy, active shell, and connected shells.

State updates are scoped by session_id to prevent concurrent requests
from clobbering each other (e.g. web chat setting idle while WhatsApp
is still thinking).
"""
import logging
from datetime import datetime, timezone
from typing import Dict, Optional
import threading

logger = logging.getLogger(__name__)

_presence_store: Dict[str, dict] = {}
_lock = threading.Lock()

VALID_STATES = {"idle", "listening", "thinking", "responding", "focused", "alert", "sleep", "handoff", "private_mode", "error"}
VALID_MOODS = {"calm", "warm", "playful", "serious", "empathetic", "neutral"}
VALID_PRIVACY = {"open", "passive", "muted", "camera_off", "mic_off", "private"}
VALID_TOOL_STATUS = {"idle", "running", "waiting", "error"}

# States that are "active" — idle transitions should only fire if the
# current owner session matches, to avoid clobbering concurrent work.
_ACTIVE_STATES = {"listening", "thinking", "responding", "focused"}

# Staleness: if the last real update was more than this many seconds ago
# and state is still active, force back to idle.
_STALENESS_SECONDS = 120


def _default_presence() -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "state": "idle",
        "mood": "calm",
        "privacy": "open",
        "active_shell": None,
        "connected_shells": [],
        "tool_status": "idle",
        "attention_target": None,
        "session_id": None,
        "updated_at": now,  # last real state change
    }


def get_presence(tenant_id) -> dict:
    """Return current presence snapshot. Applies staleness check."""
    tid = str(tenant_id)
    with _lock:
        if tid not in _presence_store:
            _presence_store[tid] = _default_presence()
        snap = dict(_presence_store[tid])
        # Copy connected_shells to avoid shared list reference
        snap["connected_shells"] = list(snap.get("connected_shells", []))

    # Staleness: if last real update is old and state is active, force idle
    updated_at = snap.get("updated_at")
    if updated_at and snap["state"] in _ACTIVE_STATES:
        try:
            last = datetime.fromisoformat(updated_at)
            age = (datetime.now(timezone.utc) - last).total_seconds()
            if age > _STALENESS_SECONDS:
                snap["state"] = "idle"
                snap["tool_status"] = "idle"
        except (ValueError, TypeError):
            pass

    snap["timestamp"] = datetime.now(timezone.utc).isoformat()
    return snap


def update_state(tenant_id, state: Optional[str] = None, mood: Optional[str] = None,
                 privacy: Optional[str] = None, active_shell: Optional[str] = None,
                 tool_status: Optional[str] = None, attention_target: Optional[str] = None,
                 session_id: Optional[str] = None) -> dict:
    """Update presence state. Session-scoped to prevent concurrent clobbering.

    When setting 'idle', only applies if the stored session_id matches
    (or no session_id was passed). This prevents one chat setting idle
    while another is still thinking.
    """
    tid = str(tenant_id)
    now = datetime.now(timezone.utc).isoformat()

    with _lock:
        if tid not in _presence_store:
            _presence_store[tid] = _default_presence()
        p = _presence_store[tid]

        # Guard: only allow idle transition if the requesting session owns
        # the current active state, or if no session scoping is in play.
        if state == "idle" and session_id and p.get("session_id"):
            if p["session_id"] != session_id and p["state"] in _ACTIVE_STATES:
                # Another session is still active — don't clobber
                return dict(p)

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
        p["updated_at"] = now
        return dict(p)


def register_shell(tenant_id, shell_name: str) -> dict:
    tid = str(tenant_id)
    now = datetime.now(timezone.utc).isoformat()
    with _lock:
        if tid not in _presence_store:
            _presence_store[tid] = _default_presence()
        shells = _presence_store[tid]["connected_shells"]
        if shell_name not in shells:
            shells.append(shell_name)
        _presence_store[tid]["active_shell"] = shell_name
        _presence_store[tid]["updated_at"] = now
        snap = dict(_presence_store[tid])
        snap["connected_shells"] = list(shells)
        return snap


def deregister_shell(tenant_id, shell_name: str) -> dict:
    tid = str(tenant_id)
    now = datetime.now(timezone.utc).isoformat()
    with _lock:
        if tid not in _presence_store:
            _presence_store[tid] = _default_presence()
        shells = _presence_store[tid]["connected_shells"]
        if shell_name in shells:
            shells.remove(shell_name)
        if _presence_store[tid]["active_shell"] == shell_name:
            _presence_store[tid]["active_shell"] = shells[0] if shells else None
        _presence_store[tid]["updated_at"] = now
        snap = dict(_presence_store[tid])
        snap["connected_shells"] = list(shells)
        return snap
