"""
AgentProvision MCP Server (REST API + MCP)

This server exposes REST endpoints for the AgentProvision API to consume,
acting as a bridge to PostgreSQL and other integrations.
"""
import inspect
import os
import logging
from contextlib import asynccontextmanager

import asyncpg
import uvicorn
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional, List, Dict, Any

from src.config import settings
from src.clients.postgres_client import PostgreSQLClient
from src.tools import postgres_tools, ingestion
from src.services.browser_service import get_browser_service
from src.tools.web_scraper import scrape_webpage, scrape_structured_data, search_and_scrape
from src.mcp_app import mcp as mcp_server
import src.mcp_tools  # noqa: F401 — registers @mcp.tool() decorators
from src.mcp_tools import learning as _learning

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start browser service on startup, shut down on exit."""
    browser_service = get_browser_service()
    try:
        await browser_service.start()
        logger.info("Browser service started during lifespan")
    except Exception as e:
        logger.warning(f"Browser service failed to start (scraping will be disabled): {e}")
    
    yield
    
    try:
        await browser_service.stop()
        logger.info("Browser service stopped during lifespan")
    except Exception:
        pass


app = FastAPI(
    title="AgentProvision MCP Server",
    docs_url="/docs",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)
# NOTE: FastMCP streamable HTTP requires its own ASGI lifecycle.
# It runs as a separate process on port 8001 (see Dockerfile CMD).
# Do NOT mount it here — app.mount() breaks the task group initialization.
postgres = PostgreSQLClient()

# ==================== Models ====================

class CreateCatalogRequest(BaseModel):
    tenant_id: str
    catalog_name: str
    comment: Optional[str] = None

class CreateDatasetRequest(BaseModel):
    tenant_id: str
    name: str
    schema_def: List[Dict[str, str]] = [] # Renamed from schema to avoid conflict
    data: List[Dict[str, Any]] = []

class QueryDatasetRequest(BaseModel):
    tenant_id: str
    dataset_name: str
    sql: str

class TransformSilverRequest(BaseModel):
    bronze_table: str
    tenant_id: str

class ScrapeRequest(BaseModel):
    url: str
    selectors: Optional[Dict[str, str]] = None
    wait_for: Optional[str] = None
    extract_links: bool = False
    timeout: int = 30000

class ScrapeStructuredRequest(BaseModel):
    url: str
    schema: Dict[str, str]
    selectors: Optional[Dict[str, str]] = None
    timeout: int = 30000

class SearchAndScrapeRequest(BaseModel):
    query: str
    engine: str = ""
    max_results: int = 5

class CookieImportRequest(BaseModel):
    cookies: List[Dict[str, Any]]

class GoogleLoginRequest(BaseModel):
    email: str
    password: str

class LinkedInLoginRequest(BaseModel):
    email: str
    password: str

# ==================== Health ====================

@app.get("/agentprovision/v1/health")
async def health_check():
    """Health check endpoint with real connectivity tests."""
    db_status = "not_configured"
    browser_status = "unknown"

    # Check database connectivity via asyncpg
    db_url = os.environ.get("DATABASE_URL", "")
    if db_url:
        try:
            conn = await asyncpg.connect(db_url, timeout=5)
            await conn.execute("SELECT 1")
            await conn.close()
            db_status = "connected"
        except Exception as e:
            db_status = f"error: {str(e)[:100]}"

    # Check PostgreSQL connection if configured
    postgres_connected = bool(settings.POSTGRESQL_HOST)

    # Check browser service
    try:
        bs = get_browser_service()
        if bs._browser and bs._browser.is_connected():
            browser_status = "running"
        else:
            browser_status = "stopped"
    except Exception:
        browser_status = "error"

    status = "healthy" if browser_status in ("running", "stopped") else "degraded"

    return {
        "status": status,
        "database": db_status,
        "postgres_connected": postgres_connected,
        "browser": browser_status,
        "version": "1.1.0",
    }

# ==================== Scraper Endpoints ====================

@app.post("/agentprovision/v1/scrape")
async def scrape_endpoint(request: ScrapeRequest):
    """Scrape a webpage and extract content."""
    try:
        result = await scrape_webpage(
            url=request.url,
            selectors=request.selectors,
            wait_for=request.wait_for,
            extract_links_flag=request.extract_links,
            timeout=request.timeout,
        )
        return result
    except Exception as e:
        logger.error("Scrape failed for %s: %s", request.url, e)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/agentprovision/v1/scrape/structured")
async def scrape_structured_endpoint(request: ScrapeStructuredRequest):
    """Scrape a webpage and extract structured data using CSS selectors."""
    try:
        result = await scrape_structured_data(
            url=request.url,
            schema=request.schema,
            selectors=request.selectors,
            timeout=request.timeout,
        )
        return result
    except Exception as e:
        logger.error("Structured scrape failed for %s: %s", request.url, e)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/agentprovision/v1/search-and-scrape")
async def search_and_scrape_endpoint(request: SearchAndScrapeRequest):
    """Search the web and scrape top results."""
    try:
        results = await search_and_scrape(
            query=request.query,
            engine=request.engine,
            max_results=request.max_results,
        )
        return {"query": request.query, "results": results}
    except Exception as e:
        logger.error("Search and scrape failed for '%s': %s", request.query, e)
        raise HTTPException(status_code=500, detail=str(e))

# ==================== Cookie Auth ====================

@app.post("/agentprovision/v1/auth/cookies")
async def import_cookies(request: CookieImportRequest):
    """Import browser cookies for authenticated scraping (Google, LinkedIn, etc.)."""
    bs = get_browser_service()
    bs.set_cookies(request.cookies)
    domains = set()
    for c in request.cookies:
        d = c.get("domain", "")
        if d:
            domains.add(d.lstrip("."))
    return {
        "status": "ok",
        "cookies_imported": len(request.cookies),
        "domains": sorted(domains),
    }


@app.get("/agentprovision/v1/auth/cookies")
async def get_cookie_status():
    """Check what cookies are stored."""
    bs = get_browser_service()
    cookies = bs.get_cookies()
    domains = set()
    for c in cookies:
        d = c.get("domain", "")
        if d:
            domains.add(d.lstrip("."))
    return {
        "cookies_count": len(cookies),
        "domains": sorted(domains),
    }


@app.post("/agentprovision/v1/auth/google/login")
async def google_login(request: GoogleLoginRequest):
    """Login to Google via Playwright browser.

    Navigates to accounts.google.com, enters email/password,
    and stores session cookies for authenticated Google Search and other services.
    """
    try:
        bs = get_browser_service()
        result = await bs.login_google(email=request.email, password=request.password)
        return result
    except Exception as e:
        logger.error("Google login endpoint failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/agentprovision/v1/auth/linkedin/login")
async def linkedin_login(request: LinkedInLoginRequest):
    """Login to LinkedIn via Playwright browser.

    Navigates to linkedin.com/login, enters email/password,
    and stores session cookies for authenticated LinkedIn scraping.
    """
    try:
        bs = get_browser_service()
        result = await bs.login_linkedin(email=request.email, password=request.password)
        return result
    except Exception as e:
        logger.error("LinkedIn login endpoint failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ==================== PostgreSQL Routes ====================

# --- PostgreSQL Catalogs ---

@app.post("/agentprovision/v1/postgres/catalogs")
async def create_catalog(request: CreateCatalogRequest):
    try:
        return {
            "catalog_name": request.catalog_name,
            "tenant_id": request.tenant_id,
            "status": "created"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/agentprovision/v1/postgres/catalogs/{tenant_id}")
async def get_catalog_status(tenant_id: str):
    try:
        catalog_name = f"agentprovision_{tenant_id.replace('-', '_')}"
        return {
            "exists": True,
            "catalog_name": catalog_name,
            "schemas": ["default", "bronze", "silver", "gold"]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- PostgreSQL Datasets ---

@app.post("/agentprovision/v1/postgres/datasets")
async def create_dataset(request: CreateDatasetRequest):
    return {"status": "created", "table": f"{request.name}"}

@app.post("/agentprovision/v1/postgres/datasets/upload")
async def upload_dataset(
    tenant_id: str,
    dataset_name: str,
    format: str,
    file: UploadFile = File(...)
):
    content = await file.read()
    return {"status": "uploaded", "size": len(content)}

@app.post("/agentprovision/v1/postgres/datasets/query")
async def query_dataset(request: QueryDatasetRequest):
    try:
        result = await postgres_tools.query_sql(request.sql, request.tenant_id)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/agentprovision/v1/postgres/datasets/{tenant_id}/{dataset_name}")
async def get_dataset(tenant_id: str, dataset_name: str):
    try:
        result = await postgres_tools.describe_table(dataset_name, tenant_id)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/agentprovision/v1/postgres/transformations/silver")
async def transform_silver(request: TransformSilverRequest):
    try:
        result = await postgres_tools.transform_to_silver(request.bronze_table, request.tenant_id)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==================== Luna Learn HTTP Shim ====================
# Task 1.2a — translate typed Python exceptions raised by
# ``src.mcp_tools.learning`` primitives into HTTP responses with
# ``error_type`` + ``message`` body fields so the Temporal activities
# in T3.1 can branch on the error type without parsing free-form 500s.
#
# The ``error_type`` body field is authoritative — the status-code map
# below is a fast-path hint only. See
# ``docs/superpowers/plans/2026-05-25-luna-learn-from-media-plan.md``
# (Task 1.2a) for the contract + rationale on the chosen codes
# (e.g. 451 / 424 repurposed from RFC 7725 / WebDAV for internal use).

_LEARNING_EXC_STATUS: Dict[type, int] = {
    _learning.MediaTooLong: 413,
    _learning.MediaPrivate: 451,
    _learning.MediaNotFound: 404,
    _learning.MediaGeoBlocked: 403,
    _learning.MediaAntiScrape: 429,
    _learning.DraftInvalid: 422,
    _learning.DraftForbiddenShellout: 424,
    _learning.ReviewerNotProvisioned: 503,
    _learning.ReviewTimeout: 504,
    _learning.SlugExhausted: 409,
}


def _learning_error_response(status_code: int, error_type: str, message: str) -> JSONResponse:
    """Uniform error envelope used by the Luna Learn dispatch shim.

    Body shape (matches T3.1's ``_STATUS_TO_TYPE`` consumer)::

        {"error_type": "<ExceptionClassName>", "message": "<str(exc)>"}
    """
    return JSONResponse(
        status_code=status_code,
        content={"error_type": error_type, "message": message},
    )


@app.post("/agentprovision/v1/tools/{tool_name}")
async def dispatch_learning_tool(tool_name: str, payload: Dict[str, Any] | None = None):
    """Dispatch a Luna Learn MCP primitive over HTTP for Temporal activities.

    The ``src.mcp_tools.learning.TOOLS`` registry maps tool name → callable
    (async or sync). The shim:

    1. Looks the tool up; missing → 404 ``ToolNotFound`` (distinct from
       ``MediaNotFound`` so activities don't confuse "I asked for the
       wrong endpoint" with "the video was deleted").
    2. Awaits the result if it's a coroutine, otherwise returns the value
       directly.
    3. Catches the typed ``LearningToolError`` subclasses and converts
       them per ``_LEARNING_EXC_STATUS``.
    4. Catches any other ``Exception`` and returns 500 +
       ``error_type="UnknownError"``.

    Auth: the route currently relies on the existing reverse-proxy /
    internal network boundary (the FastAPI server runs inside the docker
    network on port 8000). Temporal activities call it with the same
    ``X-Internal-Key`` header the other MCP-server REST routes accept;
    enforcement is delegated to future middleware in line with the rest
    of this file's routes (none of which authenticate inline today).
    """
    tools = _learning.TOOLS
    if tool_name not in tools:
        return _learning_error_response(
            status_code=404,
            error_type="ToolNotFound",
            message=f"No Luna Learn tool registered under name '{tool_name}'",
        )

    tool = tools[tool_name]
    kwargs = payload or {}

    try:
        result = tool(**kwargs)
        if inspect.isawaitable(result):
            result = await result
        return result
    except tuple(_LEARNING_EXC_STATUS.keys()) as exc:
        status = _LEARNING_EXC_STATUS[type(exc)]
        return _learning_error_response(
            status_code=status,
            error_type=type(exc).__name__,
            message=str(exc),
        )
    except Exception as exc:  # noqa: BLE001 — intentional catch-all
        logger.exception("Luna Learn tool %s raised an unmapped exception", tool_name)
        return _learning_error_response(
            status_code=500,
            error_type="UnknownError",
            message=str(exc),
        )


def main():
    host = os.environ.get("FASTMCP_HOST", "0.0.0.0")
    port = int(os.environ.get("FASTMCP_PORT", "8000"))
    uvicorn.run(app, host=host, port=port)

if __name__ == "__main__":
    main()
