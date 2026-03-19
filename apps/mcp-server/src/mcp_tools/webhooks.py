"""Universal webhook connector MCP tools.

Allows agents to register, manage, test, and fire webhooks for any
external service — both inbound (receive events) and outbound (send events).
"""
import json
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


def _headers(tenant_id: str) -> dict:
    return {
        "X-Internal-Key": _get_internal_key(),
        "X-Tenant-Id": tenant_id,
        "Content-Type": "application/json",
    }


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def register_webhook(
    name: str,
    direction: str,
    events: str,
    target_url: str = "",
    auth_type: str = "none",
    secret: str = "",
    headers: str = "",
    payload_transform: str = "",
    description: str = "",
    tenant_id: str = "",
    ctx: Context = None,
) -> dict:
    """Register a new universal webhook connector.

    Supports both inbound (receive events from external services) and
    outbound (send events to external URLs when things happen).

    Args:
        name: Human-readable name for the webhook (e.g. "Stripe payments", "Slack notifications").
        direction: "inbound" (receive) or "outbound" (send).
        events: Comma-separated event types to subscribe to. Examples:
            "entity.created,lead.scored" or "*" for all events.
            Standard events: entity.created, entity.updated, entity.deleted,
            relation.created, lead.scored, notification.created, chat.message.received,
            workflow.completed, workflow.failed, webhook.test.
        target_url: Destination URL for outbound webhooks. Required when direction="outbound".
        auth_type: Authentication method. "none", "hmac_sha256", "bearer", or "basic".
        secret: HMAC secret key, bearer token, or basic auth credentials.
        headers: Optional JSON string of custom HTTP headers for outbound requests.
            Example: '{"X-Api-Key": "abc123"}'
        payload_transform: Optional JSON string mapping output fields to input paths.
            Example: '{"customer_name": "$.data.name"}'
        description: Optional description of what this webhook does.
        tenant_id: Tenant UUID (resolved from session if omitted).
        ctx: MCP request context (injected automatically).

    Returns:
        Created webhook details including slug (for inbound) or target_url (for outbound).
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"error": "tenant_id is required."}

    if direction not in ("inbound", "outbound"):
        return {"error": "direction must be 'inbound' or 'outbound'"}
    if direction == "outbound" and not target_url:
        return {"error": "target_url is required for outbound webhooks"}

    event_list = [e.strip() for e in events.split(",") if e.strip()]
    if not event_list:
        return {"error": "At least one event type is required"}

    payload = {
        "name": name,
        "direction": direction,
        "events": event_list,
        "auth_type": auth_type,
        "enabled": True,
    }
    if target_url:
        payload["target_url"] = target_url
    if secret:
        payload["secret"] = secret
    if description:
        payload["description"] = description
    if headers:
        try:
            payload["headers"] = json.loads(headers)
        except json.JSONDecodeError:
            return {"error": "headers must be valid JSON"}
    if payload_transform:
        try:
            payload["payload_transform"] = json.loads(payload_transform)
        except json.JSONDecodeError:
            return {"error": "payload_transform must be valid JSON"}

    api_base = _get_api_base_url()
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{api_base}/api/v1/webhook-connectors/internal/create",
                headers=_headers(tid),
                params={"tenant_id": tid},
                json=payload,
            )
            if resp.status_code in (200, 201):
                data = resp.json()
                result = {
                    "status": "created",
                    "webhook_id": data.get("id"),
                    "name": data.get("name"),
                    "direction": data.get("direction"),
                    "events": data.get("events"),
                }
                if data.get("slug"):
                    result["inbound_url"] = f"/api/v1/webhook-connectors/in/{data['slug']}"
                    result["slug"] = data["slug"]
                if data.get("target_url"):
                    result["target_url"] = data["target_url"]
                return result
            return {"error": f"Failed to create webhook: {resp.status_code} - {resp.text[:300]}"}
    except Exception as e:
        logger.exception("register_webhook failed")
        return {"error": str(e)}


@mcp.tool()
async def list_webhooks(
    direction: str = "",
    tenant_id: str = "",
    ctx: Context = None,
) -> dict:
    """List all registered webhook connectors.

    Args:
        direction: Filter by direction — "inbound", "outbound", or empty for all.
        tenant_id: Tenant UUID (resolved from session if omitted).
        ctx: MCP request context (injected automatically).

    Returns:
        List of webhook connectors with their status, events, and trigger counts.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"error": "tenant_id is required."}

    api_base = _get_api_base_url()
    params = {"tenant_id": tid}
    if direction:
        params["direction"] = direction

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{api_base}/api/v1/webhook-connectors/internal/list",
                headers=_headers(tid),
                params=params,
            )
            if resp.status_code == 200:
                webhooks = resp.json()
                return {
                    "count": len(webhooks),
                    "webhooks": [
                        {
                            "id": w["id"],
                            "name": w["name"],
                            "direction": w["direction"],
                            "events": w["events"],
                            "enabled": w["enabled"],
                            "status": w["status"],
                            "trigger_count": w.get("trigger_count", 0),
                            "slug": w.get("slug"),
                            "target_url": w.get("target_url"),
                        }
                        for w in webhooks
                    ],
                }
            return {"error": f"Failed to list webhooks: {resp.status_code}"}
    except Exception as e:
        logger.exception("list_webhooks failed")
        return {"error": str(e)}


@mcp.tool()
async def delete_webhook(
    webhook_id: str,
    tenant_id: str = "",
    ctx: Context = None,
) -> dict:
    """Delete a webhook connector and all its delivery logs.

    Args:
        webhook_id: UUID of the webhook to delete.
        tenant_id: Tenant UUID (resolved from session if omitted).
        ctx: MCP request context (injected automatically).

    Returns:
        Confirmation of deletion.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"error": "tenant_id is required."}

    api_base = _get_api_base_url()
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.delete(
                f"{api_base}/api/v1/webhook-connectors/internal/{webhook_id}",
                headers=_headers(tid),
                params={"tenant_id": tid},
            )
            if resp.status_code == 200:
                return {"status": "deleted", "webhook_id": webhook_id}
            if resp.status_code == 404:
                return {"error": "Webhook not found"}
            return {"error": f"Failed to delete webhook: {resp.status_code}"}
    except Exception as e:
        logger.exception("delete_webhook failed")
        return {"error": str(e)}


@mcp.tool()
async def test_webhook(
    webhook_id: str,
    test_payload: str = "",
    event_type: str = "webhook.test",
    tenant_id: str = "",
    ctx: Context = None,
) -> dict:
    """Send a test event to a webhook to verify it works.

    For outbound webhooks, sends a test POST to the target URL.
    For inbound webhooks, returns a curl command to test with.

    Args:
        webhook_id: UUID of the webhook to test.
        test_payload: Optional JSON string payload. Default: {"test": true}.
        event_type: Event type for the test. Default: "webhook.test".
        tenant_id: Tenant UUID (resolved from session if omitted).
        ctx: MCP request context (injected automatically).

    Returns:
        Test result with delivery status or curl command.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"error": "tenant_id is required."}

    payload = {}
    if test_payload:
        try:
            payload = json.loads(test_payload)
        except json.JSONDecodeError:
            return {"error": "test_payload must be valid JSON"}

    api_base = _get_api_base_url()
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{api_base}/api/v1/webhook-connectors/internal/{webhook_id}/test",
                headers=_headers(tid),
                params={"tenant_id": tid},
                json={"payload": payload, "event_type": event_type},
            )
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 404:
                return {"error": "Webhook not found"}
            return {"error": f"Test failed: {resp.status_code} - {resp.text[:300]}"}
    except Exception as e:
        logger.exception("test_webhook failed")
        return {"error": str(e)}


@mcp.tool()
async def send_webhook_event(
    event_type: str,
    payload: str,
    tenant_id: str = "",
    ctx: Context = None,
) -> dict:
    """Fire an outbound webhook event to all subscribers matching the event type.

    Use this to programmatically trigger events from agent actions.

    Args:
        event_type: Event type to fire (e.g. "entity.created", "lead.scored", "custom.event").
        payload: JSON string with the event data to send.
        tenant_id: Tenant UUID (resolved from session if omitted).
        ctx: MCP request context (injected automatically).

    Returns:
        Number of webhooks notified and delivery results.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"error": "tenant_id is required."}

    try:
        payload_dict = json.loads(payload)
    except json.JSONDecodeError:
        return {"error": "payload must be valid JSON"}

    api_base = _get_api_base_url()
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{api_base}/api/v1/webhook-connectors/internal/fire",
                headers=_headers(tid),
                params={"tenant_id": tid},
                json={"event_type": event_type, "payload": payload_dict},
            )
            if resp.status_code == 200:
                return resp.json()
            return {"error": f"Failed to fire event: {resp.status_code} - {resp.text[:300]}"}
    except Exception as e:
        logger.exception("send_webhook_event failed")
        return {"error": str(e)}


@mcp.tool()
async def get_webhook_logs(
    webhook_id: str = "",
    limit: int = 20,
    tenant_id: str = "",
    ctx: Context = None,
) -> dict:
    """Get recent delivery logs for webhook connectors.

    Shows delivery attempts, success/failure status, response codes, and timing.

    Args:
        webhook_id: Optional UUID to filter logs for a specific webhook.
        limit: Maximum number of log entries to return (default 20).
        tenant_id: Tenant UUID (resolved from session if omitted).
        ctx: MCP request context (injected automatically).

    Returns:
        List of delivery log entries with status, timing, and error details.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"error": "tenant_id is required."}

    api_base = _get_api_base_url()
    params = {"tenant_id": tid, "limit": limit}

    endpoint = f"{api_base}/api/v1/webhook-connectors/internal/logs"
    if webhook_id:
        endpoint = f"{api_base}/api/v1/webhook-connectors/internal/{webhook_id}/logs"

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(endpoint, headers=_headers(tid), params=params)
            if resp.status_code == 200:
                logs = resp.json()
                return {
                    "count": len(logs),
                    "logs": [
                        {
                            "id": l["id"],
                            "direction": l["direction"],
                            "event_type": l["event_type"],
                            "success": l["success"],
                            "response_status": l.get("response_status"),
                            "error_message": l.get("error_message"),
                            "duration_ms": l.get("duration_ms"),
                            "created_at": l.get("created_at"),
                        }
                        for l in logs
                    ],
                }
            return {"error": f"Failed to fetch logs: {resp.status_code}"}
    except Exception as e:
        logger.exception("get_webhook_logs failed")
        return {"error": str(e)}
