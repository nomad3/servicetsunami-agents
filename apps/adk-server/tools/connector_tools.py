"""Connector tools for querying tenant data sources.

Bridges ADK agents to tenant-connected databases and APIs via the
FastAPI backend's existing connector infrastructure.
"""
import logging
import re
from typing import Optional

import httpx

from config.settings import settings

logger = logging.getLogger(__name__)

_UUID_PATTERN = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.I)
_cached_default_tenant_id = None


def _resolve_tenant_id(tenant_id: str) -> str:
    """Resolve tenant_id to a valid UUID string.
    If the LLM passes a non-UUID value (like 'default_tenant' or 'auto'),
    look up the first tenant from the database."""
    global _cached_default_tenant_id
    if _UUID_PATTERN.match(tenant_id):
        return tenant_id
    if _cached_default_tenant_id:
        return _cached_default_tenant_id
    try:
        from sqlalchemy import create_engine, text
        engine = create_engine(settings.database_url)
        with engine.connect() as conn:
            result = conn.execute(text("SELECT id FROM tenants LIMIT 1")).fetchone()
            if result:
                _cached_default_tenant_id = str(result[0])
                return _cached_default_tenant_id
    except Exception:
        pass
    return tenant_id

_http_client: Optional[httpx.AsyncClient] = None


def _get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            base_url=settings.api_base_url,
            timeout=60.0,
        )
    return _http_client


async def query_data_source(
    tenant_id: str,
    query: str,
    connector_id: Optional[str] = None,
    connector_type: Optional[str] = None,
) -> dict:
    """Query a tenant's connected data source (database, API, or warehouse).

    Executes a read-only SQL query or API call against a tenant's configured
    connector. Use this to look up customer records, order status, inventory,
    product catalog, or any data the tenant has connected.

    Args:
        tenant_id: Tenant context for isolation.
        query: SQL SELECT query for databases, or search term for REST APIs.
        connector_id: Specific connector UUID to query. If omitted, uses the
            first active connector matching connector_type (or any active one).
        connector_type: Filter by type: postgres, mysql, snowflake, databricks, api.
            Ignored if connector_id is provided.

    Returns:
        Dict with columns, rows, row_count, and connector metadata.
        On error, returns {error: str}.
    """
    client = _get_http_client()
    internal_headers = {"X-Internal-Key": settings.mcp_api_key}
    tenant_id = _resolve_tenant_id(tenant_id)
    try:
        # If no connector_id, discover one via internal endpoint
        if not connector_id:
            params = {}
            if tenant_id:
                params["tenant_id"] = tenant_id
            resp = await client.get(
                "/api/v1/data_sources/internal/list",
                headers=internal_headers,
                params=params,
            )
            resp.raise_for_status()
            sources = resp.json()

            # Filter by type if requested
            if connector_type:
                sources = [s for s in sources if s.get("type") == connector_type]
            if not sources:
                return {"error": f"No data sources found (type={connector_type})"}

            # Smart selection: prefer queryable types (api, rest_api, postgres)
            # over non-queryable (warehouse, stream) unless explicitly requested
            if not connector_type and len(sources) > 1:
                preferred = [s for s in sources if s.get("type") in ("api", "rest_api", "postgres", "mysql")]
                if preferred:
                    sources = preferred
            connector_id = sources[0]["id"]

        # Execute query via the internal query endpoint
        resp = await client.post(
            f"/api/v1/data_sources/{connector_id}/internal-query",
            headers=internal_headers,
            json={"query": query, "tenant_id": tenant_id},
        )
        resp.raise_for_status()
        result = resp.json()
        return {
            "success": True,
            "columns": list(result[0].keys()) if result else [],
            "rows": result[:100],
            "row_count": len(result),
            "connector_id": connector_id,
        }
    except httpx.HTTPStatusError as e:
        logger.error("query_data_source failed: %s %s", e.response.status_code, e.response.text[:300])
        return {"error": f"Query failed with status {e.response.status_code}: {e.response.text[:200]}"}
    except Exception as e:
        logger.error("query_data_source error: %s", e, exc_info=True)
        return {"error": f"Query failed: {str(e)}"}
