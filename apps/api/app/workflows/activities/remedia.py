"""Activities for Remedia order workflow."""
import asyncio
import logging
import uuid
from datetime import datetime

import httpx
from temporalio import activity

logger = logging.getLogger(__name__)

REMEDIA_API_URL = "http://remedia-api.prod.svc.cluster.local/api/v1"


@activity.defn
async def create_remedia_order(input) -> dict:
    """Create an order on Remedia API and record execution trace."""
    from app.db.session import SessionLocal
    from app.models.execution_trace import ExecutionTrace

    token = input.token
    pharmacy_id = input.pharmacy_id
    items = input.items
    payment_provider = input.payment_provider
    tenant_id = input.tenant_id

    db = SessionLocal()
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{REMEDIA_API_URL}/orders/",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "pharmacy_id": pharmacy_id,
                    "items": items,
                    "payment_provider": payment_provider,
                },
            )

        trace = ExecutionTrace(
            id=uuid.uuid4(),
            tenant_id=uuid.UUID(tenant_id),
            step_type="remedia_order_created",
            step_order=1,
            details={
                "pharmacy_id": pharmacy_id,
                "items": items,
                "payment_provider": payment_provider,
                "response_status": resp.status_code,
                "response_body": resp.json() if resp.status_code < 400 else resp.text[:500],
            },
            created_at=datetime.utcnow(),
        )
        db.add(trace)
        db.commit()

        if resp.status_code in (200, 201):
            data = resp.json()
            return {
                "order_id": str(data.get("id", "")),
                "status": data.get("status", "pending"),
                "payment_url": data.get("payment_url"),
                "total": data.get("total", 0),
            }
        return {"error": f"Remedia API returned {resp.status_code}: {resp.text[:300]}"}

    except Exception as e:
        logger.exception("create_remedia_order failed")
        return {"error": str(e)}
    finally:
        db.close()


@activity.defn
async def send_remedia_notification(input: dict) -> dict:
    """Send WhatsApp notification for order events."""
    from app.services.whatsapp_service import whatsapp_service

    phone = input["phone_number"]
    tenant_id = input["tenant_id"]
    msg_type = input["message_type"]
    order_id = input.get("order_id", "")
    total = input.get("total", 0)

    if msg_type == "order_created":
        payment_url = input.get("payment_url", "")
        payment_provider = input.get("payment_provider", "")
        if payment_url:
            message = (
                f"✅ *Pedido creado*\n\n"
                f"Orden: #{order_id[:8]}\n"
                f"Total: ${total:,.0f}\n"
                f"Pago: {payment_provider}\n\n"
                f"Paga aquí: {payment_url}\n\n"
                f"Te avisaremos cuando se confirme el pago."
            )
        else:
            message = (
                f"✅ *Pedido creado*\n\n"
                f"Orden: #{order_id[:8]}\n"
                f"Total: ${total:,.0f}\n\n"
                f"Tu pedido está siendo procesado."
            )
    elif msg_type == "payment_confirmed":
        message = (
            f"💳 *Pago confirmado*\n\n"
            f"Orden: #{order_id[:8]}\n"
            f"Total: ${total:,.0f}\n\n"
            f"Tu pedido está siendo preparado para entrega."
        )
    elif msg_type == "delivering":
        message = (
            f"🚚 *En camino*\n\n"
            f"Orden: #{order_id[:8]}\n"
            f"Tu pedido está en camino."
        )
    elif msg_type == "completed":
        message = (
            f"📦 *Entregado*\n\n"
            f"Orden: #{order_id[:8]}\n"
            f"Tu pedido fue entregado. ¡Gracias por tu compra!"
        )
    else:
        message = f"Actualización de pedido #{order_id[:8]}: {msg_type}"

    try:
        result = await whatsapp_service.send_message(
            tenant_id=tenant_id,
            to=phone,
            message=message,
        )
        return {"status": "sent", **result}
    except Exception as e:
        logger.exception("send_remedia_notification failed")
        return {"status": "error", "error": str(e)}


@activity.defn
async def monitor_remedia_payment(input: dict) -> dict:
    """Poll Remedia order status until payment is confirmed or timeout."""
    order_id = input["order_id"]
    token = input["token"]
    timeout_minutes = input.get("timeout_minutes", 30)

    max_polls = timeout_minutes * 2  # poll every 30s
    for i in range(max_polls):
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    f"{REMEDIA_API_URL}/orders/{order_id}/status",
                    headers={"Authorization": f"Bearer {token}"},
                )
            if resp.status_code == 200:
                data = resp.json()
                status = data.get("status", "") if isinstance(data, dict) else str(data)
                if status in ("confirmed", "delivering", "completed"):
                    return {"paid": True, "status": status}
                if status in ("cancelled", "rejected"):
                    return {"paid": False, "status": status}
        except Exception:
            logger.warning(f"Payment poll {i} failed for order {order_id}")

        activity.heartbeat(f"poll_{i}")
        await asyncio.sleep(30)

    return {"paid": False, "status": "timeout"}


@activity.defn
async def track_remedia_delivery(input: dict) -> dict:
    """Poll order status for delivery updates. Sends WhatsApp on status changes."""
    order_id = input["order_id"]
    token = input["token"]
    phone = input["phone_number"]
    tenant_id = input["tenant_id"]

    last_status = None
    max_polls = 288  # poll every 5 min for 24h

    for i in range(max_polls):
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    f"{REMEDIA_API_URL}/orders/{order_id}/status",
                    headers={"Authorization": f"Bearer {token}"},
                )
            if resp.status_code == 200:
                data = resp.json()
                status = data.get("status", "") if isinstance(data, dict) else str(data)

                if status != last_status and status in ("delivering", "completed"):
                    await send_remedia_notification({
                        "phone_number": phone,
                        "tenant_id": tenant_id,
                        "message_type": status,
                        "order_id": order_id,
                    })
                    last_status = status

                if status in ("completed", "cancelled"):
                    return {"status": status}

        except Exception:
            logger.warning(f"Delivery poll {i} failed for order {order_id}")

        activity.heartbeat(f"delivery_poll_{i}")
        await asyncio.sleep(300)  # 5 minutes

    return {"status": last_status or "timeout"}
