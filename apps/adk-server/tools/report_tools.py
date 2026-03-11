"""Report tools for data extraction and Excel generation.

Provides two tools:
- extract_document_data: Returns a target JSON schema for the LLM to fill
  from uploaded file text (does NOT do extraction itself).
- generate_excel_report: Sends structured JSON to the API for Excel generation.
"""
import json
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
            timeout=60.0,
        )
    return _api_client


# --------------------------------------------------------------------------- #
# Target schema for dental practice reports
# --------------------------------------------------------------------------- #

_REPORT_SCHEMA = {
    "practice_name": {"type": "string", "description": "Name of the dental practice"},
    "report_period": {"type": "string", "description": "Period covered, e.g. 'January 2026' or 'Q1 2026'"},
    "production": {
        "type": "object",
        "properties": {
            "doctor": {"type": "number", "description": "Doctor production in dollars"},
            "specialty": {"type": "number", "description": "Specialty production in dollars"},
            "hygiene": {"type": "number", "description": "Hygiene production in dollars"},
            "total": {"type": "number", "description": "Total gross production in dollars"},
            "net_production": {"type": "number", "description": "Net production (after adjustments) in dollars"},
            "collections": {"type": "number", "description": "Total collections in dollars"},
        },
    },
    "providers": {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Provider full name"},
                "role": {"type": "string", "description": "Role: 'doctor', 'hygienist', or 'specialist'"},
                "visits": {"type": "integer", "description": "Number of patient visits"},
                "gross_production": {"type": "number", "description": "Gross production in dollars"},
                "production_per_visit": {"type": "number", "description": "Average production per visit in dollars"},
                "treatment_presented": {"type": "number", "description": "Total treatment presented in dollars"},
                "treatment_accepted": {"type": "number", "description": "Total treatment accepted in dollars"},
                "acceptance_rate": {"type": "number", "description": "Case acceptance rate as decimal 0-1"},
            },
        },
    },
    "hygiene": {
        "type": "object",
        "properties": {
            "visits": {"type": "integer", "description": "Total hygiene visits"},
            "capacity": {"type": "integer", "description": "Total hygiene capacity (available slots)"},
            "capacity_pct": {"type": "number", "description": "Capacity utilization as decimal 0-1"},
            "reappointment_rate": {"type": "number", "description": "Hygiene reappointment rate as decimal 0-1"},
            "net_production": {"type": "number", "description": "Hygiene net production in dollars"},
        },
    },
}


def extract_document_data(
    file_text: str,
    filename: str,
    document_type: str = "auto",
) -> dict:
    """Return the target JSON schema for the LLM to fill from uploaded file text.

    This tool does NOT perform extraction itself — it provides the schema
    and instructions so the LLM can extract the data.

    Args:
        file_text: The full text content of the uploaded file.
        filename: Original filename (used for context).
        document_type: Hint about document type. Use "auto" to let the LLM decide.

    Returns:
        Dict with status, target_schema, instructions, and a file preview.
    """
    return {
        "status": "schema_provided",
        "target_schema": _REPORT_SCHEMA,
        "instructions": (
            "Extract structured data from the file text below and populate "
            "the target_schema fields. Follow these rules:\n"
            "1. Convert percentages to decimals (e.g. 85% → 0.85).\n"
            "2. Remove currency symbols and commas from numbers "
            "(e.g. $1,234.56 → 1234.56).\n"
            "3. Use null for any field that cannot be found in the text.\n"
            "4. If data spans multiple files, merge values — sum dollar "
            "amounts and average rates where appropriate.\n"
            "5. Provider names should be 'Dr. FirstName LastName' for doctors.\n"
            "6. The acceptance_rate, capacity_pct, and reappointment_rate "
            "must be decimals between 0 and 1."
        ),
        "file_preview": file_text[:2000],
    }


async def generate_excel_report(
    report_data: str,
    tenant_id: str = "auto",
) -> dict:
    """Send structured report JSON to the API for Excel generation.

    Once the LLM has populated the target schema from extract_document_data,
    pass the completed JSON here to produce a downloadable Excel report.

    Args:
        report_data: JSON string with the filled report data matching the
            target schema (must include practice_name and report_period).
        tenant_id: Tenant context. Use "auto" if unknown.

    Returns:
        Dict with status, download_url, filename, file_id, and message.
    """
    tenant_id = _resolve_tenant_id(tenant_id)

    # Parse report_data from JSON string
    try:
        data = json.loads(report_data) if isinstance(report_data, str) else report_data
    except (json.JSONDecodeError, TypeError) as exc:
        return {"error": f"Invalid JSON in report_data: {exc}"}

    # Validate required fields
    missing = [f for f in ("practice_name", "report_period") if not data.get(f)]
    if missing:
        return {"error": f"Missing required fields: {', '.join(missing)}"}

    client = _get_api_client()

    try:
        resp = await client.post(
            "/api/v1/reports/internal/generate",
            headers={
                "X-Tenant-ID": tenant_id,
                "X-Internal-Key": settings.mcp_api_key,
            },
            json=data,
        )
        resp.raise_for_status()
        result = resp.json()
        return {
            "status": "success",
            "download_url": result.get("download_url"),
            "filename": result.get("filename"),
            "file_id": result.get("file_id"),
            "message": result.get("message", "Report generated successfully."),
        }
    except httpx.HTTPStatusError as exc:
        logger.error(
            "generate_excel_report HTTP error: %s %s",
            exc.response.status_code,
            exc.response.text[:300],
        )
        return {
            "error": f"API returned {exc.response.status_code}: {exc.response.text[:200]}",
        }
    except Exception as exc:
        logger.exception("generate_excel_report failed")
        return {"error": f"Failed to generate report: {exc}"}
