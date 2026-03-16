"""Run the FastMCP server as standalone Streamable HTTP on 0.0.0.0:8000."""
import uvicorn
import src.mcp_tools  # noqa: F401 — registers @mcp.tool() decorators
from src.mcp_app import mcp
from starlette.middleware.trustedhost import TrustedHostMiddleware

# Get the Starlette ASGI app from FastMCP
_app = mcp.streamable_http_app()

# Wrap with permissive TrustedHostMiddleware to allow Docker hostnames
app = _app
# Override any existing trusted host middleware by adding a permissive one
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["*"])

if __name__ == "__main__":
    uvicorn.run(
        "src.mcp_serve:app",
        host="0.0.0.0",
        port=8000,
    )
