"""Tenant authentication for MCP tool calls."""
import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

INTERNAL_KEY = os.environ.get("MCP_API_KEY", "dev_mcp_key")


def resolve_tenant_id(ctx) -> Optional[str]:
    """Extract tenant_id from MCP request context."""
    if hasattr(ctx, 'request_context') and ctx.request_context:
        return ctx.request_context.get("tenant_id")
    return None


def verify_internal_key(ctx) -> bool:
    """Verify the X-Internal-Key header."""
    if hasattr(ctx, 'request_context') and ctx.request_context:
        key = ctx.request_context.get("internal_key")
        return key == INTERNAL_KEY
    return False
