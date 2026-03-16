"""Unified MCP server for ServiceTsunami tools."""
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

mcp = FastMCP(
    "ServiceTsunami",
    stateless_http=True,
    json_response=True,
    host="0.0.0.0",
    port=8000,
    # Disable DNS rebinding protection for Docker internal networking
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
    ),
)
