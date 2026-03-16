"""Run the FastMCP server as standalone Streamable HTTP."""
import uvicorn
import src.mcp_tools  # noqa: F401 — registers @mcp.tool() decorators
from src.mcp_app import mcp

# Get the ASGI app from FastMCP
app = mcp.streamable_http_app()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)
