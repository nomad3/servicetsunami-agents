"""
Webhook handler for HCA Deal Intelligence events.

Receives webhook events from the HCA backend and triggers appropriate
Temporal workflows or stores data for agent consumption.
"""

from fastapi import APIRouter, Request
from typing import Dict, Any
import uuid
import logging

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/hca")
async def receive_hca_webhook(request: Request) -> Dict[str, Any]:
    """Receive webhook events from HCA Deal Intelligence.

    Events:
    - prospect.created: Log new prospect, optionally start scoring workflow
    - prospect.scored: If score is high, trigger research + outreach pipeline
    - prospect.stage_changed: Log stage transition, notify relevant agents
    - prospect.research_completed: Log research completion
    - outreach.status_changed: Log outreach status changes
    - integration.test: Echo test
    """
    body = await request.json()
    event = body.get("event", "")
    data = body.get("data", {})

    # Validate event header matches body
    header_event = request.headers.get("X-HCA-Event", "")
    if header_event and header_event != event:
        logger.warning(f"Event header mismatch: header={header_event}, body={event}")

    logger.info(f"Received HCA webhook: {event} | data keys: {list(data.keys())}")

    triggered_workflow = None

    if event == "prospect.created":
        logger.info(f"New prospect: {data.get('company_name')} ({data.get('industry')})")
        # If created via integration (batch), no extra action needed — scoring happens in HCA
        # If created manually and source is not 'st_integration', could trigger scoring

    elif event == "prospect.scored":
        score = data.get("score", 0)
        prospect_id = data.get("prospect_id")
        company_name = data.get("company_name", "Unknown")
        logger.info(f"Prospect scored: {company_name} = {score}/100")

        # High-score prospects: trigger research + outreach pipeline
        if score >= 70 and prospect_id:
            try:
                from app.services.dynamic_workflow_launcher import start_dynamic_workflow_by_name
                prospect_data = {
                    "tenant_id": data.get("tenant_id", "default"),
                    "industry": data.get("industry", ""),
                    "criteria": {},
                    "score_threshold": 70,
                    "outreach_type": "cold_email",
                    "skip_discovery": True,
                    "prospect_ids": [str(prospect_id)],
                }
                temporal_wf_id = await start_dynamic_workflow_by_name(
                    "Deal Pipeline", data.get("tenant_id", "default"),
                    input_data=prospect_data,
                )
                triggered_workflow = temporal_wf_id
                logger.info(f"Started Deal Pipeline workflow {temporal_wf_id} for high-scorer {company_name}")
            except Exception as exc:
                logger.error(f"Failed to start workflow for prospect {prospect_id}: {exc}")

    elif event == "prospect.stage_changed":
        logger.info(
            f"Stage change: prospect {data.get('prospect_id')} -> {data.get('new_stage')}"
            f" (source: {data.get('source', 'unknown')})"
        )

    elif event == "prospect.research_completed":
        logger.info(f"Research completed: {data.get('company_name')}")

    elif event == "outreach.status_changed":
        logger.info(f"Outreach status: {data.get('new_status')} for prospect {data.get('prospect_id')}")

    elif event == "integration.test":
        logger.info("Test webhook received successfully")

    else:
        logger.warning(f"Unknown HCA event: {event}")

    result: Dict[str, Any] = {"status": "received", "event": event}
    if triggered_workflow:
        result["triggered_workflow"] = triggered_workflow
    return result
