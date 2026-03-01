from fastapi import APIRouter, Request
from typing import Dict, Any
import logging

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/hca")
async def receive_hca_webhook(request: Request) -> Dict[str, Any]:
    """Receive webhook events from HCA Deal Intelligence."""
    body = await request.json()
    event = body.get("event", "")
    data = body.get("data", {})

    logger.info(f"Received HCA webhook: {event}")

    header_event = request.headers.get("X-HCA-Event", "")
    if header_event and header_event != event:
        logger.warning(f"Event header mismatch: header={header_event}, body={event}")

    if event == "prospect.created":
        logger.info(f"New prospect: {data.get('company_name')} ({data.get('industry')})")
    elif event == "prospect.scored":
        logger.info(f"Prospect scored: {data.get('company_name')} = {data.get('score')}/100")
    elif event == "prospect.stage_changed":
        logger.info(f"Stage change: {data.get('company_name')} {data.get('old_stage')} -> {data.get('new_stage')}")
    elif event == "outreach.status_changed":
        logger.info(f"Outreach status: {data.get('new_status')} for prospect {data.get('prospect_id')}")
    elif event == "prospect.research_completed":
        logger.info(f"Research completed: {data.get('company_name')}")
    elif event == "integration.test":
        logger.info("Test webhook received successfully")
    else:
        logger.warning(f"Unknown HCA event: {event}")

    return {"status": "received", "event": event}
