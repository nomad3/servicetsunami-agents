# Marketing Intelligence & Ads Platform Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Give Luna (and each tenant) a marketing co-pilot that monitors competitors, manages ad campaigns across Meta/Google/TikTok, and delivers actionable intelligence from a business owner's perspective.

**Architecture:** Reuse the existing knowledge graph for competitor entities/observations. Add ad platform integrations to the registry (same pattern as Gmail/Jira). Create new ADK tools for ads + competitor monitoring. Add a Temporal workflow for scheduled competitor tracking. New `marketing_analyst` sub-agent joins `marketing_team`.

**Tech Stack:** Google ADK agents, Temporal workflows, Meta Marketing API, Google Ads API, TikTok Marketing API, Meta Ad Library (public), Google Ads Transparency (public), MCP scraper for web research.

**Design doc:** `docs/plans/2026-03-10-marketing-intelligence-ads-platform-design.md`

---

## Task 1: Add Ad Platform Integrations to Registry

**Files:**
- Modify: `apps/api/app/api/v1/integration_configs.py:20-103` (add 3 entries to `INTEGRATION_CREDENTIAL_SCHEMAS`)

**Context:** The integration registry (`INTEGRATION_CREDENTIAL_SCHEMAS` dict) drives the entire credential flow. OAuth integrations have `auth_type: "oauth"` and `oauth_provider` pointing to a key in `OAUTH_PROVIDERS` (in `oauth.py`). Manual integrations have credential form fields. For Phase 1, Meta Ads uses a manual long-lived access token (simpler than full OAuth), Google Ads uses a manual developer token + customer ID, and TikTok Ads uses a manual access token.

**Step 1: Add Meta Ads entry**

Add after the `claude_code` entry (line ~103) in `INTEGRATION_CREDENTIAL_SCHEMAS`:

```python
"meta_ads": {
    "display_name": "Meta Ads",
    "description": "Manage Facebook & Instagram ad campaigns, view insights, monitor competitor ads",
    "icon": "FaFacebook",
    "credentials": [
        {"key": "access_token", "label": "Access Token", "type": "password", "required": True,
         "help": "Long-lived access token from Meta Business Suite > Settings > API"},
        {"key": "ad_account_id", "label": "Ad Account ID", "type": "text", "required": True,
         "help": "Format: act_123456789. Find in Meta Business Suite > Settings > Ad Accounts"},
    ],
},
```

**Step 2: Add Google Ads entry**

```python
"google_ads": {
    "display_name": "Google Ads",
    "description": "Manage Google search and display campaigns, view keyword performance",
    "icon": "FaGoogle",
    "credentials": [
        {"key": "developer_token", "label": "Developer Token", "type": "password", "required": True,
         "help": "From Google Ads API Center. Apply at ads.google.com/aw/apicenter"},
        {"key": "customer_id", "label": "Customer ID", "type": "text", "required": True,
         "help": "10-digit Google Ads customer ID (no dashes). Found at top-right of Google Ads UI"},
        {"key": "refresh_token", "label": "OAuth Refresh Token", "type": "password", "required": True,
         "help": "OAuth2 refresh token. Generate using Google OAuth Playground for Ads API scope"},
    ],
},
```

**Step 3: Add TikTok Ads entry**

```python
"tiktok_ads": {
    "display_name": "TikTok Ads",
    "description": "Manage TikTok ad campaigns and view performance insights",
    "icon": "FaTiktok",
    "credentials": [
        {"key": "access_token", "label": "Access Token", "type": "password", "required": True,
         "help": "From TikTok Business Center > Developer Portal > My Apps"},
        {"key": "advertiser_id", "label": "Advertiser ID", "type": "text", "required": True,
         "help": "Found in TikTok Ads Manager > Account Info"},
    ],
},
```

**Step 4: Commit**

```bash
git add apps/api/app/api/v1/integration_configs.py
git commit -m "feat: add Meta Ads, Google Ads, TikTok Ads to integration registry"
```

---

## Task 2: Create Competitor Tools

**Files:**
- Create: `apps/adk-server/tools/competitor_tools.py`

**Context:** Competitor tools manage competitor entities in the knowledge graph. They use the existing `knowledge_tools.py` functions (`create_entity`, `find_entities`, `create_relation`, `record_observation`, `get_entity`, `get_entity_timeline`). Competitors are stored as entities with `category="competitor"`. Follow the Jira tools pattern: `async` functions, `tenant_id="auto"`, `_resolve_tenant_id()`, return `dict` with `status` key.

**Step 1: Create competitor_tools.py**

```python
"""Competitor monitoring tools.

Manages competitor entities in the knowledge graph and retrieves
competitive intelligence from public sources (ad libraries, web).
"""
import json
import logging
from typing import Optional

from tools.knowledge_tools import (
    _resolve_tenant_id,
    create_entity,
    find_entities,
    create_relation,
    record_observation,
    get_entity,
    get_entity_timeline,
    find_relations,
    update_entity,
)

logger = logging.getLogger(__name__)


async def add_competitor(
    tenant_id: str = "auto",
    name: str = "",
    website: str = "",
    facebook_url: str = "",
    instagram_url: str = "",
    tiktok_url: str = "",
    google_ads_advertiser_id: str = "",
    monitor_frequency: str = "daily",
    notes: str = "",
) -> dict:
    """Add a competitor to monitor. Creates a knowledge graph entity and starts tracking.

    Args:
        tenant_id: Tenant context. Use "auto" if unknown.
        name: Competitor company name. Required.
        website: Competitor's website URL.
        facebook_url: Facebook page URL (for Meta Ad Library lookups).
        instagram_url: Instagram profile URL.
        tiktok_url: TikTok profile URL.
        google_ads_advertiser_id: Google Ads advertiser ID (for Transparency Center lookups).
        monitor_frequency: How often to check: "daily", "weekly", or "hourly". Default: "daily".
        notes: Any additional context about this competitor.

    Returns:
        Dict with status, entity_id, and competitor details.
    """
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
    # Remove empty values
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

    if "error" in result:
        return result

    entity_id = result.get("entity_id") or result.get("id")

    # Find tenant's own company entity to create competes_with relation
    own_entities = await find_entities(
        query="",
        tenant_id=tenant_id,
        entity_types=["company"],
        limit=1,
    )
    if isinstance(own_entities, list) and own_entities:
        own_id = own_entities[0].get("id")
        if own_id and entity_id:
            await create_relation(
                source_entity_id=str(own_id),
                target_entity_id=str(entity_id),
                relation_type="competes_with",
                tenant_id=tenant_id,
                strength=1.0,
                evidence=f"User added {name} as competitor",
                bidirectional=True,
            )

    return {
        "status": "success",
        "entity_id": entity_id,
        "name": name,
        "properties": properties,
        "message": f"Added {name} as a competitor. Monitoring frequency: {monitor_frequency}.",
    }


async def remove_competitor(
    tenant_id: str = "auto",
    name: str = "",
) -> dict:
    """Remove a competitor from monitoring.

    Args:
        tenant_id: Tenant context. Use "auto" if unknown.
        name: Name of the competitor to remove. Required.

    Returns:
        Dict with status and confirmation message.
    """
    tenant_id = _resolve_tenant_id(tenant_id)
    if not name:
        return {"error": "Competitor name is required."}

    entities = await find_entities(
        query=name,
        tenant_id=tenant_id,
        entity_types=["company"],
        limit=5,
    )

    if not isinstance(entities, list) or not entities:
        return {"error": f"Competitor '{name}' not found in knowledge graph."}

    # Find the competitor entity (category="competitor")
    competitor = None
    for e in entities:
        props = e.get("properties", {})
        cat = e.get("category", "")
        if isinstance(props, str):
            try:
                props = json.loads(props)
            except Exception:
                props = {}
        if cat == "competitor" or props.get("monitor_frequency"):
            competitor = e
            break

    if not competitor:
        competitor = entities[0]

    entity_id = competitor.get("id")
    await update_entity(
        entity_id=str(entity_id),
        updates=json.dumps({"status": "archived"}),
        reason=f"Competitor {name} removed from monitoring",
    )

    return {
        "status": "success",
        "message": f"Stopped monitoring competitor '{name}'. Entity archived.",
    }


async def get_competitor_report(
    tenant_id: str = "auto",
    name: str = "",
) -> dict:
    """Get a comprehensive report on a competitor including latest observations and ad activity.

    Args:
        tenant_id: Tenant context. Use "auto" if unknown.
        name: Competitor name. Required.

    Returns:
        Dict with competitor details, properties, recent observations, and relations.
    """
    tenant_id = _resolve_tenant_id(tenant_id)
    if not name:
        return {"error": "Competitor name is required."}

    entities = await find_entities(
        query=name,
        tenant_id=tenant_id,
        entity_types=["company"],
        limit=5,
    )

    if not isinstance(entities, list) or not entities:
        return {"error": f"Competitor '{name}' not found."}

    # Pick best match
    competitor = entities[0]
    entity_id = str(competitor.get("id"))

    # Get full entity with relations
    full = await get_entity(entity_id=entity_id, include_relations=True)

    # Get observation timeline
    timeline = await get_entity_timeline(entity_id=entity_id)

    return {
        "status": "success",
        "competitor": full if isinstance(full, dict) else competitor,
        "timeline": timeline if isinstance(timeline, list) else [],
        "message": f"Report for competitor '{name}'",
    }


async def list_competitors(
    tenant_id: str = "auto",
) -> dict:
    """List all tracked competitors for this tenant.

    Args:
        tenant_id: Tenant context. Use "auto" if unknown.

    Returns:
        Dict with list of competitor entities and their monitoring settings.
    """
    tenant_id = _resolve_tenant_id(tenant_id)

    # Find all competitor-category entities
    from services.knowledge_graph import get_knowledge_service
    svc = get_knowledge_service()
    try:
        import sqlalchemy
        results = svc.engine.execute(sqlalchemy.text(
            "SELECT id, name, entity_type, properties, description, category, status "
            "FROM knowledge_entities "
            "WHERE tenant_id = :tid AND category = 'competitor' AND status != 'archived' "
            "ORDER BY created_at DESC"
        ), {"tid": tenant_id}).fetchall()
    except Exception:
        # Fallback: use find_entities with broad query
        results = await find_entities(
            query="competitor",
            tenant_id=tenant_id,
            limit=50,
        )
        if isinstance(results, list):
            competitors = [e for e in results if e.get("category") == "competitor"]
            return {
                "status": "success",
                "competitors": competitors,
                "count": len(competitors),
            }
        return {"status": "success", "competitors": [], "count": 0}

    competitors = []
    for row in results:
        props = row[3]
        if isinstance(props, str):
            try:
                props = json.loads(props)
            except Exception:
                props = {}
        competitors.append({
            "id": str(row[0]),
            "name": row[1],
            "entity_type": row[2],
            "properties": props or {},
            "description": row[4],
            "category": row[5],
            "status": row[6],
        })

    return {
        "status": "success",
        "competitors": competitors,
        "count": len(competitors),
    }


async def compare_campaigns(
    tenant_id: str = "auto",
    competitor_name: str = "",
) -> dict:
    """Compare your ad performance against a competitor's observed activity.

    Pulls your campaign data from connected ad platforms and compares with
    competitor observations stored in the knowledge graph.

    Args:
        tenant_id: Tenant context. Use "auto" if unknown.
        competitor_name: Name of the competitor to compare against. Required.

    Returns:
        Dict with your metrics, competitor observations, and comparison insights.
    """
    tenant_id = _resolve_tenant_id(tenant_id)
    if not competitor_name:
        return {"error": "competitor_name is required."}

    # Get competitor report
    report = await get_competitor_report(tenant_id=tenant_id, name=competitor_name)
    if "error" in report:
        return report

    competitor_data = report.get("competitor", {})
    timeline = report.get("timeline", [])

    # Extract recent ad-related observations
    ad_observations = []
    for entry in (timeline if isinstance(timeline, list) else []):
        text = str(entry.get("observation_text", "") or entry.get("text", ""))
        if any(kw in text.lower() for kw in ["campaign", "ad ", "ads ", "advertisement", "promotion", "sponsored"]):
            ad_observations.append(entry)

    return {
        "status": "success",
        "competitor": {
            "name": competitor_data.get("name", competitor_name),
            "properties": competitor_data.get("properties", {}),
        },
        "ad_observations": ad_observations[:20],
        "observation_count": len(ad_observations),
        "message": (
            f"Found {len(ad_observations)} ad-related observations for {competitor_name}. "
            "Use your connected ad platform tools to pull your own campaign metrics for comparison."
        ),
    }
```

**Step 2: Commit**

```bash
git add apps/adk-server/tools/competitor_tools.py
git commit -m "feat: add competitor monitoring tools for knowledge graph"
```

---

## Task 3: Create Ads Tools — Meta Ads

**Files:**
- Create: `apps/adk-server/tools/ads_tools.py`

**Context:** Ads tools fetch credentials from the internal token endpoint (same pattern as `jira_tools.py`). Three integrations: `meta_ads`, `google_ads`, `tiktok_ads`. Each integration's credentials are retrieved via `GET /api/v1/oauth/internal/token/{integration_name}?tenant_id={tid}`. Public APIs (Meta Ad Library, Google Ads Transparency, TikTok Creative Center) don't need auth. Use `httpx.AsyncClient` for all HTTP calls.

**Step 1: Create ads_tools.py with Meta Ads tools**

```python
"""Ad platform tools for Meta Ads, Google Ads, and TikTok Ads.

Manages campaigns, retrieves insights, and searches public ad libraries
for competitive intelligence. Credentials fetched via internal token endpoint.
"""
import logging
from typing import Optional

import httpx

from config.settings import settings
from tools.knowledge_tools import _resolve_tenant_id

logger = logging.getLogger(__name__)

_api_client: Optional[httpx.AsyncClient] = None


def _get_api_client() -> httpx.AsyncClient:
    global _api_client
    if _api_client is None:
        _api_client = httpx.AsyncClient(
            base_url=settings.api_base_url,
            timeout=30.0,
        )
    return _api_client


async def _get_ads_credentials(tenant_id: str, integration_name: str) -> Optional[dict]:
    """Retrieve ad platform credentials from the vault."""
    client = _get_api_client()
    try:
        resp = await client.get(
            f"/api/v1/oauth/internal/token/{integration_name}",
            headers={"X-Internal-Key": settings.mcp_api_key},
            params={"tenant_id": tenant_id},
        )
        if resp.status_code == 200:
            return resp.json()
        logger.warning("%s credential retrieval returned %s", integration_name, resp.status_code)
    except Exception:
        logger.exception("Failed to retrieve %s credentials", integration_name)
    return None


# ─── Meta Ads (Facebook/Instagram) ────────────────────────────────────


META_GRAPH_URL = "https://graph.facebook.com/v21.0"


async def list_meta_campaigns(
    tenant_id: str = "auto",
    status_filter: str = "",
    limit: int = 25,
) -> dict:
    """List Meta (Facebook/Instagram) ad campaigns.

    Args:
        tenant_id: Tenant context. Use "auto" if unknown.
        status_filter: Filter by status: "ACTIVE", "PAUSED", "ARCHIVED", or "" for all.
        limit: Max campaigns to return (1-100). Default: 25.

    Returns:
        Dict with list of campaigns (id, name, status, objective, daily_budget).
    """
    tenant_id = _resolve_tenant_id(tenant_id)
    creds = await _get_ads_credentials(tenant_id, "meta_ads")
    if not creds:
        return {"error": "Meta Ads not connected. Ask the user to configure in Connected Apps."}

    access_token = creds.get("access_token")
    ad_account_id = creds.get("ad_account_id", "")
    if not access_token or not ad_account_id:
        return {"error": "Meta Ads credentials incomplete. Need access_token and ad_account_id."}

    if not ad_account_id.startswith("act_"):
        ad_account_id = f"act_{ad_account_id}"

    params = {
        "access_token": access_token,
        "fields": "id,name,status,objective,daily_budget,lifetime_budget,start_time,stop_time",
        "limit": min(limit, 100),
    }
    if status_filter:
        params["filtering"] = f'[{{"field":"effective_status","operator":"IN","value":["{status_filter}"]}}]'

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{META_GRAPH_URL}/{ad_account_id}/campaigns",
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()

            campaigns = []
            for c in data.get("data", []):
                campaigns.append({
                    "id": c.get("id"),
                    "name": c.get("name"),
                    "status": c.get("status"),
                    "objective": c.get("objective"),
                    "daily_budget": c.get("daily_budget"),
                    "lifetime_budget": c.get("lifetime_budget"),
                    "start_time": c.get("start_time"),
                    "stop_time": c.get("stop_time"),
                })

            return {"status": "success", "campaigns": campaigns, "count": len(campaigns)}

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 190:
            return {"error": "Meta access token expired. Re-enter token in Connected Apps."}
        return {"error": f"Meta API error: {e.response.status_code} - {e.response.text[:200]}"}
    except Exception as e:
        logger.exception("list_meta_campaigns failed")
        return {"error": f"Failed to list Meta campaigns: {str(e)}"}


async def get_meta_campaign_insights(
    tenant_id: str = "auto",
    campaign_id: str = "",
    date_preset: str = "last_7d",
) -> dict:
    """Get performance insights for a Meta ad campaign.

    Args:
        tenant_id: Tenant context. Use "auto" if unknown.
        campaign_id: The Meta campaign ID. Required.
        date_preset: Time range: "today", "yesterday", "last_7d", "last_14d",
                     "last_30d", "this_month", "last_month". Default: "last_7d".

    Returns:
        Dict with impressions, clicks, spend, CTR, CPC, conversions.
    """
    tenant_id = _resolve_tenant_id(tenant_id)
    if not campaign_id:
        return {"error": "campaign_id is required."}

    creds = await _get_ads_credentials(tenant_id, "meta_ads")
    if not creds:
        return {"error": "Meta Ads not connected."}

    access_token = creds.get("access_token")
    if not access_token:
        return {"error": "Meta Ads access_token missing."}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{META_GRAPH_URL}/{campaign_id}/insights",
                params={
                    "access_token": access_token,
                    "fields": "impressions,clicks,spend,ctr,cpc,cpm,reach,frequency,actions,cost_per_action_type",
                    "date_preset": date_preset,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            insights = data.get("data", [{}])[0] if data.get("data") else {}

            return {"status": "success", "campaign_id": campaign_id, "date_preset": date_preset, "insights": insights}

    except httpx.HTTPStatusError as e:
        return {"error": f"Meta Insights API error: {e.response.status_code}"}
    except Exception as e:
        logger.exception("get_meta_campaign_insights failed")
        return {"error": str(e)}


async def pause_meta_campaign(
    tenant_id: str = "auto",
    campaign_id: str = "",
) -> dict:
    """Pause an active Meta ad campaign.

    Args:
        tenant_id: Tenant context. Use "auto" if unknown.
        campaign_id: The Meta campaign ID to pause. Required.

    Returns:
        Dict with confirmation of status change.
    """
    tenant_id = _resolve_tenant_id(tenant_id)
    if not campaign_id:
        return {"error": "campaign_id is required."}

    creds = await _get_ads_credentials(tenant_id, "meta_ads")
    if not creds:
        return {"error": "Meta Ads not connected."}

    access_token = creds.get("access_token")
    if not access_token:
        return {"error": "Meta Ads access_token missing."}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{META_GRAPH_URL}/{campaign_id}",
                params={"access_token": access_token},
                data={"status": "PAUSED"},
            )
            resp.raise_for_status()
            return {"status": "success", "campaign_id": campaign_id, "new_status": "PAUSED", "message": f"Campaign {campaign_id} paused."}

    except httpx.HTTPStatusError as e:
        return {"error": f"Failed to pause campaign: {e.response.status_code}"}
    except Exception as e:
        logger.exception("pause_meta_campaign failed")
        return {"error": str(e)}


async def search_meta_ad_library(
    query: str = "",
    country: str = "US",
    ad_type: str = "ALL",
    limit: int = 10,
) -> dict:
    """Search Meta Ad Library for competitor ads. NO AUTH REQUIRED — public API.

    Args:
        query: Search term (company name, keyword, or topic). Required.
        country: ISO country code. Default: "US".
        ad_type: "ALL", "POLITICAL_AND_ISSUE_ADS", or "EMPLOYMENT_HOUSING_CREDIT". Default: "ALL".
        limit: Max results (1-50). Default: 10.

    Returns:
        Dict with list of active ads (page_name, ad_creative_body, impressions, spend).
    """
    if not query:
        return {"error": "Search query is required."}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{META_GRAPH_URL}/ads_archive",
                params={
                    "search_terms": query,
                    "ad_reached_countries": f'["{country}"]',
                    "ad_type": ad_type,
                    "fields": "page_name,ad_creative_bodies,ad_delivery_start_time,ad_delivery_stop_time,impressions,spend,currency,demographic_distribution",
                    "limit": min(limit, 50),
                    "access_token": "",  # Public endpoint, empty token works for basic search
                },
            )
            # Ad Library API may return 400 without token for some queries;
            # fall back to scraping approach
            if resp.status_code != 200:
                return {
                    "status": "partial",
                    "message": f"Meta Ad Library returned {resp.status_code}. Use web_researcher to scrape facebook.com/ads/library/?q={query} for results.",
                    "fallback_url": f"https://www.facebook.com/ads/library/?active_status=active&ad_type=all&country={country}&q={query}",
                }

            data = resp.json()
            ads = []
            for ad in data.get("data", []):
                ads.append({
                    "page_name": ad.get("page_name"),
                    "ad_text": (ad.get("ad_creative_bodies") or [""])[0][:300],
                    "start_date": ad.get("ad_delivery_start_time"),
                    "end_date": ad.get("ad_delivery_stop_time"),
                    "impressions": ad.get("impressions"),
                    "spend": ad.get("spend"),
                })

            return {"status": "success", "query": query, "country": country, "ads": ads, "count": len(ads)}

    except Exception as e:
        logger.exception("search_meta_ad_library failed")
        return {
            "status": "partial",
            "message": f"Meta Ad Library API error: {str(e)}. Use web_researcher to scrape the Ad Library page.",
            "fallback_url": f"https://www.facebook.com/ads/library/?active_status=active&ad_type=all&country={country}&q={query}",
        }


# ─── Google Ads ────────────────────────────────────────────────────────


GOOGLE_ADS_API_URL = "https://googleads.googleapis.com/v18"


async def list_google_campaigns(
    tenant_id: str = "auto",
    status_filter: str = "",
    limit: int = 25,
) -> dict:
    """List Google Ads campaigns.

    Args:
        tenant_id: Tenant context. Use "auto" if unknown.
        status_filter: Filter by status: "ENABLED", "PAUSED", "REMOVED", or "" for all.
        limit: Max campaigns (1-100). Default: 25.

    Returns:
        Dict with list of campaigns (id, name, status, type, budget).
    """
    tenant_id = _resolve_tenant_id(tenant_id)
    creds = await _get_ads_credentials(tenant_id, "google_ads")
    if not creds:
        return {"error": "Google Ads not connected. Configure in Connected Apps."}

    developer_token = creds.get("developer_token")
    customer_id = creds.get("customer_id", "").replace("-", "")
    refresh_token = creds.get("refresh_token")

    if not developer_token or not customer_id or not refresh_token:
        return {"error": "Google Ads credentials incomplete. Need developer_token, customer_id, and refresh_token."}

    # Get access token from refresh token
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            token_resp = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": settings.google_client_id if hasattr(settings, 'google_client_id') else "",
                    "client_secret": settings.google_client_secret if hasattr(settings, 'google_client_secret') else "",
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
            )
            if token_resp.status_code != 200:
                return {"error": "Failed to refresh Google Ads OAuth token. Re-enter credentials."}
            access_token = token_resp.json().get("access_token")
    except Exception as e:
        return {"error": f"Google token refresh failed: {str(e)}"}

    where_clause = f' AND campaign.status = "{status_filter}"' if status_filter else ""
    query = (
        f"SELECT campaign.id, campaign.name, campaign.status, "
        f"campaign.advertising_channel_type, campaign_budget.amount_micros "
        f"FROM campaign WHERE campaign.status != 'REMOVED'{where_clause} "
        f"LIMIT {min(limit, 100)}"
    )

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{GOOGLE_ADS_API_URL}/customers/{customer_id}/googleAds:searchStream",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "developer-token": developer_token,
                    "Content-Type": "application/json",
                },
                json={"query": query},
            )
            resp.raise_for_status()
            data = resp.json()

            campaigns = []
            for batch in data if isinstance(data, list) else [data]:
                for result in batch.get("results", []):
                    campaign = result.get("campaign", {})
                    budget = result.get("campaignBudget", {})
                    campaigns.append({
                        "id": campaign.get("id"),
                        "name": campaign.get("name"),
                        "status": campaign.get("status"),
                        "type": campaign.get("advertisingChannelType"),
                        "budget_micros": budget.get("amountMicros"),
                    })

            return {"status": "success", "campaigns": campaigns, "count": len(campaigns)}

    except httpx.HTTPStatusError as e:
        return {"error": f"Google Ads API error: {e.response.status_code} - {e.response.text[:200]}"}
    except Exception as e:
        logger.exception("list_google_campaigns failed")
        return {"error": str(e)}


async def get_google_campaign_metrics(
    tenant_id: str = "auto",
    campaign_id: str = "",
    date_range: str = "LAST_7_DAYS",
) -> dict:
    """Get performance metrics for a Google Ads campaign.

    Args:
        tenant_id: Tenant context. Use "auto" if unknown.
        campaign_id: Google Ads campaign ID. Required.
        date_range: Time range: "TODAY", "YESTERDAY", "LAST_7_DAYS", "LAST_30_DAYS",
                    "THIS_MONTH", "LAST_MONTH". Default: "LAST_7_DAYS".

    Returns:
        Dict with impressions, clicks, cost, CTR, conversions, average CPC.
    """
    tenant_id = _resolve_tenant_id(tenant_id)
    if not campaign_id:
        return {"error": "campaign_id is required."}

    creds = await _get_ads_credentials(tenant_id, "google_ads")
    if not creds:
        return {"error": "Google Ads not connected."}

    developer_token = creds.get("developer_token")
    customer_id = creds.get("customer_id", "").replace("-", "")
    refresh_token = creds.get("refresh_token")
    if not developer_token or not customer_id or not refresh_token:
        return {"error": "Google Ads credentials incomplete."}

    # Refresh access token
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            token_resp = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": settings.google_client_id if hasattr(settings, 'google_client_id') else "",
                    "client_secret": settings.google_client_secret if hasattr(settings, 'google_client_secret') else "",
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
            )
            access_token = token_resp.json().get("access_token")
    except Exception:
        return {"error": "Failed to refresh Google token."}

    query = (
        f"SELECT campaign.id, campaign.name, metrics.impressions, metrics.clicks, "
        f"metrics.cost_micros, metrics.ctr, metrics.average_cpc, metrics.conversions, "
        f"metrics.cost_per_conversion "
        f"FROM campaign WHERE campaign.id = {campaign_id} "
        f"AND segments.date DURING {date_range}"
    )

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{GOOGLE_ADS_API_URL}/customers/{customer_id}/googleAds:searchStream",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "developer-token": developer_token,
                    "Content-Type": "application/json",
                },
                json={"query": query},
            )
            resp.raise_for_status()
            data = resp.json()

            metrics = {}
            for batch in data if isinstance(data, list) else [data]:
                for result in batch.get("results", []):
                    m = result.get("metrics", {})
                    metrics = {
                        "impressions": m.get("impressions"),
                        "clicks": m.get("clicks"),
                        "cost_micros": m.get("costMicros"),
                        "ctr": m.get("ctr"),
                        "average_cpc": m.get("averageCpc"),
                        "conversions": m.get("conversions"),
                        "cost_per_conversion": m.get("costPerConversion"),
                    }

            return {"status": "success", "campaign_id": campaign_id, "date_range": date_range, "metrics": metrics}

    except httpx.HTTPStatusError as e:
        return {"error": f"Google Ads metrics error: {e.response.status_code}"}
    except Exception as e:
        logger.exception("get_google_campaign_metrics failed")
        return {"error": str(e)}


async def pause_google_campaign(
    tenant_id: str = "auto",
    campaign_id: str = "",
) -> dict:
    """Pause an active Google Ads campaign.

    Args:
        tenant_id: Tenant context. Use "auto" if unknown.
        campaign_id: Google Ads campaign ID to pause. Required.

    Returns:
        Dict with confirmation.
    """
    tenant_id = _resolve_tenant_id(tenant_id)
    if not campaign_id:
        return {"error": "campaign_id is required."}

    creds = await _get_ads_credentials(tenant_id, "google_ads")
    if not creds:
        return {"error": "Google Ads not connected."}

    developer_token = creds.get("developer_token")
    customer_id = creds.get("customer_id", "").replace("-", "")
    refresh_token = creds.get("refresh_token")
    if not developer_token or not customer_id or not refresh_token:
        return {"error": "Google Ads credentials incomplete."}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            token_resp = await client.post("https://oauth2.googleapis.com/token", data={
                "client_id": settings.google_client_id if hasattr(settings, 'google_client_id') else "",
                "client_secret": settings.google_client_secret if hasattr(settings, 'google_client_secret') else "",
                "refresh_token": refresh_token, "grant_type": "refresh_token",
            })
            access_token = token_resp.json().get("access_token")
    except Exception:
        return {"error": "Failed to refresh Google token."}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{GOOGLE_ADS_API_URL}/customers/{customer_id}/campaigns:mutate",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "developer-token": developer_token,
                    "Content-Type": "application/json",
                },
                json={
                    "operations": [{
                        "update": {
                            "resourceName": f"customers/{customer_id}/campaigns/{campaign_id}",
                            "status": "PAUSED",
                        },
                        "updateMask": "status",
                    }],
                },
            )
            resp.raise_for_status()
            return {"status": "success", "campaign_id": campaign_id, "new_status": "PAUSED"}

    except httpx.HTTPStatusError as e:
        return {"error": f"Google Ads pause failed: {e.response.status_code}"}
    except Exception as e:
        logger.exception("pause_google_campaign failed")
        return {"error": str(e)}


async def search_google_ads_transparency(
    advertiser: str = "",
    region: str = "US",
) -> dict:
    """Search Google Ads Transparency Center for competitor ads. NO AUTH REQUIRED.

    Args:
        advertiser: Advertiser name or domain to search. Required.
        region: Region code. Default: "US".

    Returns:
        Dict with search URL and instructions. The Transparency Center
        doesn't have a public API, so this returns a URL for web_researcher to scrape.
    """
    if not advertiser:
        return {"error": "Advertiser name is required."}

    search_url = f"https://adstransparency.google.com/?region={region}&query={advertiser}"

    return {
        "status": "success",
        "advertiser": advertiser,
        "transparency_url": search_url,
        "message": (
            f"Google Ads Transparency Center doesn't have a public API. "
            f"Use web_researcher to scrape this URL for {advertiser}'s ads: {search_url}"
        ),
    }


# ─── TikTok Ads ────────────────────────────────────────────────────────


TIKTOK_ADS_URL = "https://business-api.tiktok.com/open_api/v1.3"


async def list_tiktok_campaigns(
    tenant_id: str = "auto",
    status_filter: str = "",
    limit: int = 25,
) -> dict:
    """List TikTok ad campaigns.

    Args:
        tenant_id: Tenant context. Use "auto" if unknown.
        status_filter: Filter by status: "CAMPAIGN_STATUS_ENABLE", "CAMPAIGN_STATUS_DISABLE", or "" for all.
        limit: Max campaigns (1-100). Default: 25.

    Returns:
        Dict with list of campaigns (id, name, status, objective, budget).
    """
    tenant_id = _resolve_tenant_id(tenant_id)
    creds = await _get_ads_credentials(tenant_id, "tiktok_ads")
    if not creds:
        return {"error": "TikTok Ads not connected. Configure in Connected Apps."}

    access_token = creds.get("access_token")
    advertiser_id = creds.get("advertiser_id")
    if not access_token or not advertiser_id:
        return {"error": "TikTok Ads credentials incomplete."}

    params = {
        "advertiser_id": advertiser_id,
        "page_size": min(limit, 100),
    }
    if status_filter:
        params["filtering"] = f'{{"status": "{status_filter}"}}'

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{TIKTOK_ADS_URL}/campaign/get/",
                headers={"Access-Token": access_token},
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()

            if data.get("code") != 0:
                return {"error": f"TikTok API error: {data.get('message', 'Unknown error')}"}

            campaigns = []
            for c in data.get("data", {}).get("list", []):
                campaigns.append({
                    "id": c.get("campaign_id"),
                    "name": c.get("campaign_name"),
                    "status": c.get("status"),
                    "objective": c.get("objective_type"),
                    "budget": c.get("budget"),
                    "budget_mode": c.get("budget_mode"),
                })

            return {"status": "success", "campaigns": campaigns, "count": len(campaigns)}

    except httpx.HTTPStatusError as e:
        return {"error": f"TikTok Ads error: {e.response.status_code}"}
    except Exception as e:
        logger.exception("list_tiktok_campaigns failed")
        return {"error": str(e)}


async def get_tiktok_campaign_insights(
    tenant_id: str = "auto",
    campaign_id: str = "",
    date_range: str = "LAST_7_DAYS",
) -> dict:
    """Get performance insights for a TikTok ad campaign.

    Args:
        tenant_id: Tenant context. Use "auto" if unknown.
        campaign_id: TikTok campaign ID. Required.
        date_range: "LAST_7_DAYS", "LAST_14_DAYS", "LAST_30_DAYS", "LIFETIME". Default: "LAST_7_DAYS".

    Returns:
        Dict with impressions, clicks, spend, CTR, CPC, conversions.
    """
    tenant_id = _resolve_tenant_id(tenant_id)
    if not campaign_id:
        return {"error": "campaign_id is required."}

    creds = await _get_ads_credentials(tenant_id, "tiktok_ads")
    if not creds:
        return {"error": "TikTok Ads not connected."}

    access_token = creds.get("access_token")
    advertiser_id = creds.get("advertiser_id")
    if not access_token or not advertiser_id:
        return {"error": "TikTok Ads credentials incomplete."}

    # Map date range to start/end dates
    from datetime import datetime, timedelta
    end = datetime.utcnow().strftime("%Y-%m-%d")
    days_map = {"LAST_7_DAYS": 7, "LAST_14_DAYS": 14, "LAST_30_DAYS": 30, "LIFETIME": 365}
    start = (datetime.utcnow() - timedelta(days=days_map.get(date_range, 7))).strftime("%Y-%m-%d")

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{TIKTOK_ADS_URL}/report/integrated/get/",
                headers={"Access-Token": access_token},
                params={
                    "advertiser_id": advertiser_id,
                    "report_type": "BASIC",
                    "dimensions": '["campaign_id"]',
                    "data_level": "AUCTION_CAMPAIGN",
                    "metrics": '["impressions","clicks","spend","ctr","cpc","conversion","cost_per_conversion"]',
                    "start_date": start,
                    "end_date": end,
                    "filtering": f'{{"campaign_ids": ["{campaign_id}"]}}',
                },
            )
            resp.raise_for_status()
            data = resp.json()

            if data.get("code") != 0:
                return {"error": f"TikTok API error: {data.get('message')}"}

            rows = data.get("data", {}).get("list", [])
            metrics = rows[0].get("metrics", {}) if rows else {}

            return {"status": "success", "campaign_id": campaign_id, "date_range": date_range, "metrics": metrics}

    except Exception as e:
        logger.exception("get_tiktok_campaign_insights failed")
        return {"error": str(e)}


async def pause_tiktok_campaign(
    tenant_id: str = "auto",
    campaign_id: str = "",
) -> dict:
    """Pause an active TikTok ad campaign.

    Args:
        tenant_id: Tenant context. Use "auto" if unknown.
        campaign_id: TikTok campaign ID to pause. Required.

    Returns:
        Dict with confirmation.
    """
    tenant_id = _resolve_tenant_id(tenant_id)
    if not campaign_id:
        return {"error": "campaign_id is required."}

    creds = await _get_ads_credentials(tenant_id, "tiktok_ads")
    if not creds:
        return {"error": "TikTok Ads not connected."}

    access_token = creds.get("access_token")
    advertiser_id = creds.get("advertiser_id")
    if not access_token or not advertiser_id:
        return {"error": "TikTok Ads credentials incomplete."}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{TIKTOK_ADS_URL}/campaign/status/update/",
                headers={"Access-Token": access_token, "Content-Type": "application/json"},
                json={
                    "advertiser_id": advertiser_id,
                    "campaign_ids": [campaign_id],
                    "opt_status": "DISABLE",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != 0:
                return {"error": f"TikTok pause error: {data.get('message')}"}
            return {"status": "success", "campaign_id": campaign_id, "new_status": "PAUSED"}

    except Exception as e:
        logger.exception("pause_tiktok_campaign failed")
        return {"error": str(e)}


async def search_tiktok_creative_center(
    keyword: str = "",
    country: str = "US",
    limit: int = 10,
) -> dict:
    """Search TikTok Creative Center for trending ads. NO AUTH REQUIRED.

    Args:
        keyword: Search keyword or industry. Required.
        country: Country code. Default: "US".
        limit: Max results (1-20). Default: 10.

    Returns:
        Dict with search URL for web_researcher to scrape.
        TikTok Creative Center doesn't have a public API.
    """
    if not keyword:
        return {"error": "Keyword is required."}

    search_url = f"https://ads.tiktok.com/business/creativecenter/inspiration/topads/pc/en?countryCode={country}&keyword={keyword}"

    return {
        "status": "success",
        "keyword": keyword,
        "creative_center_url": search_url,
        "message": (
            f"TikTok Creative Center doesn't have a public API. "
            f"Use web_researcher to scrape trending ads at: {search_url}"
        ),
    }
```

**Step 2: Commit**

```bash
git add apps/adk-server/tools/ads_tools.py
git commit -m "feat: add Meta/Google/TikTok ads tools with campaign management and public ad library search"
```

---

## Task 4: Create Marketing Analyst Sub-Agent

**Files:**
- Create: `apps/adk-server/servicetsunami_supervisor/marketing_analyst.py`
- Modify: `apps/adk-server/servicetsunami_supervisor/marketing_team.py` (add marketing_analyst as sub-agent)

**Context:** The marketing_analyst combines ads tools + competitor tools + knowledge graph tools. It's a leaf agent (has tools, no sub-agents). Follow the personal_assistant pattern for tool imports and the web_researcher pattern for Agent definition.

**Step 1: Create marketing_analyst.py**

```python
"""Marketing Analyst specialist agent.

Manages ad campaigns across Meta/Google/TikTok, monitors competitors,
and provides cross-platform marketing intelligence with business owner insights.
"""
from google.adk.agents import Agent

from tools.ads_tools import (
    # Meta Ads
    list_meta_campaigns,
    get_meta_campaign_insights,
    pause_meta_campaign,
    search_meta_ad_library,
    # Google Ads
    list_google_campaigns,
    get_google_campaign_metrics,
    pause_google_campaign,
    search_google_ads_transparency,
    # TikTok Ads
    list_tiktok_campaigns,
    get_tiktok_campaign_insights,
    pause_tiktok_campaign,
    search_tiktok_creative_center,
)
from tools.competitor_tools import (
    add_competitor,
    remove_competitor,
    get_competitor_report,
    list_competitors,
    compare_campaigns,
)
from tools.knowledge_tools import (
    find_entities,
    create_entity,
    update_entity,
    create_relation,
    record_observation,
    get_entity_timeline,
    ask_knowledge_graph,
)
from config.settings import settings


marketing_analyst = Agent(
    name="marketing_analyst",
    model=settings.adk_model,
    instruction="""You are a Marketing Analyst — a business-savvy digital marketing specialist who manages ad campaigns and monitors competitors.

IMPORTANT: For the tenant_id parameter in all tools, use the value from the session state.
If you cannot access the session state, use "auto" as tenant_id and the system will resolve it.

== YOUR ROLE ==

You help business owners understand their marketing landscape and make data-driven decisions. Think like a CMO who speaks in plain business language, not marketing jargon.

== CAPABILITIES ==

1. **Ad Campaign Management** (Meta/Google/TikTok):
   - List campaigns and their status across all connected platforms
   - Get performance insights (impressions, clicks, spend, CTR, CPC, conversions)
   - Pause campaigns when needed
   - Compare performance across platforms

2. **Competitor Intelligence**:
   - Add/remove competitors to monitor (stores in knowledge graph)
   - Search public ad libraries for competitor ad creatives
   - Generate competitor reports from stored observations
   - Compare your campaigns vs. competitor activity

3. **Knowledge Graph Integration**:
   - Store competitor findings as observations
   - Track competitor entities and relationships
   - Build a timeline of competitor actions

== WORKFLOW ==

When asked about campaign performance:
1. Check which ad platforms are connected (try listing campaigns from each)
2. Pull metrics for the requested timeframe
3. Present in plain language: "You spent $X on Meta and got Y clicks at $Z per click"
4. Compare with competitor observations if available
5. Suggest actionable next steps

When asked to monitor a competitor:
1. Use add_competitor to create the entity with their URLs/social profiles
2. Search public ad libraries for their current ads
3. Store findings as observations on the competitor entity
4. Provide an initial report

When asked for a competitive comparison:
1. Pull your campaign metrics from connected platforms
2. Get competitor report from knowledge graph
3. Search public ad libraries for latest competitor ads
4. Present side-by-side analysis with recommendations

== GUIDELINES ==
- Always present metrics in business terms (cost per result, ROI, not raw impressions)
- When a platform isn't connected, say so clearly and suggest connecting it
- Store all competitor intel as observations in the knowledge graph for historical tracking
- If public ad library APIs fail, recommend using web_researcher to scrape the ad library pages
- Never modify campaign budgets without explicit user approval — only pause/status changes
- Cross-reference findings across platforms for holistic view
""",
    tools=[
        # Meta Ads
        list_meta_campaigns,
        get_meta_campaign_insights,
        pause_meta_campaign,
        search_meta_ad_library,
        # Google Ads
        list_google_campaigns,
        get_google_campaign_metrics,
        pause_google_campaign,
        search_google_ads_transparency,
        # TikTok Ads
        list_tiktok_campaigns,
        get_tiktok_campaign_insights,
        pause_tiktok_campaign,
        search_tiktok_creative_center,
        # Competitor tools
        add_competitor,
        remove_competitor,
        get_competitor_report,
        list_competitors,
        compare_campaigns,
        # Knowledge graph
        find_entities,
        create_entity,
        update_entity,
        create_relation,
        record_observation,
        get_entity_timeline,
        ask_knowledge_graph,
    ],
)
```

**Step 2: Update marketing_team.py to include marketing_analyst**

Replace the entire file `apps/adk-server/servicetsunami_supervisor/marketing_team.py`:

```python
"""Marketing Team sub-supervisor.

Routes research, knowledge management, and marketing analytics requests
to the appropriate specialist.
"""
from google.adk.agents import Agent

from .web_researcher import web_researcher
from .knowledge_manager import knowledge_manager
from .marketing_analyst import marketing_analyst
from config.settings import settings

marketing_team = Agent(
    name="marketing_team",
    model=settings.adk_model,
    instruction="""You are the Marketing Team supervisor. You route research, knowledge management, and marketing analytics requests to the appropriate specialist.

IMPORTANT: You are a ROUTING agent only. You do NOT have tools. Your ONLY capability is to transfer tasks to your sub-agents using transfer_to_agent.

## Your team:
- **web_researcher** — Web scraping, internet search, lead generation, market intelligence, structured data extraction
- **knowledge_manager** — Entity CRUD, knowledge graph, relationships, lead scoring, semantic search, memory management
- **marketing_analyst** — Ad campaign management (Meta/Google/TikTok), competitor monitoring, ad library research, campaign performance analysis

## Routing:
- Web research, scraping, internet search, market intelligence -> transfer to web_researcher
- Lead generation, finding companies/contacts online -> transfer to web_researcher
- Storing entities, updating records, entity CRUD -> transfer to knowledge_manager
- Lead scoring (ai_lead, hca_deal, marketing_signal rubrics) -> transfer to knowledge_manager
- Knowledge graph queries, semantic search, entity relationships -> transfer to knowledge_manager
- Research + store results -> transfer to web_researcher first, then knowledge_manager
- **Campaign performance, ad metrics, ad insights** -> transfer to marketing_analyst
- **Competitor monitoring, add/remove competitor** -> transfer to marketing_analyst
- **Ad library search, competitor ads** -> transfer to marketing_analyst
- **Compare campaigns, competitive analysis of ads** -> transfer to marketing_analyst
- **Pause/resume campaigns** -> transfer to marketing_analyst
- **"What are my competitors doing?"** -> transfer to marketing_analyst
- **"How are my ads performing?"** -> transfer to marketing_analyst

## Entity categories in knowledge graph:
- lead: Companies that might buy products/services
- contact: Decision makers at companies
- investor: VCs, angels, funding sources
- accelerator: Programs, incubators
- organization: Generic companies
- person: Generic people
- competitor: Companies being monitored for competitive intelligence

Always explain which specialist you're routing to and why.
""",
    sub_agents=[web_researcher, knowledge_manager, marketing_analyst],
)
```

**Step 3: Commit**

```bash
git add apps/adk-server/servicetsunami_supervisor/marketing_analyst.py apps/adk-server/servicetsunami_supervisor/marketing_team.py
git commit -m "feat: add marketing_analyst sub-agent to marketing_team with ads and competitor tools"
```

---

## Task 5: Update Root Supervisor Routing

**Files:**
- Modify: `apps/adk-server/servicetsunami_supervisor/agent.py:23-120` (update routing instructions)

**Context:** The root supervisor needs updated routing to send competitor monitoring + ad campaign requests to the right places. Per the design: "Monitor competitor X" → personal_assistant (Luna manages directly). "Campaign performance" / "ad analytics" → marketing_team → marketing_analyst.

**Step 1: Update marketing_team routing section in root agent instruction**

In `apps/adk-server/servicetsunami_supervisor/agent.py`, find the `### marketing_team:` section (lines ~79-86) and replace it with:

```
### marketing_team:
- Web research, scraping, competitive analysis
- Internet presence audits, company digital footprint
- Market intelligence, industry research
- Community management research, social media analysis
- "Research X company", "Analyze their online presence"
- "Find competitors in Y market"
- Store research findings in knowledge graph
- **Ad campaign management**: "How are my Meta/Google/TikTok ads performing?"
- **Campaign metrics and insights**: "Show campaign performance", "What's my CTR?"
- **Competitor ad monitoring**: "What ads is competitor X running?"
- **Ad library research**: "Search Meta Ad Library for X"
- **Pause/resume campaigns**: "Pause my Google campaign"
- **Compare campaigns**: "Compare my ads vs competitor X"
```

Also update the `### personal_assistant (Luna):` section to add:

```
- "Add competitor X", "monitor competitor X" (Luna manages competitor entities directly)
- Competitor briefing requests ("what's new with our competitors?")
```

**Step 2: Add competitor tools to personal_assistant**

Modify `apps/adk-server/servicetsunami_supervisor/personal_assistant.py` to import and wire competitor tools into Luna:

Add import at top (after the jira_tools import block):

```python
from tools.competitor_tools import (
    add_competitor,
    remove_competitor,
    get_competitor_report,
    list_competitors,
)
```

Add these 4 tools to Luna's `tools=[]` array (after the GitHub tools block):

```python
        # Competitor monitoring
        add_competitor,
        remove_competitor,
        get_competitor_report,
        list_competitors,
```

Also add a section to Luna's instruction about competitors:

```
- Competitor Monitoring: add_competitor, remove_competitor, get_competitor_report, list_competitors
```

**Step 3: Commit**

```bash
git add apps/adk-server/servicetsunami_supervisor/agent.py apps/adk-server/servicetsunami_supervisor/personal_assistant.py
git commit -m "feat: update root supervisor routing for ads/competitors, wire competitor tools to Luna"
```

---

## Task 6: Create CompetitorMonitorWorkflow (Temporal)

**Files:**
- Create: `apps/api/app/workflows/competitor_monitor.py`
- Create: `apps/api/app/workflows/activities/competitor_monitor.py`
- Modify: `apps/api/app/workers/orchestration_worker.py` (register workflow + activities)

**Context:** Follow the `InboxMonitorWorkflow` pattern exactly. One workflow instance per tenant. Uses `continue_as_new` to prevent history growth. Queue: `servicetsunami-orchestration`. The workflow fetches competitors from the knowledge graph, scrapes their web presence via the MCP scraper, checks public ad libraries, stores observations, and creates notifications for notable changes.

**Step 1: Create the workflow**

Create `apps/api/app/workflows/competitor_monitor.py`:

```python
"""Temporal workflow for competitor monitoring.

Long-running workflow (one per tenant) that periodically checks competitor
websites, social profiles, and public ad libraries for changes. Stores
observations in the knowledge graph and creates notifications for the user.

Uses continue_as_new to prevent history growth (same pattern as InboxMonitorWorkflow).
"""
from temporalio import workflow
from temporalio.common import RetryPolicy
from datetime import timedelta
from typing import Optional


@workflow.defn(sandboxed=False)
class CompetitorMonitorWorkflow:
    """Periodic competitor monitor.

    Runs every N seconds (default 24h):
    fetch competitors → scrape activity → check ad libraries →
    analyze changes → store observations → create notifications → continue_as_new

    One workflow instance per tenant. Workflow ID: competitor-monitor-{tenant_id}
    """

    @workflow.run
    async def run(
        self,
        tenant_id: str,
        check_interval_seconds: int = 86400,
        last_run_summary: Optional[str] = None,
    ) -> dict:
        retry_policy = RetryPolicy(
            maximum_attempts=3,
            initial_interval=timedelta(seconds=30),
            backoff_coefficient=2.0,
        )
        activity_timeout = timedelta(minutes=5)

        workflow.logger.info(f"Competitor monitor cycle for tenant {tenant_id[:8]}")

        # Step 1: Fetch competitor entities from knowledge graph
        competitors = await workflow.execute_activity(
            "fetch_competitors",
            args=[tenant_id],
            start_to_close_timeout=activity_timeout,
            retry_policy=retry_policy,
        )
        competitor_list = competitors.get("competitors", [])

        if not competitor_list:
            workflow.logger.info("No competitors configured, sleeping...")
            await workflow.sleep(timedelta(seconds=check_interval_seconds))
            workflow.continue_as_new(args=[tenant_id, check_interval_seconds, None])

        # Step 2: Scrape competitor web presence
        scrape_results = await workflow.execute_activity(
            "scrape_competitor_activity",
            args=[tenant_id, competitor_list],
            start_to_close_timeout=timedelta(minutes=10),
            retry_policy=retry_policy,
        )

        # Step 3: Check public ad libraries
        ad_results = await workflow.execute_activity(
            "check_ad_libraries",
            args=[tenant_id, competitor_list],
            start_to_close_timeout=timedelta(minutes=10),
            retry_policy=retry_policy,
        )

        # Step 4: Analyze changes vs previous observations
        analysis = await workflow.execute_activity(
            "analyze_competitor_changes",
            args=[tenant_id, competitor_list, scrape_results, ad_results, last_run_summary],
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=retry_policy,
        )

        # Step 5: Store observations in knowledge graph
        await workflow.execute_activity(
            "store_competitor_observations",
            args=[tenant_id, analysis],
            start_to_close_timeout=activity_timeout,
            retry_policy=retry_policy,
        )

        # Step 6: Create notifications for notable changes
        notif_result = await workflow.execute_activity(
            "create_competitor_notifications",
            args=[tenant_id, analysis],
            start_to_close_timeout=activity_timeout,
            retry_policy=retry_policy,
        )

        # Step 7: Log cycle
        wf_info = workflow.info()
        new_summary = analysis.get("summary", "")

        workflow.logger.info(
            f"Competitor monitor cycle complete: {len(competitor_list)} competitors, "
            f"{notif_result.get('created', 0)} notifications"
        )

        # Sleep then continue as new
        await workflow.sleep(timedelta(seconds=check_interval_seconds))

        workflow.continue_as_new(args=[
            tenant_id,
            check_interval_seconds,
            new_summary,
        ])
```

**Step 2: Create the activities**

Create `apps/api/app/workflows/activities/competitor_monitor.py`:

```python
"""Temporal activities for competitor monitoring.

Each activity is a standalone function decorated with @activity.defn.
Activities handle: fetching competitors from knowledge graph, scraping
websites/socials, checking ad libraries, analyzing changes, storing
observations, and creating notifications.
"""
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
from temporalio import activity

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.notification import Notification
from app.models.knowledge_entity import KnowledgeEntity

logger = logging.getLogger(__name__)


@activity.defn
async def fetch_competitors(tenant_id: str) -> dict:
    """Fetch all competitor entities from the knowledge graph."""
    db = SessionLocal()
    try:
        entities = db.query(KnowledgeEntity).filter(
            KnowledgeEntity.tenant_id == tenant_id,
            KnowledgeEntity.category == "competitor",
            KnowledgeEntity.status != "archived",
        ).all()

        competitors = []
        for e in entities:
            props = e.properties or {}
            if isinstance(props, str):
                try:
                    props = json.loads(props)
                except Exception:
                    props = {}
            competitors.append({
                "id": str(e.id),
                "name": e.name,
                "website_url": props.get("website_url", ""),
                "facebook_url": props.get("facebook_url", ""),
                "instagram_url": props.get("instagram_url", ""),
                "tiktok_url": props.get("tiktok_url", ""),
                "google_ads_advertiser_id": props.get("google_ads_advertiser_id", ""),
                "monitor_frequency": props.get("monitor_frequency", "daily"),
            })

        return {"competitors": competitors, "count": len(competitors)}
    finally:
        db.close()


@activity.defn
async def scrape_competitor_activity(tenant_id: str, competitors: list) -> dict:
    """Scrape competitor websites and social profiles via MCP scraper."""
    results = {}
    mcp_url = settings.MCP_SCRAPER_URL if hasattr(settings, 'MCP_SCRAPER_URL') else getattr(settings, 'mcp_scraper_url', 'http://servicetsunami-mcp')

    async with httpx.AsyncClient(
        base_url=mcp_url,
        headers={
            "X-API-Key": settings.MCP_API_KEY if hasattr(settings, 'MCP_API_KEY') else getattr(settings, 'mcp_api_key', ''),
            "X-Tenant-ID": "scdp",
        },
        timeout=60.0,
    ) as client:
        for comp in competitors:
            name = comp.get("name", "unknown")
            comp_results = {"name": name, "scrapes": []}

            # Scrape website
            website = comp.get("website_url", "")
            if website:
                try:
                    resp = await client.post("/servicetsunami/v1/scrape", json={
                        "url": website,
                        "extract_links": True,
                    })
                    if resp.status_code == 200:
                        data = resp.json()
                        comp_results["scrapes"].append({
                            "source": "website",
                            "url": website,
                            "title": data.get("title", ""),
                            "content": (data.get("content") or "")[:2000],
                        })
                except Exception as e:
                    logger.warning("Failed to scrape %s website: %s", name, e)

            # Search for recent news
            try:
                resp = await client.post("/servicetsunami/v1/search-and-scrape", json={
                    "query": f"{name} company news {datetime.now().year}",
                    "max_results": 3,
                })
                if resp.status_code == 200:
                    data = resp.json()
                    for r in data.get("results", []):
                        comp_results["scrapes"].append({
                            "source": "news",
                            "url": r.get("url", ""),
                            "title": r.get("title", ""),
                            "snippet": (r.get("snippet") or r.get("content", ""))[:500],
                        })
            except Exception as e:
                logger.warning("Failed to search news for %s: %s", name, e)

            results[name] = comp_results

    return {"scrape_results": results}


@activity.defn
async def check_ad_libraries(tenant_id: str, competitors: list) -> dict:
    """Check public ad libraries for competitor ad activity."""
    results = {}

    async with httpx.AsyncClient(timeout=30.0) as client:
        for comp in competitors:
            name = comp.get("name", "unknown")
            ad_data = {"name": name, "ads": []}

            # Meta Ad Library (public, no auth)
            facebook_url = comp.get("facebook_url", "")
            try:
                resp = await client.get(
                    "https://graph.facebook.com/v21.0/ads_archive",
                    params={
                        "search_terms": name,
                        "ad_reached_countries": '["US"]',
                        "ad_type": "ALL",
                        "fields": "page_name,ad_creative_bodies,ad_delivery_start_time",
                        "limit": 5,
                        "access_token": "",
                    },
                )
                if resp.status_code == 200:
                    for ad in resp.json().get("data", []):
                        ad_data["ads"].append({
                            "platform": "meta",
                            "page_name": ad.get("page_name"),
                            "text": (ad.get("ad_creative_bodies") or [""])[0][:300],
                            "start_date": ad.get("ad_delivery_start_time"),
                        })
            except Exception as e:
                logger.debug("Meta Ad Library check for %s: %s", name, e)

            results[name] = ad_data

    return {"ad_results": results}


@activity.defn
async def analyze_competitor_changes(
    tenant_id: str,
    competitors: list,
    scrape_results: dict,
    ad_results: dict,
    last_summary: Optional[str],
) -> dict:
    """Analyze competitor activity and identify notable changes."""
    analysis = {"competitors": {}, "summary": "", "notable_changes": []}

    for comp in competitors:
        name = comp.get("name", "unknown")
        scrapes = scrape_results.get("scrape_results", {}).get(name, {}).get("scrapes", [])
        ads = ad_results.get("ad_results", {}).get(name, {}).get("ads", [])

        comp_analysis = {
            "name": name,
            "web_activity": len(scrapes),
            "ad_activity": len(ads),
            "observations": [],
        }

        # Build observation text from scraped data
        if scrapes:
            news_items = [s for s in scrapes if s.get("source") == "news"]
            if news_items:
                news_text = "; ".join([f"{n.get('title', '')}" for n in news_items[:3]])
                comp_analysis["observations"].append(f"Recent news: {news_text}")

        if ads:
            ad_text = f"Found {len(ads)} active ads on Meta."
            comp_analysis["observations"].append(ad_text)
            analysis["notable_changes"].append(f"{name}: {ad_text}")

        analysis["competitors"][name] = comp_analysis

    analysis["summary"] = f"Monitored {len(competitors)} competitors. " + \
        f"Notable changes: {len(analysis['notable_changes'])}"

    return analysis


@activity.defn
async def store_competitor_observations(tenant_id: str, analysis: dict) -> dict:
    """Store analysis results as observations in the knowledge graph."""
    db = SessionLocal()
    stored = 0
    try:
        for name, comp_data in analysis.get("competitors", {}).items():
            entity = db.query(KnowledgeEntity).filter(
                KnowledgeEntity.tenant_id == tenant_id,
                KnowledgeEntity.name == name,
                KnowledgeEntity.category == "competitor",
            ).first()

            if not entity:
                continue

            for obs_text in comp_data.get("observations", []):
                from app.models.knowledge_entity import KnowledgeObservation
                try:
                    obs = KnowledgeObservation(
                        entity_id=entity.id,
                        tenant_id=tenant_id,
                        observation_text=obs_text,
                        observation_type="fact",
                        source_type="workflow",
                    )
                    db.add(obs)
                    stored += 1
                except Exception as e:
                    logger.warning("Failed to store observation for %s: %s", name, e)

        db.commit()
    except Exception as e:
        db.rollback()
        logger.exception("store_competitor_observations failed")
    finally:
        db.close()

    return {"stored": stored}


@activity.defn
async def create_competitor_notifications(tenant_id: str, analysis: dict) -> dict:
    """Create notifications for notable competitor changes."""
    db = SessionLocal()
    created = 0
    try:
        for change in analysis.get("notable_changes", []):
            notif = Notification(
                tenant_id=tenant_id,
                title="Competitor Activity",
                message=change,
                source="competitor_monitor",
                priority="medium",
            )
            db.add(notif)
            created += 1

        db.commit()
    except Exception as e:
        db.rollback()
        logger.exception("create_competitor_notifications failed")
    finally:
        db.close()

    return {"created": created}
```

**Step 3: Register workflow and activities in orchestration worker**

Modify `apps/api/app/workers/orchestration_worker.py`:

Add imports (after the inbox_monitor imports, ~line 58):

```python
from app.workflows.competitor_monitor import CompetitorMonitorWorkflow
from app.workflows.activities.competitor_monitor import (
    fetch_competitors,
    scrape_competitor_activity,
    check_ad_libraries,
    analyze_competitor_changes,
    store_competitor_observations,
    create_competitor_notifications,
)
```

Add `CompetitorMonitorWorkflow` to the `workflows=[]` list (after `InboxMonitorWorkflow`):

```python
CompetitorMonitorWorkflow,
```

Add all 6 activity functions to the `activities=[]` list (after `log_monitor_cycle`):

```python
fetch_competitors,
scrape_competitor_activity,
check_ad_libraries,
analyze_competitor_changes,
store_competitor_observations,
create_competitor_notifications,
```

**Step 4: Commit**

```bash
git add apps/api/app/workflows/competitor_monitor.py apps/api/app/workflows/activities/competitor_monitor.py apps/api/app/workers/orchestration_worker.py
git commit -m "feat: add CompetitorMonitorWorkflow with 6 activities for scheduled competitor tracking"
```

---

## Task 7: Add Monitor Control Tools to Luna

**Files:**
- Modify: `apps/adk-server/tools/monitor_tools.py` (add competitor monitor start/stop/status)

**Context:** `monitor_tools.py` already has `start_inbox_monitor`, `stop_inbox_monitor`, `check_inbox_monitor_status`. Add matching functions for the competitor monitor. These call the API's Temporal client to start/stop the `CompetitorMonitorWorkflow`. The API needs a new endpoint too, but we can reuse the pattern from the inbox monitor endpoints.

**Step 1: Read current monitor_tools.py to understand the pattern**

Read the file first, then add 3 new functions: `start_competitor_monitor`, `stop_competitor_monitor`, `check_competitor_monitor_status`.

The functions should follow the exact same pattern as the inbox monitor tools, but targeting workflow ID `competitor-monitor-{tenant_id}` and workflow class `CompetitorMonitorWorkflow`.

**Step 2: Add competitor monitor functions to monitor_tools.py**

After the existing `check_inbox_monitor_status` function, add:

```python
async def start_competitor_monitor(
    tenant_id: str = "auto",
    check_interval_hours: int = 24,
) -> dict:
    """Start the competitor monitoring workflow for this tenant.

    Monitors all competitors in the knowledge graph on a schedule.
    Checks websites, public ad libraries, and news for changes.

    Args:
        tenant_id: Tenant context. Use "auto" if unknown.
        check_interval_hours: How often to check (in hours). Default: 24 (daily).

    Returns:
        Dict with status and workflow ID.
    """
    tenant_id = _resolve_tenant_id(tenant_id)
    client = _get_api_client()

    try:
        resp = await client.post(
            "/api/v1/workflows/competitor-monitor/start",
            headers={"X-Internal-Key": settings.mcp_api_key},
            json={
                "tenant_id": tenant_id,
                "check_interval_seconds": check_interval_hours * 3600,
            },
        )
        if resp.status_code == 200:
            return resp.json()
        return {"error": f"Failed to start competitor monitor: {resp.status_code} - {resp.text[:200]}"}
    except Exception as e:
        logger.exception("start_competitor_monitor failed")
        return {"error": str(e)}


async def stop_competitor_monitor(
    tenant_id: str = "auto",
) -> dict:
    """Stop the competitor monitoring workflow for this tenant.

    Args:
        tenant_id: Tenant context. Use "auto" if unknown.

    Returns:
        Dict with status confirmation.
    """
    tenant_id = _resolve_tenant_id(tenant_id)
    client = _get_api_client()

    try:
        resp = await client.post(
            "/api/v1/workflows/competitor-monitor/stop",
            headers={"X-Internal-Key": settings.mcp_api_key},
            json={"tenant_id": tenant_id},
        )
        if resp.status_code == 200:
            return resp.json()
        return {"error": f"Failed to stop competitor monitor: {resp.status_code}"}
    except Exception as e:
        logger.exception("stop_competitor_monitor failed")
        return {"error": str(e)}


async def check_competitor_monitor_status(
    tenant_id: str = "auto",
) -> dict:
    """Check if the competitor monitoring workflow is running.

    Args:
        tenant_id: Tenant context. Use "auto" if unknown.

    Returns:
        Dict with running status and configuration.
    """
    tenant_id = _resolve_tenant_id(tenant_id)
    client = _get_api_client()

    try:
        resp = await client.get(
            "/api/v1/workflows/competitor-monitor/status",
            headers={"X-Internal-Key": settings.mcp_api_key},
            params={"tenant_id": tenant_id},
        )
        if resp.status_code == 200:
            return resp.json()
        return {"status": "not_running", "message": "Competitor monitor is not active."}
    except Exception as e:
        logger.exception("check_competitor_monitor_status failed")
        return {"error": str(e)}
```

**Step 3: Wire monitor tools into personal_assistant.py**

Add to the import block in `personal_assistant.py`:

```python
from tools.monitor_tools import (
    start_inbox_monitor,
    stop_inbox_monitor,
    check_inbox_monitor_status,
    start_competitor_monitor,
    stop_competitor_monitor,
    check_competitor_monitor_status,
)
```

Add the 3 new tools to Luna's `tools=[]` array (after the existing monitor tools):

```python
        start_competitor_monitor,
        stop_competitor_monitor,
        check_competitor_monitor_status,
```

Add to Luna's instruction:

```
- Competitor Monitor: start_competitor_monitor, stop_competitor_monitor, check_competitor_monitor_status
```

**Step 4: Add API endpoints for competitor monitor workflow control**

Create new route or extend existing workflow routes to handle:
- `POST /api/v1/workflows/competitor-monitor/start`
- `POST /api/v1/workflows/competitor-monitor/stop`
- `GET /api/v1/workflows/competitor-monitor/status`

These should follow the same pattern as the inbox monitor endpoints in `oauth.py` (or wherever they live). Connect to Temporal, start/stop/query `CompetitorMonitorWorkflow`.

**Step 5: Commit**

```bash
git add apps/adk-server/tools/monitor_tools.py apps/adk-server/servicetsunami_supervisor/personal_assistant.py
git commit -m "feat: add competitor monitor control tools and wire to Luna"
```

---

## Task 8: Add Competitor Monitor API Endpoints

**Files:**
- Modify: `apps/api/app/api/v1/workflows.py` (or create if it doesn't exist)
- Modify: `apps/api/app/api/v1/routes.py` (mount new router if needed)

**Context:** The inbox monitor start/stop endpoints are in `oauth.py`. For cleaner separation, create workflow control endpoints. These endpoints connect to Temporal and start/stop/query the `CompetitorMonitorWorkflow`. Use the same internal key auth pattern as the inbox monitor (`X-Internal-Key` header).

**Step 1: Check if workflows.py route file exists**

Run: `ls apps/api/app/api/v1/workflows.py`

If it doesn't exist, look at how inbox monitor endpoints are defined (they're in `oauth.py` lines ~467-520) and create a similar pattern.

**Step 2: Create or modify workflow endpoints**

Add these 3 endpoints:

```python
@router.post("/competitor-monitor/start")
async def start_competitor_monitor(
    request: dict,
    _auth: None = Depends(_verify_internal_key),
):
    """Start competitor monitoring workflow for a tenant."""
    tenant_id = request.get("tenant_id")
    interval = request.get("check_interval_seconds", 86400)

    client = await Client.connect(settings.TEMPORAL_ADDRESS)
    wf_id = f"competitor-monitor-{tenant_id}"

    try:
        await client.start_workflow(
            "CompetitorMonitorWorkflow",
            args=[tenant_id, interval, None],
            id=wf_id,
            task_queue="servicetsunami-orchestration",
        )
        return {"status": "started", "workflow_id": wf_id, "interval_hours": interval // 3600}
    except Exception as e:
        if "already running" in str(e).lower():
            return {"status": "already_running", "workflow_id": wf_id}
        raise


@router.post("/competitor-monitor/stop")
async def stop_competitor_monitor(
    request: dict,
    _auth: None = Depends(_verify_internal_key),
):
    """Stop competitor monitoring workflow."""
    tenant_id = request.get("tenant_id")
    client = await Client.connect(settings.TEMPORAL_ADDRESS)
    wf_id = f"competitor-monitor-{tenant_id}"

    try:
        handle = client.get_workflow_handle(wf_id)
        await handle.cancel()
        return {"status": "stopped", "workflow_id": wf_id}
    except Exception as e:
        return {"status": "not_running", "message": str(e)}


@router.get("/competitor-monitor/status")
async def competitor_monitor_status(
    tenant_id: str = Query(...),
    _auth: None = Depends(_verify_internal_key),
):
    """Check competitor monitor workflow status."""
    client = await Client.connect(settings.TEMPORAL_ADDRESS)
    wf_id = f"competitor-monitor-{tenant_id}"

    try:
        handle = client.get_workflow_handle(wf_id)
        desc = await handle.describe()
        return {
            "status": "running" if desc.status == 1 else "completed",
            "workflow_id": wf_id,
            "start_time": str(desc.start_time) if desc.start_time else None,
        }
    except Exception:
        return {"status": "not_running"}
```

**Step 3: Commit**

```bash
git add apps/api/app/api/v1/
git commit -m "feat: add competitor monitor start/stop/status API endpoints"
```

---

## Task 9: Deploy & Verify

**Files:**
- No new files. Push to main triggers CI.

**Step 1: Push all changes**

```bash
git push origin main
```

**Step 2: Watch ADK deployment**

The CI auto-deploys on changes to `apps/adk-server/**`. Monitor:

```bash
kubectl rollout status deployment/servicetsunami-adk -n prod
```

**Step 3: Watch API/Worker deployment**

Changes to `apps/api/**` trigger API + worker deploys:

```bash
kubectl rollout status deployment/servicetsunami-api -n prod
kubectl rollout status deployment/servicetsunami-worker -n prod
```

**Step 4: Verify integration registry**

```bash
curl -s https://servicetsunami.com/api/v1/integration_configs/registry -H "Authorization: Bearer $TOKEN" | python3 -m json.tool | grep -A2 meta_ads
```

Should show `meta_ads`, `google_ads`, `tiktok_ads` entries.

**Step 5: Verify via chat**

Send to Luna via WhatsApp or web chat:
- "List my competitors" — should return empty list or existing competitors
- "Add competitor Acme Corp with website acme.com" — should create entity
- "How are my Meta ads performing?" — should route to marketing_team → marketing_analyst → return "not connected" if no creds

---

## Summary

| Task | Description | Files |
|------|-------------|-------|
| 1 | Integration registry entries | `integration_configs.py` |
| 2 | Competitor tools | `competitor_tools.py` (new) |
| 3 | Ads tools (Meta/Google/TikTok) | `ads_tools.py` (new) |
| 4 | Marketing analyst agent | `marketing_analyst.py` (new), `marketing_team.py` |
| 5 | Root supervisor + Luna routing | `agent.py`, `personal_assistant.py` |
| 6 | Temporal workflow + activities | `competitor_monitor.py` (new), activities (new), `orchestration_worker.py` |
| 7 | Monitor control tools | `monitor_tools.py`, `personal_assistant.py` |
| 8 | API endpoints for monitor | `workflows.py` or routes |
| 9 | Deploy & verify | Push + kubectl |
