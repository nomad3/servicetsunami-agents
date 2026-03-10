"""Competitor tracking tools for marketing intelligence.

Manages competitor entities in the knowledge graph. Competitors are stored as
knowledge entities with category="competitor" and entity_type="company", reusing
the existing knowledge graph infrastructure.
"""
import json
import logging

from services.knowledge_graph import get_knowledge_service
from tools.knowledge_tools import (
    _resolve_tenant_id,
    create_entity,
    find_entities,
    create_relation,
    get_entity,
    get_entity_timeline,
)

logger = logging.getLogger(__name__)

# Keywords used to filter ad/campaign-related observations
_AD_KEYWORDS = {"campaign", "ad", "ads", "advertisement", "promotion", "sponsored"}


async def add_competitor(
    tenant_id: str = "auto",
    name: str = "",
    website: str = "",
    facebook_url: str = "",
    instagram_url: str = "",
    tiktok_url: str = "",
    google_ads_advertiser_id: str = "",
    monitor_frequency: str = "weekly",
    notes: str = "",
) -> dict:
    """Add a new competitor to track in the knowledge graph.

    Creates a knowledge entity with category="competitor" and entity_type="company",
    storing social/ad profile URLs as properties. Automatically links the competitor
    to the tenant's own company entity via a "competes_with" relation if found.

    Args:
        tenant_id: Tenant context. Use "auto" if unknown.
        name: Competitor company name. Required.
        website: Competitor website URL.
        facebook_url: Facebook page or profile URL.
        instagram_url: Instagram profile URL.
        tiktok_url: TikTok profile URL.
        google_ads_advertiser_id: Google Ads Transparency Center advertiser ID.
        monitor_frequency: How often to monitor: "daily", "weekly", "monthly". Default "weekly".
        notes: Free-form notes about this competitor.

    Returns:
        Dict with status, entity_id, and confirmation message.
    """
    try:
        tenant_id = _resolve_tenant_id(tenant_id)

        if not name:
            return {"error": "Competitor name is required."}

        properties = {
            "website_url": website,
            "facebook_url": facebook_url,
            "instagram_url": instagram_url,
            "tiktok_url": tiktok_url,
            "google_ads_advertiser_id": google_ads_advertiser_id,
            "monitor_frequency": monitor_frequency,
        }
        # Remove empty string values to keep properties clean
        properties = {k: v for k, v in properties.items() if v}

        result = await create_entity(
            name=name,
            entity_type="company",
            tenant_id=tenant_id,
            properties=json.dumps(properties),
            description=notes or f"Competitor: {name}",
            category="competitor",
            confidence=1.0,
        )

        entity_id = result.get("id")

        # Try to find the tenant's own company entity and create a competes_with relation
        try:
            own_entities = await find_entities(
                query="company",
                tenant_id=tenant_id,
                entity_types=["company"],
                limit=5,
            )
            # Look for an entity that is NOT a competitor (i.e., the tenant's own company)
            own_company = None
            for ent in own_entities:
                if ent.get("category") != "competitor" and ent.get("id") != entity_id:
                    own_company = ent
                    break

            if own_company and entity_id:
                await create_relation(
                    source_entity_id=own_company["id"],
                    target_entity_id=entity_id,
                    relation_type="competes_with",
                    tenant_id=tenant_id,
                    strength=1.0,
                    evidence=f"Competitor tracked via marketing intelligence",
                )
        except Exception:
            logger.debug("Could not create competes_with relation", exc_info=True)

        return {
            "status": "success",
            "entity_id": entity_id,
            "name": name,
            "message": f"Competitor '{name}' added to tracking.",
        }

    except Exception as e:
        logger.exception("add_competitor failed")
        return {"error": f"Failed to add competitor: {str(e)}"}


async def remove_competitor(
    tenant_id: str = "auto",
    name: str = "",
) -> dict:
    """Archive a competitor so it no longer appears in active lists.

    Finds the competitor entity by name (category="competitor") and sets its
    status to "archived". The entity is not deleted and can be restored.

    Args:
        tenant_id: Tenant context. Use "auto" if unknown.
        name: Competitor company name. Required.

    Returns:
        Dict with status and confirmation message.
    """
    try:
        tenant_id = _resolve_tenant_id(tenant_id)

        if not name:
            return {"error": "Competitor name is required."}

        # Find the competitor entity
        entities = await find_entities(
            query=name,
            tenant_id=tenant_id,
            entity_types=["company"],
            limit=10,
        )

        # Filter for exact-ish match with category="competitor"
        competitor = None
        for ent in entities:
            if ent.get("category") == "competitor" and ent.get("name", "").lower() == name.lower():
                competitor = ent
                break

        # Fallback: partial match
        if not competitor:
            for ent in entities:
                if ent.get("category") == "competitor":
                    competitor = ent
                    break

        if not competitor:
            return {"error": f"Competitor '{name}' not found."}

        entity_id = competitor["id"]

        # Archive by setting status column directly via the knowledge service
        kg = get_knowledge_service()
        with kg.Session() as session:
            from sqlalchemy import text
            session.execute(
                text("""
                    UPDATE knowledge_entities
                    SET status = 'archived', updated_at = NOW()
                    WHERE id = :entity_id
                """),
                {"entity_id": entity_id},
            )
            session.commit()

        return {
            "status": "success",
            "entity_id": entity_id,
            "name": name,
            "message": f"Competitor '{name}' archived.",
        }

    except Exception as e:
        logger.exception("remove_competitor failed")
        return {"error": f"Failed to remove competitor: {str(e)}"}


async def get_competitor_report(
    tenant_id: str = "auto",
    name: str = "",
) -> dict:
    """Get a detailed report for a specific competitor.

    Retrieves the competitor entity with all its relations and the full
    observation/change timeline from the knowledge graph.

    Args:
        tenant_id: Tenant context. Use "auto" if unknown.
        name: Competitor company name. Required.

    Returns:
        Dict with status, competitor details, relations, and timeline.
    """
    try:
        tenant_id = _resolve_tenant_id(tenant_id)

        if not name:
            return {"error": "Competitor name is required."}

        # Find the competitor entity
        entities = await find_entities(
            query=name,
            tenant_id=tenant_id,
            entity_types=["company"],
            limit=10,
        )

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
        full_entity = await get_entity(entity_id=entity_id, include_relations=True)

        # Get observation timeline
        timeline = await get_entity_timeline(entity_id=entity_id, include_relations=True)

        return {
            "status": "success",
            "competitor": full_entity,
            "timeline": timeline,
        }

    except Exception as e:
        logger.exception("get_competitor_report failed")
        return {"error": f"Failed to get competitor report: {str(e)}"}


async def list_competitors(
    tenant_id: str = "auto",
) -> dict:
    """List all active (non-archived) competitors for the tenant.

    Queries the knowledge_entities table directly for entities with
    category="competitor" that are not archived.

    Args:
        tenant_id: Tenant context. Use "auto" if unknown.

    Returns:
        Dict with status and list of competitor entities.
    """
    try:
        tenant_id = _resolve_tenant_id(tenant_id)

        # Direct SQL for precise category + status filtering
        try:
            kg = get_knowledge_service()
            with kg.Session() as session:
                from sqlalchemy import text
                from services.knowledge_graph import _serialize_row

                result = session.execute(
                    text("""
                        SELECT id, name, entity_type, category, description,
                               properties, status, confidence, created_at, updated_at
                        FROM knowledge_entities
                        WHERE tenant_id = :tenant_id
                          AND category = 'competitor'
                          AND (status IS NULL OR status != 'archived')
                        ORDER BY name
                    """),
                    {"tenant_id": tenant_id},
                )

                competitors = [_serialize_row(row._mapping) for row in result]

        except Exception:
            logger.debug("Direct SQL failed, falling back to find_entities", exc_info=True)
            # Fallback: use find_entities and filter client-side
            raw = await find_entities(
                query="competitor",
                tenant_id=tenant_id,
                entity_types=["company"],
                limit=100,
            )
            competitors = [
                ent for ent in raw
                if ent.get("category") == "competitor"
            ]

        return {
            "status": "success",
            "competitors": competitors,
            "count": len(competitors),
        }

    except Exception as e:
        logger.exception("list_competitors failed")
        return {"error": f"Failed to list competitors: {str(e)}"}


async def compare_campaigns(
    tenant_id: str = "auto",
    competitor_name: str = "",
) -> dict:
    """Compare campaign/ad activity for a competitor.

    Retrieves the competitor report and filters the observation timeline
    for ad-related entries (campaigns, ads, promotions, sponsored content).

    Args:
        tenant_id: Tenant context. Use "auto" if unknown.
        competitor_name: Competitor company name. Required.

    Returns:
        Dict with status, competitor details, and filtered ad observations.
    """
    try:
        tenant_id = _resolve_tenant_id(tenant_id)

        if not competitor_name:
            return {"error": "Competitor name is required."}

        # Get the full competitor report
        report = await get_competitor_report(tenant_id=tenant_id, name=competitor_name)

        if "error" in report:
            return report

        competitor = report.get("competitor", {})
        timeline = report.get("timeline", [])

        # Filter timeline for ad-related observations
        ad_observations = []
        for entry in timeline:
            # Check change_reason and properties_snapshot for ad keywords
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

    except Exception as e:
        logger.exception("compare_campaigns failed")
        return {"error": f"Failed to compare campaigns: {str(e)}"}
