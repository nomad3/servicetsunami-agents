"""Value-layer MCP tools — PR 2 of #647.

Same shape as the affect MCP tool (#640): an internal-key
authenticated endpoint on the api side, an MCP @tool wrapper that
the agent layer invokes. Closes the JWT-only-route gap so Luna
can inspect her own value set independently of an operator session.

Reads only — writes stay on the operator JWT path. The Phase 2
``value_proposal`` synthesis mechanism will go through a
purpose-built proposal endpoint, not this read tool.
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
async def get_agent_value_set(
    agent_id: str,
    tenant_id: str = "",
    ctx: Context = None,
) -> dict:
    """Return an agent's current value set (protect / pursue / avoid).

    Args:
        agent_id: UUID of the agent to inspect. Cross-tenant access
                  returns "agent not found".
        tenant_id: Tenant UUID. Optional — resolved from MCP context
                   if omitted.

    Returns:
        On success::

            {
                "status": "success",
                "tenant_id": "<uuid>",
                "agent_id": "<uuid>",
                "protect": [{slug, description, added_at, added_by,
                             evidence_memory_ids}, ...],
                "pursue":  [...],
                "avoid":   [...],
                "version": int,
                "updated_at": "<iso>",
            }

        On failure::

            {"status": "error", "error": "<message>"}

    Use this to: introspect what the agent currently treats as
    protected/pursued/avoided BEFORE deciding whether a proposed
    action will trigger a value-layer block. Pairs with
    consult_with_audit on the server side — the same verdict logic
    the live chat path runs.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"status": "error", "error": "tenant_id required"}

    headers = {
        "X-Internal-Key": API_INTERNAL_KEY,
        "X-Tenant-Id": tid,
    }
    url = (
        f"{API_BASE_URL}/api/v1/internal/values/agents/{agent_id}"
    )

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=headers)
    except httpx.RequestError as exc:
        logger.warning(
            "get_agent_value_set: transport error agent=%s err=%s",
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


__all__ = ["get_agent_value_set"]
