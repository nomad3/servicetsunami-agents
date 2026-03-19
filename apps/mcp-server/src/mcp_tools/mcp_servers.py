"""MCP server connector MCP tools.

Allows agents to connect, manage, discover tools from, and call
external MCP servers — bringing third-party tool ecosystems into
ServiceTsunami.
"""
import json
import logging

import httpx
from mcp.server.fastmcp import Context

from src.mcp_app import mcp
from src.mcp_auth import resolve_tenant_id

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_api_base_url() -> str:
    from src.config import settings
    return settings.API_BASE_URL.rstrip("/")


def _get_internal_key() -> str:
    from src.config import settings
    return settings.API_INTERNAL_KEY


def _headers(tenant_id: str) -> dict:
    return {
        "X-Internal-Key": _get_internal_key(),
        "X-Tenant-Id": tenant_id,
        "Content-Type": "application/json",
    }


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def connect_mcp_server(
    name: str,
    server_url: str,
    transport: str = "sse",
    auth_type: str = "none",
    auth_token: str = "",
    auth_header: str = "",
    custom_headers: str = "",
    description: str = "",
    tenant_id: str = "",
    ctx: Context = None,
) -> dict:
    """Connect a new external MCP server to ServiceTsunami.

    Registers an external MCP server so we can discover its tools and
    proxy calls through it. Supports SSE, streamable-http, and stdio transports.

    Args:
        name: Human-readable name (e.g. "Stripe MCP", "GitHub MCP Server").
        server_url: Server URL or endpoint. For SSE: "http://host:port/sse".
            For streamable-http: "http://host:port/mcp". For stdio: command string.
        transport: Transport protocol — "sse", "streamable-http", or "stdio".
        auth_type: Authentication — "none", "bearer", "api_key", or "basic".
        auth_token: Bearer token, API key, or basic auth credentials.
        auth_header: Custom header name for auth (default: "Authorization").
        custom_headers: Optional JSON string of extra HTTP headers.
            Example: '{"X-Api-Key": "abc123"}'
        description: Optional description of what this MCP server provides.
        tenant_id: Tenant UUID (resolved from session if omitted).
        ctx: MCP request context (injected automatically).

    Returns:
        Created connector details with ID and status.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"error": "tenant_id is required."}

    if transport not in ("sse", "streamable-http", "stdio"):
        return {"error": "transport must be 'sse', 'streamable-http', or 'stdio'"}
    if not server_url:
        return {"error": "server_url is required"}

    payload = {
        "name": name,
        "server_url": server_url,
        "transport": transport,
        "auth_type": auth_type,
        "enabled": True,
    }
    if auth_token:
        payload["auth_token"] = auth_token
    if auth_header:
        payload["auth_header"] = auth_header
    if description:
        payload["description"] = description
    if custom_headers:
        try:
            payload["custom_headers"] = json.loads(custom_headers)
        except json.JSONDecodeError:
            return {"error": "custom_headers must be valid JSON"}

    api_base = _get_api_base_url()
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{api_base}/api/v1/mcp-servers/internal/create",
                headers=_headers(tid),
                params={"tenant_id": tid},
                json=payload,
            )
            if resp.status_code in (200, 201):
                data = resp.json()
                return {
                    "status": "created",
                    "connector_id": data.get("id"),
                    "name": data.get("name"),
                    "server_url": data.get("server_url"),
                    "transport": data.get("transport"),
                    "message": f"MCP server '{name}' connected. Use discover_mcp_tools to see available tools.",
                }
            return {"error": f"Failed to connect MCP server: {resp.status_code} - {resp.text[:300]}"}
    except Exception as e:
        logger.exception("connect_mcp_server failed")
        return {"error": str(e)}


@mcp.tool()
async def list_mcp_servers(
    status: str = "",
    tenant_id: str = "",
    ctx: Context = None,
) -> dict:
    """List all connected MCP servers.

    Args:
        status: Filter by status — "connected", "error", "pending", or empty for all.
        tenant_id: Tenant UUID (resolved from session if omitted).
        ctx: MCP request context (injected automatically).

    Returns:
        List of MCP server connectors with their status, tool count, and call counts.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"error": "tenant_id is required."}

    api_base = _get_api_base_url()
    params = {"tenant_id": tid}
    if status:
        params["status"] = status

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{api_base}/api/v1/mcp-servers/internal/list",
                headers=_headers(tid),
                params=params,
            )
            if resp.status_code == 200:
                servers = resp.json()
                return {
                    "count": len(servers),
                    "servers": [
                        {
                            "id": s["id"],
                            "name": s["name"],
                            "server_url": s["server_url"],
                            "transport": s["transport"],
                            "status": s["status"],
                            "tool_count": s.get("tool_count", 0),
                            "call_count": s.get("call_count", 0),
                            "enabled": s["enabled"],
                            "last_error": s.get("last_error"),
                        }
                        for s in servers
                    ],
                }
            return {"error": f"Failed to list MCP servers: {resp.status_code}"}
    except Exception as e:
        logger.exception("list_mcp_servers failed")
        return {"error": str(e)}


@mcp.tool()
async def discover_mcp_tools(
    connector_id: str,
    tenant_id: str = "",
    ctx: Context = None,
) -> dict:
    """Discover available tools from a connected MCP server.

    Sends a tools/list JSON-RPC call to the server and caches the results.
    Run this after connecting a new server to see what tools it offers.

    Args:
        connector_id: UUID of the MCP server connector to discover tools from.
        tenant_id: Tenant UUID (resolved from session if omitted).
        ctx: MCP request context (injected automatically).

    Returns:
        List of discovered tools with names, descriptions, and input schemas.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"error": "tenant_id is required."}

    api_base = _get_api_base_url()
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{api_base}/api/v1/mcp-servers/internal/{connector_id}/discover",
                headers=_headers(tid),
                params={"tenant_id": tid},
            )
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 404:
                return {"error": "MCP server connector not found"}
            return {"error": f"Discovery failed: {resp.status_code} - {resp.text[:300]}"}
    except Exception as e:
        logger.exception("discover_mcp_tools failed")
        return {"error": str(e)}


@mcp.tool()
async def call_mcp_tool(
    connector_id: str,
    tool_name: str,
    arguments: str = "{}",
    tenant_id: str = "",
    ctx: Context = None,
) -> dict:
    """Call a tool on a connected external MCP server.

    Proxies a tool call to the remote MCP server and returns the result.
    Use discover_mcp_tools first to see available tools and their parameters.

    Args:
        connector_id: UUID of the MCP server connector.
        tool_name: Name of the tool to call on the remote server.
        arguments: JSON string of arguments to pass to the tool.
            Example: '{"query": "SELECT * FROM users LIMIT 10"}'
        tenant_id: Tenant UUID (resolved from session if omitted).
        ctx: MCP request context (injected automatically).

    Returns:
        Tool execution result from the remote MCP server.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"error": "tenant_id is required."}

    try:
        args_dict = json.loads(arguments)
    except json.JSONDecodeError:
        return {"error": "arguments must be valid JSON"}

    api_base = _get_api_base_url()
    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            resp = await client.post(
                f"{api_base}/api/v1/mcp-servers/internal/{connector_id}/call",
                headers=_headers(tid),
                params={"tenant_id": tid},
                json={"tool_name": tool_name, "arguments": args_dict},
            )
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 404:
                return {"error": "MCP server connector not found"}
            if resp.status_code == 400:
                return {"error": resp.json().get("detail", "Bad request")}
            return {"error": f"Tool call failed: {resp.status_code} - {resp.text[:300]}"}
    except Exception as e:
        logger.exception("call_mcp_tool failed")
        return {"error": str(e)}


@mcp.tool()
async def disconnect_mcp_server(
    connector_id: str,
    tenant_id: str = "",
    ctx: Context = None,
) -> dict:
    """Disconnect and remove an MCP server connector.

    Deletes the connector and all its call logs.

    Args:
        connector_id: UUID of the MCP server connector to remove.
        tenant_id: Tenant UUID (resolved from session if omitted).
        ctx: MCP request context (injected automatically).

    Returns:
        Confirmation of deletion.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"error": "tenant_id is required."}

    api_base = _get_api_base_url()
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.delete(
                f"{api_base}/api/v1/mcp-servers/internal/{connector_id}",
                headers=_headers(tid),
                params={"tenant_id": tid},
            )
            if resp.status_code == 200:
                return {"status": "disconnected", "connector_id": connector_id}
            if resp.status_code == 404:
                return {"error": "MCP server connector not found"}
            return {"error": f"Failed to disconnect: {resp.status_code}"}
    except Exception as e:
        logger.exception("disconnect_mcp_server failed")
        return {"error": str(e)}


@mcp.tool()
async def health_check_mcp_server(
    connector_id: str,
    tenant_id: str = "",
    ctx: Context = None,
) -> dict:
    """Check if a connected MCP server is healthy and responding.

    Sends an initialize JSON-RPC request to verify connectivity and
    get server info.

    Args:
        connector_id: UUID of the MCP server connector to check.
        tenant_id: Tenant UUID (resolved from session if omitted).
        ctx: MCP request context (injected automatically).

    Returns:
        Health status with server info and response time.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"error": "tenant_id is required."}

    api_base = _get_api_base_url()
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{api_base}/api/v1/mcp-servers/internal/{connector_id}/health",
                headers=_headers(tid),
                params={"tenant_id": tid},
                json={"timeout": 10},
            )
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 404:
                return {"error": "MCP server connector not found"}
            return {"error": f"Health check failed: {resp.status_code} - {resp.text[:300]}"}
    except Exception as e:
        logger.exception("health_check_mcp_server failed")
        return {"error": str(e)}


@mcp.tool()
async def get_mcp_server_logs(
    connector_id: str = "",
    limit: int = 20,
    tenant_id: str = "",
    ctx: Context = None,
) -> dict:
    """Get recent tool call logs for MCP server connectors.

    Shows call attempts, success/failure status, timing, and errors.

    Args:
        connector_id: Optional UUID to filter logs for a specific server.
        limit: Maximum number of log entries to return (default 20).
        tenant_id: Tenant UUID (resolved from session if omitted).
        ctx: MCP request context (injected automatically).

    Returns:
        List of call log entries with status, timing, and error details.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"error": "tenant_id is required."}

    api_base = _get_api_base_url()
    params = {"tenant_id": tid, "limit": limit}

    endpoint = f"{api_base}/api/v1/mcp-servers/internal/logs"
    if connector_id:
        endpoint = f"{api_base}/api/v1/mcp-servers/internal/{connector_id}/logs"

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(endpoint, headers=_headers(tid), params=params)
            if resp.status_code == 200:
                logs = resp.json()
                return {
                    "count": len(logs),
                    "logs": [
                        {
                            "id": l["id"],
                            "tool_name": l["tool_name"],
                            "success": l["success"],
                            "error_message": l.get("error_message"),
                            "duration_ms": l.get("duration_ms"),
                            "created_at": l.get("created_at"),
                        }
                        for l in logs
                    ],
                }
            return {"error": f"Failed to fetch logs: {resp.status_code}"}
    except Exception as e:
        logger.exception("get_mcp_server_logs failed")
        return {"error": str(e)}
