"""Run the FastMCP server as standalone Streamable HTTP."""
import src.mcp_tools  # noqa: F401 — registers @mcp.tool() decorators
from src.mcp_app import mcp

if __name__ == "__main__":
    mcp.run(transport="streamable-http")
