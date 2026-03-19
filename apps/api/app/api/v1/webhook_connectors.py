"""Webhook connector API routes — CRUD, inbound receiver, and outbound fire.

Route ordering: Static and prefixed paths (/fire, /internal/*, /in/*) are
registered BEFORE parameterised paths (/{webhook_id}) so that FastAPI never
tries to parse literal segments like "fire" or "internal" as a UUID.
"""
import json
import logging
import time
from typing import Optional, List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api import deps
from app.models.user import User
from app.schemas.webhook_connector import (
    WebhookConnectorCreate,
    WebhookConnectorUpdate,
    WebhookConnectorInDB,
    WebhookDeliveryLogInDB,
    WebhookTestRequest,
    WebhookFireRequest,
)
from app.services import webhook_connectors as svc

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# List + Create (no path param — safe anywhere)
# ---------------------------------------------------------------------------

@router.get("", response_model=List[WebhookConnectorInDB])
def list_webhook_connectors(
    direction: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """List all webhook connectors for the current tenant."""
    return svc.list_webhooks(db, current_user.tenant_id, direction=direction, skip=skip, limit=limit)


@router.post("", response_model=WebhookConnectorInDB, status_code=201)
def create_webhook_connector(
    item_in: WebhookConnectorCreate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Create a new webhook connector."""
    if item_in.direction == "outbound" and not item_in.target_url:
        raise HTTPException(status_code=400, detail="target_url is required for outbound webhooks")
    webhook = svc.create_webhook(
        db,
        tenant_id=current_user.tenant_id,
        name=item_in.name,
        direction=item_in.direction,
        events=item_in.events,
        target_url=item_in.target_url,
        headers=item_in.headers,
        auth_type=item_in.auth_type,
        secret=item_in.secret,
        payload_transform=item_in.payload_transform,
        description=item_in.description,
        enabled=item_in.enabled,
    )
    return webhook


# ---------------------------------------------------------------------------
# Fire outbound events (authenticated)
# ---------------------------------------------------------------------------

@router.post("/fire")
def fire_webhook_event(
    body: WebhookFireRequest,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Fire an outbound event to all matching webhooks."""
    results = svc.fire_outbound_event(db, current_user.tenant_id, body.event_type, body.payload)
    return {
        "event_type": body.event_type,
        "webhooks_notified": len(results),
        "results": results,
    }


# ---------------------------------------------------------------------------
# Internal endpoints (for MCP tools / service-to-service, no JWT)
# ---------------------------------------------------------------------------

@router.post("/internal/fire")
def fire_webhook_event_internal(
    body: WebhookFireRequest,
    tenant_id: str = "",
    db: Session = Depends(deps.get_db),
):
    """Fire an outbound event (internal, no JWT required — uses X-Internal-Key)."""
    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id query param required")
    results = svc.fire_outbound_event(db, tenant_id, body.event_type, body.payload)
    return {
        "event_type": body.event_type,
        "webhooks_notified": len(results),
        "results": results,
    }


@router.post("/internal/create")
def create_webhook_internal(
    item_in: WebhookConnectorCreate,
    tenant_id: str = "",
    db: Session = Depends(deps.get_db),
):
    """Create webhook (internal, for MCP tools)."""
    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id query param required")
    if item_in.direction == "outbound" and not item_in.target_url:
        raise HTTPException(status_code=400, detail="target_url is required for outbound webhooks")
    webhook = svc.create_webhook(
        db, tenant_id=tenant_id, name=item_in.name, direction=item_in.direction,
        events=item_in.events, target_url=item_in.target_url, headers=item_in.headers,
        auth_type=item_in.auth_type, secret=item_in.secret,
        payload_transform=item_in.payload_transform, description=item_in.description,
        enabled=item_in.enabled,
    )
    return WebhookConnectorInDB.model_validate(webhook).model_dump(mode="json")


@router.get("/internal/list")
def list_webhooks_internal(
    tenant_id: str = "",
    direction: Optional[str] = None,
    db: Session = Depends(deps.get_db),
):
    """List webhooks (internal, for MCP tools)."""
    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id query param required")
    webhooks = svc.list_webhooks(db, tenant_id, direction=direction)
    return [WebhookConnectorInDB.model_validate(w).model_dump(mode="json") for w in webhooks]


@router.get("/internal/logs")
def get_all_logs_internal(
    tenant_id: str = "",
    limit: int = 50,
    db: Session = Depends(deps.get_db),
):
    """Get all webhook delivery logs (internal, for MCP tools)."""
    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id query param required")
    logs = svc.get_delivery_logs(db, tenant_id, limit=limit)
    return [WebhookDeliveryLogInDB.model_validate(l).model_dump(mode="json") for l in logs]


@router.delete("/internal/{webhook_id}")
def delete_webhook_internal(
    webhook_id: UUID,
    tenant_id: str = "",
    db: Session = Depends(deps.get_db),
):
    """Delete webhook (internal, for MCP tools)."""
    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id query param required")
    if not svc.delete_webhook(db, tenant_id, webhook_id):
        raise HTTPException(status_code=404, detail="Webhook not found")
    return {"status": "deleted", "webhook_id": str(webhook_id)}


@router.post("/internal/{webhook_id}/test")
def test_webhook_internal(
    webhook_id: UUID,
    body: WebhookTestRequest,
    tenant_id: str = "",
    db: Session = Depends(deps.get_db),
):
    """Test webhook (internal, for MCP tools)."""
    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id query param required")
    webhook = svc.get_webhook(db, tenant_id, webhook_id)
    if not webhook:
        raise HTTPException(status_code=404, detail="Webhook not found")
    if webhook.direction == "inbound":
        from app.core.config import settings
        base = getattr(settings, "BASE_URL", "https://servicetsunami.com")
        url = f"{base}/api/v1/webhook-connectors/in/{webhook.slug}"
        curl = f'curl -X POST "{url}" -H "Content-Type: application/json"'
        if webhook.auth_type == "hmac_sha256":
            curl += ' -H "X-Webhook-Signature: <compute-hmac>"'
        curl += f" -d '{json.dumps(body.payload or {})}'"
        return {"direction": "inbound", "test_curl": curl, "slug": webhook.slug}
    test_payload = body.payload or {"test": True, "timestamp": time.time()}
    result = svc._deliver_outbound(db, webhook, body.event_type, test_payload)
    return {"direction": "outbound", "result": result}


@router.get("/internal/{webhook_id}/logs")
def get_webhook_logs_internal(
    webhook_id: UUID,
    tenant_id: str = "",
    limit: int = 50,
    db: Session = Depends(deps.get_db),
):
    """Get webhook logs (internal, for MCP tools)."""
    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id query param required")
    logs = svc.get_delivery_logs(db, tenant_id, webhook_id=webhook_id, limit=limit)
    return [WebhookDeliveryLogInDB.model_validate(l).model_dump(mode="json") for l in logs]


# ---------------------------------------------------------------------------
# Public inbound receiver (unauthenticated, secured by slug + HMAC)
# ---------------------------------------------------------------------------

@router.post("/in/{slug}")
async def receive_inbound_webhook(
    slug: str,
    request: Request,
    db: Session = Depends(deps.get_db),
):
    """Receive an inbound webhook event from an external service.

    URL: POST /api/v1/webhook-connectors/in/{slug}
    The slug is a unique, unguessable token generated when the inbound webhook is created.
    """
    webhook = svc.get_webhook_by_slug(db, slug)
    if not webhook:
        raise HTTPException(status_code=404, detail="Webhook not found")

    raw_body = await request.body()

    # HMAC verification
    if webhook.auth_type == "hmac_sha256" and webhook.secret:
        signature = request.headers.get("X-Webhook-Signature", "")
        if not svc.verify_hmac_signature(webhook.secret, raw_body, signature):
            svc.log_delivery(
                db, webhook.tenant_id, webhook.id, "inbound", "auth_failed",
                success=False, error_message="HMAC signature verification failed",
            )
            raise HTTPException(status_code=401, detail="Invalid webhook signature")

    try:
        body = json.loads(raw_body) if raw_body else {}
    except (json.JSONDecodeError, ValueError):
        body = {"raw": raw_body.decode("utf-8", errors="replace")}

    event_type = (
        request.headers.get("X-Webhook-Event")
        or body.get("event")
        or body.get("type")
        or "unknown"
    )

    # Log successful delivery
    svc.log_delivery(
        db, webhook.tenant_id, webhook.id, "inbound", event_type,
        payload=body, success=True, response_status=200,
    )

    # Update counters
    from datetime import datetime
    webhook.trigger_count = (webhook.trigger_count or 0) + 1
    webhook.last_triggered_at = datetime.utcnow()
    db.commit()

    logger.info("Inbound webhook received: slug=%s event=%s tenant=%s", slug, event_type, webhook.tenant_id)

    return {"status": "received", "event": event_type, "webhook_id": str(webhook.id)}


# ---------------------------------------------------------------------------
# Authenticated CRUD (parameterised /{webhook_id} — MUST come after static paths)
# ---------------------------------------------------------------------------

@router.get("/{webhook_id}", response_model=WebhookConnectorInDB)
def get_webhook_connector(
    webhook_id: UUID,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Get a single webhook connector by ID."""
    webhook = svc.get_webhook(db, current_user.tenant_id, webhook_id)
    if not webhook:
        raise HTTPException(status_code=404, detail="Webhook connector not found")
    return webhook


@router.put("/{webhook_id}", response_model=WebhookConnectorInDB)
def update_webhook_connector(
    webhook_id: UUID,
    item_in: WebhookConnectorUpdate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Update a webhook connector."""
    updates = item_in.model_dump(exclude_unset=True)
    webhook = svc.update_webhook(db, current_user.tenant_id, webhook_id, updates)
    if not webhook:
        raise HTTPException(status_code=404, detail="Webhook connector not found")
    return webhook


@router.delete("/{webhook_id}")
def delete_webhook_connector(
    webhook_id: UUID,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Delete a webhook connector and its delivery logs."""
    if not svc.delete_webhook(db, current_user.tenant_id, webhook_id):
        raise HTTPException(status_code=404, detail="Webhook connector not found")
    return {"status": "deleted", "webhook_id": str(webhook_id)}


@router.get("/{webhook_id}/logs", response_model=List[WebhookDeliveryLogInDB])
def get_webhook_logs(
    webhook_id: UUID,
    limit: int = 50,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Get delivery logs for a specific webhook."""
    return svc.get_delivery_logs(db, current_user.tenant_id, webhook_id=webhook_id, limit=limit)


@router.post("/{webhook_id}/test")
def test_webhook_connector(
    webhook_id: UUID,
    body: WebhookTestRequest,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Send a test event to a webhook (outbound) or generate test curl (inbound)."""
    webhook = svc.get_webhook(db, current_user.tenant_id, webhook_id)
    if not webhook:
        raise HTTPException(status_code=404, detail="Webhook connector not found")

    if webhook.direction == "inbound":
        from app.core.config import settings
        base = getattr(settings, "BASE_URL", "https://servicetsunami.com")
        url = f"{base}/api/v1/webhook-connectors/in/{webhook.slug}"
        curl = f'curl -X POST "{url}" -H "Content-Type: application/json"'
        if webhook.auth_type == "hmac_sha256":
            curl += ' -H "X-Webhook-Signature: <compute-hmac>"'
        curl += f" -d '{json.dumps(body.payload or {})}'"
        return {"direction": "inbound", "test_curl": curl, "slug": webhook.slug}

    # Outbound: actually fire a test event
    test_payload = body.payload or {"test": True, "timestamp": time.time()}
    results = svc._deliver_outbound(db, webhook, body.event_type, test_payload)
    return {"direction": "outbound", "result": results}
