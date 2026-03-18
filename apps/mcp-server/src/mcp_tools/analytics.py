"""Analytics and calculation MCP tools.

Mathematical calculation and time-series analytics tools.
Provides calculations and Databricks-powered time-series analytics.
"""
import logging

from mcp.server.fastmcp import Context

from src.mcp_app import mcp

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_databricks_client():
    """Return a Databricks client from MCP server config."""
    try:
        from src.databricks_client import get_databricks_client
        return get_databricks_client()
    except ImportError:
        class _FallbackClient:
            async def query_sql(self, sql: str, **kwargs) -> dict:
                return {"error": "Databricks client not available in this MCP deployment."}
        return _FallbackClient()


def _parse_json(val, default=None):
    if val is None:
        return default
    if isinstance(val, (dict, list)):
        return val
    import json
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return default


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def calculate(
    expression: str,
    ctx: Context = None,
) -> dict:
    """Evaluate a mathematical expression safely.

    Args:
        expression: Mathematical expression (e.g., "100 * 1.15", "(500 - 300) / 200").
            Only numbers and +-*/() operators are allowed. Required.
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with expression and calculated result.
    """
    if not expression:
        return {"error": "expression is required."}

    allowed = set("0123456789+-*/(). ")
    if not all(c in allowed for c in expression):
        return {"error": "Invalid characters in expression. Only numbers and +-*/() allowed."}

    try:
        result = eval(expression)  # noqa: S307 — input validated above
        return {
            "status": "success",
            "expression": expression,
            "result": result,
        }
    except Exception as e:
        return {"error": f"Calculation error: {str(e)}"}


@mcp.tool()
async def compare_periods(
    dataset_id: str,
    metric: str,
    period1: str,
    period2: str,
    time_column: str = "date",
    tenant_id: str = "",
    ctx: Context = None,
) -> dict:
    """Compare metrics across two time periods.

    Args:
        dataset_id: Dataset identifier (format: catalog.schema.table). Required.
        metric: Column name to compare (e.g., "revenue", "count"). Required.
        period1: First period as JSON string, e.g.
            '{"start": "2024-01-01", "end": "2024-03-31"}'. Required.
        period2: Second period as JSON string, e.g.
            '{"start": "2024-04-01", "end": "2024-06-30"}'. Required.
        time_column: Name of the date/timestamp column. Default: "date".
        tenant_id: Tenant UUID (resolved from session if omitted).
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with comparison including absolute and percentage changes.
    """
    if not dataset_id or not metric or not period1 or not period2:
        return {"error": "dataset_id, metric, period1, and period2 are required."}

    p1 = _parse_json(period1, {})
    p2 = _parse_json(period2, {})

    client = _get_databricks_client()

    sql = f"""
    WITH period1_data AS (
        SELECT SUM({metric}) as total, AVG({metric}) as avg, COUNT(*) as count
        FROM {dataset_id}
        WHERE {time_column} BETWEEN '{p1.get("start", "")}' AND '{p1.get("end", "")}'
    ),
    period2_data AS (
        SELECT SUM({metric}) as total, AVG({metric}) as avg, COUNT(*) as count
        FROM {dataset_id}
        WHERE {time_column} BETWEEN '{p2.get("start", "")}' AND '{p2.get("end", "")}'
    )
    SELECT
        p1.total as period1_total,
        p1.avg as period1_avg,
        p1.count as period1_count,
        p2.total as period2_total,
        p2.avg as period2_avg,
        p2.count as period2_count,
        (p2.total - p1.total) as absolute_change,
        CASE WHEN p1.total > 0
             THEN ((p2.total - p1.total) / p1.total * 100)
             ELSE NULL END as pct_change
    FROM period1_data p1, period2_data p2
    """

    try:
        result = await client.query_sql(sql=sql)
        return {
            "status": "success",
            "metric": metric,
            "period1": p1,
            "period2": p2,
            "comparison": result.get("rows", [{}])[0] if result.get("rows") else {},
        }
    except Exception as e:
        logger.exception("compare_periods failed")
        return {"error": f"Failed to compare periods: {str(e)}"}


@mcp.tool()
async def forecast(
    dataset_id: str,
    target_column: str,
    time_column: str,
    horizon: int = 30,
    tenant_id: str = "",
    ctx: Context = None,
) -> dict:
    """Generate a time-series trend analysis with moving average context.

    Note: Advanced statistical forecasting requires dedicated models. This tool
    provides historical data and a moving average as context for the LLM to
    describe trends.

    Args:
        dataset_id: Dataset identifier (format: catalog.schema.table). Required.
        target_column: Column to forecast. Required.
        time_column: Date/timestamp column. Required.
        horizon: Number of periods to forecast (default 30, used as context).
        tenant_id: Tenant UUID (resolved from session if omitted).
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with historical data and moving average for trend analysis.
    """
    if not dataset_id or not target_column or not time_column:
        return {"error": "dataset_id, target_column, and time_column are required."}

    client = _get_databricks_client()

    sql = f"""
    SELECT
        {time_column},
        {target_column},
        AVG({target_column}) OVER (
            ORDER BY {time_column}
            ROWS BETWEEN 6 PRECEDING AND CURRENT ROW
        ) as moving_avg
    FROM {dataset_id}
    ORDER BY {time_column} DESC
    LIMIT 100
    """

    try:
        result = await client.query_sql(sql=sql)
        return {
            "status": "success",
            "dataset": dataset_id,
            "target": target_column,
            "horizon": horizon,
            "historical_data": result.get("rows", []),
            "note": "Advanced forecasting requires statistical models. This provides historical context for trend analysis.",
        }
    except Exception as e:
        logger.exception("forecast failed")
        return {"error": f"Failed to generate forecast: {str(e)}"}
