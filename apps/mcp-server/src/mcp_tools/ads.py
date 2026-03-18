"""Ad platform MCP tools for campaign management.

Campaign management for Meta, Google Ads, and TikTok Ads.
Covers Meta (Facebook/Instagram), Google Ads, and TikTok Ads.
Uses stored credentials from the credential vault via the internal API.
Public ad library/transparency search functions require no authentication.
"""
import logging
import os
from datetime import datetime, timedelta
from typing import Optional

import httpx
from mcp.server.fastmcp import Context

from src.mcp_app import mcp
from src.mcp_auth import resolve_tenant_id

logger = logging.getLogger(__name__)

META_GRAPH_URL = "https://graph.facebook.com/v21.0"
GOOGLE_ADS_API_URL = "https://googleads.googleapis.com/v18"
TIKTOK_ADS_URL = "https://business-api.tiktok.com/open_api/v1.3"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_api_base_url() -> str:
    from src.config import settings
    return settings.API_BASE_URL.rstrip("/")


def _get_internal_key() -> str:
    from src.config import settings
    return settings.API_INTERNAL_KEY


async def _get_ads_credentials(tenant_id: str, integration_name: str) -> Optional[dict]:
    """Retrieve ad platform credentials from the vault."""
    api_base_url = _get_api_base_url()
    internal_key = _get_internal_key()
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{api_base_url}/api/v1/oauth/internal/token/{integration_name}",
                headers={"X-Internal-Key": internal_key},
                params={"tenant_id": tenant_id},
            )
            if resp.status_code == 200:
                return resp.json()
            logger.warning("%s credential retrieval returned %s", integration_name, resp.status_code)
    except Exception:
        logger.exception("Failed to retrieve %s credentials", integration_name)
    return None


async def _refresh_google_access_token(refresh_token: str) -> Optional[str]:
    """Exchange a Google refresh token for a new access token."""
    client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        logger.warning("GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET not configured")
        return None
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
            )
            resp.raise_for_status()
            return resp.json().get("access_token")
    except Exception:
        logger.exception("Failed to refresh Google access token")
        return None


# ===========================================================================
# Meta Ads tools
# ===========================================================================


@mcp.tool()
async def list_meta_campaigns(
    tenant_id: str = "",
    status_filter: str = "",
    limit: int = 25,
    ctx: Context = None,
) -> dict:
    """List Meta (Facebook/Instagram) ad campaigns.

    Args:
        tenant_id: Tenant UUID (resolved from session if omitted).
        status_filter: Filter by status: "ACTIVE", "PAUSED", "ARCHIVED", or "" for all.
        limit: Maximum number of campaigns to return (1-100).
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with list of campaigns including id, name, status, objective, and budget.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"error": "tenant_id is required."}

    creds = await _get_ads_credentials(tid, "meta_ads")
    if not creds:
        return {"error": "Meta Ads not connected. Ask the user to configure Meta Ads in Connected Apps (Integrations page)."}

    access_token = creds.get("access_token")
    ad_account_id = creds.get("ad_account_id", "").strip()
    if not access_token or not ad_account_id:
        return {"error": "Meta Ads credentials incomplete. Need access_token and ad_account_id."}

    if not ad_account_id.startswith("act_"):
        ad_account_id = f"act_{ad_account_id}"

    params = {
        "access_token": access_token,
        "fields": "id,name,status,objective,daily_budget,lifetime_budget,start_time,stop_time",
        "limit": min(max(limit, 1), 100),
    }
    if status_filter:
        params["effective_status"] = f'["{status_filter.upper()}"]'

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{META_GRAPH_URL}/{ad_account_id}/campaigns",
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()

            campaigns = [
                {
                    "id": c.get("id"),
                    "name": c.get("name"),
                    "status": c.get("status"),
                    "objective": c.get("objective"),
                    "daily_budget": c.get("daily_budget"),
                    "lifetime_budget": c.get("lifetime_budget"),
                    "start_time": c.get("start_time"),
                    "stop_time": c.get("stop_time"),
                }
                for c in data.get("data", [])
            ]

            return {
                "status": "success",
                "campaigns": campaigns,
                "count": len(campaigns),
                "ad_account_id": ad_account_id,
            }

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            return {"error": "Meta Ads access token expired or invalid. Reconnect Meta Ads in Connected Apps."}
        if e.response.status_code == 403:
            return {"error": "Insufficient permissions on this Meta ad account."}
        if e.response.status_code == 429:
            return {"error": "Meta API rate limit reached. Please try again in a few minutes."}
        return {"error": f"Meta API error ({e.response.status_code}): {e.response.text[:300]}"}
    except Exception as e:
        logger.exception("list_meta_campaigns failed")
        return {"error": f"Failed to list Meta campaigns: {str(e)}"}


@mcp.tool()
async def get_meta_campaign_insights(
    campaign_id: str,
    tenant_id: str = "",
    date_preset: str = "last_7d",
    ctx: Context = None,
) -> dict:
    """Get performance insights for a Meta ad campaign.

    Args:
        campaign_id: The Meta campaign ID. Required.
        tenant_id: Tenant UUID (resolved from session if omitted).
        date_preset: Time range: "today", "yesterday", "last_7d", "last_14d", "last_30d".
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with impressions, clicks, spend, CTR, CPC, and other metrics.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"error": "tenant_id is required."}
    if not campaign_id:
        return {"error": "campaign_id is required."}

    creds = await _get_ads_credentials(tid, "meta_ads")
    if not creds:
        return {"error": "Meta Ads not connected. Ask the user to configure Meta Ads in Connected Apps."}

    access_token = creds.get("access_token")
    if not access_token:
        return {"error": "Meta Ads access_token missing."}

    valid_presets = {"today", "yesterday", "last_7d", "last_14d", "last_30d"}
    if date_preset not in valid_presets:
        date_preset = "last_7d"

    params = {
        "access_token": access_token,
        "fields": "impressions,clicks,spend,ctr,cpc,cpp,reach,frequency,actions",
        "date_preset": date_preset,
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{META_GRAPH_URL}/{campaign_id}/insights",
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()

            insights = data.get("data", [])
            if not insights:
                return {
                    "status": "success",
                    "campaign_id": campaign_id,
                    "date_preset": date_preset,
                    "message": "No data available for this date range.",
                    "metrics": {},
                }

            row = insights[0]
            conversions = {
                action.get("action_type", ""): action.get("value", "0")
                for action in row.get("actions", [])
            }

            return {
                "status": "success",
                "campaign_id": campaign_id,
                "date_preset": date_preset,
                "metrics": {
                    "impressions": row.get("impressions", "0"),
                    "clicks": row.get("clicks", "0"),
                    "spend": row.get("spend", "0"),
                    "ctr": row.get("ctr", "0"),
                    "cpc": row.get("cpc", "0"),
                    "cpp": row.get("cpp", "0"),
                    "reach": row.get("reach", "0"),
                    "frequency": row.get("frequency", "0"),
                },
                "conversions": conversions,
            }

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            return {"error": "Meta Ads access token expired. Reconnect in Connected Apps."}
        if e.response.status_code == 429:
            return {"error": "Meta API rate limit reached. Try again shortly."}
        return {"error": f"Meta API error ({e.response.status_code}): {e.response.text[:300]}"}
    except Exception as e:
        logger.exception("get_meta_campaign_insights failed")
        return {"error": f"Failed to get Meta insights: {str(e)}"}


@mcp.tool()
async def pause_meta_campaign(
    campaign_id: str,
    tenant_id: str = "",
    ctx: Context = None,
) -> dict:
    """Pause a Meta (Facebook/Instagram) ad campaign.

    Args:
        campaign_id: The Meta campaign ID to pause. Required.
        tenant_id: Tenant UUID (resolved from session if omitted).
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with confirmation of the pause action.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"error": "tenant_id is required."}
    if not campaign_id:
        return {"error": "campaign_id is required."}

    creds = await _get_ads_credentials(tid, "meta_ads")
    if not creds:
        return {"error": "Meta Ads not connected. Ask the user to configure Meta Ads in Connected Apps."}

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
            result = resp.json()

            return {
                "status": "success",
                "campaign_id": campaign_id,
                "action": "paused",
                "message": f"Campaign {campaign_id} has been paused.",
                "api_success": result.get("success", True),
            }

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            return {"error": "Meta Ads access token expired. Reconnect in Connected Apps."}
        if e.response.status_code == 403:
            return {"error": "Insufficient permissions to modify this campaign."}
        return {"error": f"Meta API error ({e.response.status_code}): {e.response.text[:300]}"}
    except Exception as e:
        logger.exception("pause_meta_campaign failed")
        return {"error": f"Failed to pause campaign: {str(e)}"}


@mcp.tool()
async def search_meta_ad_library(
    query: str,
    country: str = "US",
    ad_type: str = "ALL",
    limit: int = 25,
    ctx: Context = None,
) -> dict:
    """Search the Meta Ad Library for active ads. PUBLIC API — no authentication required.

    Args:
        query: Search terms (brand name, keyword, etc.). Required.
        country: Two-letter country code (e.g., "US", "GB", "BR"). Default: "US".
        ad_type: Filter by ad type: "ALL", "POLITICAL_AND_ISSUE_ADS", "HOUSING_ADS",
                 "EMPLOYMENT_ADS", "CREDIT_ADS". Default: "ALL".
        limit: Maximum results to return (1-50).
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with ad results or a fallback URL to the web-based Ad Library.
    """
    if not query:
        return {"error": "query is required (e.g., a brand name or keyword)."}

    fallback_url = (
        f"https://www.facebook.com/ads/library/"
        f"?active_status=active&ad_type={ad_type.lower()}"
        f"&country={country.upper()}&q={query}"
    )

    params = {
        "search_terms": query,
        "ad_reached_countries": f'["{country.upper()}"]',
        "ad_type": ad_type.upper(),
        "limit": min(max(limit, 1), 50),
        "search_type": "KEYWORD_EXACT_PHRASE",
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{META_GRAPH_URL}/ads_archive",
                params=params,
            )
            if resp.status_code != 200:
                return {
                    "status": "fallback",
                    "message": (
                        "Meta Ad Library API requires an access token for search. "
                        "Use the web URL below to search manually, or ask web_researcher to scrape it."
                    ),
                    "url": fallback_url,
                    "query": query,
                    "country": country,
                }

            data = resp.json()
            ads = [
                {
                    "id": ad.get("id"),
                    "page_name": ad.get("page_name"),
                    "ad_creative_body": (ad.get("ad_creative_bodies") or [""])[0][:500],
                    "ad_delivery_start_time": ad.get("ad_delivery_start_time"),
                    "ad_delivery_stop_time": ad.get("ad_delivery_stop_time"),
                    "publisher_platforms": ad.get("publisher_platforms", []),
                }
                for ad in data.get("data", [])
            ]

            return {
                "status": "success",
                "ads": ads,
                "count": len(ads),
                "query": query,
                "country": country,
                "fallback_url": fallback_url,
            }

    except Exception as e:
        logger.warning("Meta Ad Library API call failed, returning fallback URL: %s", e)
        return {
            "status": "fallback",
            "message": "Meta Ad Library API is unavailable. Use the web URL below.",
            "url": fallback_url,
            "query": query,
            "country": country,
        }


# ===========================================================================
# Google Ads tools
# ===========================================================================


@mcp.tool()
async def list_google_campaigns(
    tenant_id: str = "",
    status_filter: str = "",
    limit: int = 25,
    ctx: Context = None,
) -> dict:
    """List Google Ads campaigns.

    Args:
        tenant_id: Tenant UUID (resolved from session if omitted).
        status_filter: Filter by status: "ENABLED", "PAUSED", "REMOVED", or "" for all.
        limit: Maximum number of campaigns to return (1-100).
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with list of campaigns including id, name, status, type, and budget.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"error": "tenant_id is required."}

    creds = await _get_ads_credentials(tid, "google_ads")
    if not creds:
        return {"error": "Google Ads not connected. Ask the user to configure Google Ads in Connected Apps (Integrations page)."}

    developer_token = creds.get("developer_token")
    customer_id = creds.get("customer_id", "").replace("-", "")
    refresh_token = creds.get("refresh_token")

    if not developer_token or not customer_id or not refresh_token:
        return {"error": "Google Ads credentials incomplete. Need developer_token, customer_id, and refresh_token."}

    access_token = await _refresh_google_access_token(refresh_token)
    if not access_token:
        return {"error": "Failed to refresh Google access token. Check GOOGLE_CLIENT_ID/SECRET and refresh_token."}

    where_clause = f" WHERE campaign.status = '{status_filter.upper()}'" if status_filter else ""
    gaql = (
        "SELECT campaign.id, campaign.name, campaign.status, "
        "campaign.advertising_channel_type, campaign.campaign_budget, "
        "campaign.start_date, campaign.end_date "
        f"FROM campaign{where_clause} "
        f"ORDER BY campaign.name ASC LIMIT {min(max(limit, 1), 100)}"
    )

    headers = {
        "Authorization": f"Bearer {access_token}",
        "developer-token": developer_token,
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{GOOGLE_ADS_API_URL}/customers/{customer_id}/googleAds:searchStream",
                headers=headers,
                json={"query": gaql},
            )
            resp.raise_for_status()
            data = resp.json()

            campaigns = []
            for batch in (data if isinstance(data, list) else [data]):
                for result in batch.get("results", []):
                    campaign = result.get("campaign", {})
                    campaigns.append({
                        "id": campaign.get("id"),
                        "resource_name": campaign.get("resourceName"),
                        "name": campaign.get("name"),
                        "status": campaign.get("status"),
                        "channel_type": campaign.get("advertisingChannelType"),
                        "budget": campaign.get("campaignBudget"),
                        "start_date": campaign.get("startDate"),
                        "end_date": campaign.get("endDate"),
                    })

            return {
                "status": "success",
                "campaigns": campaigns,
                "count": len(campaigns),
                "customer_id": customer_id,
            }

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            return {"error": "Google Ads authentication failed. Token may be expired or revoked."}
        if e.response.status_code == 403:
            return {"error": "Insufficient permissions for this Google Ads account."}
        if e.response.status_code == 429:
            return {"error": "Google Ads API rate limit reached. Try again shortly."}
        return {"error": f"Google Ads API error ({e.response.status_code}): {e.response.text[:300]}"}
    except Exception as e:
        logger.exception("list_google_campaigns failed")
        return {"error": f"Failed to list Google campaigns: {str(e)}"}


@mcp.tool()
async def get_google_campaign_metrics(
    campaign_id: str,
    tenant_id: str = "",
    date_range: str = "LAST_7_DAYS",
    ctx: Context = None,
) -> dict:
    """Get performance metrics for a Google Ads campaign.

    Args:
        campaign_id: The Google Ads campaign ID. Required.
        tenant_id: Tenant UUID (resolved from session if omitted).
        date_range: Time range: "TODAY", "YESTERDAY", "LAST_7_DAYS", "LAST_30_DAYS".
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with impressions, clicks, spend, CTR, CPC, conversions, and cost/conversion.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"error": "tenant_id is required."}
    if not campaign_id:
        return {"error": "campaign_id is required."}

    creds = await _get_ads_credentials(tid, "google_ads")
    if not creds:
        return {"error": "Google Ads not connected. Ask the user to configure Google Ads in Connected Apps."}

    developer_token = creds.get("developer_token")
    customer_id = creds.get("customer_id", "").replace("-", "")
    refresh_token = creds.get("refresh_token")

    if not developer_token or not customer_id or not refresh_token:
        return {"error": "Google Ads credentials incomplete."}

    access_token = await _refresh_google_access_token(refresh_token)
    if not access_token:
        return {"error": "Failed to refresh Google access token."}

    valid_ranges = {"TODAY", "YESTERDAY", "LAST_7_DAYS", "LAST_30_DAYS"}
    date_range = date_range.upper() if date_range.upper() in valid_ranges else "LAST_7_DAYS"

    gaql = (
        "SELECT campaign.id, campaign.name, "
        "metrics.impressions, metrics.clicks, metrics.cost_micros, "
        "metrics.ctr, metrics.average_cpc, metrics.conversions, "
        "metrics.cost_per_conversion, metrics.average_cpm "
        "FROM campaign "
        f"WHERE campaign.id = {campaign_id} "
        f"AND segments.date DURING {date_range}"
    )

    headers = {
        "Authorization": f"Bearer {access_token}",
        "developer-token": developer_token,
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{GOOGLE_ADS_API_URL}/customers/{customer_id}/googleAds:searchStream",
                headers=headers,
                json={"query": gaql},
            )
            resp.raise_for_status()
            data = resp.json()

            total_impressions = total_clicks = total_cost_micros = 0
            total_conversions = 0.0
            campaign_name = ""

            for batch in (data if isinstance(data, list) else [data]):
                for result in batch.get("results", []):
                    campaign_name = result.get("campaign", {}).get("name", campaign_name)
                    m = result.get("metrics", {})
                    total_impressions += int(m.get("impressions", 0))
                    total_clicks += int(m.get("clicks", 0))
                    total_cost_micros += int(m.get("costMicros", 0))
                    total_conversions += float(m.get("conversions", 0))

            spend = total_cost_micros / 1_000_000
            ctr = (total_clicks / total_impressions * 100) if total_impressions else 0
            avg_cpc = (spend / total_clicks) if total_clicks else 0
            cost_per_conv = (spend / total_conversions) if total_conversions else 0

            return {
                "status": "success",
                "campaign_id": campaign_id,
                "campaign_name": campaign_name,
                "date_range": date_range,
                "metrics": {
                    "impressions": total_impressions,
                    "clicks": total_clicks,
                    "spend": round(spend, 2),
                    "ctr": round(ctr, 4),
                    "average_cpc": round(avg_cpc, 2),
                    "conversions": round(total_conversions, 2),
                    "cost_per_conversion": round(cost_per_conv, 2),
                },
            }

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            return {"error": "Google Ads authentication failed. Token may be expired."}
        if e.response.status_code == 429:
            return {"error": "Google Ads API rate limit reached."}
        return {"error": f"Google Ads API error ({e.response.status_code}): {e.response.text[:300]}"}
    except Exception as e:
        logger.exception("get_google_campaign_metrics failed")
        return {"error": f"Failed to get Google metrics: {str(e)}"}


@mcp.tool()
async def pause_google_campaign(
    campaign_id: str,
    tenant_id: str = "",
    ctx: Context = None,
) -> dict:
    """Pause a Google Ads campaign.

    Args:
        campaign_id: The Google Ads campaign ID to pause. Required.
        tenant_id: Tenant UUID (resolved from session if omitted).
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with confirmation of the pause action.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"error": "tenant_id is required."}
    if not campaign_id:
        return {"error": "campaign_id is required."}

    creds = await _get_ads_credentials(tid, "google_ads")
    if not creds:
        return {"error": "Google Ads not connected. Ask the user to configure Google Ads in Connected Apps."}

    developer_token = creds.get("developer_token")
    customer_id = creds.get("customer_id", "").replace("-", "")
    refresh_token = creds.get("refresh_token")

    if not developer_token or not customer_id or not refresh_token:
        return {"error": "Google Ads credentials incomplete."}

    access_token = await _refresh_google_access_token(refresh_token)
    if not access_token:
        return {"error": "Failed to refresh Google access token."}

    resource_name = f"customers/{customer_id}/campaigns/{campaign_id}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "developer-token": developer_token,
        "Content-Type": "application/json",
    }

    payload = {
        "operations": [
            {
                "updateMask": "status",
                "update": {
                    "resourceName": resource_name,
                    "status": "PAUSED",
                },
            }
        ],
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{GOOGLE_ADS_API_URL}/customers/{customer_id}/campaigns:mutate",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()

            return {
                "status": "success",
                "campaign_id": campaign_id,
                "action": "paused",
                "message": f"Google Ads campaign {campaign_id} has been paused.",
                "resource_name": resource_name,
            }

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            return {"error": "Google Ads authentication failed."}
        if e.response.status_code == 403:
            return {"error": "Insufficient permissions to modify this Google Ads campaign."}
        return {"error": f"Google Ads mutate error ({e.response.status_code}): {e.response.text[:300]}"}
    except Exception as e:
        logger.exception("pause_google_campaign failed")
        return {"error": f"Failed to pause Google campaign: {str(e)}"}


@mcp.tool()
async def search_google_ads_transparency(
    advertiser: str,
    region: str = "US",
    ctx: Context = None,
) -> dict:
    """Search the Google Ads Transparency Center. PUBLIC — no authentication required.

    Note: The Google Ads Transparency Center does not have a public API.
    Returns the search URL for a web_researcher agent to scrape.

    Args:
        advertiser: Advertiser name or domain to search for. Required.
        region: Two-letter region code (e.g., "US", "EU", "GB"). Default: "US".
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with the Transparency Center URL for the search.
    """
    if not advertiser:
        return {"error": "advertiser is required (e.g., a company name or domain)."}

    search_url = (
        f"https://adstransparency.google.com/"
        f"?region={region.lower()}&query={advertiser}"
    )

    return {
        "status": "url_only",
        "message": (
            "Google Ads Transparency Center does not provide a public API. "
            "Use the URL below to view ads, or ask web_researcher to scrape the page."
        ),
        "url": search_url,
        "advertiser": advertiser,
        "region": region,
    }


# ===========================================================================
# TikTok Ads tools
# ===========================================================================


@mcp.tool()
async def list_tiktok_campaigns(
    tenant_id: str = "",
    status_filter: str = "",
    limit: int = 25,
    ctx: Context = None,
) -> dict:
    """List TikTok Ads campaigns.

    Args:
        tenant_id: Tenant UUID (resolved from session if omitted).
        status_filter: Filter by status: "CAMPAIGN_STATUS_ENABLE", "CAMPAIGN_STATUS_DISABLE",
                       "CAMPAIGN_STATUS_DELETE", or "" for all.
        limit: Maximum number of campaigns to return (1-100).
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with list of campaigns including id, name, status, objective, and budget.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"error": "tenant_id is required."}

    creds = await _get_ads_credentials(tid, "tiktok_ads")
    if not creds:
        return {"error": "TikTok Ads not connected. Ask the user to configure TikTok Ads in Connected Apps (Integrations page)."}

    access_token = creds.get("access_token")
    advertiser_id = creds.get("advertiser_id", "").strip()
    if not access_token or not advertiser_id:
        return {"error": "TikTok Ads credentials incomplete. Need access_token and advertiser_id."}

    headers = {"Access-Token": access_token, "Content-Type": "application/json"}
    params = {
        "advertiser_id": advertiser_id,
        "page_size": min(max(limit, 1), 100),
        "page": 1,
    }
    if status_filter:
        params["filtering"] = f'{{"status": "{status_filter.upper()}"}}'

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{TIKTOK_ADS_URL}/campaign/get/",
                headers=headers,
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()

            if data.get("code") != 0:
                return {"error": f"TikTok API error: {data.get('message', 'Unknown error')}"}

            campaigns = [
                {
                    "campaign_id": c.get("campaign_id"),
                    "campaign_name": c.get("campaign_name"),
                    "status": c.get("status") or c.get("secondary_status"),
                    "objective_type": c.get("objective_type"),
                    "budget": c.get("budget"),
                    "budget_mode": c.get("budget_mode"),
                    "create_time": c.get("create_time"),
                    "modify_time": c.get("modify_time"),
                }
                for c in data.get("data", {}).get("list", [])
            ]

            return {
                "status": "success",
                "campaigns": campaigns,
                "count": len(campaigns),
                "advertiser_id": advertiser_id,
            }

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            return {"error": "TikTok Ads access token expired. Reconnect in Connected Apps."}
        if e.response.status_code == 429:
            return {"error": "TikTok API rate limit reached. Try again shortly."}
        return {"error": f"TikTok API error ({e.response.status_code}): {e.response.text[:300]}"}
    except Exception as e:
        logger.exception("list_tiktok_campaigns failed")
        return {"error": f"Failed to list TikTok campaigns: {str(e)}"}


@mcp.tool()
async def get_tiktok_campaign_insights(
    campaign_id: str,
    tenant_id: str = "",
    date_range: str = "LAST_7_DAYS",
    ctx: Context = None,
) -> dict:
    """Get performance insights for a TikTok Ads campaign.

    Args:
        campaign_id: The TikTok campaign ID. Required.
        tenant_id: Tenant UUID (resolved from session if omitted).
        date_range: Time range: "TODAY", "YESTERDAY", "LAST_7_DAYS", "LAST_30_DAYS".
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with impressions, clicks, spend, CTR, CPC, and conversion metrics.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"error": "tenant_id is required."}
    if not campaign_id:
        return {"error": "campaign_id is required."}

    creds = await _get_ads_credentials(tid, "tiktok_ads")
    if not creds:
        return {"error": "TikTok Ads not connected. Ask the user to configure TikTok Ads in Connected Apps."}

    access_token = creds.get("access_token")
    advertiser_id = creds.get("advertiser_id", "").strip()
    if not access_token or not advertiser_id:
        return {"error": "TikTok Ads credentials incomplete."}

    today = datetime.utcnow().date()
    date_map = {
        "TODAY": (today, today),
        "YESTERDAY": (today - timedelta(days=1), today - timedelta(days=1)),
        "LAST_7_DAYS": (today - timedelta(days=7), today),
        "LAST_30_DAYS": (today - timedelta(days=30), today),
    }
    date_key = date_range.upper() if date_range.upper() in date_map else "LAST_7_DAYS"
    start_date, end_date = date_map[date_key]

    headers = {"Access-Token": access_token, "Content-Type": "application/json"}
    params = {
        "advertiser_id": advertiser_id,
        "report_type": "BASIC",
        "dimensions": '["campaign_id"]',
        "data_level": "AUCTION_CAMPAIGN",
        "start_date": start_date.strftime("%Y-%m-%d"),
        "end_date": end_date.strftime("%Y-%m-%d"),
        "metrics": '["spend","impressions","clicks","ctr","cpc","conversion","cost_per_conversion"]',
        "filtering": f'[{{"field_name":"campaign_id","filter_type":"IN","filter_value":"[\\"{campaign_id}\\"]"}}]',
        "page_size": 10,
        "page": 1,
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{TIKTOK_ADS_URL}/report/integrated/get/",
                headers=headers,
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()

            if data.get("code") != 0:
                return {"error": f"TikTok API error: {data.get('message', 'Unknown error')}"}

            rows = data.get("data", {}).get("list", [])
            if not rows:
                return {
                    "status": "success",
                    "campaign_id": campaign_id,
                    "date_range": date_key,
                    "message": "No data available for this date range.",
                    "metrics": {},
                }

            row = rows[0].get("metrics", {})
            return {
                "status": "success",
                "campaign_id": campaign_id,
                "date_range": date_key,
                "start_date": start_date.strftime("%Y-%m-%d"),
                "end_date": end_date.strftime("%Y-%m-%d"),
                "metrics": {
                    "impressions": row.get("impressions", "0"),
                    "clicks": row.get("clicks", "0"),
                    "spend": row.get("spend", "0"),
                    "ctr": row.get("ctr", "0"),
                    "cpc": row.get("cpc", "0"),
                    "conversions": row.get("conversion", "0"),
                    "cost_per_conversion": row.get("cost_per_conversion", "0"),
                },
            }

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            return {"error": "TikTok Ads access token expired. Reconnect in Connected Apps."}
        if e.response.status_code == 429:
            return {"error": "TikTok API rate limit reached. Try again shortly."}
        return {"error": f"TikTok API error ({e.response.status_code}): {e.response.text[:300]}"}
    except Exception as e:
        logger.exception("get_tiktok_campaign_insights failed")
        return {"error": f"Failed to get TikTok insights: {str(e)}"}


@mcp.tool()
async def pause_tiktok_campaign(
    campaign_id: str,
    tenant_id: str = "",
    ctx: Context = None,
) -> dict:
    """Pause (disable) a TikTok Ads campaign.

    Args:
        campaign_id: The TikTok campaign ID to pause. Required.
        tenant_id: Tenant UUID (resolved from session if omitted).
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with confirmation of the pause action.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"error": "tenant_id is required."}
    if not campaign_id:
        return {"error": "campaign_id is required."}

    creds = await _get_ads_credentials(tid, "tiktok_ads")
    if not creds:
        return {"error": "TikTok Ads not connected. Ask the user to configure TikTok Ads in Connected Apps."}

    access_token = creds.get("access_token")
    advertiser_id = creds.get("advertiser_id", "").strip()
    if not access_token or not advertiser_id:
        return {"error": "TikTok Ads credentials incomplete."}

    headers = {"Access-Token": access_token, "Content-Type": "application/json"}
    payload = {
        "advertiser_id": advertiser_id,
        "campaign_ids": [campaign_id],
        "opt_status": "DISABLE",
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{TIKTOK_ADS_URL}/campaign/status/update/",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

            if data.get("code") != 0:
                return {"error": f"TikTok API error: {data.get('message', 'Unknown error')}"}

            return {
                "status": "success",
                "campaign_id": campaign_id,
                "action": "paused",
                "message": f"TikTok campaign {campaign_id} has been paused (DISABLE).",
            }

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            return {"error": "TikTok Ads access token expired. Reconnect in Connected Apps."}
        if e.response.status_code == 403:
            return {"error": "Insufficient permissions to modify this TikTok campaign."}
        return {"error": f"TikTok API error ({e.response.status_code}): {e.response.text[:300]}"}
    except Exception as e:
        logger.exception("pause_tiktok_campaign failed")
        return {"error": f"Failed to pause TikTok campaign: {str(e)}"}


@mcp.tool()
async def search_tiktok_creative_center(
    keyword: str,
    country: str = "US",
    limit: int = 25,
    ctx: Context = None,
) -> dict:
    """Search the TikTok Creative Center for ad inspiration. PUBLIC — no authentication required.

    Note: TikTok Creative Center does not provide a public API.
    Returns the search URL for a web_researcher agent to scrape.

    Args:
        keyword: Search keyword or brand name. Required.
        country: Two-letter country code (e.g., "US", "GB"). Default: "US".
        limit: Desired number of results (used as hint for scraper).
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with the Creative Center URL for the search.
    """
    if not keyword:
        return {"error": "keyword is required (e.g., a brand name or ad category)."}

    search_url = (
        f"https://ads.tiktok.com/business/creativecenter/inspiration/topads/pc/en"
        f"?region={country.upper()}&keyword={keyword}&limit={min(max(limit, 1), 50)}"
    )

    return {
        "status": "url_only",
        "message": (
            "TikTok Creative Center does not provide a public API. "
            "Use the URL below to browse top ads, or ask web_researcher to scrape the page."
        ),
        "url": search_url,
        "keyword": keyword,
        "country": country,
    }
