"""Competitor tracking MCP tools.

Competitor tracking and analysis tools.
Manages competitor entities in the knowledge graph via the internal API.
Competitors are stored as knowledge entities with category="competitor".
"""
import logging
from typing import Optional

import httpx
from mcp.server.fastmcp import Context

from src.mcp_app import mcp
from src.mcp_auth import resolve_tenant_id

logger = logging.getLogger(__name__)

# Keywords used to filter ad/campaign-related observations
_AD_KEYWORDS = {"campaign", "ad", "ads", "advertisement", "promotion", "sponsored"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_api_base_url() -> str:
    from src.config import settings
    return settings.API_BASE_URL.rstrip("/")


def _get_internal_key() -> str:
    from src.config import settings
    return settings.API_INTERNAL_KEY


async def _api_post(path: str, json: dict) -> dict:
    api_base_url = _get_api_base_url()
    internal_key = _get_internal_key()
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{api_base_url}{path}",
            headers={"X-Internal-Key": internal_key},
            json=json,
        )
        resp.raise_for_status()
        return resp.json()


async def _api_get(path: str, params: dict = None) -> dict:
    api_base_url = _get_api_base_url()
    internal_key = _get_internal_key()
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            f"{api_base_url}{path}",
            headers={"X-Internal-Key": internal_key},
            params=params or {},
        )
        resp.raise_for_status()
        return resp.json()


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def add_competitor(
    name: str,
    tenant_id: str = "",
    website: str = "",
    facebook_url: str = "",
    instagram_url: str = "",
    tiktok_url: str = "",
    google_ads_advertiser_id: str = "",
    monitor_frequency: str = "weekly",
    notes: str = "",
    ctx: Context = None,
) -> dict:
    """Add a new competitor to track in the knowledge graph.

    Creates a knowledge entity with category="competitor" and entity_type="company",
    storing social/ad profile URLs as properties. Automatically links the competitor
    to the tenant's own company entity via a "competes_with" relation if found.

    Args:
        name: Competitor company name. Required.
        tenant_id: Tenant UUID (resolved from session if omitted).
        website: Competitor website URL.
        facebook_url: Facebook page or profile URL.
        instagram_url: Instagram profile URL.
        tiktok_url: TikTok profile URL.
        google_ads_advertiser_id: Google Ads Transparency Center advertiser ID.
        monitor_frequency: How often to monitor: "daily", "weekly", "monthly". Default "weekly".
        notes: Free-form notes about this competitor.
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with status, entity_id, and confirmation message.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"error": "tenant_id is required."}
    if not name:
        return {"error": "Competitor name is required."}

    properties = {k: v for k, v in {
        "website_url": website,
        "facebook_url": facebook_url,
        "instagram_url": instagram_url,
        "tiktok_url": tiktok_url,
        "google_ads_advertiser_id": google_ads_advertiser_id,
        "monitor_frequency": monitor_frequency,
    }.items() if v}

    try:
        result = await _api_post(
            "/api/v1/knowledge/entities/internal",
            {
                "tenant_id": tid,
                "name": name,
                "entity_type": "company",
                "category": "competitor",
                "description": notes or f"Competitor: {name}",
                "properties": properties,
                "confidence": 1.0,
            },
        )
        entity_id = result.get("id")
        return {
            "status": "success",
            "entity_id": entity_id,
            "name": name,
            "message": f"Competitor '{name}' added to tracking.",
        }
    except Exception as e:
        logger.exception("add_competitor failed")
        return {"error": f"Failed to add competitor: {str(e)}"}


@mcp.tool()
async def list_competitors(
    tenant_id: str = "",
    ctx: Context = None,
) -> dict:
    """List all active (non-archived) competitors for the tenant.

    Args:
        tenant_id: Tenant UUID (resolved from session if omitted).
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with status and list of competitor entities.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"error": "tenant_id is required."}

    try:
        result = await _api_get(
            "/api/v1/knowledge/entities/internal",
            params={
                "tenant_id": tid,
                "category": "competitor",
                "entity_type": "company",
                "exclude_archived": "true",
            },
        )
        competitors = result if isinstance(result, list) else result.get("entities", [])
        return {
            "status": "success",
            "competitors": competitors,
            "count": len(competitors),
        }
    except Exception as e:
        logger.exception("list_competitors failed")
        return {"error": f"Failed to list competitors: {str(e)}"}


@mcp.tool()
async def remove_competitor(
    name: str,
    tenant_id: str = "",
    ctx: Context = None,
) -> dict:
    """Archive a competitor so it no longer appears in active lists.

    The entity is not deleted and can be restored.

    Args:
        name: Competitor company name. Required.
        tenant_id: Tenant UUID (resolved from session if omitted).
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with status and confirmation message.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"error": "tenant_id is required."}
    if not name:
        return {"error": "Competitor name is required."}

    try:
        # Find the competitor entity first
        search_result = await _api_get(
            "/api/v1/knowledge/entities/internal/search",
            params={"tenant_id": tid, "q": name, "entity_type": "company", "limit": 10},
        )
        entities = search_result if isinstance(search_result, list) else search_result.get("entities", [])

        competitor = None
        for ent in entities:
            if ent.get("category") == "competitor" and ent.get("name", "").lower() == name.lower():
                competitor = ent
                break
        if not competitor:
            for ent in entities:
                if ent.get("category") == "competitor":
                    competitor = ent
                    break

        if not competitor:
            return {"error": f"Competitor '{name}' not found."}

        entity_id = competitor["id"]

        # Archive via PATCH
        await _api_post(
            f"/api/v1/knowledge/entities/{entity_id}/internal/archive",
            {"tenant_id": tid},
        )

        return {
            "status": "success",
            "entity_id": entity_id,
            "name": name,
            "message": f"Competitor '{name}' archived.",
        }
    except Exception as e:
        logger.exception("remove_competitor failed")
        return {"error": f"Failed to remove competitor: {str(e)}"}


@mcp.tool()
async def get_competitor_report(
    name: str,
    tenant_id: str = "",
    ctx: Context = None,
) -> dict:
    """Get a detailed report for a specific competitor.

    Retrieves the competitor entity with all its relations and the full
    observation/change timeline from the knowledge graph.

    Args:
        name: Competitor company name. Required.
        tenant_id: Tenant UUID (resolved from session if omitted).
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with status, competitor details, relations, and timeline.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"error": "tenant_id is required."}
    if not name:
        return {"error": "Competitor name is required."}

    try:
        search_result = await _api_get(
            "/api/v1/knowledge/entities/internal/search",
            params={"tenant_id": tid, "q": name, "entity_type": "company", "limit": 10},
        )
        entities = search_result if isinstance(search_result, list) else search_result.get("entities", [])

        competitor = None
        for ent in entities:
            if ent.get("category") == "competitor" and ent.get("name", "").lower() == name.lower():
                competitor = ent
                break
        if not competitor:
            for ent in entities:
                if ent.get("category") == "competitor":
                    competitor = ent
                    break

        if not competitor:
            return {"error": f"Competitor '{name}' not found."}

        entity_id = competitor["id"]

        # Get full entity with relations
        full_entity = await _api_get(
            f"/api/v1/knowledge/entities/{entity_id}/internal",
            params={"tenant_id": tid, "include_relations": "true"},
        )

        # Get timeline
        timeline_result = await _api_get(
            f"/api/v1/knowledge/entities/{entity_id}/internal/timeline",
            params={"tenant_id": tid},
        )
        timeline = timeline_result if isinstance(timeline_result, list) else timeline_result.get("history", [])

        return {
            "status": "success",
            "competitor": full_entity,
            "timeline": timeline,
        }
    except Exception as e:
        logger.exception("get_competitor_report failed")
        return {"error": f"Failed to get competitor report: {str(e)}"}


@mcp.tool()
async def compare_campaigns(
    competitor_name: str,
    tenant_id: str = "",
    ctx: Context = None,
) -> dict:
    """Compare campaign/ad activity for a competitor.

    Retrieves the competitor report and filters the observation timeline
    for ad-related entries (campaigns, ads, promotions, sponsored content).

    Args:
        competitor_name: Competitor company name. Required.
        tenant_id: Tenant UUID (resolved from session if omitted).
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with status, competitor details, and filtered ad observations.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"error": "tenant_id is required."}
    if not competitor_name:
        return {"error": "Competitor name is required."}

    report = await get_competitor_report(name=competitor_name, tenant_id=tid, ctx=ctx)
    if "error" in report:
        return report

    competitor = report.get("competitor", {})
    timeline = report.get("timeline", [])

    ad_observations = []
    for entry in timeline:
        text_to_check = " ".join([
            str(entry.get("change_reason", "") or ""),
            str(entry.get("properties_snapshot", "") or ""),
        ]).lower()
        if any(keyword in text_to_check for keyword in _AD_KEYWORDS):
            ad_observations.append(entry)

    return {
        "status": "success",
        "competitor": competitor,
        "ad_observations": ad_observations,
        "ad_observation_count": len(ad_observations),
        "total_timeline_entries": len(timeline),
    }
