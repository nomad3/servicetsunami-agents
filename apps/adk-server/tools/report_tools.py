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
    "report_period": {"type": "string", "description": "Period covered, e.g. 'June 2025' or 'Q1 2026'"},
    "production": {
        "type": "object",
        "description": "Aggregate production and collections for the entire practice",
        "properties": {
            "doctor": {"type": "number", "description": "Sum of production for all providers with role=doctor"},
            "specialty": {"type": "number", "description": "Sum of production for all providers with role=specialist"},
            "hygiene": {"type": "number", "description": "Sum of production for all providers with role=hygienist"},
            "total": {"type": "number", "description": "Total gross production (doctor + specialty + hygiene)"},
            "net_production": {"type": "number", "description": "Net production after adjustments/write-offs"},
            "collections": {"type": "number", "description": "Total collections across all providers"},
        },
    },
    "providers": {
        "type": "array",
        "description": "EVERY provider from the source document. Include ALL staff with production or collections > $0.",
        "items": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Provider full name as shown in the document"},
                "role": {
                    "type": "string",
                    "description": (
                        "Provider role. Use ONLY these values: 'doctor', 'hygienist', 'specialist', 'staff'. "
                        "Classification rules for dental practices: "
                        "- doctor: Has 'D.D.S.', 'D.M.D.', 'Dr.' in name, or is listed as the practice owner. "
                        "- specialist: Oral surgeons, orthodontists, periodontists, endodontists. "
                        "- hygienist: Names appearing under 'Hygiene' sections, or labeled 'Sub Hygiene'/'Sub Hygienist'. "
                        "- staff: Front office, assistants, billing coordinators, lab techs — anyone not a doctor/specialist/hygienist. "
                        "When unsure, use 'staff'. Do NOT guess 'doctor' for non-clinical staff."
                    ),
                },
                "visits": {"type": "integer", "description": "Number of patient visits (null if not available)"},
                "gross_production": {"type": "number", "description": "Total production amount from 'Totals' line"},
                "collections": {"type": "number", "description": "Total collections amount from 'Totals' line"},
                "production_per_visit": {"type": "number", "description": "Production / visits (null if visits unknown)"},
                "treatment_presented": {"type": "number", "description": "From treatment plan reports: total proposed/posted to walkout"},
                "treatment_accepted": {"type": "number", "description": "From treatment plan reports: total accepted"},
                "acceptance_rate": {"type": "number", "description": "treatment_accepted / treatment_presented as decimal 0-1"},
            },
        },
    },
    "hygiene": {
        "type": "object",
        "description": "Aggregate hygiene department metrics (capacity, reappointment, etc.)",
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
            "Extract structured data from the file text and populate the target_schema fields.\n\n"
            "## Extraction Rules\n"
            "1. Convert percentages to decimals (85% → 0.85).\n"
            "2. Remove currency symbols and commas (e.g. $1,234.56 → 1234.56).\n"
            "3. Use null for any field not found in the text.\n"
            "4. Negative values in parentheses: ($1,234) → -1234.\n\n"
            "## Provider Classification (CRITICAL)\n"
            "Include EVERY provider/person listed in the document as a separate entry.\n"
            "- **doctor**: Has 'D.D.S.', 'D.M.D.', 'Dr.' prefix, or is the practice owner\n"
            "- **specialist**: Oral surgeons, orthodontists, periodontists, endodontists\n"
            "- **hygienist**: Listed under 'Hygiene' sections, or name contains "
            "'Sub Hygiene', 'Sub Hygienist', 'RDH'\n"
            "- **staff**: Everyone else — front office, assistants, billing, lab techs, "
            "coordinators. When in doubt, use 'staff'\n"
            "Do NOT default to 'doctor'. Most non-clinical staff are 'staff'.\n\n"
            "## Performance Summary Documents\n"
            "Each page typically shows one provider with:\n"
            "- 'Services' line = gross production\n"
            "- 'Totals' line = net production (after deleted services, discounts, etc.)\n"
            "- 'Collections' column totals = total collections\n"
            "Use the 'Totals' line for gross_production and the Collections 'Totals' for collections.\n"
            "Include providers even if production is $0 but collections > $0.\n\n"
            "## Treatment Plan Documents\n"
            "Look for 'Total Proposed/Posted to Walkout' = treatment_presented.\n"
            "Look for 'Total Accepted' = treatment_accepted.\n"
            "acceptance_rate = treatment_accepted / treatment_presented.\n\n"
            "## Multi-file Merging\n"
            "When merging with previously extracted data:\n"
            "- Match providers by name (case-insensitive, ignore 'Dr.' prefix)\n"
            "- Sum production/collections from different sources if they represent different data\n"
            "- Do NOT duplicate providers — merge into existing entries\n"
            "- Treatment plan data adds treatment_presented/accepted to existing provider entries\n"
            "- Recalculate aggregate production.doctor/hygiene/total after merging"
        ),
        "file_preview": file_text[:3000],
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
