"""Affect / emotion MCP tools.

Read-only surface for an agent to inspect its own (or another tenant
agent's) PAD state. Closes the Luna-flagged 2026-05-21 gap:
``get_affect_baseline`` lived behind the user-JWT-only
``/api/v1/affect/agents/{id}`` route, so an MCP-resident agent
couldn't independently verify its own baseline.

The corresponding internal endpoint is
``/api/v1/internal/affect/agents/{agent_id}``, authenticated via
``X-Internal-Key`` + ``X-Tenant-Id`` (same threat model as
``/api/v1/internal/embed``). Tenant isolation is enforced by the
underlying agent-lookup guard: a cross-tenant ``agent_id`` returns 404.
"""
import logging
import os
from typing import Optional

import httpx
from mcp.server.fastmcp import Context

from src.mcp_app import mcp
from src.mcp_auth import resolve_tenant_id

logger = logging.getLogger(__name__)

API_BASE_URL = os.environ.get("API_BASE_URL", "http://api:8000")
API_INTERNAL_KEY = os.environ.get("MCP_API_KEY", "dev_mcp_key")


@mcp.tool()
async def get_agent_affect(
    agent_id: str,
    tenant_id: str = "",
    ctx: Context = None,
) -> dict:
    """Return an agent's stable affect baseline + current PAD vector.

    Args:
        agent_id: UUID of the agent to inspect. Cross-tenant access
                  returns "agent not found".
        tenant_id: Tenant UUID. Optional — resolved from MCP context
                   if omitted.

    Returns:
        On success::

            {
                "status": "success",
                "agent_id": "<uuid>",
                "agent_name": "<string>",
                "baseline": {"pleasure":..,"arousal":..,"dominance":..,
                             "label":..,"updated_at":..},
                "current":  same shape or null,
                "has_live_state": <bool>
            }

        On failure::

            {"status": "error", "error": "<message>"}

    The baseline is the agent's stable trait — what the emotion
    engine decays toward. The ``current`` field is the most recent
    non-null ``affect_vector`` from any session this agent owns; null
    when no episode has affect_vector yet.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"status": "error", "error": "tenant_id required"}

    headers = {
        "X-Internal-Key": API_INTERNAL_KEY,
        "X-Tenant-Id": tid,
    }
    url = f"{API_BASE_URL}/api/v1/internal/affect/agents/{agent_id}"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=headers)
    except httpx.RequestError as exc:
        logger.warning(
            "get_agent_affect: HTTP transport error agent=%s err=%s",
            agent_id, exc,
        )
        return {"status": "error", "error": f"transport: {exc}"}

    if resp.status_code == 404:
        return {"status": "error", "error": "agent not found"}
    if resp.status_code == 401:
        return {"status": "error", "error": "invalid internal key"}
    if resp.status_code == 400:
        return {
            "status": "error",
            "error": f"bad request: {resp.text[:200]}",
        }
    if resp.status_code != 200:
        return {
            "status": "error",
            "error": f"upstream {resp.status_code}: {resp.text[:200]}",
        }

    payload = resp.json()
    return {"status": "success", **payload}


__all__ = ["get_agent_affect"]
