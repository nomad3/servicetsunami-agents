"""Temporal activities for competitor monitoring.

Scrapes competitor websites/news, checks ad libraries, analyzes changes,
stores observations in the knowledge graph, and creates notifications.
"""
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx
from sqlalchemy import text as sa_text
from temporalio import activity

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.knowledge_entity import KnowledgeEntity
from app.models.notification import Notification
from app.services.memory_activity import log_activity

logger = logging.getLogger(__name__)


@activity.defn
async def fetch_competitors(tenant_id: str) -> List[Dict[str, Any]]:
    """Fetch competitor entities from the knowledge graph.

    Queries knowledge_entities for category='competitor' AND status != 'archived'.
    Returns list of competitor dicts with id, name, properties.
    """
    db = SessionLocal()
    try:
        tid = uuid.UUID(tenant_id)
        competitors_q = (
            db.query(KnowledgeEntity)
            .filter(
                KnowledgeEntity.tenant_id == tid,
                KnowledgeEntity.category == "competitor",
                KnowledgeEntity.status != "archived",
            )
            .all()
        )

        competitors = []
        for c in competitors_q:
            competitors.append({
                "id": str(c.id),
                "name": c.name,
                "description": c.description or "",
                "properties": c.properties or {},
                "attributes": c.attributes or {},
            })

        logger.info(
            "fetch_competitors: found %d competitors for tenant %s",
            len(competitors), tenant_id[:8],
        )
        return competitors

    except Exception as e:
        logger.exception("fetch_competitors failed: %s", e)
        return []
    finally:
        db.close()


@activity.defn
async def scrape_competitor_activity(
    tenant_id: str, competitors: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Scrape competitor websites and search for recent news.

    For each competitor:
    - Scrape their website URL if available
    - Search for recent news mentions
    Uses the MCP scraper service.
    """
    if not competitors:
        return {"results": {}, "errors": []}

    mcp_base = getattr(settings, "MCP_SCRAPER_URL", None) or settings.MCP_SERVER_URL.rstrip("/")
    results: Dict[str, Any] = {}
    errors: List[str] = []

    async with httpx.AsyncClient(timeout=30.0) as client:
        for comp in competitors:
            comp_id = comp["id"]
            comp_name = comp["name"]
            props = comp.get("properties", {})
            website_url = props.get("website_url") or props.get("website") or props.get("url")

            comp_result: Dict[str, Any] = {"website": None, "news": None}

            # Scrape website
            if website_url:
                try:
                    resp = await client.post(
                        f"{mcp_base}/servicetsunami/v1/scrape",
                        json={"url": website_url, "max_length": 5000},
                        timeout=20.0,
                    )
                    if resp.status_code == 200:
                        comp_result["website"] = resp.json()
                    else:
                        errors.append(f"Scrape {comp_name} website: HTTP {resp.status_code}")
                except Exception as e:
                    errors.append(f"Scrape {comp_name} website: {e}")

            # Search for news
            try:
                resp = await client.post(
                    f"{mcp_base}/servicetsunami/v1/search-and-scrape",
                    json={
                        "query": f"{comp_name} news latest updates",
                        "max_results": 3,
                        "max_length": 2000,
                    },
                    timeout=25.0,
                )
                if resp.status_code == 200:
                    comp_result["news"] = resp.json()
                else:
                    errors.append(f"News search {comp_name}: HTTP {resp.status_code}")
            except Exception as e:
                errors.append(f"News search {comp_name}: {e}")

            results[comp_id] = comp_result

    logger.info(
        "scrape_competitor_activity: scraped %d competitors, %d errors",
        len(results), len(errors),
    )
    return {"results": results, "errors": errors}


@activity.defn
async def check_ad_libraries(
    tenant_id: str, competitors: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Check public Meta Ad Library for competitor ads.

    Uses the Meta Ad Library API (public, no auth required for basic search).
    """
    if not competitors:
        return {"results": {}, "errors": []}

    results: Dict[str, Any] = {}
    errors: List[str] = []

    async with httpx.AsyncClient(timeout=20.0) as client:
        for comp in competitors:
            comp_id = comp["id"]
            comp_name = comp["name"]

            try:
                resp = await client.get(
                    "https://graph.facebook.com/v21.0/ads_archive",
                    params={
                        "search_terms": comp_name,
                        "ad_reached_countries": "US",
                        "ad_type": "ALL",
                        "limit": 10,
                    },
                    timeout=15.0,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    ads = data.get("data", [])
                    results[comp_id] = {
                        "ad_count": len(ads),
                        "ads": [
                            {
                                "id": ad.get("id"),
                                "page_name": ad.get("page_name"),
                                "ad_creation_time": ad.get("ad_creation_time"),
                                "ad_creative_bodies": ad.get("ad_creative_bodies", [])[:2],
                            }
                            for ad in ads[:5]
                        ],
                    }
                else:
                    # Meta API may require access token — gracefully degrade
                    results[comp_id] = {"ad_count": 0, "ads": [], "note": f"HTTP {resp.status_code}"}
                    errors.append(f"Ad library {comp_name}: HTTP {resp.status_code}")
            except Exception as e:
                results[comp_id] = {"ad_count": 0, "ads": [], "note": str(e)}
                errors.append(f"Ad library {comp_name}: {e}")

    logger.info(
        "check_ad_libraries: checked %d competitors, %d errors",
        len(results), len(errors),
    )
    return {"results": results, "errors": errors}


@activity.defn
async def analyze_competitor_changes(
    tenant_id: str,
    competitors: List[Dict[str, Any]],
    scrape_results: Dict[str, Any],
    ad_results: Dict[str, Any],
    last_summary: Optional[str] = None,
) -> Dict[str, Any]:
    """Analyze scraped data and ad findings to identify notable changes.

    Uses LLM to compare current findings with previous summary and
    generate per-competitor observations and a list of notable changes.
    """
    if not competitors:
        return {"observations": {}, "notable_changes": [], "summary": ""}

    scrape_data = scrape_results.get("results", {})
    ad_data = ad_results.get("results", {})

    # Build analysis prompt
    comp_sections = []
    for comp in competitors:
        comp_id = comp["id"]
        section = f"\n## {comp['name']}\n"

        scrape = scrape_data.get(comp_id, {})
        if scrape.get("website"):
            website_text = json.dumps(scrape["website"], default=str)[:2000]
            section += f"Website scrape: {website_text}\n"
        if scrape.get("news"):
            news_text = json.dumps(scrape["news"], default=str)[:2000]
            section += f"Recent news: {news_text}\n"

        ads = ad_data.get(comp_id, {})
        if ads.get("ad_count", 0) > 0:
            section += f"Active ads: {ads['ad_count']} found\n"
            for ad in ads.get("ads", [])[:3]:
                bodies = ad.get("ad_creative_bodies", [])
                body_text = bodies[0][:200] if bodies else "N/A"
                section += f"  - {ad.get('page_name', 'Unknown')}: {body_text}\n"

        comp_sections.append(section)

    context = "\n".join(comp_sections)
    previous = f"\n\nPrevious summary:\n{last_summary}" if last_summary else ""

    system_prompt = """You are a competitive intelligence analyst. Analyze the competitor data and identify notable changes.

For each competitor, write a concise observation (1-3 sentences) summarizing their current activity.
Identify notable changes that the user should know about (new products, pricing changes, ad campaigns, partnerships, etc.).

Respond ONLY with a JSON object (no markdown fences):
{
  "observations": {
    "competitor_id": "Observation text for this competitor"
  },
  "notable_changes": [
    {
      "competitor_id": "...",
      "competitor_name": "...",
      "change_type": "new_product|pricing|campaign|partnership|expansion|other",
      "title": "Brief title (max 100 chars)",
      "description": "What changed and why it matters (1-2 sentences)"
    }
  ],
  "summary": "Overall summary of competitive landscape (2-3 sentences)"
}"""

    # Try local Gemma 4 first (zero cost)
    try:
        from app.services.local_inference import analyze_competitors_local
        analysis = await analyze_competitors_local(
            competitors_context=context,
            previous_summary=last_summary or "",
        )
        if analysis:
            logger.info("analyze_competitor_changes: used local Gemma 4 (saved Anthropic tokens)")
            return analysis
    except Exception as e:
        logger.debug("Gemma 4 competitor analysis failed, falling back to Anthropic: %s", e)

    # Fall back to Anthropic
    try:
        from app.services.llm.legacy_service import get_llm_service
        llm = get_llm_service()
        response = llm.generate_chat_response(
            user_message=f"Analyze these competitors:{context}{previous}",
            conversation_history=[],
            system_prompt=system_prompt,
            max_tokens=3000,
            temperature=0.3,
        )

        text = response.get("text", "{}").strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        analysis = json.loads(text)
        if not isinstance(analysis, dict):
            analysis = {"observations": {}, "notable_changes": [], "summary": ""}

        # Ensure required keys
        analysis.setdefault("observations", {})
        analysis.setdefault("notable_changes", [])
        analysis.setdefault("summary", "")

        return analysis

    except Exception as e:
        logger.exception("analyze_competitor_changes LLM call failed: %s", e)
        # Fallback: generate basic observations from raw data
        observations = {}
        for comp in competitors:
            comp_id = comp["id"]
            scrape = scrape_data.get(comp_id, {})
            ads = ad_data.get(comp_id, {})
            parts = []
            if scrape.get("website"):
                parts.append("Website accessible")
            if scrape.get("news"):
                parts.append("Recent news found")
            if ads.get("ad_count", 0) > 0:
                parts.append(f"{ads['ad_count']} active ads")
            observations[comp_id] = "; ".join(parts) if parts else "No data collected"

        return {
            "observations": observations,
            "notable_changes": [],
            "summary": f"Scanned {len(competitors)} competitors (LLM analysis unavailable)",
        }


@activity.defn
async def store_competitor_observations(
    tenant_id: str, analysis: Dict[str, Any],
) -> Dict[str, Any]:
    """Store observations on competitor knowledge entities.

    Creates knowledge_observations records for each competitor with new observations.
    """
    observations = analysis.get("observations", {})
    if not observations:
        return {"stored": 0}

    db = SessionLocal()
    try:
        tid = uuid.UUID(tenant_id)
        stored = 0

        for comp_id_str, observation_text in observations.items():
            if not observation_text:
                continue

            try:
                comp_id = uuid.UUID(comp_id_str)
            except ValueError:
                logger.warning("Invalid competitor ID: %s", comp_id_str)
                continue

            # Verify entity exists
            entity = db.query(KnowledgeEntity).filter(
                KnowledgeEntity.id == comp_id,
                KnowledgeEntity.tenant_id == tid,
            ).first()

            if not entity:
                logger.warning("Competitor entity %s not found", comp_id_str)
                continue

            # Insert observation via raw SQL (no SQLAlchemy model for knowledge_observations)
            obs_id = uuid.uuid4()
            db.execute(
                sa_text(
                    "INSERT INTO knowledge_observations "
                    "(id, tenant_id, observation_text, observation_type, source_type, processed, created_at) "
                    "VALUES (:id, :tenant_id, :text, :type, :source, :processed, :created_at)"
                ),
                {
                    "id": str(obs_id),
                    "tenant_id": tenant_id,
                    "text": observation_text[:5000],
                    "type": "competitor_activity",
                    "source": "competitor_monitor",
                    "processed": False,
                    "created_at": datetime.now(timezone.utc),
                },
            )
            stored += 1

        db.commit()

        if stored > 0:
            log_activity(
                db,
                tenant_id=tid,
                event_type="entity_updated",
                description=f"Competitor monitor stored {stored} observations",
                source="competitor_monitor",
                event_metadata={"observations_stored": stored},
            )

        logger.info("store_competitor_observations: stored %d for tenant %s", stored, tenant_id[:8])
        return {"stored": stored}

    except Exception as e:
        logger.exception("store_competitor_observations failed: %s", e)
        db.rollback()
        return {"stored": 0, "error": str(e)}
    finally:
        db.close()


@activity.defn
async def create_competitor_notifications(
    tenant_id: str, analysis: Dict[str, Any],
) -> Dict[str, Any]:
    """Create notifications for notable competitor changes.

    Creates a Notification record for each notable change with
    source='competitor_monitor' and priority='medium'.
    """
    notable_changes = analysis.get("notable_changes", [])
    if not notable_changes:
        return {"created": 0}

    db = SessionLocal()
    try:
        tid = uuid.UUID(tenant_id)
        created = 0

        for change in notable_changes:
            comp_name = change.get("competitor_name", "Unknown")
            title = change.get("title", f"Competitor update: {comp_name}")[:255]
            description = change.get("description", "")
            change_type = change.get("change_type", "other")
            comp_id = change.get("competitor_id", "")

            # Deduplicate: check if similar notification exists in last 24h
            cutoff = datetime.utcnow() - timedelta(hours=24)
            existing = db.query(Notification.id).filter(
                Notification.tenant_id == tid,
                Notification.source == "competitor_monitor",
                Notification.title == title,
                Notification.created_at >= cutoff,
            ).first()

            if existing:
                continue

            notif = Notification(
                tenant_id=tid,
                title=title,
                body=description,
                source="competitor_monitor",
                priority="medium",
                reference_id=comp_id,
                reference_type=change_type,
                event_metadata={"competitor_name": comp_name, "change_type": change_type},
            )
            db.add(notif)
            created += 1

        db.commit()

        if created > 0:
            log_activity(
                db,
                tenant_id=tid,
                event_type="notification_created",
                description=f"Competitor monitor created {created} notifications",
                source="competitor_monitor",
                event_metadata={"created": created, "total_changes": len(notable_changes)},
            )

        logger.info(
            "create_competitor_notifications: created %d for tenant %s",
            created, tenant_id[:8],
        )
        return {"created": created}

    except Exception as e:
        logger.exception("create_competitor_notifications failed: %s", e)
        db.rollback()
        return {"created": 0, "error": str(e)}
    finally:
        db.close()
