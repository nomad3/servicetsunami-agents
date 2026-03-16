"""Unified MCP server for ServiceTsunami tools."""
from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "ServiceTsunami",
    stateless_http=True,
    json_response=True,
)
