"""MCP tools for device management."""
import logging
from typing import Optional

from mcp.server.fastmcp import Context

from src.mcp_app import mcp
from src.mcp_auth import resolve_tenant_id

logger = logging.getLogger(__name__)


@mcp.tool()
async def list_connected_devices(tenant_id: str = "", ctx: Context = None) -> dict:
    """List all registered devices for the tenant."""
    from src.mcp_tools.knowledge import _get_pool

    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"error": "tenant_id required"}
    pool = await _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT device_id, device_name, device_type, status, last_heartbeat "
            "FROM device_registry WHERE tenant_id = $1 "
            "ORDER BY last_heartbeat DESC NULLS LAST",
            tid,
        )
    return {"devices": [dict(r) for r in rows], "count": len(rows)}


@mcp.tool()
async def get_device_status(device_id: str, tenant_id: str = "", ctx: Context = None) -> dict:
    """Get status of a specific device."""
    from src.mcp_tools.knowledge import _get_pool

    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"error": "tenant_id required"}
    pool = await _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM device_registry WHERE device_id = $1 AND tenant_id = $2",
            device_id, tid,
        )
    if not row:
        return {"error": "Device not found"}
    return dict(row)
