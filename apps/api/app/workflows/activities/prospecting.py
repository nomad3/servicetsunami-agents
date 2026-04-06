"""
Prospecting pipeline activities — Module 1 (research) + Module 2 (score/qualify/notify).
"""

import uuid
from typing import Any, Dict, List, Optional

import httpx
from temporalio import activity

from app.core.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

_API_BASE = settings.API_BASE_URL if hasattr(settings, "API_BASE_URL") else "http://api:8000"
_INTERNAL_KEY = settings.API_INTERNAL_KEY if hasattr(settings, "API_INTERNAL_KEY") else settings.MCP_API_KEY


def _headers(tenant_id: str) -> dict:
    return {"X-Internal-Key": _INTERNAL_KEY, "X-Tenant-Id": tenant_id}


# ---------------------------------------------------------------------------
# Module 1 — Lead Research / Enrichment
# ---------------------------------------------------------------------------

@activity.defn
async def prospect_research(
    tenant_id: str, entity_ids: List[str], params: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Enrich prospect entities with external data.

    Calls the internal knowledge API to fetch entity details, then
    enriches properties (company size, pain signals, tech stack) via
    web-scraping the company website when available.

    Falls back gracefully — enrichment failures do not abort the pipeline.
    """
    enriched = []
    failed = []

    async with httpx.AsyncClient(timeout=30.0) as client:
        for eid in entity_ids:
            try:
                # Fetch entity
                resp = await client.get(
                    f"{_API_BASE}/api/v1/knowledge/entities/{eid}",
                    headers=_headers(tenant_id),
                )
                if resp.status_code != 200:
                    failed.append(eid)
                    continue

                entity = resp.json()
                props = entity.get("properties") or {}

                # Determine company website to scrape
                website = props.get("website") or props.get("company_website")
                company = props.get("company") or entity.get("name", "")

                enriched_props = {}

                if website:
                    # Try to scrape website for pain signals via MCP scraper
                    scrape_resp = await client.post(
                        f"{_API_BASE}/api/v1/internal/scrape",
                        headers=_headers(tenant_id),
                        json={"url": website, "extract": ["description", "team_size", "tech_stack"]},
                    )
                    if scrape_resp.status_code == 200:
                        scraped = scrape_resp.json()
                        enriched_props["website_description"] = scraped.get("description", "")[:500]
                        enriched_props["team_size_signal"] = scraped.get("team_size")

                # Mark as enriched
                enriched_props["enriched"] = True
                enriched_props["enrichment_source"] = "website_scrape" if website else "manual"

                # Patch entity with enriched properties
                merged = {**props, **{k: v for k, v in enriched_props.items() if v}}
                await client.patch(
                    f"{_API_BASE}/api/v1/knowledge/entities/{eid}",
                    headers=_headers(tenant_id),
                    json={"properties": merged},
                )
                enriched.append(eid)

            except Exception as e:
                logger.warning(f"Enrichment failed for entity {eid}: {e}")
                failed.append(eid)

    activity.logger.info(
        f"prospect_research done: tenant={tenant_id} enriched={len(enriched)} failed={len(failed)}"
    )
    return {
        "status": "completed",
        "enriched_count": len(enriched),
        "failed_count": len(failed),
        "entity_ids": enriched,
    }


# ---------------------------------------------------------------------------
# Module 2.1 — Scoring (delegates to qualify_lead internal endpoint)
# ---------------------------------------------------------------------------

@activity.defn
async def prospect_score(
    tenant_id: str, entity_ids: List[str], rubric_id: Optional[str]
) -> Dict[str, Any]:
    """
    Score each prospect by calling the internal qualify_lead endpoint.

    Reuses existing BANT scoring logic — no duplicate implementation.
    Returns a dict mapping entity_id → score.
    """
    scores: Dict[str, float] = {}
    failed = []

    async with httpx.AsyncClient(timeout=20.0) as client:
        for eid in entity_ids:
            try:
                resp = await client.post(
                    f"{_API_BASE}/api/v1/knowledge/leads/{eid}/qualify",
                    headers=_headers(tenant_id),
                    json={"rubric_id": rubric_id} if rubric_id else {},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    scores[eid] = data.get("score", 0.0)
                else:
                    failed.append(eid)
                    scores[eid] = 0.0
            except Exception as e:
                logger.warning(f"Scoring failed for {eid}: {e}")
                failed.append(eid)
                scores[eid] = 0.0

    activity.logger.info(
        f"prospect_score done: tenant={tenant_id} scored={len(scores)} failed={len(failed)}"
    )
    return {
        "status": "completed",
        "scores": scores,
        "entity_ids": list(scores.keys()),
        "failed_count": len(failed),
    }


# ---------------------------------------------------------------------------
# Module 2.3 — Threshold routing (qualify vs disqualify vs review)
# ---------------------------------------------------------------------------

@activity.defn
async def prospect_qualify(
    tenant_id: str, entity_ids: List[str], threshold: int
) -> Dict[str, Any]:
    """
    Route scored prospects by threshold:
      score >= threshold   → 'qualified' + trigger outreach
      40 <= score < threshold → 'review' + notify Simon
      score < 40           → 'disqualified' + log reason

    Reads scores from entity.score field (set by prospect_score).
    """
    qualified = []
    needs_review = []
    disqualified = []

    async with httpx.AsyncClient(timeout=20.0) as client:
        for eid in entity_ids:
            try:
                resp = await client.get(
                    f"{_API_BASE}/api/v1/knowledge/entities/{eid}",
                    headers=_headers(tenant_id),
                )
                if resp.status_code != 200:
                    continue

                entity = resp.json()
                score = entity.get("score") or 0.0

                if score >= threshold:
                    new_stage = "qualified"
                    qualified.append(eid)
                elif score >= 40:
                    new_stage = "review"
                    needs_review.append(eid)
                else:
                    new_stage = "disqualified"
                    disqualified.append(eid)

                await client.patch(
                    f"{_API_BASE}/api/v1/sales/leads/{eid}/stage",
                    headers=_headers(tenant_id),
                    json={"stage": new_stage, "reason": f"Auto-qualified: score={score:.0f}"},
                )

            except Exception as e:
                logger.warning(f"Qualify routing failed for {eid}: {e}")

    activity.logger.info(
        f"prospect_qualify done: qualified={len(qualified)} review={len(needs_review)} disqualified={len(disqualified)}"
    )
    return {
        "status": "completed",
        "qualified": qualified,
        "needs_review": needs_review,
        "disqualified": disqualified,
    }


# ---------------------------------------------------------------------------
# Module 3 — Outreach drafting
# ---------------------------------------------------------------------------

@activity.defn
async def prospect_outreach(
    tenant_id: str, entity_ids: List[str], template: str
) -> Dict[str, Any]:
    """Draft personalised outreach for each qualified entity via draft_outreach MCP tool."""
    drafted = []
    failed = []

    async with httpx.AsyncClient(timeout=30.0) as client:
        for eid in entity_ids:
            try:
                resp = await client.post(
                    f"{_API_BASE}/api/v1/internal/mcp-call",
                    headers=_headers(tenant_id),
                    json={
                        "tool": "draft_outreach",
                        "params": {
                            "lead_entity_id": eid,
                            "channel": "email",
                            "template_hint": template,
                            "tenant_id": tenant_id,
                        },
                    },
                )
                if resp.status_code == 200:
                    drafted.append(eid)
                else:
                    failed.append(eid)
            except Exception as e:
                logger.warning(f"Outreach draft failed for {eid}: {e}")
                failed.append(eid)

    return {
        "status": "completed",
        "drafted_count": len(drafted),
        "failed_count": len(failed),
        "entity_ids": drafted,
    }


# ---------------------------------------------------------------------------
# Module — Pipeline notification
# ---------------------------------------------------------------------------

@activity.defn
async def prospect_notify(
    tenant_id: str, results: Dict[str, Any]
) -> Dict[str, Any]:
    """Create a pipeline run summary notification for the tenant."""
    qualified = results.get("qualified", [])
    needs_review = results.get("needs_review", [])
    disqualified = results.get("disqualified", [])
    drafted = results.get("drafted_count", 0)

    lines = ["📊 *Prospecting pipeline complete*"]
    if qualified:
        lines.append(f"✅ {len(qualified)} qualified — outreach drafted")
    if needs_review:
        lines.append(f"👀 {len(needs_review)} need your review")
    if disqualified:
        lines.append(f"❌ {len(disqualified)} disqualified (low score)")
    if drafted:
        lines.append(f"✉️ {drafted} outreach drafts ready to send")

    message = "\n".join(lines)

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            await client.post(
                f"{_API_BASE}/api/v1/notifications",
                headers=_headers(tenant_id),
                json={
                    "source": "system",
                    "priority": "high",
                    "title": "Prospecting pipeline complete",
                    "body": message,
                },
            )
        except Exception as e:
            logger.warning(f"Notification failed: {e}")

    return {"status": "completed", "message": message}
