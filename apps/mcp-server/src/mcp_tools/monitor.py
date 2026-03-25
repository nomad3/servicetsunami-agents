"""Inbox and competitor monitor control MCP tools.

Inbox and competitor monitor lifecycle tools.
Allows agents to start, stop, and check the status of proactive monitoring
workflows via the API's Temporal workflow endpoints.
"""
import logging

import httpx
from mcp.server.fastmcp import Context

from src.mcp_app import mcp
from src.mcp_auth import resolve_tenant_id

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_api_base_url() -> str:
    from src.config import settings
    return settings.API_BASE_URL.rstrip("/")


def _get_internal_key() -> str:
    from src.config import settings
    return settings.API_INTERNAL_KEY


# ---------------------------------------------------------------------------
# MCP Tools — Inbox Monitor
# ---------------------------------------------------------------------------


@mcp.tool()
async def start_inbox_monitor(
    tenant_id: str = "",
    interval_minutes: int = 15,
    ctx: Context = None,
) -> dict:
    """Start proactive monitoring of the user's Gmail and Calendar.

    The monitor checks for new emails and upcoming events every N minutes,
    creates notifications for important items, and extracts entities from
    significant emails.

    Args:
        tenant_id: Tenant UUID (resolved from session if omitted).
        interval_minutes: How often to check (5-60 minutes, default 15).
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with monitoring status.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"error": "tenant_id is required."}

    api_base_url = _get_api_base_url()
    internal_key = _get_internal_key()

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{api_base_url}/api/v1/workflows/inbox-monitor/start",
                headers={"X-Internal-Key": internal_key},
                params={
                    "tenant_id": tid,
                    "check_interval_minutes": interval_minutes,
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("status") == "already_running":
                    return {"status": "already_active", "message": "Inbox monitoring is already active."}
                return {
                    "status": "started",
                    "message": f"Inbox monitoring started. Checking every {interval_minutes} minutes.",
                    "interval_minutes": interval_minutes,
                }
            return {"error": f"Failed to start monitor: {resp.status_code}"}
    except Exception as e:
        logger.exception("start_inbox_monitor failed")
        return {"error": f"Failed to start monitoring: {str(e)}"}


@mcp.tool()
async def stop_inbox_monitor(
    tenant_id: str = "",
    ctx: Context = None,
) -> dict:
    """Stop proactive monitoring of the user's Gmail and Calendar.

    Args:
        tenant_id: Tenant UUID (resolved from session if omitted).
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with status.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"error": "tenant_id is required."}

    api_base_url = _get_api_base_url()
    internal_key = _get_internal_key()

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{api_base_url}/api/v1/workflows/inbox-monitor/stop",
                headers={"X-Internal-Key": internal_key},
                params={"tenant_id": tid},
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("status") == "not_running":
                    return {"status": "not_running", "message": "Inbox monitoring was not active."}
                return {"status": "stopped", "message": "Inbox monitoring has been stopped."}
            return {"error": f"Failed to stop monitor: {resp.status_code}"}
    except Exception as e:
        logger.exception("stop_inbox_monitor failed")
        return {"error": f"Failed to stop monitoring: {str(e)}"}


@mcp.tool()
async def check_inbox_monitor_status(
    tenant_id: str = "",
    ctx: Context = None,
) -> dict:
    """Check if proactive inbox monitoring is currently active.

    Args:
        tenant_id: Tenant UUID (resolved from session if omitted).
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with monitoring status and details.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"error": "tenant_id is required."}

    api_base_url = _get_api_base_url()
    internal_key = _get_internal_key()

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{api_base_url}/api/v1/workflows/inbox-monitor/status",
                headers={"X-Internal-Key": internal_key},
                params={"tenant_id": tid},
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("running"):
                    return {
                        "status": "active",
                        "message": "Inbox monitoring is active.",
                        "since": data.get("start_time"),
                    }
                return {"status": "inactive", "message": "Inbox monitoring is not active."}
            return {"error": f"Status check failed: {resp.status_code}"}
    except Exception as e:
        logger.exception("check_inbox_monitor_status failed")
        return {"error": f"Failed to check status: {str(e)}"}


# ---------------------------------------------------------------------------
# MCP Tools — Competitor Monitor
# ---------------------------------------------------------------------------


@mcp.tool()
async def start_competitor_monitor(
    tenant_id: str = "",
    check_interval_hours: int = 24,
    ctx: Context = None,
) -> dict:
    """Start the competitor monitoring workflow for this tenant.

    Monitors all competitors in the knowledge graph on a schedule.
    Checks websites, public ad libraries, and news for changes.

    Args:
        tenant_id: Tenant UUID (resolved from session if omitted).
        check_interval_hours: How often to check (in hours). Default: 24 (daily).
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with status and workflow ID.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"error": "tenant_id is required."}

    api_base_url = _get_api_base_url()
    internal_key = _get_internal_key()

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{api_base_url}/api/v1/workflows/competitor-monitor/start",
                headers={"X-Internal-Key": internal_key},
                json={
                    "tenant_id": tid,
                    "check_interval_seconds": check_interval_hours * 3600,
                },
            )
            if resp.status_code == 200:
                return resp.json()
            return {"error": f"Failed to start competitor monitor: {resp.status_code} - {resp.text[:200]}"}
    except Exception as e:
        logger.exception("start_competitor_monitor failed")
        return {"error": str(e)}


@mcp.tool()
async def stop_competitor_monitor(
    tenant_id: str = "",
    ctx: Context = None,
) -> dict:
    """Stop the competitor monitoring workflow for this tenant.

    Args:
        tenant_id: Tenant UUID (resolved from session if omitted).
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with status confirmation.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"error": "tenant_id is required."}

    api_base_url = _get_api_base_url()
    internal_key = _get_internal_key()

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{api_base_url}/api/v1/workflows/competitor-monitor/stop",
                headers={"X-Internal-Key": internal_key},
                json={"tenant_id": tid},
            )
            if resp.status_code == 200:
                return resp.json()
            return {"error": f"Failed to stop competitor monitor: {resp.status_code}"}
    except Exception as e:
        logger.exception("stop_competitor_monitor failed")
        return {"error": str(e)}


@mcp.tool()
async def check_competitor_monitor_status(
    tenant_id: str = "",
    ctx: Context = None,
) -> dict:
    """Check if the competitor monitoring workflow is running.

    Args:
        tenant_id: Tenant UUID (resolved from session if omitted).
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with running status and configuration.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"error": "tenant_id is required."}

    api_base_url = _get_api_base_url()
    internal_key = _get_internal_key()

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{api_base_url}/api/v1/workflows/competitor-monitor/status",
                headers={"X-Internal-Key": internal_key},
                params={"tenant_id": tid},
            )
            if resp.status_code == 200:
                return resp.json()
            return {"status": "not_running", "message": "Competitor monitor is not active."}
    except Exception as e:
        logger.exception("check_competitor_monitor_status failed")
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# MCP Tools — Autonomous Learning Monitor
# ---------------------------------------------------------------------------


@mcp.tool()
async def start_autonomous_learning(
    tenant_id: str = "",
    cycle_interval_hours: int = 24,
    ctx: Context = None,
) -> dict:
    """Start the nightly autonomous learning cycle for the tenant.

    The learning cycle runs every N hours and:
    - Collects RL experience stats, trust profiles, goal/commitment health
    - Auto-generates policy routing candidates from real usage patterns
    - Runs offline evaluation and auto-rejects regressions
    - Manages live rollouts and auto-promotes successful experiments
    - Sends a morning learning report as a notification

    Args:
        tenant_id: Tenant UUID (resolved from session if omitted).
        cycle_interval_hours: How often to run the cycle (1-168 hours, default 24).
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with workflow_id and status.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"error": "tenant_id is required."}

    api_base_url = _get_api_base_url()
    internal_key = _get_internal_key()

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{api_base_url}/api/v1/workflows/autonomous-learning/start",
                headers={"X-Internal-Key": internal_key},
                params={"tenant_id": tid, "cycle_interval_hours": cycle_interval_hours},
            )
            data = resp.json()
            if resp.status_code == 200:
                status = data.get("status", "unknown")
                if status == "already_running":
                    return {
                        "message": "Autonomous learning is already running.",
                        **data,
                    }
                return {
                    "message": f"Autonomous learning started (cycle every {cycle_interval_hours}h).",
                    **data,
                }
            return {"error": data.get("detail", "Failed to start autonomous learning.")}
    except Exception as e:
        logger.exception("start_autonomous_learning failed")
        return {"error": str(e)}


@mcp.tool()
async def stop_autonomous_learning(
    tenant_id: str = "",
    ctx: Context = None,
) -> dict:
    """Stop the autonomous learning cycle for the tenant.

    Args:
        tenant_id: Tenant UUID (resolved from session if omitted).
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with status.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"error": "tenant_id is required."}

    api_base_url = _get_api_base_url()
    internal_key = _get_internal_key()

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{api_base_url}/api/v1/workflows/autonomous-learning/stop",
                headers={"X-Internal-Key": internal_key},
                params={"tenant_id": tid},
            )
            data = resp.json()
            if resp.status_code == 200:
                status = data.get("status", "unknown")
                if status == "not_running":
                    return {"message": "Autonomous learning was not running.", **data}
                return {"message": "Autonomous learning stopped.", **data}
            return {"error": data.get("detail", "Failed to stop autonomous learning.")}
    except Exception as e:
        logger.exception("stop_autonomous_learning failed")
        return {"error": str(e)}


@mcp.tool()
async def check_autonomous_learning_status(
    tenant_id: str = "",
    ctx: Context = None,
) -> dict:
    """Check if the autonomous learning workflow is running and get its status.

    Args:
        tenant_id: Tenant UUID (resolved from session if omitted).
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with running status, workflow_id, and last start time.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"error": "tenant_id is required."}

    api_base_url = _get_api_base_url()
    internal_key = _get_internal_key()

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{api_base_url}/api/v1/workflows/autonomous-learning/status",
                headers={"X-Internal-Key": internal_key},
                params={"tenant_id": tid},
            )
            if resp.status_code == 200:
                return resp.json()
            return {"running": False, "message": "Autonomous learning is not active."}
    except Exception as e:
        logger.exception("check_autonomous_learning_status failed")
        return {"error": str(e)}
