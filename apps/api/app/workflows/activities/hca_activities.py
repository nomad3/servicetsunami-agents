"""
Temporal activities for HCA (deal pipeline) integration.

Activities:
- hca_discover_prospects: AI prospect discovery via HCA
- hca_score_prospects: Score batch of prospects for sell-likelihood
- hca_generate_research: Generate research briefs for prospects
- hca_generate_outreach: Generate outreach drafts for prospects
- hca_advance_pipeline: Advance prospects to a new pipeline stage
- hca_sync_knowledge_graph: Fetch prospect data from HCA and store in KG
"""

from temporalio import activity
from typing import Dict, Any, List
import uuid
import httpx

from app.core.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Shared HTTP client
# ---------------------------------------------------------------------------

_client: httpx.AsyncClient | None = None


def _get_hca_client() -> httpx.AsyncClient:
    """Return a singleton httpx.AsyncClient for HCA API calls."""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            base_url=settings.HCA_API_URL,
            headers={
                "Authorization": f"Bearer {settings.HCA_SERVICE_KEY}",
                "Content-Type": "application/json",
            },
            timeout=60.0,
        )
    return _client


# ---------------------------------------------------------------------------
# Activities
# ---------------------------------------------------------------------------


@activity.defn
async def hca_discover_prospects(
    tenant_id: str, industry: str, criteria: Dict[str, Any]
) -> Dict[str, Any]:
    """Discover prospects via HCA and persist results.

    POST /api/integration/discover-and-save
    """
    activity.logger.info(f"Discovering prospects for industry={industry}")
    client = _get_hca_client()
    try:
        resp = await client.post(
            "/api/integration/discover-and-save",
            json={
                "tenant_id": tenant_id,
                "industry": industry,
                "criteria": criteria,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        prospect_ids = [str(p["id"]) for p in data.get("prospects", [])]
        return {"status": "success", "prospect_ids": prospect_ids}
    except httpx.HTTPStatusError as exc:
        logger.error(f"HCA discover failed: {exc.response.status_code} {exc.response.text}")
        return {"status": "error", "error": exc.response.text}
    except Exception as exc:
        logger.error(f"HCA discover error: {exc}")
        return {"status": "error", "error": str(exc)}


@activity.defn
async def hca_score_prospects(
    tenant_id: str, prospect_ids: List[str]
) -> Dict[str, Any]:
    """Score a batch of prospects for sell-likelihood.

    POST /api/integration/score
    """
    activity.logger.info(f"Scoring {len(prospect_ids)} prospects")
    client = _get_hca_client()
    try:
        resp = await client.post(
            "/api/integration/score",
            json={
                "tenant_id": tenant_id,
                "prospect_ids": prospect_ids,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return {"status": "success", "results": data.get("results", [])}
    except httpx.HTTPStatusError as exc:
        logger.error(f"HCA score failed: {exc.response.status_code} {exc.response.text}")
        return {"status": "error", "results": [], "error": exc.response.text}
    except Exception as exc:
        logger.error(f"HCA score error: {exc}")
        return {"status": "error", "results": [], "error": str(exc)}


@activity.defn
async def hca_generate_research(
    tenant_id: str, prospect_ids: List[str]
) -> Dict[str, Any]:
    """Generate research briefs for each prospect.

    POST /api/integration/research
    """
    activity.logger.info(f"Generating research for {len(prospect_ids)} prospects")
    client = _get_hca_client()
    try:
        resp = await client.post(
            "/api/integration/research",
            json={
                "tenant_id": tenant_id,
                "prospect_ids": prospect_ids,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return {"status": "success", "count": data.get("count", len(prospect_ids))}
    except httpx.HTTPStatusError as exc:
        logger.error(f"HCA research failed: {exc.response.status_code} {exc.response.text}")
        return {"status": "error", "count": 0, "error": exc.response.text}
    except Exception as exc:
        logger.error(f"HCA research error: {exc}")
        return {"status": "error", "count": 0, "error": str(exc)}


@activity.defn
async def hca_generate_outreach(
    tenant_id: str, prospect_ids: List[str], outreach_type: str
) -> Dict[str, Any]:
    """Generate outreach drafts for each prospect.

    POST /api/integration/outreach
    """
    activity.logger.info(f"Generating {outreach_type} outreach for {len(prospect_ids)} prospects")
    client = _get_hca_client()
    try:
        resp = await client.post(
            "/api/integration/outreach",
            json={
                "tenant_id": tenant_id,
                "prospect_ids": prospect_ids,
                "outreach_type": outreach_type,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return {"status": "success", "count": data.get("count", len(prospect_ids))}
    except httpx.HTTPStatusError as exc:
        logger.error(f"HCA outreach failed: {exc.response.status_code} {exc.response.text}")
        return {"status": "error", "count": 0, "error": exc.response.text}
    except Exception as exc:
        logger.error(f"HCA outreach error: {exc}")
        return {"status": "error", "count": 0, "error": str(exc)}


@activity.defn
async def hca_advance_pipeline(
    tenant_id: str, prospect_ids: List[str], new_stage: str
) -> Dict[str, Any]:
    """Advance each prospect to a new pipeline stage.

    POST /api/integration/advance
    """
    activity.logger.info(f"Advancing {len(prospect_ids)} prospects to stage={new_stage}")
    client = _get_hca_client()
    try:
        resp = await client.post(
            "/api/integration/advance",
            json={
                "tenant_id": tenant_id,
                "prospect_ids": prospect_ids,
                "new_stage": new_stage,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return {"status": "success", "count": data.get("count", len(prospect_ids))}
    except httpx.HTTPStatusError as exc:
        logger.error(f"HCA advance failed: {exc.response.status_code} {exc.response.text}")
        return {"status": "error", "count": 0, "error": exc.response.text}
    except Exception as exc:
        logger.error(f"HCA advance error: {exc}")
        return {"status": "error", "count": 0, "error": str(exc)}


@activity.defn
async def hca_sync_knowledge_graph(
    tenant_id: str, prospect_ids: List[str]
) -> Dict[str, Any]:
    """Fetch prospect details from HCA and store entities in the knowledge graph.

    GET /api/integration/prospects/:id for each prospect, then use
    KnowledgeExtractionService to extract and persist entities.
    """
    activity.logger.info(f"Syncing {len(prospect_ids)} prospects to knowledge graph")

    from app.db.session import SessionLocal
    from app.services.knowledge_extraction import KnowledgeExtractionService

    client = _get_hca_client()
    db = SessionLocal()
    synced = 0
    try:
        extraction_service = KnowledgeExtractionService()
        for prospect_id in prospect_ids:
            try:
                resp = await client.get(
                    f"/api/integration/prospects/{prospect_id}",
                    params={"tenant_id": tenant_id},
                )
                resp.raise_for_status()
                prospect = resp.json()

                # Build a textual summary for entity extraction
                content = (
                    f"Company: {prospect.get('company_name', 'Unknown')}\n"
                    f"Industry: {prospect.get('industry', '')}\n"
                    f"Revenue: {prospect.get('revenue', '')}\n"
                    f"Location: {prospect.get('location', '')}\n"
                    f"Description: {prospect.get('description', '')}\n"
                )

                extraction_service.extract_from_content(
                    db=db,
                    tenant_id=uuid.UUID(tenant_id),
                    content=content,
                    content_type="plain_text",
                    source_url=f"hca://prospects/{prospect_id}",
                    entity_schema={"entity_type": "prospect"},
                )
                synced += 1
            except Exception as exc:
                logger.warning(f"Failed to sync prospect {prospect_id}: {exc}")

        return {"status": "success", "count": synced}
    except Exception as exc:
        logger.error(f"KG sync error: {exc}")
        return {"status": "error", "count": synced, "error": str(exc)}
    finally:
        db.close()
