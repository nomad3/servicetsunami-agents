"""
Custom ADK server wrapper with path prefix stripping middleware.

GCE Ingress forwards /adk/* paths without stripping the prefix.
This middleware strips the /adk prefix before passing to the ADK app.
"""
import os
import sys
import uvicorn
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request


class StripPrefixMiddleware(BaseHTTPMiddleware):
    """Middleware to strip /adk prefix and extract tenant_id from requests."""

    def __init__(self, app, prefix: str = "/adk"):
        super().__init__(app)
        self.prefix = prefix

    async def dispatch(self, request: Request, call_next):
        # Strip prefix from path if present
        if request.scope["path"].startswith(self.prefix):
            request.scope["path"] = request.scope["path"][len(self.prefix):] or "/"
            if request.scope.get("raw_path"):
                raw_path = request.scope["raw_path"].decode()
                if raw_path.startswith(self.prefix):
                    request.scope["raw_path"] = (raw_path[len(self.prefix):] or "/").encode()

        # Extract tenant_id from /run requests so tools resolve the correct tenant
        if request.scope["path"] == "/run" and request.method == "POST":
            try:
                import json
                body = await request.body()
                data = json.loads(body)
                # Check state_delta first, then fall back to state
                tid = None
                if isinstance(data.get("state_delta"), dict):
                    tid = data["state_delta"].get("tenant_id")
                if not tid and isinstance(data.get("state"), dict):
                    tid = data["state"].get("tenant_id")
                if tid:
                    from tools.knowledge_tools import set_current_tenant_id
                    from tools.code_tools import set_current_tenant_id as set_code_tenant_id
                    set_current_tenant_id(tid)
                    set_code_tenant_id(tid)
                # Re-inject body so downstream can read it
                async def receive():
                    return {"type": "http.request", "body": body}
                request._receive = receive
            except Exception:
                pass

        response = await call_next(request)
        return response


def _build_session_db_url() -> str:
    """Build async PostgreSQL URL for ADK session persistence.

    Parses DATABASE_URL (postgresql://user:pass@host:port/db) and converts
    to async format (postgresql+asyncpg://user:pass@host:port/db).
    """
    from config.settings import settings
    db_url = settings.database_url
    if db_url.startswith("postgresql://"):
        return db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if db_url.startswith("postgresql+asyncpg://"):
        return db_url
    # Fallback: build from individual components
    return (
        f"postgresql+asyncpg://{settings.database_user}:{settings.database_password}"
        f"@{settings.database_host}:{settings.database_port}/{settings.database_name}"
    )


def main():
    """Start ADK server with prefix-stripping middleware."""
    # Import ADK's FastAPI app factory
    from google.adk.cli.fast_api import get_fast_api_app

    # Build async DB URL for session persistence
    session_db_url = _build_session_db_url()

    # Get the base ADK app with PostgreSQL session persistence
    app = get_fast_api_app(
        agents_dir=".",
        web=False,
        allow_origins=["*"],
        session_service_uri=session_db_url,
    )

    # Add prefix-stripping middleware
    app.add_middleware(StripPrefixMiddleware, prefix="/adk")

    # Get configuration from environment
    host = os.getenv("ADK_HOST", "0.0.0.0")
    port = int(os.getenv("ADK_PORT", "8080"))

    # Run the server
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
