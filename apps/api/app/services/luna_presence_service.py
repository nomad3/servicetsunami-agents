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

VALID_STATES = {"idle", "listening", "thinking", "responding", "focused", "alert", "sleep", "handoff", "private_mode", "error", "happy"}
VALID_MOODS = {"calm", "warm", "playful", "serious", "empathetic", "neutral"}
VALID_PRIVACY = {"open", "passive", "muted", "camera_off", "mic_off", "private"}
VALID_TOOL_STATUS = {"idle", "running", "waiting", "error"}

# States that are "active" — idle transitions should only fire if the
# current owner session matches, to avoid clobbering concurrent work.
_ACTIVE_STATES = {"listening", "thinking", "responding", "focused"}

# Staleness: if the last real update was more than this many seconds ago
# and state is still active, force back to idle.
_STALENESS_SECONDS = 120

# Shell liveness: shells that haven't heartbeated in this many seconds
# are pruned from connected_shells.
_SHELL_TTL_SECONDS = 30


def _default_presence() -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "state": "idle",
        "mood": "calm",
        "privacy": "open",
        "active_shell": None,
        "connected_shells": [],
        "shell_capabilities": {},  # {shell_name: {cap: bool, ...}}
        "shell_heartbeats": {},  # {shell_name: iso_timestamp}
        "tool_status": "idle",
        "attention_target": None,
        "session_id": None,
        "updated_at": now,  # last real state change
    }


def _prune_dead_shells(p: dict) -> None:
    """Remove shells that haven't heartbeated within TTL. Caller must hold _lock."""
    heartbeats = p.get("shell_heartbeats", {})
    if not heartbeats:
        return
    now = datetime.now(timezone.utc)
    dead = []
    for shell_name, ts in heartbeats.items():
        try:
            last = datetime.fromisoformat(ts)
            if (now - last).total_seconds() > _SHELL_TTL_SECONDS:
                dead.append(shell_name)
        except (ValueError, TypeError):
            dead.append(shell_name)

    shells = p["connected_shells"]
    caps = p.get("shell_capabilities", {})
    for name in dead:
        if name in shells:
            shells.remove(name)
        caps.pop(name, None)
        heartbeats.pop(name, None)
        if p.get("active_shell") == name:
            p["active_shell"] = shells[0] if shells else None


def get_presence(tenant_id) -> dict:
    """Return current presence snapshot. Applies staleness check + shell pruning."""
    tid = str(tenant_id)
    with _lock:
        if tid not in _presence_store:
            _presence_store[tid] = _default_presence()
        # Prune dead shells before returning
        _prune_dead_shells(_presence_store[tid])
        snap = dict(_presence_store[tid])
        # Copy mutable fields to avoid shared references
        snap["connected_shells"] = list(snap.get("connected_shells", []))

    # Staleness: if last real update is old and state is active, force idle
    # Handoff clears after 30s. After 30 min of idle, transition to sleep.
    updated_at = snap.get("updated_at")
    if updated_at:
        try:
            last = datetime.fromisoformat(updated_at)
            age = (datetime.now(timezone.utc) - last).total_seconds()
            if age > 1800 and snap["state"] == "idle":
                snap["state"] = "sleep"
            elif age > _STALENESS_SECONDS and snap["state"] in _ACTIVE_STATES:
                snap["state"] = "idle"
                snap["tool_status"] = "idle"
            elif age > 30 and snap["state"] == "handoff":
                snap["state"] = "idle"
        except (ValueError, TypeError):
            pass

    # Don't expose internal tracking fields
    snap.pop("shell_heartbeats", None)
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

        changed = False
        if state and state in VALID_STATES:
            p["state"] = state
            changed = True
        if mood and mood in VALID_MOODS:
            p["mood"] = mood
            changed = True
        if privacy and privacy in VALID_PRIVACY:
            p["privacy"] = privacy
            changed = True
        if active_shell is not None:
            p["active_shell"] = active_shell
            # Record heartbeat for this shell
            p.setdefault("shell_heartbeats", {})[active_shell] = now
        if tool_status and tool_status in VALID_TOOL_STATUS:
            p["tool_status"] = tool_status
            changed = True
        if attention_target is not None:
            p["attention_target"] = attention_target
        if session_id is not None:
            p["session_id"] = session_id
        # Only refresh updated_at on real state changes, not heartbeats
        if changed:
            p["updated_at"] = now
        return dict(p)


def register_shell(tenant_id, shell_name: str, capabilities: Optional[dict] = None) -> dict:
    tid = str(tenant_id)
    now = datetime.now(timezone.utc).isoformat()
    with _lock:
        if tid not in _presence_store:
            _presence_store[tid] = _default_presence()
        p = _presence_store[tid]
        # Prune dead shells before evaluating handoff
        _prune_dead_shells(p)
        # Handoff: switching from a different active shell
        old_shell = p.get("active_shell")
        if old_shell and old_shell != shell_name:
            p["state"] = "handoff"
            p["updated_at"] = now
        shells = p["connected_shells"]
        if shell_name not in shells:
            shells.append(shell_name)
        p["active_shell"] = shell_name
        # Record heartbeat
        p.setdefault("shell_heartbeats", {})[shell_name] = now
        # Store capabilities for this shell
        if capabilities:
            caps = p.setdefault("shell_capabilities", {})
            caps[shell_name] = capabilities
        snap = dict(p)
        snap["connected_shells"] = list(shells)
        snap["shell_capabilities"] = dict(snap.get("shell_capabilities", {}))
        snap.pop("shell_heartbeats", None)
        return snap


def deregister_shell(tenant_id, shell_name: str) -> dict:
    tid = str(tenant_id)
    now = datetime.now(timezone.utc).isoformat()
    with _lock:
        if tid not in _presence_store:
            _presence_store[tid] = _default_presence()
        p = _presence_store[tid]
        shells = p["connected_shells"]
        if shell_name in shells:
            shells.remove(shell_name)
        if p["active_shell"] == shell_name:
            p["active_shell"] = shells[0] if shells else None
        # Remove capabilities and heartbeat for this shell
        p.get("shell_capabilities", {}).pop(shell_name, None)
        p.get("shell_heartbeats", {}).pop(shell_name, None)
        p["updated_at"] = now
        snap = dict(p)
        snap["connected_shells"] = list(shells)
        snap["shell_capabilities"] = dict(snap.get("shell_capabilities", {}))
        snap.pop("shell_heartbeats", None)
        return snap
