"""Inbox monitor control tools for Luna.

Allows Luna to start, stop, and check the status of the proactive
inbox monitor via the API's Temporal workflow endpoints.
"""
import logging
from typing import Optional

import httpx

from config.settings import settings
from tools.knowledge_tools import _resolve_tenant_id

logger = logging.getLogger(__name__)

_api_client: Optional[httpx.AsyncClient] = None


def _get_api_client() -> httpx.AsyncClient:
    global _api_client
    if _api_client is None:
        _api_client = httpx.AsyncClient(
            base_url=settings.api_base_url,
            timeout=30.0,
        )
    return _api_client


async def start_inbox_monitor(
    tenant_id: str = "auto",
    interval_minutes: int = 15,
) -> dict:
    """Start proactive monitoring of the user's Gmail and Calendar.

    Luna will check for new emails and upcoming events every N minutes,
    create notifications for important items, and extract entities from
    significant emails.

    Args:
        tenant_id: Tenant context. Use "auto" if unknown.
        interval_minutes: How often to check (5-60 minutes, default 15).

    Returns:
        Dict with monitoring status.
    """
    tenant_id = _resolve_tenant_id(tenant_id)
    client = _get_api_client()

    try:
        resp = await client.post(
            "/api/v1/workflows/inbox-monitor/start",
            headers={"X-Internal-Key": settings.mcp_api_key},
            params={
                "tenant_id": tenant_id,
                "check_interval_minutes": interval_minutes,
            },
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("status") == "already_running":
                return {"status": "already_active", "message": "Inbox monitoring is already active."}
            return {
                "status": "started",
                "message": f"I'll now monitor your inbox every {interval_minutes} minutes and notify you of important items.",
                "interval_minutes": interval_minutes,
            }
        return {"error": f"Failed to start monitor: {resp.status_code}"}
    except Exception as e:
        logger.exception("start_inbox_monitor failed")
        return {"error": f"Failed to start monitoring: {str(e)}"}


async def stop_inbox_monitor(
    tenant_id: str = "auto",
) -> dict:
    """Stop proactive monitoring of the user's Gmail and Calendar.

    Args:
        tenant_id: Tenant context. Use "auto" if unknown.

    Returns:
        Dict with status.
    """
    tenant_id = _resolve_tenant_id(tenant_id)
    client = _get_api_client()

    try:
        resp = await client.post(
            "/api/v1/workflows/inbox-monitor/stop",
            headers={"X-Internal-Key": settings.mcp_api_key},
            params={"tenant_id": tenant_id},
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


async def check_inbox_monitor_status(
    tenant_id: str = "auto",
) -> dict:
    """Check if proactive inbox monitoring is currently active.

    Args:
        tenant_id: Tenant context. Use "auto" if unknown.

    Returns:
        Dict with monitoring status and details.
    """
    tenant_id = _resolve_tenant_id(tenant_id)
    client = _get_api_client()

    try:
        resp = await client.get(
            "/api/v1/workflows/inbox-monitor/status",
            headers={"X-Internal-Key": settings.mcp_api_key},
            params={"tenant_id": tenant_id},
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("running"):
                return {
                    "status": "active",
                    "message": "Inbox monitoring is active. I'm checking your email and calendar periodically.",
                    "since": data.get("start_time"),
                }
            return {"status": "inactive", "message": "Inbox monitoring is not active."}
        return {"error": f"Status check failed: {resp.status_code}"}
    except Exception as e:
        logger.exception("check_inbox_monitor_status failed")
        return {"error": f"Failed to check status: {str(e)}"}
