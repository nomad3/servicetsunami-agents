"""Google Calendar MCP tools.

Google Calendar event management tools.
Uses httpx.AsyncClient to call Google Calendar API via stored OAuth tokens fetched from
the internal credential vault endpoint.
"""
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from mcp.server.fastmcp import Context

from src.mcp_app import mcp
from src.mcp_auth import resolve_tenant_id

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _api_base_url() -> str:
    return os.environ.get("API_BASE_URL", "http://api:8000")


def _mcp_api_key() -> str:
    return os.environ.get("MCP_API_KEY", "dev_mcp_key")


# ---------------------------------------------------------------------------
# OAuth helper
# ---------------------------------------------------------------------------

async def _get_oauth_token(
    tenant_id: str, integration_name: str, account_email: Optional[str] = None,
) -> Optional[str]:
    """Retrieve decrypted OAuth access token from the API credential vault."""
    params: dict = {"tenant_id": tenant_id}
    if account_email:
        params["account_email"] = account_email
    try:
        async with httpx.AsyncClient(base_url=_api_base_url(), timeout=30.0) as client:
            resp = await client.get(
                f"/api/v1/oauth/internal/token/{integration_name}",
                headers={"X-Internal-Key": _mcp_api_key()},
                params=params,
            )
            if resp.status_code == 200:
                return resp.json().get("oauth_token")
            logger.warning("Token retrieval for %s returned %s", integration_name, resp.status_code)
    except Exception:
        logger.exception("Failed to retrieve %s token", integration_name)
    return None


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def list_calendar_events(
    tenant_id: str = "",
    days_ahead: int = 7,
    max_results: int = 20,
    account_email: str = "",
    ctx: Context = None,
) -> dict:
    """List upcoming Google Calendar events.

    Args:
        tenant_id: Tenant UUID (resolved from session if omitted).
        days_ahead: Number of days to look ahead (1-30, default 7).
        max_results: Maximum events to return (1-50, default 20).
        account_email: Specific Google account for calendar. If empty, uses default.
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with list of calendar events (summary, start, end, location, attendees).
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    token = await _get_oauth_token(tid, "google_calendar", account_email or None)
    if not token:
        return {"error": "Google Calendar not connected. Ask user to connect Google in Connected Apps."}

    auth = {"Authorization": f"Bearer {token}"}

    now = datetime.now(timezone.utc)
    time_min = now.isoformat()
    time_max = (now + timedelta(days=min(days_ahead, 30))).isoformat()

    try:
        async with httpx.AsyncClient(timeout=30.0) as google:
            resp = await google.get(
                "https://www.googleapis.com/calendar/v3/calendars/primary/events",
                headers=auth,
                params={
                    "timeMin": time_min,
                    "timeMax": time_max,
                    "maxResults": min(max_results, 50),
                    "singleEvents": "true",
                    "orderBy": "startTime",
                },
            )
            resp.raise_for_status()
            data = resp.json()

            events = []
            for item in data.get("items", []):
                start = item.get("start", {})
                end = item.get("end", {})
                events.append({
                    "id": item.get("id"),
                    "summary": item.get("summary", "(no title)"),
                    "start": start.get("dateTime", start.get("date", "")),
                    "end": end.get("dateTime", end.get("date", "")),
                    "location": item.get("location", ""),
                    "description": (item.get("description", "") or "")[:200],
                    "attendees": [a.get("email", "") for a in item.get("attendees", [])][:10],
                })

            return {"status": "success", "events": events, "count": len(events)}

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            return {"error": "Calendar token expired. Ask user to reconnect Google in Connected Apps."}
        return {"error": f"Calendar API error: {e.response.status_code}"}
    except Exception as e:
        logger.exception("list_calendar_events failed")
        return {"error": f"Failed to list events: {str(e)}"}


@mcp.tool()
async def create_calendar_event(
    summary: str,
    start_time: str,
    end_time: str,
    tenant_id: str = "",
    description: str = "",
    attendees: str = "",
    account_email: str = "",
    ctx: Context = None,
) -> dict:
    """Create a new Google Calendar event.

    Args:
        summary: Event title/name.
        start_time: Start time in ISO 8601 format e.g. '2026-03-15T10:00:00-05:00'.
        end_time: End time in ISO 8601 format e.g. '2026-03-15T11:00:00-05:00'.
        tenant_id: Tenant UUID (resolved from session if omitted).
        description: Optional event description.
        attendees: Optional comma-separated list of attendee emails.
        account_email: Specific Google account for calendar. If empty, uses default.
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with created event details (event_id, summary, start, end, link).
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not summary or not start_time or not end_time:
        return {"error": "summary, start_time, and end_time are required."}

    token = await _get_oauth_token(tid, "google_calendar", account_email or None)
    if not token:
        return {"error": "Google Calendar not connected. Ask user to connect Google in Connected Apps."}

    auth = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    event_body: dict = {
        "summary": summary,
        "start": {"dateTime": start_time},
        "end": {"dateTime": end_time},
    }
    if description:
        event_body["description"] = description
    if attendees:
        event_body["attendees"] = [{"email": e.strip()} for e in attendees.split(",") if e.strip()]

    try:
        async with httpx.AsyncClient(timeout=30.0) as google:
            resp = await google.post(
                "https://www.googleapis.com/calendar/v3/calendars/primary/events",
                headers=auth,
                json=event_body,
            )
            resp.raise_for_status()
            created = resp.json()

            return {
                "status": "success",
                "event_id": created.get("id"),
                "summary": created.get("summary"),
                "start": created.get("start", {}).get("dateTime", ""),
                "end": created.get("end", {}).get("dateTime", ""),
                "link": created.get("htmlLink", ""),
                "message": f"Event '{summary}' created.",
            }

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            return {"error": "Calendar token expired. Ask user to reconnect Google."}
        return {"error": f"Calendar create failed: {e.response.status_code}"}
    except Exception as e:
        logger.exception("create_calendar_event failed")
        return {"error": f"Failed to create event: {str(e)}"}
