"""Tenant authentication for MCP tool calls.

FastMCP's Context.request_context is a RequestContext object, not a dict.
We extract tenant_id and internal_key from HTTP headers passed via the
Streamable HTTP transport.
"""
import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

INTERNAL_KEY = os.environ.get("MCP_API_KEY", "dev_mcp_key")


def _get_header(ctx, header_name: str) -> Optional[str]:
    """Safely extract an HTTP header from MCP request context.

    Handles both dict-like and object-like request_context,
    and checks common header variations (X-Tenant-Id, x-tenant-id).
    """
    if ctx is None:
        return None

    rc = getattr(ctx, 'request_context', None)
    if rc is None:
        return None

    # Try dict-like access first
    if isinstance(rc, dict):
        return rc.get(header_name) or rc.get(header_name.lower())

    # Try attribute access on RequestContext object
    # FastMCP exposes headers via the request_context.headers or similar
    headers = getattr(rc, 'headers', None)
    if headers:
        if isinstance(headers, dict):
            return headers.get(header_name) or headers.get(header_name.lower())
        # httpx/starlette Headers object
        if hasattr(headers, 'get'):
            return headers.get(header_name) or headers.get(header_name.lower())

    # Try direct attribute access
    for attr in [header_name, header_name.lower(), header_name.replace("-", "_").lower()]:
        val = getattr(rc, attr, None)
        if val is not None:
            return str(val)

    return None


def resolve_tenant_id(ctx) -> Optional[str]:
    """Extract tenant_id from MCP request context headers."""
    return _get_header(ctx, "X-Tenant-Id") or _get_header(ctx, "tenant_id")


def resolve_user_id(ctx) -> Optional[str]:
    """Extract the calling user's UUID from MCP request context headers.

    Set by ``cli_session_manager.generate_mcp_config`` so chat-side mutating
    tools (update_skill_definition / update_agent_definition) can attribute
    a revision to the user actually driving the chat session.
    """
    return _get_header(ctx, "X-User-Id") or _get_header(ctx, "user_id")


def verify_internal_key(ctx) -> bool:
    """Verify the X-Internal-Key header matches the configured key."""
    key = _get_header(ctx, "X-Internal-Key") or _get_header(ctx, "internal_key")
    if not key:
        return False
    return key == INTERNAL_KEY
