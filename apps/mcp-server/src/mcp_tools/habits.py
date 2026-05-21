"""Habit-observation MCP tools (#297 platform-side cut).

Implements the platform contract from Luna's 2026-05-19 design doc
``docs/plans/2026-05-19-luna-tauri-habit-tracker-design.md`` §3
("Memory & RL Integration"). The Tauri client (separate effort —
vision pipeline, posture/hydration/focus detectors) writes derived
semantic signals through this tool; the platform persists them as
``agent_memory`` rows tagged with the habit name + signal shape.

What this tool is for:

  - The Tauri client's vision pipeline derives a signal (e.g.
    ``posture_score=0.4``, ``bottle_lifted=true``,
    ``low_blink_rate``) — frames stay local; only the semantic
    signal leaves the device.
  - The tool persists each signal as a tenant-scoped agent_memory
    row so the RL feedback loop can learn the optimal nudge timing
    and the agent can converse about "you've been hunched for 45
    minutes" without a separate dashboard.

What this tool is NOT:

  - Not the vision pipeline itself. Frames never reach the server.
  - Not a generic event sink — habit observations are a specific
    shape with a small, controlled set of habit_name + signal_kind
    values so a malicious caller can't pollute agent memory.

The Tauri client must call this with a tenant-scoped JWT (or use
the MCP tenant resolver). Cross-tenant writes are refused by the
underlying api endpoint.
"""
import logging
import os
from typing import Any, Dict, Optional

import httpx
from mcp.server.fastmcp import Context

from src.mcp_app import mcp
from src.mcp_auth import resolve_tenant_id

logger = logging.getLogger(__name__)

API_BASE_URL = os.environ.get("API_BASE_URL", "http://api:8000")
API_INTERNAL_KEY = os.environ.get("MCP_API_KEY", "dev_mcp_key")

# Locked allow-list per Luna's design doc §1 ("Scope Sketch"):
# posture, hydration, focus. Other habits can be added but ONLY by
# updating this list — keeps the agent_memory tag set small and
# auditable, and prevents arbitrary text from becoming a habit tag.
_ALLOWED_HABITS = frozenset({
    "posture", "hydration", "focus",
})

# Signal kinds the vision pipeline emits. Locked the same way.
_ALLOWED_SIGNALS = frozenset({
    "score",         # numeric in [0, 1] — posture_score, focus_score
    "event",         # discrete observation — bottle_lifted, blink_rate_low
    "duration",      # accumulating duration — minutes_at_screen
})


@mcp.tool()
async def log_habit_observation(
    habit_name: str,
    signal_kind: str,
    value: Any,
    source: str = "luna_tauri_client",
    confidence: float = 0.8,
    tenant_id: str = "",
    ctx: Context = None,
) -> dict:
    """Write a habit observation derived by the Tauri vision pipeline.

    Args:
        habit_name: one of ``posture``, ``hydration``, ``focus``
                    (locked per Luna's design §1).
        signal_kind: one of ``score`` / ``event`` / ``duration``.
        value: the derived signal value (float for score, str/bool
               for event, int seconds for duration).
        source: free-form identifier of the producer (default
                ``luna_tauri_client``); helps debug stale clients.
        confidence: the producer's confidence in the observation,
                    [0, 1]. Defaults to 0.8 — adjustable by the
                    client based on lighting, occlusion, etc.
        tenant_id: optional explicit tenant UUID; resolved from MCP
                   context when omitted.

    Returns:
        ``{"status": "success", "memory_id": "<uuid>"}`` on accept.
        ``{"status": "error", "error": "<reason>"}`` on rejection.

    Rejections:
        - habit_name or signal_kind not in the allow-list
        - confidence outside [0, 1]
        - missing tenant_id
        - upstream 4xx/5xx propagated as a clean error message
    """
    if habit_name not in _ALLOWED_HABITS:
        return {
            "status": "error",
            "error": (
                f"habit_name must be one of {sorted(_ALLOWED_HABITS)}, "
                f"got {habit_name!r}"
            ),
        }
    if signal_kind not in _ALLOWED_SIGNALS:
        return {
            "status": "error",
            "error": (
                f"signal_kind must be one of {sorted(_ALLOWED_SIGNALS)}, "
                f"got {signal_kind!r}"
            ),
        }
    if not 0.0 <= float(confidence) <= 1.0:
        return {
            "status": "error",
            "error": f"confidence must be in [0, 1], got {confidence}",
        }

    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"status": "error", "error": "tenant_id required"}

    payload: Dict[str, Any] = {
        "tenant_id": tid,
        "habit_name": habit_name,
        "signal_kind": signal_kind,
        "value": value,
        "source": source,
        "confidence": float(confidence),
    }
    headers = {
        "X-Internal-Key": API_INTERNAL_KEY,
        "X-Tenant-Id": tid,
    }
    url = f"{API_BASE_URL}/api/v1/internal/habits/observations"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
    except httpx.RequestError as exc:
        logger.warning(
            "log_habit_observation: transport error tenant=%s habit=%s err=%s",
            tid, habit_name, exc,
        )
        return {"status": "error", "error": f"transport: {exc}"}

    if resp.status_code == 200:
        body = resp.json()
        return {
            "status": "success",
            "memory_id": body.get("memory_id"),
        }
    if resp.status_code in (400, 401, 403, 404):
        return {
            "status": "error",
            "error": f"upstream {resp.status_code}: {resp.text[:200]}",
        }
    return {
        "status": "error",
        "error": f"upstream {resp.status_code}: {resp.text[:200]}",
    }


__all__ = ["log_habit_observation"]
