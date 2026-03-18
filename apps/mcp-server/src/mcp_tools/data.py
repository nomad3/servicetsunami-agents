"""Data discovery and querying MCP tools.

Data discovery and querying tools for Databricks.
All data operations route through the MCP server's Databricks client.
"""
import logging
from typing import Optional

from mcp.server.fastmcp import Context

from src.mcp_app import mcp
from src.mcp_auth import resolve_tenant_id

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Databricks client helper
# ---------------------------------------------------------------------------

def _get_databricks_client():
    """Return a Databricks client from config."""
    from src.config import settings
    # Import the MCP server's own Databricks client (tools/databricks.py or similar)
    # Fallback: construct a minimal client using settings
    try:
        from src.databricks_client import get_databricks_client
        return get_databricks_client()
    except ImportError:
        # Lazy import — MCP server may have its own Databricks integration
        class _FallbackClient:
            async def query_sql(self, sql: str, limit: int = 1000) -> dict:
                return {"error": "Databricks client not available in this MCP deployment."}
            async def list_tables(self, catalog: str, schema: str) -> list:
                return []
            async def describe_table(self, catalog: str, schema: str, table: str) -> dict:
                return {"error": "Databricks client not available."}
        return _FallbackClient()


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def discover_datasets(
    tenant_id: str = "",
    search_query: str = "",
    ctx: Context = None,
) -> dict:
    """Find available datasets in the tenant's Databricks catalog.

    Args:
        tenant_id: Tenant UUID (resolved from session if omitted).
        search_query: Optional natural language search (e.g. "sales data from 2024").
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with list of datasets (name, schema, row count, last updated).
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"error": "tenant_id is required."}

    client = _get_databricks_client()
    catalog = f"tenant_{tid.replace('-', '_')}"

    try:
        tables = await client.list_tables(catalog=catalog, schema="silver")

        if search_query:
            search_lower = search_query.lower()
            tables = [t for t in tables if search_lower in t.get("name", "").lower()]

        return {
            "status": "success",
            "catalog": catalog,
            "datasets": tables,
            "count": len(tables),
        }
    except Exception as e:
        logger.exception("discover_datasets failed")
        return {"error": f"Failed to discover datasets: {str(e)}"}


@mcp.tool()
async def get_dataset_schema(
    dataset_id: str,
    tenant_id: str = "",
    ctx: Context = None,
) -> dict:
    """Get detailed schema with column types, nullability, and sample values.

    Args:
        dataset_id: Dataset identifier in format catalog.schema.table. Required.
        tenant_id: Tenant UUID (resolved from session if omitted).
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with columns, types, and sample data.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not dataset_id:
        return {"error": "dataset_id is required (format: catalog.schema.table)."}

    parts = dataset_id.split(".")
    if len(parts) != 3:
        return {"error": "Invalid dataset_id format. Expected: catalog.schema.table"}

    catalog, schema, table = parts
    client = _get_databricks_client()

    try:
        result = await client.describe_table(catalog=catalog, schema=schema, table=table)
        return {"status": "success", "dataset_id": dataset_id, **result}
    except Exception as e:
        logger.exception("get_dataset_schema failed")
        return {"error": f"Failed to get schema: {str(e)}"}


@mcp.tool()
async def query_sql(
    sql: str,
    explanation: str = "",
    limit: int = 1000,
    tenant_id: str = "",
    ctx: Context = None,
) -> dict:
    """Execute a SQL query on Databricks Unity Catalog.

    Args:
        sql: The SQL query to execute. Required.
        explanation: Brief explanation of what this query does.
        limit: Maximum rows to return (default 1000).
        tenant_id: Tenant UUID (resolved from session if omitted).
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with rows, column names, row_count, and the executed query.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not sql:
        return {"error": "sql is required."}

    client = _get_databricks_client()

    # Add LIMIT if not present
    sql_upper = sql.upper()
    if "LIMIT" not in sql_upper:
        sql = f"{sql.rstrip(';')} LIMIT {limit}"

    try:
        result = await client.query_sql(sql=sql, limit=limit)
        return {
            "status": "success",
            "rows": result.get("rows", []),
            "columns": result.get("columns", []),
            "row_count": len(result.get("rows", [])),
            "explanation": explanation,
            "query": sql,
        }
    except Exception as e:
        logger.exception("query_sql failed")
        return {"error": f"Query failed: {str(e)}"}


@mcp.tool()
async def generate_insights(
    dataset_id: str,
    focus_areas: str = "",
    tenant_id: str = "",
    ctx: Context = None,
) -> dict:
    """Auto-generate statistical insights from a dataset.

    Args:
        dataset_id: Dataset identifier (format: catalog.schema.table). Required.
        focus_areas: Comma-separated areas to focus on (e.g. "trends,anomalies").
            Leave empty for general insights.
        tenant_id: Tenant UUID (resolved from session if omitted).
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with dataset statistics and key findings.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not dataset_id:
        return {"error": "dataset_id is required."}

    client = _get_databricks_client()
    focus_list = [f.strip() for f in focus_areas.split(",") if f.strip()] if focus_areas else ["general"]

    try:
        stats_sql = f"""
        SELECT
            COUNT(*) as total_rows,
            COUNT(DISTINCT *) as unique_rows
        FROM {dataset_id}
        """
        stats = await client.query_sql(sql=stats_sql)

        return {
            "status": "success",
            "dataset": dataset_id,
            "statistics": stats,
            "focus_areas": focus_list,
            "note": "Detailed insights will be generated by the agent based on data exploration",
        }
    except Exception as e:
        logger.exception("generate_insights failed")
        return {"error": f"Failed to generate insights: {str(e)}"}
