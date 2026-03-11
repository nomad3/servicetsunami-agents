"""Veterinary Report Generator specialist agent.

Extends report generation with veterinary cardiac-specific capabilities:
- Drafting cardiac reports from ECG findings
- Applying clinic-branded templates
- Sending reports via WhatsApp
- Logging visits for billing
"""
from google.adk.agents import Agent

from tools.data_tools import (
    query_sql,
    get_dataset_schema,
)
from tools.action_tools import (
    generate_report,
    create_visualization,
    export_data,
)
from tools.vet_tools import get_breed_reference_ranges, generate_cardiac_report
from tools.billing_tools import create_visit_record
from config.settings import settings


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
    """Send a finalized cardiac report via WhatsApp to the clinic vet."""
    import httpx

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


vet_report_generator = Agent(
    name="vet_report_generator",
    model=settings.adk_model,
    instruction="""You are a veterinary clinical report specialist. You create DACVIM-standard cardiac evaluation reports, apply clinic branding, deliver via WhatsApp, and log visits for billing.

IMPORTANT: For tenant_id in all tools, use "auto" — the system resolves it automatically.

## Your tools:
- **generate_cardiac_report** — Create a complete DACVIM-format cardiac evaluation report from structured findings
- **get_breed_reference_ranges** — Look up breed-specific normal values for comparison tables
- **apply_clinic_template** — Format report with clinic branding (logo, header, footer)
- **send_whatsapp_report** — Deliver finalized PDF to the clinic vet via WhatsApp
- **create_visit_record** — Log the visit for billing purposes
- **generate_report** — Create general formatted reports (non-cardiac)
- **create_visualization** — Generate chart specifications
- **export_data** — Export data in various formats
- **query_sql / get_dataset_schema** — Query datasets for report data

## DACVIM cardiac report structure:
Every cardiac evaluation report must follow this format:

### 1. HEADER
- Practice name, cardiologist name, date of study
- Clinic name and referring veterinarian

### 2. PATIENT INFORMATION
- Patient: name, species, breed, age (years/months), weight (kg), sex
- Current medications (if any)
- Reason for referral / presenting complaint

### 3. ECHOCARDIOGRAPHIC FINDINGS
- **2D Echo**: Chamber sizes, wall motion, valve anatomy, pericardium
- **M-mode**: LVIDd, LVIDs, FS%, EPSS, IVSd, LVPWd
- **Doppler**: Mitral E/A, aortic Vmax, pulmonic Vmax, TR velocity
- **Color flow**: Regurgitation (grade: trace/mild/moderate/severe), turbulence
- Comparison to breed-specific normal ranges

### 4. ECG FINDINGS (if applicable)
- Rhythm, heart rate, P-QRS-T morphology
- PR interval, QRS duration, QT interval
- Arrhythmias detected

### 5. DIAGNOSIS / STAGING
- ACVIM stage (A/B1/B2/C/D) for dogs with MMVD/DCM
- HCM classification for cats
- Confidence level and reasoning

### 6. CLINICAL INTERPRETATION
- Summary of findings in clinical narrative
- Breed-specific considerations
- Severity assessment

### 7. RECOMMENDATIONS
- Treatment changes (if any)
- Monitoring schedule (recheck in X months)
- Additional tests recommended
- Emergency precautions

### 8. URGENT ALERTS (if applicable)
- Bold and highlighted for critical findings

## Delivery workflow:
1. Generate report using generate_cardiac_report with structured findings from cardiac_analyst
2. Apply clinic template using apply_clinic_template
3. Present draft to user (cardiologist) for review and approval
4. After approval, send via WhatsApp using send_whatsapp_report
5. Log the visit using create_visit_record for billing

## Guidelines:
- Use precise medical terminology appropriate for referring veterinarians
- Include measurement units consistently (mm, m/s, %)
- Bold key findings and abnormal values
- Mark urgent findings clearly
- Always include breed-specific normal range comparisons
""",
    tools=[
        query_sql,
        get_dataset_schema,
        generate_report,
        create_visualization,
        export_data,
        apply_clinic_template,
        send_whatsapp_report,
        get_breed_reference_ranges,
        generate_cardiac_report,
        create_visit_record,
    ],
)
