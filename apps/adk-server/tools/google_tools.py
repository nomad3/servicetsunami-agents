"""Gmail and Google Calendar tools for the personal assistant.

Uses stored OAuth tokens (via credential vault) to call Google APIs
on behalf of the authenticated user.
"""
import base64
import logging
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from typing import Optional

import httpx

from config.settings import settings
from tools.knowledge_tools import _resolve_tenant_id

logger = logging.getLogger(__name__)

_api_client: Optional[httpx.AsyncClient] = None
_google_client: Optional[httpx.AsyncClient] = None


def _get_api_client() -> httpx.AsyncClient:
    global _api_client
    if _api_client is None:
        _api_client = httpx.AsyncClient(
            base_url=settings.api_base_url,
            timeout=30.0,
        )
    return _api_client


def _get_google_client() -> httpx.AsyncClient:
    global _google_client
    if _google_client is None:
        _google_client = httpx.AsyncClient(timeout=30.0)
    return _google_client


async def _get_google_token(tenant_id: str, integration_name: str) -> Optional[str]:
    """Retrieve decrypted OAuth access token from the API credential vault."""
    client = _get_api_client()
    try:
        resp = await client.get(
            f"/api/v1/oauth/internal/token/{integration_name}",
            headers={"X-Internal-Key": settings.mcp_api_key},
            params={"tenant_id": tenant_id},
        )
        if resp.status_code == 200:
            return resp.json().get("oauth_token")
        logger.warning("Token retrieval for %s returned %s", integration_name, resp.status_code)
    except Exception:
        logger.exception("Failed to retrieve %s token", integration_name)
    return None


# ---------------------------------------------------------------------------
# Gmail tools
# ---------------------------------------------------------------------------

async def search_emails(
    tenant_id: str = "auto",
    query: str = "",
    max_results: int = 10,
) -> dict:
    """Search Gmail for emails matching a query.

    Args:
        tenant_id: Tenant context. Use "auto" if unknown.
        query: Gmail search query (e.g. "from:alice@example.com", "subject:invoice",
               "is:unread", "newer_than:2d"). Leave empty for recent inbox messages.
        max_results: Maximum number of emails to return (1-20).

    Returns:
        Dict with list of email summaries (subject, from, date, snippet).
    """
    tenant_id = _resolve_tenant_id(tenant_id)
    token = await _get_google_token(tenant_id, "gmail")
    if not token:
        return {"error": "Gmail not connected. Ask the user to connect Gmail in Connected Apps."}

    google = _get_google_client()
    auth = {"Authorization": f"Bearer {token}"}

    try:
        # List message IDs
        params = {"maxResults": min(max_results, 20)}
        if query:
            params["q"] = query
        resp = await google.get(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages",
            headers=auth,
            params=params,
        )
        resp.raise_for_status()
        data = resp.json()
        messages = data.get("messages", [])

        if not messages:
            return {"status": "success", "emails": [], "message": "No emails found."}

        # Fetch details for each message
        emails = []
        for msg in messages:
            detail = await google.get(
                f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg['id']}",
                headers=auth,
                params=[
                    ("format", "metadata"),
                    ("metadataHeaders", "Subject"),
                    ("metadataHeaders", "From"),
                    ("metadataHeaders", "Date"),
                ],
            )
            if detail.status_code != 200:
                continue
            md = detail.json()
            headers = {h["name"]: h["value"] for h in md.get("payload", {}).get("headers", [])}
            emails.append({
                "id": msg["id"],
                "subject": headers.get("Subject", "(no subject)"),
                "from": headers.get("From", ""),
                "date": headers.get("Date", ""),
                "snippet": md.get("snippet", ""),
            })

        return {"status": "success", "emails": emails, "total": data.get("resultSizeEstimate", len(emails))}

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            return {"error": "Gmail token expired. Ask user to reconnect Gmail in Connected Apps."}
        return {"error": f"Gmail API error: {e.response.status_code}"}
    except Exception as e:
        logger.exception("search_emails failed")
        return {"error": f"Failed to search emails: {str(e)}"}


async def read_email(
    tenant_id: str = "auto",
    message_id: str = "",
) -> dict:
    """Read the full content of a specific email by its message ID.

    Args:
        tenant_id: Tenant context. Use "auto" if unknown.
        message_id: Gmail message ID (from search_emails results).

    Returns:
        Dict with email subject, from, to, date, and body text.
    """
    tenant_id = _resolve_tenant_id(tenant_id)
    if not message_id:
        return {"error": "message_id is required. Use search_emails first to get message IDs."}

    token = await _get_google_token(tenant_id, "gmail")
    if not token:
        return {"error": "Gmail not connected. Ask the user to connect Gmail in Connected Apps."}

    google = _get_google_client()
    auth = {"Authorization": f"Bearer {token}"}

    try:
        resp = await google.get(
            f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}",
            headers=auth,
            params={"format": "full"},
        )
        resp.raise_for_status()
        msg = resp.json()

        headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}

        # Extract body text
        body = _extract_body(msg.get("payload", {}))

        return {
            "status": "success",
            "id": message_id,
            "subject": headers.get("Subject", "(no subject)"),
            "from": headers.get("From", ""),
            "to": headers.get("To", ""),
            "date": headers.get("Date", ""),
            "body": body[:5000],  # Limit body size
            "labels": msg.get("labelIds", []),
        }

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            return {"error": "Gmail token expired. Ask user to reconnect Gmail."}
        return {"error": f"Gmail API error: {e.response.status_code}"}
    except Exception as e:
        logger.exception("read_email failed")
        return {"error": f"Failed to read email: {str(e)}"}


def _extract_body(payload: dict) -> str:
    """Recursively extract plain text body from Gmail message payload."""
    mime = payload.get("mimeType", "")

    if mime == "text/plain" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")

    for part in payload.get("parts", []):
        text = _extract_body(part)
        if text:
            return text

    # Fallback: try HTML
    if mime == "text/html" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")

    return ""


async def send_email(
    tenant_id: str = "auto",
    to: str = "",
    subject: str = "",
    body: str = "",
) -> dict:
    """Send an email via Gmail.

    Args:
        tenant_id: Tenant context. Use "auto" if unknown.
        to: Recipient email address.
        subject: Email subject line.
        body: Email body text (plain text).

    Returns:
        Dict with send status and message ID.
    """
    tenant_id = _resolve_tenant_id(tenant_id)
    if not to or not subject:
        return {"error": "Both 'to' and 'subject' are required."}

    token = await _get_google_token(tenant_id, "gmail")
    if not token:
        return {"error": "Gmail not connected. Ask the user to connect Gmail in Connected Apps."}

    google = _get_google_client()
    auth = {"Authorization": f"Bearer {token}"}

    try:
        message = MIMEText(body)
        message["to"] = to
        message["subject"] = subject
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")

        resp = await google.post(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
            headers=auth,
            json={"raw": raw},
        )
        resp.raise_for_status()
        result = resp.json()

        return {
            "status": "success",
            "message_id": result.get("id"),
            "message": f"Email sent to {to}.",
        }

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            return {"error": "Gmail token expired. Ask user to reconnect Gmail."}
        return {"error": f"Gmail send failed: {e.response.status_code}"}
    except Exception as e:
        logger.exception("send_email failed")
        return {"error": f"Failed to send email: {str(e)}"}


# ---------------------------------------------------------------------------
# Google Calendar tools
# ---------------------------------------------------------------------------

async def list_calendar_events(
    tenant_id: str = "auto",
    days_ahead: int = 7,
    max_results: int = 20,
) -> dict:
    """List upcoming Google Calendar events.

    Args:
        tenant_id: Tenant context. Use "auto" if unknown.
        days_ahead: Number of days to look ahead (1-30).
        max_results: Maximum events to return (1-50).

    Returns:
        Dict with list of calendar events (summary, start, end, location).
    """
    tenant_id = _resolve_tenant_id(tenant_id)
    token = await _get_google_token(tenant_id, "google_calendar")
    if not token:
        return {"error": "Google Calendar not connected. Ask user to connect Google in Connected Apps."}

    google = _get_google_client()
    auth = {"Authorization": f"Bearer {token}"}

    now = datetime.now(timezone.utc)
    time_min = now.isoformat()
    time_max = (now + timedelta(days=min(days_ahead, 30))).isoformat()

    try:
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


async def create_calendar_event(
    tenant_id: str = "auto",
    summary: str = "",
    start_time: str = "",
    end_time: str = "",
    description: str = "",
    attendees: str = "",
) -> dict:
    """Create a new Google Calendar event.

    Args:
        tenant_id: Tenant context. Use "auto" if unknown.
        summary: Event title/name.
        start_time: Start time in ISO 8601 format (e.g. "2026-03-15T10:00:00-05:00").
        end_time: End time in ISO 8601 format (e.g. "2026-03-15T11:00:00-05:00").
        description: Optional event description.
        attendees: Optional comma-separated list of attendee emails.

    Returns:
        Dict with created event details.
    """
    tenant_id = _resolve_tenant_id(tenant_id)
    if not summary or not start_time or not end_time:
        return {"error": "summary, start_time, and end_time are required."}

    token = await _get_google_token(tenant_id, "google_calendar")
    if not token:
        return {"error": "Google Calendar not connected. Ask user to connect Google in Connected Apps."}

    google = _get_google_client()
    auth = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    event_body = {
        "summary": summary,
        "start": {"dateTime": start_time},
        "end": {"dateTime": end_time},
    }
    if description:
        event_body["description"] = description
    if attendees:
        event_body["attendees"] = [{"email": e.strip()} for e in attendees.split(",") if e.strip()]

    try:
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
