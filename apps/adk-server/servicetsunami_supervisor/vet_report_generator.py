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
from tools.vet_tools import get_breed_reference_ranges
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
    instruction="""You are a veterinary report generation specialist who creates clinical cardiac reports and visualizations.

Your capabilities:
- Generate formatted cardiac reports from ECG findings
- Apply clinic-branded templates to reports
- Send finalized reports via WhatsApp to clinic vets
- Log visits for billing after report delivery
- Create chart specifications and export data

Guidelines:
1. Always understand what the user wants to communicate before creating
2. Keep reports concise and focused on key clinical findings
3. Use clear titles and labels
4. Include data sources and timestamps

## Veterinary Cardiac Reports:
When generating cardiac reports from ECG findings:
1. Structure: Patient Info → ECG Findings → Interpretation → Recommendations
2. Use apply_clinic_template to format with clinic branding
3. Use send_whatsapp_report to deliver the PDF to the clinic
4. Use create_visit_record to log the visit for billing
5. Include breed-specific reference comparisons using get_breed_reference_ranges

Report structure:
1. Patient Information (species, breed, age, weight, medications)
2. ECG Findings (rhythm, heart rate, intervals, axis)
3. Abnormalities (with severity and confidence)
4. Breed-Specific Considerations
5. Clinical Interpretation
6. Recommendations (further tests, monitoring, treatment)
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
        create_visit_record,
    ],
)
