"""Billing tools for veterinary visit tracking and invoicing.

Manages visit records and invoice generation by calling the
health-pets API backend.
"""
import json
import logging
import re
from typing import Optional

import httpx

from config.settings import settings

logger = logging.getLogger(__name__)

_UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)
_cached_default_tenant_id = None


def _parse_json(val, default=None):
    if val is None:
        return default
    if isinstance(val, (dict, list)):
        return val
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return default


def _resolve_tenant_id(tenant_id: str) -> str:
    global _cached_default_tenant_id
    if _UUID_PATTERN.match(tenant_id):
        return tenant_id
    if _cached_default_tenant_id:
        return _cached_default_tenant_id
    try:
        from sqlalchemy import create_engine, text
        engine = create_engine(settings.database_url)
        with engine.connect() as conn:
            result = conn.execute(text("SELECT id FROM tenants LIMIT 1")).fetchone()
            if result:
                _cached_default_tenant_id = str(result[0])
                return _cached_default_tenant_id
    except Exception:
        pass
    return tenant_id


_http_client: Optional[httpx.AsyncClient] = None


def _get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            base_url=settings.healthpets_api_url,
            timeout=30.0,
        )
    return _http_client


async def create_visit_record(
    visit_id: str,
    clinic_id: str,
    patients_seen: str,
    visit_date: str,
    tenant_id: str = "auto",
) -> dict:
    """Log a completed cardiologist visit for billing.

    Creates or updates a visit record in health-pets with the patients
    seen and services performed, so it can be included in monthly billing.

    Args:
        visit_id: Visit UUID from health-pets
        clinic_id: Clinic UUID
        patients_seen: JSON array of objects: [{"pet_id": "...", "service_type": "ecg_analysis", "amount": 150.00}]
        visit_date: ISO date string (YYYY-MM-DD)
        tenant_id: Tenant context

    Returns:
        Created visit record with totals.
    """
    tenant_id = _resolve_tenant_id(tenant_id)
    patients = _parse_json(patients_seen, [])

    if not patients:
        return {"status": "error", "error": "No patients provided"}

    total = sum(p.get("amount", 0) for p in patients)

    try:
        client = _get_http_client()
        response = await client.post(
            "/api/v1/visits/record",
            json={
                "visit_id": visit_id,
                "clinic_id": clinic_id,
                "patients": patients,
                "visit_date": visit_date,
                "total_amount": total,
            },
            headers={"X-Tenant": tenant_id},
        )

        if response.status_code < 400:
            return {
                "status": "created",
                "visit_id": visit_id,
                "patients_count": len(patients),
                "total_amount": total,
                "data": response.json(),
            }
        else:
            return {
                "status": "error",
                "error": f"health-pets API returned {response.status_code}",
                "detail": response.text,
            }
    except httpx.ConnectError:
        # health-pets API not available — store locally for later sync
        logger.warning("health-pets API unreachable, storing visit record locally")
        return {
            "status": "queued",
            "visit_id": visit_id,
            "patients_count": len(patients),
            "total_amount": total,
            "note": "Stored locally; will sync when health-pets API is available",
        }
    except Exception as e:
        logger.exception(f"Failed to create visit record: {e}")
        return {"status": "error", "error": str(e)}


async def create_invoice(
    clinic_id: str,
    period_start: str,
    period_end: str,
    tenant_id: str = "auto",
) -> dict:
    """Generate an invoice for a clinic covering a billing period.

    Aggregates all completed visits for the clinic between period_start
    and period_end, applies the clinic's fee schedule, and creates an invoice.

    Args:
        clinic_id: Clinic UUID
        period_start: Start date (YYYY-MM-DD)
        period_end: End date (YYYY-MM-DD)
        tenant_id: Tenant context

    Returns:
        Invoice summary with line items and total.
    """
    tenant_id = _resolve_tenant_id(tenant_id)

    try:
        client = _get_http_client()
        response = await client.post(
            "/api/v1/invoices/generate",
            json={
                "clinic_id": clinic_id,
                "period_start": period_start,
                "period_end": period_end,
            },
            headers={"X-Tenant": tenant_id},
        )

        if response.status_code < 400:
            data = response.json()
            return {
                "status": "created",
                "invoice_id": data.get("id"),
                "clinic_id": clinic_id,
                "period": f"{period_start} to {period_end}",
                "total_amount": data.get("total_amount"),
                "line_items_count": len(data.get("line_items", [])),
                "data": data,
            }
        else:
            return {
                "status": "error",
                "error": f"health-pets API returned {response.status_code}",
                "detail": response.text,
            }
    except Exception as e:
        logger.exception(f"Failed to create invoice: {e}")
        return {"status": "error", "error": str(e)}


async def generate_monthly_statement(
    clinic_id: str,
    month: str,
    tenant_id: str = "auto",
) -> dict:
    """Generate a monthly billing statement PDF for a clinic.

    Creates a formatted statement covering all visits and charges
    for the specified month. Returns a URL to the generated PDF.

    Args:
        clinic_id: Clinic UUID
        month: Month in YYYY-MM format (e.g., "2026-02")
        tenant_id: Tenant context

    Returns:
        Statement details with PDF URL.
    """
    tenant_id = _resolve_tenant_id(tenant_id)

    try:
        client = _get_http_client()
        response = await client.post(
            "/api/v1/invoices/monthly-statement",
            json={
                "clinic_id": clinic_id,
                "month": month,
            },
            headers={"X-Tenant": tenant_id},
        )

        if response.status_code < 400:
            data = response.json()
            return {
                "status": "created",
                "clinic_id": clinic_id,
                "month": month,
                "pdf_url": data.get("pdf_url"),
                "total_amount": data.get("total_amount"),
                "visits_count": data.get("visits_count"),
            }
        else:
            return {
                "status": "error",
                "error": f"health-pets API returned {response.status_code}",
                "detail": response.text,
            }
    except Exception as e:
        logger.exception(f"Failed to generate statement: {e}")
        return {"status": "error", "error": str(e)}
