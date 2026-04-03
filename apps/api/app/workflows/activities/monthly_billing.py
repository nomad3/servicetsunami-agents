"""Activities for monthly billing workflow."""
import logging
from typing import List, Optional

from temporalio import activity

logger = logging.getLogger(__name__)


@activity.defn
async def aggregate_billing_visits(
    tenant_id: str, month: str, clinic_ids: Optional[List[str]] = None
) -> dict:
    """Aggregate completed visits for the billing period.

    Queries health-pets API for all completed visits in the month,
    grouped by clinic.
    """
    import httpx
    from app.core.config import settings

    healthpets_url = getattr(settings, "HEALTHPETS_API_URL", "http://localhost:8000")

    logger.info(f"Aggregating visits for {month}, tenant {tenant_id[:8]}")

    try:
        async with httpx.AsyncClient(base_url=healthpets_url, timeout=30.0) as client:
            response = await client.get(
                "/api/v1/visits/aggregate",
                params={"month": month, "clinic_ids": clinic_ids},
                headers={"X-Tenant": tenant_id},
            )
            if response.status_code < 400:
                return response.json()
            else:
                return {"status": "error", "error": response.text, "clinics": []}
    except Exception as e:
        logger.exception(f"Failed to aggregate visits: {e}")
        return {"status": "error", "error": str(e), "clinics": []}


@activity.defn
async def generate_billing_invoices(
    tenant_id: str, month: str, clinics: list
) -> dict:
    """Generate invoices for each clinic."""
    import httpx
    from app.core.config import settings

    healthpets_url = getattr(settings, "HEALTHPETS_API_URL", "http://localhost:8000")
    invoice_ids = []

    logger.info(f"Generating invoices for {len(clinics)} clinics")

    try:
        async with httpx.AsyncClient(base_url=healthpets_url, timeout=60.0) as client:
            for clinic in clinics:
                response = await client.post(
                    "/api/v1/invoices/generate",
                    json={
                        "clinic_id": clinic["clinic_id"],
                        "month": month,
                    },
                    headers={"X-Tenant": tenant_id},
                )
                if response.status_code < 400:
                    data = response.json()
                    invoice_ids.append(data.get("id"))
                else:
                    logger.warning(
                        f"Invoice generation failed for clinic {clinic['clinic_id']}: {response.text}"
                    )
    except Exception as e:
        logger.exception(f"Invoice generation error: {e}")

    return {"status": "generated", "invoice_ids": invoice_ids}


@activity.defn
async def send_billing_invoices(tenant_id: str, invoice_ids: list) -> dict:
    """Send generated invoices via email and WhatsApp."""
    import httpx
    from app.core.config import settings

    healthpets_url = getattr(settings, "HEALTHPETS_API_URL", "http://localhost:8000")
    sent_count = 0

    logger.info(f"Sending {len(invoice_ids)} invoices")

    try:
        async with httpx.AsyncClient(base_url=healthpets_url, timeout=30.0) as client:
            for invoice_id in invoice_ids:
                response = await client.post(
                    f"/api/v1/invoices/{invoice_id}/send",
                    headers={"X-Tenant": tenant_id},
                )
                if response.status_code < 400:
                    sent_count += 1
                else:
                    logger.warning(f"Failed to send invoice {invoice_id}: {response.text}")
    except Exception as e:
        logger.exception(f"Invoice sending error: {e}")

    return {"status": "sent", "sent_count": sent_count, "total": len(invoice_ids)}


@activity.defn
async def schedule_billing_followups(tenant_id: str, invoice_ids: list) -> dict:
    """Schedule 7-day follow-up reminders for unpaid invoices."""
    from app.services.dynamic_workflow_launcher import start_dynamic_workflow_by_name

    logger.info(f"Scheduling follow-ups for {len(invoice_ids)} invoices")

    count = 0
    try:
        for invoice_id in invoice_ids:
            await start_dynamic_workflow_by_name(
                "Sales Follow-Up",
                tenant_id,
                {"entity_id": invoice_id, "action": "reminder", "delay_hours": 168},
            )
            count += 1
    except Exception as e:
        logger.exception(f"Follow-up scheduling error: {e}")

    return {"status": "scheduled", "count": count}
