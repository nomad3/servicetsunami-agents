"""Service layer for universal webhook connectors."""
import hmac
import hashlib
import logging
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx
from sqlalchemy.orm import Session

from app.models.webhook_connector import WebhookConnector, WebhookDeliveryLog

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def create_webhook(
    db: Session,
    tenant_id: uuid.UUID,
    name: str,
    direction: str,
    events: List[str],
    target_url: Optional[str] = None,
    headers: Optional[Dict[str, str]] = None,
    auth_type: str = "none",
    secret: Optional[str] = None,
    payload_transform: Optional[Dict[str, str]] = None,
    description: Optional[str] = None,
    enabled: bool = True,
) -> WebhookConnector:
    """Create a new webhook connector."""
    webhook = WebhookConnector(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        name=name,
        description=description,
        direction=direction,
        events=events,
        target_url=target_url,
        headers=headers,
        auth_type=auth_type,
        secret=secret,
        payload_transform=payload_transform,
        enabled=enabled,
    )
    if direction == "inbound":
        webhook.slug = WebhookConnector.generate_slug()
    db.add(webhook)
    db.commit()
    db.refresh(webhook)
    logger.info("Created %s webhook '%s' (id=%s) for tenant %s", direction, name, webhook.id, tenant_id)
    return webhook


def list_webhooks(
    db: Session,
    tenant_id: uuid.UUID,
    direction: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
) -> List[WebhookConnector]:
    """List webhook connectors for a tenant."""
    query = db.query(WebhookConnector).filter(WebhookConnector.tenant_id == tenant_id)
    if direction:
        query = query.filter(WebhookConnector.direction == direction)
    return query.order_by(WebhookConnector.created_at.desc()).offset(skip).limit(limit).all()


def get_webhook(db: Session, tenant_id: uuid.UUID, webhook_id: uuid.UUID) -> Optional[WebhookConnector]:
    """Get a single webhook connector by ID."""
    return db.query(WebhookConnector).filter(
        WebhookConnector.id == webhook_id,
        WebhookConnector.tenant_id == tenant_id,
    ).first()


def get_webhook_by_slug(db: Session, slug: str) -> Optional[WebhookConnector]:
    """Get a webhook by its public slug (for inbound receiver)."""
    return db.query(WebhookConnector).filter(
        WebhookConnector.slug == slug,
        WebhookConnector.enabled.is_(True),
    ).first()


def update_webhook(
    db: Session,
    tenant_id: uuid.UUID,
    webhook_id: uuid.UUID,
    updates: Dict[str, Any],
) -> Optional[WebhookConnector]:
    """Update a webhook connector."""
    webhook = get_webhook(db, tenant_id, webhook_id)
    if not webhook:
        return None
    for key, value in updates.items():
        if value is not None and hasattr(webhook, key):
            setattr(webhook, key, value)
    webhook.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(webhook)
    return webhook


def delete_webhook(db: Session, tenant_id: uuid.UUID, webhook_id: uuid.UUID) -> bool:
    """Delete a webhook connector and its delivery logs."""
    webhook = get_webhook(db, tenant_id, webhook_id)
    if not webhook:
        return False
    db.query(WebhookDeliveryLog).filter(WebhookDeliveryLog.webhook_connector_id == webhook_id).delete()
    db.delete(webhook)
    db.commit()
    logger.info("Deleted webhook %s for tenant %s", webhook_id, tenant_id)
    return True


# ---------------------------------------------------------------------------
# Delivery logs
# ---------------------------------------------------------------------------

def log_delivery(
    db: Session,
    tenant_id: uuid.UUID,
    webhook_id: uuid.UUID,
    direction: str,
    event_type: str,
    payload: Optional[Dict] = None,
    response_status: Optional[int] = None,
    response_body: Optional[str] = None,
    success: bool = False,
    error_message: Optional[str] = None,
    duration_ms: Optional[int] = None,
    attempt: int = 1,
) -> WebhookDeliveryLog:
    """Record a webhook delivery attempt."""
    log = WebhookDeliveryLog(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        webhook_connector_id=webhook_id,
        direction=direction,
        event_type=event_type,
        payload=payload,
        response_status=response_status,
        response_body=response_body[:2000] if response_body else None,
        success=success,
        error_message=error_message,
        duration_ms=duration_ms,
        attempt=attempt,
    )
    db.add(log)
    db.commit()
    return log


def get_delivery_logs(
    db: Session,
    tenant_id: uuid.UUID,
    webhook_id: Optional[uuid.UUID] = None,
    limit: int = 50,
) -> List[WebhookDeliveryLog]:
    """Fetch recent delivery logs."""
    query = db.query(WebhookDeliveryLog).filter(WebhookDeliveryLog.tenant_id == tenant_id)
    if webhook_id:
        query = query.filter(WebhookDeliveryLog.webhook_connector_id == webhook_id)
    return query.order_by(WebhookDeliveryLog.created_at.desc()).limit(limit).all()


# ---------------------------------------------------------------------------
# HMAC signature
# ---------------------------------------------------------------------------

def compute_hmac_signature(secret: str, body: bytes) -> str:
    """Compute HMAC-SHA256 signature for a webhook payload."""
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def verify_hmac_signature(secret: str, body: bytes, signature: str) -> bool:
    """Verify an HMAC-SHA256 signature (constant-time comparison)."""
    expected = compute_hmac_signature(secret, body)
    return hmac.compare_digest(expected, signature)


# ---------------------------------------------------------------------------
# Outbound event dispatch
# ---------------------------------------------------------------------------

def _matches_event(subscribed_events: List[str], event_type: str) -> bool:
    """Check if an event type matches a subscription list (supports wildcard)."""
    if "*" in subscribed_events:
        return True
    if event_type in subscribed_events:
        return True
    # Prefix match: "entity.*" matches "entity.created"
    prefix = event_type.rsplit(".", 1)[0] + ".*" if "." in event_type else ""
    if prefix and prefix in subscribed_events:
        return True
    return False


def fire_outbound_event(
    db: Session,
    tenant_id: uuid.UUID,
    event_type: str,
    payload: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Find all matching outbound webhooks and POST to them."""
    webhooks = (
        db.query(WebhookConnector)
        .filter(
            WebhookConnector.tenant_id == tenant_id,
            WebhookConnector.direction == "outbound",
            WebhookConnector.enabled.is_(True),
        )
        .all()
    )

    results = []
    for wh in webhooks:
        if not _matches_event(wh.events or [], event_type):
            continue
        result = _deliver_outbound(db, wh, event_type, payload)
        results.append(result)
    return results


def _deliver_outbound(
    db: Session,
    webhook: WebhookConnector,
    event_type: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """POST payload to a single outbound webhook."""
    delivery_id = str(uuid.uuid4())
    timestamp = str(int(time.time()))

    # Build headers
    send_headers = {
        "Content-Type": "application/json",
        "X-Webhook-Event": event_type,
        "X-Webhook-Delivery-Id": delivery_id,
        "X-Webhook-Timestamp": timestamp,
    }
    if webhook.headers:
        send_headers.update(webhook.headers)

    # Auth
    body_bytes = __import__("json").dumps(payload).encode()
    if webhook.auth_type == "hmac_sha256" and webhook.secret:
        send_headers["X-Webhook-Signature"] = compute_hmac_signature(webhook.secret, body_bytes)
    elif webhook.auth_type == "bearer" and webhook.secret:
        send_headers["Authorization"] = f"Bearer {webhook.secret}"
    elif webhook.auth_type == "basic" and webhook.secret:
        send_headers["Authorization"] = f"Basic {webhook.secret}"

    start = time.time()
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(webhook.target_url, content=body_bytes, headers=send_headers)
        duration_ms = int((time.time() - start) * 1000)
        success = 200 <= resp.status_code < 300

        log_delivery(
            db, webhook.tenant_id, webhook.id, "outbound", event_type,
            payload=payload, response_status=resp.status_code,
            response_body=resp.text[:2000], success=success, duration_ms=duration_ms,
        )

        # Update counters
        webhook.trigger_count = (webhook.trigger_count or 0) + 1
        webhook.last_triggered_at = datetime.utcnow()
        if not success:
            webhook.error_count = (webhook.error_count or 0) + 1
        db.commit()

        return {
            "webhook_id": str(webhook.id),
            "name": webhook.name,
            "status": resp.status_code,
            "success": success,
            "duration_ms": duration_ms,
        }
    except Exception as e:
        duration_ms = int((time.time() - start) * 1000)
        log_delivery(
            db, webhook.tenant_id, webhook.id, "outbound", event_type,
            payload=payload, success=False, error_message=str(e), duration_ms=duration_ms,
        )
        webhook.error_count = (webhook.error_count or 0) + 1
        db.commit()
        logger.exception("Outbound webhook delivery failed for %s", webhook.id)
        return {
            "webhook_id": str(webhook.id),
            "name": webhook.name,
            "success": False,
            "error": str(e),
        }
