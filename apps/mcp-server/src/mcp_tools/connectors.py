"""Connector MCP tools for querying tenant data sources.

Connector tools for querying tenant-connected data sources.
Bridges agents to tenant-connected databases and APIs via the FastAPI
backend's existing connector infrastructure.
"""
import json
import logging
import re
from typing import Optional

import httpx
from mcp.server.fastmcp import Context

from src.mcp_app import mcp
from src.mcp_auth import resolve_tenant_id

logger = logging.getLogger(__name__)

_UUID_PATTERN = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.I
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_api_base_url() -> str:
    from src.config import settings
    return settings.API_BASE_URL.rstrip("/")


def _get_internal_key() -> str:
    from src.config import settings
    return settings.API_INTERNAL_KEY


def _parse_json(val, default=None):
    if val is None:
        return default
    if isinstance(val, (dict, list)):
        return val
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return default


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def query_data_source(
    query: str,
    tenant_id: str = "",
    connector_id: str = "",
    connector_type: str = "",
    endpoint: str = "",
    params: str = "",
    method: str = "GET",
    ctx: Context = None,
) -> dict:
    """Query a tenant's connected data source using REST API endpoints or SQL.

    IMPORTANT: For REST API data sources, you MUST use the endpoint and params
    parameters. SQL queries will NOT work for REST API sources.
    Only use SQL for database-type data sources (postgres, mysql, databricks).

    Args:
        query: SQL query for database sources only. Ignored for REST API sources.
        tenant_id: Tenant UUID (resolved from session if omitted).
        connector_id: Specific connector UUID to query. If omitted, auto-discovers
            the first active connector matching connector_type.
        connector_type: Filter by type: postgres, mysql, snowflake, databricks, api.
        endpoint: REST API endpoint path, e.g. "/medications/search",
            "/prices/compare". Required for API sources.
        params: JSON string of query parameters, e.g. '{"q": "paracetamol", "limit": 10}'.
        method: HTTP method: "GET" or "POST". Default "GET".
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with columns, rows, row_count on success. {error: str} on failure.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"error": "tenant_id is required."}

    api_base_url = _get_api_base_url()
    internal_key = _get_internal_key()
    internal_headers = {"X-Internal-Key": internal_key}
    parsed_params = _parse_json(params, {})

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            effective_connector_id = connector_id

            # Auto-discover connector if not specified
            if not effective_connector_id:
                disc_params = {"tenant_id": tid}
                resp = await client.get(
                    f"{api_base_url}/api/v1/data_sources/internal/list",
                    headers=internal_headers,
                    params=disc_params,
                )
                resp.raise_for_status()
                sources = resp.json()

                if connector_type:
                    sources = [s for s in sources if s.get("type") == connector_type]
                if not sources:
                    return {"error": f"No data sources found (type={connector_type or 'any'})"}

                # Prefer queryable types unless explicitly requested
                if not connector_type and len(sources) > 1:
                    preferred = [
                        s for s in sources
                        if s.get("type") in ("api", "rest_api", "postgres", "mysql")
                    ]
                    if preferred:
                        sources = preferred
                effective_connector_id = sources[0]["id"]

            # Build request body
            body: dict = {"query": query, "tenant_id": tid}
            if endpoint:
                body["endpoint"] = endpoint
                body["params"] = parsed_params
                body["method"] = method

            resp = await client.post(
                f"{api_base_url}/api/v1/data_sources/{effective_connector_id}/internal-query",
                headers=internal_headers,
                json=body,
            )
            resp.raise_for_status()
            result = resp.json()

            return {
                "success": True,
                "columns": list(result[0].keys()) if result else [],
                "rows": result[:100],
                "row_count": len(result),
                "connector_id": effective_connector_id,
            }

    except httpx.HTTPStatusError as e:
        logger.error("query_data_source failed: %s %s", e.response.status_code, e.response.text[:300])
        return {"error": f"Query failed with status {e.response.status_code}: {e.response.text[:200]}"}
    except Exception as e:
        logger.exception("query_data_source error: %s", e)
        return {"error": f"Query failed: {str(e)}"}
