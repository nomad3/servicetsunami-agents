"""Run the FastMCP server as standalone SSE.

We use SSE rather than streamable-http because gemini-cli's MCP client
sends `Accept: application/json` only and FastMCP's streamable-http
transport requires the client to include `text/event-stream` in Accept,
returning 406 otherwise. SSE transport doesn't have that restriction
and is supported by gemini-cli via `type: "sse"` in the MCP config.
"""
import src.mcp_tools  # noqa: F401 — registers @mcp.tool() decorators
from src.mcp_app import mcp
from src.tool_audit import install_audit

# Wrap mcp.call_tool with audit logging. Idempotent; audit failures
# never propagate to callers (see tool_audit._log_call).
install_audit(mcp)

if __name__ == "__main__":
    mcp.run(transport="sse")
