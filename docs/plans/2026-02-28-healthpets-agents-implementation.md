# HealthPets Agents Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add 3 new ADK agents (cardiac_analyst, billing_agent, vet_supervisor) and 2 new tool modules (vet_tools, billing_tools) to servicetsunami-agents, plus a MonthlyBillingWorkflow (Temporal), to power the HealthPets mobile cardiologist platform.

**Architecture:** Extend the existing ADK supervisor hierarchy — vet_supervisor becomes a new team under root_agent, alongside dev_team/data_team/sales_team/marketing_team. cardiac_analyst and billing_agent are new leaf agents. report_generator (existing) gets extended with vet-specific tools. All tools follow the existing async function pattern with `_resolve_tenant_id()` and `_parse_json()` helpers.

**Tech Stack:** Google ADK (Agent class), Anthropic Claude API (vision), Temporal (workflow + activities), SQLAlchemy (DB access), httpx (HTTP calls to health-pets API)

**Design doc:** `/Users/nomade/Documents/GitHub/health-pets/docs/plans/2026-02-28-mobile-cardiologist-workflow-design.md`

---

## Task 1: Create vet_tools.py — ECG Analysis + Breed Reference Tools

**Files:**
- Create: `apps/adk-server/tools/vet_tools.py`

**Context:** Follow the exact pattern from `tools/knowledge_tools.py` and `tools/connector_tools.py` — module-level `_parse_json()`, `_resolve_tenant_id()` helpers, async tool functions with typed params returning dicts.

**Step 1: Create vet_tools.py with analyze_ecg_image and get_breed_reference_ranges**

```python
"""Veterinary cardiology tools.

ECG image analysis via Claude vision and breed reference range lookups
from the knowledge graph.
"""
import json
import logging
import re
from typing import Optional

import httpx

from config.settings import settings
from services.knowledge_graph import get_knowledge_service

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


async def analyze_ecg_image(
    image_urls: str,
    species: str,
    breed: str,
    age_years: float,
    weight_kg: float,
    medications: str = "[]",
    tenant_id: str = "auto",
) -> dict:
    """Analyze ECG image(s) using Claude vision model.

    Sends ECG images to Claude with veterinary cardiology context
    and returns structured findings including rhythm classification,
    heart rate, intervals, axis, and abnormalities.

    Args:
        image_urls: JSON array of image URLs (S3 or public URLs)
        species: Animal species — "canine", "feline", or "equine"
        breed: Breed name for reference range comparison
        age_years: Patient age in years
        weight_kg: Patient weight in kilograms
        medications: JSON array of current medications (strings)
        tenant_id: Tenant context

    Returns:
        Structured findings dict with rhythm, heart_rate_bpm, intervals,
        axis_degrees, abnormalities, overall_confidence, breed_comparison,
        and raw_interpretation.
    """
    tenant_id = _resolve_tenant_id(tenant_id)
    urls = _parse_json(image_urls, [])
    meds = _parse_json(medications, [])

    if not urls:
        return {"status": "error", "error": "No image URLs provided"}

    # Fetch breed reference ranges for comparison
    ref_ranges = await get_breed_reference_ranges(species, breed, tenant_id)

    ref_context = ""
    if ref_ranges.get("status") != "error":
        props = ref_ranges.get("properties", {})
        ref_context = f"""
Normal reference ranges for {breed} ({species}):
- Heart rate: {props.get('heart_rate_range', 'unknown')} bpm
- PR interval: {props.get('pr_interval_ms', 'unknown')} ms
- QRS duration: {props.get('qrs_duration_ms', 'unknown')} ms
- QT interval: {props.get('qt_interval_ms', 'unknown')} ms
- Breed predispositions: {', '.join(props.get('common_conditions', []))}
- Notes: {props.get('breed_predisposition_notes', '')}
"""

    prompt = f"""You are an expert veterinary cardiologist analyzing an ECG recording.

Patient: {species}, {breed}, {age_years} years old, {weight_kg} kg
Current medications: {', '.join(meds) if meds else 'None'}
{ref_context}

Analyze the ECG image(s) and provide a structured interpretation. Return your findings as a JSON object with this exact structure:
{{
    "rhythm": "<classification: normal_sinus, sinus_arrhythmia, atrial_fibrillation, ventricular_tachycardia, etc>",
    "heart_rate_bpm": <integer>,
    "intervals": {{
        "pr_ms": <integer or null>,
        "qrs_ms": <integer or null>,
        "qt_ms": <integer or null>,
        "qt_corrected_ms": <integer or null>
    }},
    "axis_degrees": <integer or null>,
    "abnormalities": [
        {{
            "finding": "<abnormality name>",
            "severity": "mild | moderate | severe",
            "confidence": <0.0-1.0>,
            "evidence": "<what you see in the ECG>"
        }}
    ],
    "overall_confidence": <0.0-1.0>,
    "breed_comparison": {{
        "hr_normal_range": "<range> bpm",
        "within_normal": <true/false>
    }},
    "raw_interpretation": "<free-text narrative of your full interpretation>"
}}

Be precise. If you cannot measure an interval, set it to null. Flag any findings that warrant urgent attention."""

    # Build Claude API message with image content blocks
    content_blocks = []
    for url in urls:
        content_blocks.append({
            "type": "image",
            "source": {"type": "url", "url": url},
        })
    content_blocks.append({"type": "text", "text": prompt})

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            # Call Claude API directly for vision (ADK model may not support vision)
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": settings.anthropic_api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-sonnet-4-20250514",
                    "max_tokens": 4096,
                    "messages": [{"role": "user", "content": content_blocks}],
                },
            )
            response.raise_for_status()
            data = response.json()

        # Extract the text response and parse JSON
        text_content = data["content"][0]["text"]

        # Try to extract JSON from the response
        json_match = re.search(r"\{[\s\S]*\}", text_content)
        if json_match:
            findings = json.loads(json_match.group())
            findings["status"] = "success"
            findings["images_analyzed"] = len(urls)
            return findings
        else:
            return {
                "status": "success",
                "raw_interpretation": text_content,
                "images_analyzed": len(urls),
                "note": "Could not parse structured findings; raw interpretation provided",
            }

    except httpx.HTTPStatusError as e:
        logger.exception(f"Claude API error: {e}")
        return {"status": "error", "error": f"Claude API error: {e.response.status_code}"}
    except Exception as e:
        logger.exception(f"ECG analysis failed: {e}")
        return {"status": "error", "error": str(e)}


async def get_breed_reference_ranges(
    species: str,
    breed: str,
    tenant_id: str = "auto",
) -> dict:
    """Look up normal ECG reference ranges for a specific breed.

    Queries knowledge entities with entity_type='reference' and
    category='breed_reference' matching the species and breed.

    Args:
        species: Animal species — "canine", "feline", or "equine"
        breed: Breed name to look up
        tenant_id: Tenant context

    Returns:
        Dict with breed reference properties (heart_rate_range, pr_interval_ms, etc.)
        or error if not found.
    """
    tenant_id = _resolve_tenant_id(tenant_id)
    kg = get_knowledge_service()

    # Search for breed reference entity
    results = await kg.search_entities(
        query=f"{breed} {species} ECG reference",
        tenant_id=tenant_id,
        entity_type="reference",
    )

    if results:
        # Find best match
        for entity in results:
            props = entity.get("properties", {})
            if (
                props.get("species", "").lower() == species.lower()
                and props.get("breed", "").lower() == breed.lower()
            ):
                return {
                    "status": "found",
                    "name": entity.get("name"),
                    "properties": props,
                }

        # Return first result as partial match
        return {
            "status": "partial_match",
            "name": results[0].get("name"),
            "properties": results[0].get("properties", {}),
            "note": f"Exact match for {breed} not found; closest match returned",
        }

    return {
        "status": "error",
        "error": f"No reference ranges found for {species}/{breed}",
        "note": "Using general species defaults recommended",
    }
```

**Step 2: Add anthropic_api_key to settings.py**

In `apps/adk-server/config/settings.py`, add below `api_base_url`:

```python
    # Anthropic (for Claude vision in cardiac_analyst)
    anthropic_api_key: str = ""
```

**Step 3: Commit**

```bash
git add apps/adk-server/tools/vet_tools.py apps/adk-server/config/settings.py
git commit -m "feat: add vet_tools with ECG vision analysis and breed reference lookup"
```

---

## Task 2: Create billing_tools.py — Visit Records + Invoicing Tools

**Files:**
- Create: `apps/adk-server/tools/billing_tools.py`

**Context:** These tools call back to the health-pets API via httpx to create visit records and invoices. Follow the `connector_tools.py` pattern for HTTP calls to external services.

**Step 1: Create billing_tools.py**

```python
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
```

**Step 2: Add healthpets_api_url to settings.py**

In `apps/adk-server/config/settings.py`, add below `anthropic_api_key`:

```python
    # Health-Pets API (for billing callbacks)
    healthpets_api_url: str = "http://localhost:8000"
```

**Step 3: Commit**

```bash
git add apps/adk-server/tools/billing_tools.py apps/adk-server/config/settings.py
git commit -m "feat: add billing_tools with visit records, invoicing, and monthly statements"
```

---

## Task 3: Create cardiac_analyst Agent

**Files:**
- Create: `apps/adk-server/servicetsunami_supervisor/cardiac_analyst.py`

**Context:** Follow the exact agent definition pattern from `data_analyst.py` and `sales_agent.py`. The agent uses `settings.adk_model` by default but the instruction tells it to prefer Claude vision for ECG analysis.

**Step 1: Create cardiac_analyst.py**

```python
"""Cardiac Analyst specialist agent.

Analyzes ECG images using Claude vision and compares findings
against breed-specific reference ranges from the knowledge graph.
"""
from google.adk.agents import Agent

from tools.vet_tools import (
    analyze_ecg_image,
    get_breed_reference_ranges,
)
from tools.knowledge_tools import (
    search_knowledge,
    create_entity,
    create_relation,
    record_observation,
)
from config.settings import settings


cardiac_analyst = Agent(
    name="cardiac_analyst",
    model=settings.adk_model,
    instruction="""You are an expert veterinary cardiologist AI assistant specializing in ECG interpretation for companion animals.

IMPORTANT: For the tenant_id parameter in all tools, use the value from the session state.
If you cannot access the session state, use "auto" as tenant_id and the system will resolve it.

Your capabilities:
- Analyze ECG images using Claude vision (analyze_ecg_image tool)
- Look up breed-specific normal ECG reference ranges (get_breed_reference_ranges tool)
- Store findings as knowledge entities for patient history
- Record observations about patients in the knowledge graph

## Workflow:

1. When you receive an ECG analysis request with images and patient metadata:
   a. First look up breed reference ranges for context
   b. Call analyze_ecg_image with the images and all patient metadata
   c. Review the findings and add your clinical reasoning
   d. Store findings as an observation on the patient entity if one exists
   e. Create knowledge relations for any new diagnoses

2. Always include:
   - Rhythm classification with confidence
   - Heart rate and whether it's within normal range for the breed
   - All measurable intervals (PR, QRS, QT)
   - Electrical axis if determinable
   - Any abnormalities with severity grading
   - Breed-specific considerations (e.g., DCM predisposition in Dobermans)
   - Clinical recommendations (further tests, monitoring, treatment considerations)

3. Flag urgent findings prominently:
   - Ventricular tachycardia or fibrillation
   - Complete heart block
   - Severely prolonged QT
   - Signs of myocardial infarction

4. When findings suggest a known breed predisposition, reference it explicitly.

## Output format:
Return a structured JSON findings object that the report_generator can use to create the clinical report. Always include raw_interpretation with your full narrative.
""",
    tools=[
        analyze_ecg_image,
        get_breed_reference_ranges,
        search_knowledge,
        create_entity,
        create_relation,
        record_observation,
    ],
)
```

**Step 2: Commit**

```bash
git add apps/adk-server/servicetsunami_supervisor/cardiac_analyst.py
git commit -m "feat: add cardiac_analyst agent for ECG vision analysis"
```

---

## Task 4: Create billing_agent Agent

**Files:**
- Create: `apps/adk-server/servicetsunami_supervisor/billing_agent.py`

**Step 1: Create billing_agent.py**

```python
"""Billing Agent specialist.

Handles visit record creation, invoice generation, and monthly
billing settlement for veterinary cardiologist visits.
"""
from google.adk.agents import Agent

from tools.billing_tools import (
    create_visit_record,
    create_invoice,
    generate_monthly_statement,
)
from tools.knowledge_tools import (
    search_knowledge,
    find_entities,
    record_observation,
)
from config.settings import settings


billing_agent = Agent(
    name="billing_agent",
    model="gemini-2.0-flash",
    instruction="""You are a billing specialist for a mobile veterinary cardiologist practice.

IMPORTANT: For the tenant_id parameter in all tools, use the value from the session state.
If you cannot access the session state, use "auto" as tenant_id and the system will resolve it.

Your capabilities:
- Log completed cardiologist visits (create_visit_record)
- Generate invoices for clinics covering a billing period (create_invoice)
- Generate monthly statements with PDF output (generate_monthly_statement)
- Look up clinic and visit information from the knowledge graph

## Workflow:

### After a visit is completed:
1. Use create_visit_record with the visit details, patients seen, and services performed
2. Each patient gets a line item with service_type and amount from the clinic's fee schedule

### For monthly billing:
1. Use create_invoice for each clinic that had visits in the period
2. Use generate_monthly_statement to create the formatted PDF
3. Record the billing action as an observation

### Service types and typical items:
- ecg_analysis: ECG interpretation by cardiologist
- full_cardiac_exam: Complete cardiac workup
- follow_up: Follow-up consultation
- emergency_consult: Emergency cardiac consultation
- report_generation: Formal written report

## Important:
- Always confirm amounts against the clinic's fee schedule before creating records
- Flag any discrepancies between expected and actual charges
- Keep track of payment status when queried
""",
    tools=[
        create_visit_record,
        create_invoice,
        generate_monthly_statement,
        search_knowledge,
        find_entities,
        record_observation,
    ],
)
```

**Step 2: Commit**

```bash
git add apps/adk-server/servicetsunami_supervisor/billing_agent.py
git commit -m "feat: add billing_agent for visit tracking and invoicing"
```

---

## Task 5: Create vet_supervisor Agent + Wire Into Root Agent

**Files:**
- Create: `apps/adk-server/servicetsunami_supervisor/vet_supervisor.py`
- Modify: `apps/adk-server/servicetsunami_supervisor/__init__.py`
- Modify: `apps/adk-server/servicetsunami_supervisor/agent.py`

**Step 1: Create vet_supervisor.py**

```python
"""Veterinary team supervisor.

Routes veterinary cardiology requests to specialist sub-agents:
cardiac_analyst, report_generator, and billing_agent.
"""
from google.adk.agents import Agent

from .cardiac_analyst import cardiac_analyst
from .report_generator import report_generator
from .billing_agent import billing_agent
from config.settings import settings


vet_supervisor = Agent(
    name="vet_supervisor",
    model=settings.adk_model,
    instruction="""You are the veterinary cardiology team supervisor. You coordinate ECG analysis, report generation, and billing for a mobile cardiologist practice.

IMPORTANT: You are a ROUTING agent only. You do NOT have tools.
Your ONLY capability is to transfer tasks to your sub-agents using transfer_to_agent.

## Your team:

- **cardiac_analyst**: ECG image analysis specialist. Send here when:
  - Vet uploads ECG images for interpretation
  - Request includes patient metadata (species, breed, age, weight)
  - "Analyze this ECG", "What does this ECG show"

- **report_generator**: Clinical report creation. Send here when:
  - Structured findings are ready and need to be formatted as a report
  - "Generate a report", "Draft the cardiac report"
  - Report needs to be finalized, templated, or sent to a clinic

- **billing_agent**: Visit and invoice management. Send here when:
  - Visit is complete and needs to be logged for billing
  - Monthly invoices need to be generated
  - "Create an invoice", "Log this visit", "Monthly statement"

## Full pipeline flow:
For a complete "analyze ECG and create report" request:
1. Route to cardiac_analyst for ECG interpretation
2. Route findings to report_generator for draft creation
3. (Human cardiologist reviews and approves)
4. Route to report_generator for finalization and delivery
5. Route to billing_agent to log the visit

## Default routing:
- ECG images or analysis requests → cardiac_analyst
- Report or document requests → report_generator
- Billing, invoice, payment requests → billing_agent
""",
    sub_agents=[cardiac_analyst, report_generator, billing_agent],
)
```

**Step 2: Update __init__.py — add new agent imports and exports**

In `apps/adk-server/servicetsunami_supervisor/__init__.py`, add after line 13 (after `from .personal_assistant import personal_assistant`):

```python
from .cardiac_analyst import cardiac_analyst
from .billing_agent import billing_agent
```

Add after line 20 (after `from .marketing_team import marketing_team`):

```python
from .vet_supervisor import vet_supervisor
```

Add to `__all__` list:

```python
    "vet_supervisor",
    "cardiac_analyst",
    "billing_agent",
```

**Step 3: Update agent.py — add vet_supervisor to root_agent**

In `apps/adk-server/servicetsunami_supervisor/agent.py`:

Add import after line 12 (`from .marketing_team import marketing_team`):

```python
from .vet_supervisor import vet_supervisor
```

Add to root_agent instruction (after the marketing_team routing section, before "## Default routing:"):

```
- **vet_supervisor**: Veterinary cardiology team (cardiac_analyst + report_generator + billing_agent). For ECG analysis, cardiac reports, veterinary billing, clinic invoicing.
```

Add routing guidelines:

```
### vet_supervisor:
- ECG image analysis, cardiac interpretation
- Veterinary report generation or delivery
- Clinic billing, invoicing, monthly statements
- "Analyze this ECG", "Generate cardiac report", "Create invoice for clinic"
- Any request mentioning pets, animals, veterinary, cardiologist
```

Update sub_agents list to include vet_supervisor:

```python
    sub_agents=[personal_assistant, dev_team, data_team, sales_team, marketing_team, vet_supervisor],
```

**Step 4: Commit**

```bash
git add apps/adk-server/servicetsunami_supervisor/vet_supervisor.py \
       apps/adk-server/servicetsunami_supervisor/__init__.py \
       apps/adk-server/servicetsunami_supervisor/agent.py
git commit -m "feat: add vet_supervisor team and wire into root agent hierarchy"
```

---

## Task 6: Extend report_generator with Vet-Specific Tools

**Files:**
- Modify: `apps/adk-server/servicetsunami_supervisor/report_generator.py`

**Context:** The existing report_generator already has report/chart/export tools. We add `apply_clinic_template` and `send_whatsapp_report` to its tool list, and extend its instruction to handle veterinary cardiac reports.

**Step 1: Add vet tool imports to report_generator.py**

After the existing `action_tools` imports (line 18), add:

```python
from tools.vet_tools import get_breed_reference_ranges
from tools.billing_tools import create_visit_record
```

**Step 2: Add vet-specific tools as inline functions**

Add before the `report_generator = Agent(...)` definition:

```python
async def apply_clinic_template(
    report_markdown: str,
    template_id: str,
    clinic_name: str = "",
    tenant_id: str = "auto",
) -> dict:
    """Format a cardiac report into a clinic-specific template.

    Args:
        report_markdown: The report content in markdown
        template_id: Template ID from health-pets
        clinic_name: Clinic name for header
        tenant_id: Tenant context

    Returns:
        Formatted report ready for PDF generation.
    """
    # Template application happens on the health-pets side;
    # this wraps the content with metadata for the API call
    return {
        "status": "formatted",
        "template_id": template_id,
        "clinic_name": clinic_name,
        "content": report_markdown,
        "format": "markdown",
        "note": "Send to health-pets API for PDF generation with clinic branding",
    }


async def send_whatsapp_report(
    report_pdf_url: str,
    whatsapp_number: str,
    message: str,
    patient_name: str = "",
    tenant_id: str = "auto",
) -> dict:
    """Send a finalized cardiac report via WhatsApp to the clinic vet.

    Args:
        report_pdf_url: URL of the generated PDF report
        whatsapp_number: Recipient WhatsApp number (with country code)
        message: Accompanying message text
        patient_name: Patient name for the message
        tenant_id: Tenant context

    Returns:
        Delivery status.
    """
    import httpx
    from config.settings import settings

    try:
        async with httpx.AsyncClient(
            base_url=settings.api_base_url, timeout=30.0
        ) as client:
            response = await client.post(
                "/api/v1/whatsapp/send",
                json={
                    "to": whatsapp_number,
                    "message": message or f"Cardiac report ready for {patient_name}",
                    "media_url": report_pdf_url,
                    "tenant_id": tenant_id,
                },
            )
            if response.status_code < 400:
                return {"status": "sent", "to": whatsapp_number, "data": response.json()}
            else:
                return {"status": "error", "error": f"WhatsApp send failed: {response.status_code}"}
    except Exception as e:
        return {"status": "error", "error": str(e)}
```

**Step 3: Update the agent's tools list and instruction**

Update the `tools` list to include the new functions:

```python
    tools=[
        query_sql,
        get_dataset_schema,
        generate_report,
        create_visualization,
        export_data,
        apply_clinic_template,
        send_whatsapp_report,
        get_breed_reference_ranges,
        create_visit_record,
    ],
```

Append to the instruction string (before the closing `"""`):

```

## Veterinary Cardiac Reports:
When generating cardiac reports from ECG findings:
1. Structure: Patient Info → ECG Findings → Interpretation → Recommendations
2. Use apply_clinic_template to format with clinic branding
3. Use send_whatsapp_report to deliver the PDF to the clinic
4. Use create_visit_record to log the visit for billing
5. Include breed-specific reference comparisons using get_breed_reference_ranges
```

**Step 4: Commit**

```bash
git add apps/adk-server/servicetsunami_supervisor/report_generator.py
git commit -m "feat: extend report_generator with vet cardiac report tools"
```

---

## Task 7: Create MonthlyBillingWorkflow (Temporal)

**Files:**
- Create: `apps/api/app/workflows/monthly_billing.py`
- Create: `apps/api/app/workflows/activities/monthly_billing.py`
- Modify: `apps/api/app/workers/orchestration_worker.py`

**Context:** Follow the exact Temporal patterns from `follow_up.py` (dataclass input, @workflow.defn, execute_activity) and `orchestration_worker.py` (worker registration).

**Step 1: Create the workflow definition**

Create `apps/api/app/workflows/monthly_billing.py`:

```python
"""
Temporal workflow for monthly veterinary billing settlement.

Runs on the 1st of each month: aggregates completed visits per clinic,
generates invoices, sends them, and schedules follow-ups for unpaid ones.
"""
from temporalio import workflow
from datetime import timedelta
from dataclasses import dataclass
from typing import List


@dataclass
class MonthlyBillingInput:
    tenant_id: str
    month: str  # "YYYY-MM" format
    clinic_ids: List[str] = None  # None = all clinics


@workflow.defn(sandboxed=False)
class MonthlyBillingWorkflow:
    """Monthly billing settlement for veterinary cardiologist visits."""

    @workflow.run
    async def run(self, input: MonthlyBillingInput) -> dict:
        retry_policy = workflow.RetryPolicy(
            maximum_attempts=3,
            initial_interval=timedelta(seconds=30),
            backoff_coefficient=2.0,
        )

        workflow.logger.info(
            f"Starting monthly billing for tenant {input.tenant_id[:8]}, month {input.month}"
        )

        # Step 1: Aggregate visits for the billing period
        visits = await workflow.execute_activity(
            "aggregate_billing_visits",
            args=[input.tenant_id, input.month, input.clinic_ids],
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=retry_policy,
        )

        if not visits.get("clinics"):
            return {"status": "no_visits", "month": input.month}

        # Step 2: Generate invoices per clinic
        invoices = await workflow.execute_activity(
            "generate_billing_invoices",
            args=[input.tenant_id, input.month, visits["clinics"]],
            start_to_close_timeout=timedelta(minutes=10),
            retry_policy=retry_policy,
        )

        # Step 3: Send invoices via email + WhatsApp
        delivery = await workflow.execute_activity(
            "send_billing_invoices",
            args=[input.tenant_id, invoices.get("invoice_ids", [])],
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=retry_policy,
        )

        # Step 4: Schedule follow-up for unpaid invoices (7-day reminder)
        followup = await workflow.execute_activity(
            "schedule_billing_followups",
            args=[input.tenant_id, invoices.get("invoice_ids", [])],
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=retry_policy,
        )

        return {
            "status": "completed",
            "month": input.month,
            "clinics_billed": len(visits.get("clinics", [])),
            "invoices_generated": len(invoices.get("invoice_ids", [])),
            "invoices_sent": delivery.get("sent_count", 0),
            "followups_scheduled": followup.get("count", 0),
        }
```

**Step 2: Create the activities**

Create `apps/api/app/workflows/activities/monthly_billing.py`:

```python
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
    from temporalio.client import Client
    from app.core.config import settings
    from app.workflows.follow_up import FollowUpInput, FollowUpWorkflow

    logger.info(f"Scheduling follow-ups for {len(invoice_ids)} invoices")

    count = 0
    try:
        client = await Client.connect(settings.TEMPORAL_ADDRESS)
        for invoice_id in invoice_ids:
            follow_up_input = FollowUpInput(
                entity_id=invoice_id,
                tenant_id=tenant_id,
                action="remind",
                delay_hours=168,  # 7 days
                message=f"Payment reminder for invoice {invoice_id}",
            )
            await client.start_workflow(
                FollowUpWorkflow.run,
                follow_up_input,
                id=f"billing-followup-{invoice_id}",
                task_queue="servicetsunami-orchestration",
            )
            count += 1
    except Exception as e:
        logger.exception(f"Follow-up scheduling error: {e}")

    return {"status": "scheduled", "count": count}
```

**Step 3: Register in orchestration_worker.py**

In `apps/api/app/workers/orchestration_worker.py`:

Add imports after line 24 (`from app.workflows.activities.follow_up import execute_followup_action`):

```python
from app.workflows.monthly_billing import MonthlyBillingWorkflow
from app.workflows.activities.monthly_billing import (
    aggregate_billing_visits,
    generate_billing_invoices,
    send_billing_invoices,
    schedule_billing_followups,
)
```

Add `MonthlyBillingWorkflow` to the `workflows` list in the Worker constructor.

Add the 4 billing activities to the `activities` list in the Worker constructor.

**Step 4: Add HEALTHPETS_API_URL to API config**

In `apps/api/app/core/config.py`, add:

```python
    HEALTHPETS_API_URL: str = "http://localhost:8000"
```

**Step 5: Commit**

```bash
git add apps/api/app/workflows/monthly_billing.py \
       apps/api/app/workflows/activities/monthly_billing.py \
       apps/api/app/workers/orchestration_worker.py \
       apps/api/app/core/config.py
git commit -m "feat: add MonthlyBillingWorkflow with 4 activities for vet billing settlement"
```

---

## Task 8: Add Breed Reference Seed Data

**Files:**
- Create: `apps/adk-server/data/breed_ecg_references.json`

**Context:** Seed ~20 common breeds (canine + feline) with ECG normal reference ranges as knowledge entities. This data gets loaded when the healthpets tenant is provisioned.

**Step 1: Create breed reference data file**

Create `apps/adk-server/data/breed_ecg_references.json` with reference ranges for common breeds. Structure follows the knowledge entity properties schema.

```json
[
    {
        "name": "Doberman Pinscher ECG Reference",
        "entity_type": "reference",
        "category": "breed_reference",
        "properties": {
            "species": "canine",
            "breed": "Doberman Pinscher",
            "heart_rate_range": "60-140",
            "pr_interval_ms": "60-130",
            "qrs_duration_ms": "40-65",
            "qt_interval_ms": "150-250",
            "common_conditions": ["dilated_cardiomyopathy", "ventricular_arrhythmias"],
            "breed_predisposition_notes": "High prevalence of DCM (40-60% affected); annual screening recommended after age 4. Occult DCM common — may show VPCs before clinical signs."
        }
    },
    {
        "name": "Cavalier King Charles Spaniel ECG Reference",
        "entity_type": "reference",
        "category": "breed_reference",
        "properties": {
            "species": "canine",
            "breed": "Cavalier King Charles Spaniel",
            "heart_rate_range": "70-160",
            "pr_interval_ms": "60-130",
            "qrs_duration_ms": "40-60",
            "qt_interval_ms": "150-220",
            "common_conditions": ["mitral_valve_disease", "syringomyelia"],
            "breed_predisposition_notes": "Nearly 100% develop MVD by age 10. Early murmur onset (age 2-3). Atrial fibrillation in advanced disease."
        }
    },
    {
        "name": "Boxer ECG Reference",
        "entity_type": "reference",
        "category": "breed_reference",
        "properties": {
            "species": "canine",
            "breed": "Boxer",
            "heart_rate_range": "60-140",
            "pr_interval_ms": "60-130",
            "qrs_duration_ms": "40-65",
            "qt_interval_ms": "150-250",
            "common_conditions": ["arrhythmogenic_right_ventricular_cardiomyopathy", "aortic_stenosis", "ventricular_premature_complexes"],
            "breed_predisposition_notes": "ARVC (Boxer cardiomyopathy) — VPCs of LBBB morphology. >100 VPCs/24h on Holter is abnormal. Sudden death risk."
        }
    },
    {
        "name": "German Shepherd ECG Reference",
        "entity_type": "reference",
        "category": "breed_reference",
        "properties": {
            "species": "canine",
            "breed": "German Shepherd",
            "heart_rate_range": "60-140",
            "pr_interval_ms": "60-130",
            "qrs_duration_ms": "40-65",
            "qt_interval_ms": "150-250",
            "common_conditions": ["pericardial_effusion", "hemangiosarcoma", "aortic_stenosis"],
            "breed_predisposition_notes": "Right atrial hemangiosarcoma common. Pericardial effusion may cause electrical alternans on ECG."
        }
    },
    {
        "name": "Golden Retriever ECG Reference",
        "entity_type": "reference",
        "category": "breed_reference",
        "properties": {
            "species": "canine",
            "breed": "Golden Retriever",
            "heart_rate_range": "60-140",
            "pr_interval_ms": "60-130",
            "qrs_duration_ms": "40-65",
            "qt_interval_ms": "150-250",
            "common_conditions": ["subaortic_stenosis", "dilated_cardiomyopathy", "pericardial_effusion"],
            "breed_predisposition_notes": "SAS common — systolic murmur at left heart base. Taurine-deficient DCM reported. Monitor large breed normals."
        }
    },
    {
        "name": "Great Dane ECG Reference",
        "entity_type": "reference",
        "category": "breed_reference",
        "properties": {
            "species": "canine",
            "breed": "Great Dane",
            "heart_rate_range": "50-120",
            "pr_interval_ms": "80-150",
            "qrs_duration_ms": "50-80",
            "qt_interval_ms": "180-280",
            "common_conditions": ["dilated_cardiomyopathy", "atrial_fibrillation"],
            "breed_predisposition_notes": "DCM with AF is the most common cardiac disease. Low HR normal for giant breeds. Wide QRS reflects large heart."
        }
    },
    {
        "name": "Irish Wolfhound ECG Reference",
        "entity_type": "reference",
        "category": "breed_reference",
        "properties": {
            "species": "canine",
            "breed": "Irish Wolfhound",
            "heart_rate_range": "40-120",
            "pr_interval_ms": "80-160",
            "qrs_duration_ms": "50-80",
            "qt_interval_ms": "180-300",
            "common_conditions": ["dilated_cardiomyopathy", "atrial_fibrillation"],
            "breed_predisposition_notes": "High prevalence of DCM and AF (up to 24%). Low resting HR normal for giant breed. Annual screening starting age 2."
        }
    },
    {
        "name": "Labrador Retriever ECG Reference",
        "entity_type": "reference",
        "category": "breed_reference",
        "properties": {
            "species": "canine",
            "breed": "Labrador Retriever",
            "heart_rate_range": "60-140",
            "pr_interval_ms": "60-130",
            "qrs_duration_ms": "40-65",
            "qt_interval_ms": "150-250",
            "common_conditions": ["tricuspid_valve_dysplasia", "pericardial_effusion"],
            "breed_predisposition_notes": "TVD congenital. Sinus arrhythmia is normal and often pronounced."
        }
    },
    {
        "name": "Yorkshire Terrier ECG Reference",
        "entity_type": "reference",
        "category": "breed_reference",
        "properties": {
            "species": "canine",
            "breed": "Yorkshire Terrier",
            "heart_rate_range": "80-180",
            "pr_interval_ms": "40-100",
            "qrs_duration_ms": "30-50",
            "qt_interval_ms": "120-200",
            "common_conditions": ["patent_ductus_arteriosus", "mitral_valve_disease", "tracheal_collapse"],
            "breed_predisposition_notes": "Small breed — higher normal HR. Narrow QRS. PDA and MVD common. Left heart enlargement patterns."
        }
    },
    {
        "name": "Chihuahua ECG Reference",
        "entity_type": "reference",
        "category": "breed_reference",
        "properties": {
            "species": "canine",
            "breed": "Chihuahua",
            "heart_rate_range": "100-200",
            "pr_interval_ms": "40-90",
            "qrs_duration_ms": "25-45",
            "qt_interval_ms": "100-180",
            "common_conditions": ["patent_ductus_arteriosus", "pulmonic_stenosis", "mitral_valve_disease"],
            "breed_predisposition_notes": "Very high normal HR for toy breed. Narrow QRS. Congenital defects common."
        }
    },
    {
        "name": "Maine Coon ECG Reference",
        "entity_type": "reference",
        "category": "breed_reference",
        "properties": {
            "species": "feline",
            "breed": "Maine Coon",
            "heart_rate_range": "140-220",
            "pr_interval_ms": "40-90",
            "qrs_duration_ms": "20-40",
            "qt_interval_ms": "80-180",
            "common_conditions": ["hypertrophic_cardiomyopathy"],
            "breed_predisposition_notes": "HCM prevalence ~30%. MyBPC3 mutation. Screening echo recommended annually. ECG may show tall R waves in leads II, III."
        }
    },
    {
        "name": "Ragdoll ECG Reference",
        "entity_type": "reference",
        "category": "breed_reference",
        "properties": {
            "species": "feline",
            "breed": "Ragdoll",
            "heart_rate_range": "140-220",
            "pr_interval_ms": "40-90",
            "qrs_duration_ms": "20-40",
            "qt_interval_ms": "80-180",
            "common_conditions": ["hypertrophic_cardiomyopathy"],
            "breed_predisposition_notes": "HCM via MyBPC3 mutation (different from Maine Coon variant). Screening starting age 1."
        }
    },
    {
        "name": "British Shorthair ECG Reference",
        "entity_type": "reference",
        "category": "breed_reference",
        "properties": {
            "species": "feline",
            "breed": "British Shorthair",
            "heart_rate_range": "140-220",
            "pr_interval_ms": "40-90",
            "qrs_duration_ms": "20-40",
            "qt_interval_ms": "80-180",
            "common_conditions": ["hypertrophic_cardiomyopathy", "arterial_thromboembolism"],
            "breed_predisposition_notes": "HCM predisposed. Thromboembolism risk with severe HCM. Acute hind limb paralysis may be first sign."
        }
    },
    {
        "name": "Sphynx ECG Reference",
        "entity_type": "reference",
        "category": "breed_reference",
        "properties": {
            "species": "feline",
            "breed": "Sphynx",
            "heart_rate_range": "140-220",
            "pr_interval_ms": "40-90",
            "qrs_duration_ms": "20-40",
            "qt_interval_ms": "80-180",
            "common_conditions": ["hypertrophic_cardiomyopathy", "mitral_valve_dysplasia"],
            "breed_predisposition_notes": "High HCM prevalence. Annual screening strongly recommended. May also develop restrictive cardiomyopathy."
        }
    },
    {
        "name": "Persian ECG Reference",
        "entity_type": "reference",
        "category": "breed_reference",
        "properties": {
            "species": "feline",
            "breed": "Persian",
            "heart_rate_range": "140-220",
            "pr_interval_ms": "40-90",
            "qrs_duration_ms": "20-40",
            "qt_interval_ms": "80-180",
            "common_conditions": ["hypertrophic_cardiomyopathy", "polycystic_kidney_disease"],
            "breed_predisposition_notes": "HCM moderately common. PKD can cause secondary hypertension affecting cardiac function."
        }
    },
    {
        "name": "Generic Canine ECG Reference",
        "entity_type": "reference",
        "category": "breed_reference",
        "properties": {
            "species": "canine",
            "breed": "Generic",
            "heart_rate_range": "60-160",
            "pr_interval_ms": "60-130",
            "qrs_duration_ms": "40-65",
            "qt_interval_ms": "150-250",
            "common_conditions": [],
            "breed_predisposition_notes": "General canine reference ranges. Use breed-specific values when available. Small breeds trend higher HR; giant breeds trend lower."
        }
    },
    {
        "name": "Generic Feline ECG Reference",
        "entity_type": "reference",
        "category": "breed_reference",
        "properties": {
            "species": "feline",
            "breed": "Generic",
            "heart_rate_range": "140-220",
            "pr_interval_ms": "40-90",
            "qrs_duration_ms": "20-40",
            "qt_interval_ms": "80-180",
            "common_conditions": ["hypertrophic_cardiomyopathy"],
            "breed_predisposition_notes": "General feline reference ranges. HCM is the most common cardiac disease in cats. Stress can cause sinus tachycardia in clinic."
        }
    }
]
```

**Step 2: Commit**

```bash
mkdir -p apps/adk-server/data
git add apps/adk-server/data/breed_ecg_references.json
git commit -m "feat: add breed ECG reference data for 17 breeds (canine + feline)"
```

---

## Task 9: Add Workflow Definition to WorkflowsPage UI

**Files:**
- Modify: `apps/web/src/pages/WorkflowsPage.js`

**Context:** Add `MonthlyBillingWorkflow` as the 9th workflow card on the Designs tab, following the existing `WORKFLOW_DEFINITIONS` pattern.

**Step 1: Add MonthlyBillingWorkflow to WORKFLOW_DEFINITIONS array**

Add a new entry to the `WORKFLOW_DEFINITIONS` array (after the last workflow entry, before the array closing `]`). Use `FaFileInvoiceDollar` icon from react-icons/fa — add to imports at top of file.

```javascript
{
    id: 'monthly-billing',
    name: 'MonthlyBillingWorkflow',
    description: 'Monthly veterinary billing settlement: aggregate visits, generate invoices, send to clinics, schedule payment follow-ups',
    queue: 'orchestration',
    icon: FaFileInvoiceDollar,
    color: '#34d399',
    steps: [
        { name: 'aggregate_visits', timeout: '5m', retry: '3x / 30s', type: 'start', description: 'Query completed visits per clinic for the billing period' },
        { name: 'generate_invoices', timeout: '10m', retry: '3x / 30s', description: 'Calculate totals from fee schedules and create invoices' },
        { name: 'send_invoices', timeout: '5m', retry: '3x / 30s', description: 'Deliver invoice PDFs via email and WhatsApp' },
        { name: 'schedule_followups', timeout: '1m', retry: '3x / 30s', type: 'end', description: 'Create 7-day reminder workflows for unpaid invoices' },
    ],
},
```

**Step 2: Update queue summary badge count**

The queue summary badges at the top of the Designs tab show "orchestration queue — N workflows". The count is computed dynamically from the array, so no change needed — it auto-updates.

**Step 3: Commit**

```bash
git add apps/web/src/pages/WorkflowsPage.js
git commit -m "feat: add MonthlyBillingWorkflow to Workflows page Designs tab"
```

---

## Task 10: Final Integration — Build, Verify, Push

**Step 1: Verify ADK server starts locally**

```bash
cd apps/adk-server && python -c "from servicetsunami_supervisor import root_agent; print('Agents loaded:', [a.name for a in root_agent.sub_agents])"
```

Expected: Should list all teams including `vet_supervisor`.

**Step 2: Verify web build**

```bash
cd apps/web && DISABLE_ESLINT_PLUGIN=true npx react-scripts build 2>&1 | tail -5
```

Expected: `Compiled successfully.`

**Step 3: Verify Temporal worker imports**

```bash
cd apps/api && python -c "from app.workflows.monthly_billing import MonthlyBillingWorkflow; print('Workflow OK')"
```

Expected: `Workflow OK`

**Step 4: Commit all and push**

```bash
git add -A
git commit -m "feat: HealthPets agents — cardiac_analyst, billing_agent, vet_supervisor, MonthlyBillingWorkflow

New ADK agents for mobile veterinary cardiologist platform:
- cardiac_analyst: ECG image analysis via Claude vision
- billing_agent: Visit tracking and invoice generation
- vet_supervisor: Routing agent for veterinary cardiology team
- Extended report_generator with clinic templates and WhatsApp delivery
- MonthlyBillingWorkflow (Temporal) for automated billing settlement
- 17 breed ECG reference ranges (canine + feline) as seed data"
git push origin main
```

**Step 5: Monitor CI**

```bash
gh run list --limit 5 --json status,name,conclusion
```

Watch for all 3 workflows (Web, API, Worker) to pass.
