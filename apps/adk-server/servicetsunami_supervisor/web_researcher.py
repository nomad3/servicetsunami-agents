"""Web Researcher specialist agent.

Handles web scraping and research operations:
- Scraping websites for content and data
- Searching the web for leads, companies, and market signals
- Extracting structured data from web pages
"""
import logging
from typing import Optional

import httpx
from google.adk.agents import Agent

from config.settings import settings

logger = logging.getLogger(__name__)


# ---------- httpx client (follows databricks_client.py pattern) ----------

class MCPScraperClient:
    """HTTP client for MCP server scraping endpoints."""

    def __init__(self):
        self.base_url = settings.mcp_scraper_url
        self.api_key = settings.mcp_api_key
        self.tenant_code = settings.mcp_tenant_code
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "X-API-Key": self.api_key,
                "X-Tenant-ID": self.tenant_code,
            },
            timeout=60.0,
        )

    async def post(self, path: str, payload: dict) -> dict:
        try:
            response = await self.client.post(path, json=payload)
            response.raise_for_status()
            return response.json()
        except httpx.ConnectError as e:
            logger.error("MCP server unreachable for %s: %s", path, e)
            return {"error": "MCP scraper server is unreachable. Please try again later."}
        except httpx.TimeoutException as e:
            logger.error("MCP %s timed out: %s", path, e)
            return {"error": "Scraping request timed out. The page may be slow or unresponsive."}
        except httpx.HTTPStatusError as e:
            logger.error("MCP %s returned %s: %s", path, e.response.status_code, e.response.text[:200])
            return {"error": f"Scraping failed with status {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            logger.error("MCP %s failed: %s", path, e)
            return {"error": f"Scraping failed: {str(e)}"}


_client: Optional[MCPScraperClient] = None


def _get_client() -> MCPScraperClient:
    global _client
    if _client is None:
        _client = MCPScraperClient()
    return _client


# ---------- ADK FunctionTool wrappers ----------

async def scrape_webpage(
    url: str,
    selectors: Optional[dict] = None,
    wait_for: Optional[str] = None,
) -> dict:
    """Scrape a single webpage and extract its content.

    Args:
        url: The full URL of the webpage to scrape.
        selectors: Optional CSS selectors mapping field names to selectors for targeted extraction.
        wait_for: Optional CSS selector to wait for before extracting content.

    Returns:
        Dict with url, title, content, links, and meta information.
    """
    payload = {"url": url}
    if selectors:
        payload["selectors"] = selectors
    if wait_for:
        payload["wait_for"] = wait_for
    payload["extract_links"] = True
    return await _get_client().post("/servicetsunami/v1/scrape", payload)


async def scrape_structured_data(
    url: str,
    schema: dict,
) -> dict:
    """Scrape a webpage and extract structured data using CSS selectors.

    Args:
        url: The full URL of the webpage to scrape.
        schema: A dict mapping field names to CSS selectors, e.g. {"company_name": "h1.title", "description": "p.about"}.

    Returns:
        Dict with url and extracted data fields.
    """
    return await _get_client().post("/servicetsunami/v1/scrape/structured", {
        "url": url,
        "schema": schema,
    })


async def search_and_scrape(
    query: str,
    max_results: int = 5,
) -> dict:
    """Search the web for a query and scrape the top results.

    Args:
        query: The search query (e.g. "AI companies hiring in Austin").
        max_results: Maximum number of results to scrape (1-10).

    Returns:
        Dict with query and list of results, each containing url, title, snippet, and content.
    """
    return await _get_client().post("/servicetsunami/v1/search-and-scrape", {
        "query": query,
        "max_results": min(max_results, 10),
    })


async def login_google(email: str, password: str) -> dict:
    """Login to Google via the MCP server's Playwright browser.

    This authenticates the scraping browser with Google credentials so that
    subsequent web searches and scrapes use an authenticated Google session,
    avoiding CAPTCHA blocks from cloud IPs.

    Args:
        email: Google/Gmail email address.
        password: Google account password.

    Returns:
        Dict with status, cookies_stored count, and authenticated domains.
    """
    return await _get_client().post("/servicetsunami/v1/auth/google/login", {
        "email": email,
        "password": password,
    })


async def login_linkedin(email: str, password: str) -> dict:
    """Login to LinkedIn via the MCP server's Playwright browser.

    This authenticates the scraping browser with LinkedIn credentials so that
    subsequent LinkedIn page scrapes can access full profile and company data.

    Args:
        email: LinkedIn email address.
        password: LinkedIn password.

    Returns:
        Dict with status, cookies_stored count, and authenticated domains.
    """
    return await _get_client().post("/servicetsunami/v1/auth/linkedin/login", {
        "email": email,
        "password": password,
    })


# ---------- Agent definition ----------

web_researcher = Agent(
    name="web_researcher",
    model=settings.adk_model,
    instruction="""You are a web research and intelligence gathering specialist. You scrape websites, search the internet, and extract structured data to support business intelligence, lead generation, and competitive analysis.

## Your tools:
- **search_and_scrape** — Search the web and scrape top results. Start here for broad queries like "AI companies in Austin" or "HVAC companies hiring in Texas". Set max_results=3-5 to balance speed and coverage.
- **scrape_webpage** — Scrape a specific URL for full content, links, and metadata. Use for known URLs or to dive deeper into promising search results.
- **scrape_structured_data** — Extract specific fields using CSS selectors. Use when you know the page structure (e.g., {"company_name": "h1.title", "revenue": ".financial-data .revenue"}).
- **login_google** — Authenticate with Google to avoid CAPTCHA blocks on searches. Only needed if searches fail with blocking errors. Persists for the session.
- **login_linkedin** — Authenticate with LinkedIn for full profile/company data. Only needed if LinkedIn returns limited results. Persists for the session.

## Research workflow:
1. **Search broadly**: Use search_and_scrape with a targeted query
2. **Deep dive**: Use scrape_webpage on the most promising results
3. **Extract structure**: Use scrape_structured_data when you need specific fields
4. **Summarize**: Present findings with company names, URLs, key contacts, and actionable intelligence
5. **Store**: Delegate to knowledge_manager to persist valuable entities in the knowledge graph

## Intelligence extraction — ALWAYS DO THIS when scraping companies:
Extract and organize these data points into the entity's properties (via knowledge_manager):
- **hiring_data**: Job titles, open positions count, seniority levels, departments
- **tech_stack**: Technologies, frameworks, platforms, cloud providers mentioned
- **funding_data**: Round type, amount, date, lead investors
- **recent_news**: Announcements, product launches, partnerships
- **company_info**: Employee count, locations, founding year, revenue if available
- **key_contacts**: Founders, C-suite — create separate "contact" entities linked to the company

After enrichment, ask knowledge_manager to score the entity using score_entity.

## Entity categorization (when delegating to knowledge_manager):
- Companies that might buy products/services → category: "lead"
- Executives and decision makers → category: "contact"
- VCs, angels, investment firms → category: "investor"
- Accelerator/incubator programs → category: "accelerator"
- Generic companies → category: "organization"
- Generic people → category: "person"

## Output format:
When presenting research results, include:
- Company/person name and URL
- Key facts (industry, size, location, tech stack)
- Relevant intelligence signals (hiring, funding, news)
- Actionable takeaway: "This company is actively hiring ML engineers and recently raised Series B — strong lead signal"

## Rate limiting:
Don't scrape more than 5-7 pages in a single request. If you need to research many companies, batch your work and summarize progress between batches.
""",
    tools=[
        scrape_webpage,
        scrape_structured_data,
        search_and_scrape,
        login_google,
        login_linkedin,
    ],
)
