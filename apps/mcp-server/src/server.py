"""
ServiceTsunami MCP Server (REST API + MCP)

This server exposes REST endpoints for the ServiceTsunami API to consume,
acting as a bridge to Databricks and other integrations.
"""
import os
import logging
from contextlib import asynccontextmanager

import asyncpg
import uvicorn
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from typing import Optional, List, Dict, Any

from src.config import settings
from src.clients.databricks_client import DatabricksClient
from src.tools import databricks_tools, ingestion
from src.services.browser_service import get_browser_service
from src.tools.web_scraper import scrape_webpage, scrape_structured_data, search_and_scrape
from src.mcp_app import mcp as mcp_server

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start browser service on startup, shut down on exit."""
    browser_service = get_browser_service()
    await browser_service.start()
    logger.info("Browser service started during lifespan")
    yield
    await browser_service.stop()
    logger.info("Browser service stopped during lifespan")


app = FastAPI(
    title="ServiceTsunami MCP Server",
    docs_url="/docs",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)
app.mount("/mcp", mcp_server.streamable_http_app())
databricks = DatabricksClient()

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

@app.get("/servicetsunami/v1/health")
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

    # Check Databricks connection if configured
    databricks_connected = bool(settings.DATABRICKS_HOST)

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
        "databricks_connected": databricks_connected,
        "browser": browser_status,
        "version": "1.1.0",
    }

# ==================== Scraper Endpoints ====================

@app.post("/servicetsunami/v1/scrape")
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


@app.post("/servicetsunami/v1/scrape/structured")
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


@app.post("/servicetsunami/v1/search-and-scrape")
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

@app.post("/servicetsunami/v1/auth/cookies")
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


@app.get("/servicetsunami/v1/auth/cookies")
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


@app.post("/servicetsunami/v1/auth/google/login")
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


@app.post("/servicetsunami/v1/auth/linkedin/login")
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


# ==================== Databricks Routes ====================

# --- Databricks Catalogs ---

@app.post("/servicetsunami/v1/databricks/catalogs")
async def create_catalog(request: CreateCatalogRequest):
    try:
        return {
            "catalog_name": request.catalog_name,
            "tenant_id": request.tenant_id,
            "status": "created"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/servicetsunami/v1/databricks/catalogs/{tenant_id}")
async def get_catalog_status(tenant_id: str):
    try:
        catalog_name = f"servicetsunami_{tenant_id.replace('-', '_')}"
        return {
            "exists": True,
            "catalog_name": catalog_name,
            "schemas": ["default", "bronze", "silver", "gold"]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- Databricks Datasets ---

@app.post("/servicetsunami/v1/databricks/datasets")
async def create_dataset(request: CreateDatasetRequest):
    return {"status": "created", "table": f"{request.name}"}

@app.post("/servicetsunami/v1/databricks/datasets/upload")
async def upload_dataset(
    tenant_id: str,
    dataset_name: str,
    format: str,
    file: UploadFile = File(...)
):
    content = await file.read()
    return {"status": "uploaded", "size": len(content)}

@app.post("/servicetsunami/v1/databricks/datasets/query")
async def query_dataset(request: QueryDatasetRequest):
    try:
        result = await databricks_tools.query_sql(request.sql, request.tenant_id)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/servicetsunami/v1/databricks/datasets/{tenant_id}/{dataset_name}")
async def get_dataset(tenant_id: str, dataset_name: str):
    try:
        result = await databricks_tools.describe_table(dataset_name, tenant_id)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/servicetsunami/v1/databricks/transformations/silver")
async def transform_silver(request: TransformSilverRequest):
    try:
        result = await databricks_tools.transform_to_silver(request.bronze_table, request.tenant_id)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def main():
    host = os.environ.get("FASTMCP_HOST", "0.0.0.0")
    port = int(os.environ.get("FASTMCP_PORT", "8000"))
    uvicorn.run(app, host=host, port=port)

if __name__ == "__main__":
    main()
