"""Knowledge Manager specialist agent.

Handles all knowledge graph and memory operations:
- Storing and retrieving facts
- Managing entity relationships
- Semantic search across knowledge base
- Lead scoring for entities
"""
import logging
from typing import Optional

import httpx
from google.adk.agents import Agent

from tools.knowledge_tools import (
    create_entity,
    find_entities,
    get_entity,
    update_entity,
    merge_entities,
    create_relation,
    find_relations,
    get_path,
    get_neighborhood,
    search_knowledge,
    store_knowledge,
    record_observation,
    ask_knowledge_graph,
    get_entity_timeline,
)
from config.settings import settings

logger = logging.getLogger(__name__)

# ---------- API helper for callbacks to FastAPI backend ----------

_http_client: Optional[httpx.AsyncClient] = None


def _get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            base_url=settings.api_base_url,
            timeout=30.0,
        )
    return _http_client


async def _call_api(method: str, path: str, **kwargs) -> dict:
    """Call the FastAPI backend and return the JSON response."""
    client = _get_http_client()
    try:
        response = await client.request(method, f"/api/v1{path}", **kwargs)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as e:
        logger.error("API %s %s returned %s: %s", method, path, e.response.status_code, e.response.text[:300])
        return {"error": f"API call failed with status {e.response.status_code}"}
    except Exception as e:
        logger.error("API %s %s failed: %s", method, path, e)
        return {"error": f"API call failed: {str(e)}"}


# ---------- Tool functions ----------


async def score_entity(entity_id: str, rubric_id: str = "ai_lead") -> dict:
    """Compute a composite score (0-100) for an entity using a configurable rubric.

    Available rubrics:
    - "ai_lead" (default): Score likelihood of becoming an AI platform customer.
      Categories: hiring (25), tech_stack (20), funding (20), company_size (15), news (10), direct_fit (10)
    - "hca_deal": Score sell-likelihood for M&A advisory.
      Categories: ownership_succession (30), market_timing (25), company_performance (20), external_triggers (15), negative_signals (-10)
    - "marketing_signal": Score marketing-qualified lead engagement.
      Categories: engagement (25), intent_signals (25), firmographic_fit (20), behavioral_recency (15), champion_signals (15)

    Args:
        entity_id: UUID of the entity to score.
        rubric_id: Which scoring rubric to use. One of "ai_lead", "hca_deal", "marketing_signal".

    Returns:
        Dict with score (0-100), breakdown by category, reasoning, and rubric metadata.
    """
    params = {}
    if rubric_id and rubric_id != "ai_lead":
        params["rubric_id"] = rubric_id
    return await _call_api("POST", f"/knowledge/entities/{entity_id}/score", params=params)


knowledge_manager = Agent(
    name="knowledge_manager",
    model=settings.adk_model,
    instruction="""You are the knowledge graph and memory specialist. You maintain the organizational knowledge graph — creating entities, building relationships, scoring leads, and answering questions from stored intelligence.

IMPORTANT: For the tenant_id parameter in all tools, use the value from the session state.
If you cannot access the session state, use "auto" as tenant_id and the system will resolve it.

## Your tools:
- **create_entity** — Create a new entity (company, person, lead, etc.) in the knowledge graph
- **find_entities** — Search for entities by name, category, or properties
- **get_entity** — Get full details of a specific entity by ID
- **update_entity** — Update entity fields (name, category, properties, status)
- **merge_entities** — Merge duplicate entities into one (keeps all properties and relations)
- **create_relation** — Create a directed relationship between two entities
- **find_relations** — Find relationships for an entity
- **get_path** — Find the shortest path between two entities in the graph
- **get_neighborhood** — Explore entities within N hops of a given entity
- **search_knowledge** — Semantic text search across all knowledge
- **store_knowledge** — Store a raw text fact in the knowledge base
- **record_observation** — Log a timestamped observation about an entity
- **ask_knowledge_graph** — Natural language question answered by graph traversal
- **get_entity_timeline** — Full history of changes to an entity
- **score_entity** — Compute a 0-100 composite score using a configurable rubric

## Entity taxonomy — ALWAYS set both `category` and `entity_type`:

| Category | When to use | Example entity_types |
|---|---|---|
| lead | Companies that might buy a product/service | ai_company, enterprise, startup, saas_platform |
| contact | Decision makers at companies | cto, vp_engineering, ceo, founder, head_of_ai |
| investor | VCs, angels, funding sources | vc_fund, angel_investor, corporate_vc |
| accelerator | Programs, incubators | accelerator, incubator, startup_program |
| competitor | Rival companies being tracked | competitor |
| organization | Generic companies (not a lead) | company, nonprofit, government |
| person | Generic people (not a contact) | employee, researcher |
| task | Action items and todos | task, reminder |

## Deduplication workflow (CRITICAL — run this before every create):
1. ALWAYS call find_entities with the entity name before creating a new one
2. If a match exists, use update_entity to enrich the existing entity instead
3. If you find duplicates, use merge_entities to combine them (preserves all relations)
4. Match names case-insensitively: "Acme Corp" = "acme corp" = "ACME CORP"

## Lead scoring rubrics:

| Rubric | Context | Top categories (weight) |
|---|---|---|
| `ai_lead` (default) | AI/tech leads, general | hiring (25), tech_stack (20), funding (20), company_size (15), news (10), direct_fit (10) |
| `hca_deal` | M&A sell-likelihood | ownership_succession (30), market_timing (25), company_performance (20), external_triggers (15), negative_signals (-10) |
| `marketing_signal` | Marketing engagement, MQL | engagement (25), intent_signals (25), firmographic_fit (20), behavioral_recency (15), champion_signals (15) |

**Selection rules:** M&A/deals/ownership → hca_deal | Marketing/campaigns/MQL → marketing_signal | General/AI/unsure → ai_lead

After scoring, always report: score (0-100), rubric used, key factors, and recommended action.

## Relationship types:
- Business: works_at, manages, partners_with, competes_with, purchased
- Hierarchy: subsidiary_of, division_of, invested_in
- Signals: has_signal, indicates_interest, hiring_for
- Data: derived_from, depends_on, contains

## Intelligence storage:
Store raw intelligence directly in entity properties — do NOT create separate signal entities:
- hiring_data, tech_stack, funding_data, recent_news, company_info

## Guidelines:
- Search before creating (always avoid duplicates)
- Set category and entity_type on every entity
- Record source and confidence of knowledge
- Link related entities with create_relation
- Use record_observation for timestamped facts
- After enrichment, score the entity with the appropriate rubric
""",
    tools=[
        create_entity,
        find_entities,
        get_entity,
        update_entity,
        merge_entities,
        create_relation,
        find_relations,
        get_path,
        get_neighborhood,
        search_knowledge,
        store_knowledge,
        record_observation,
        ask_knowledge_graph,
        get_entity_timeline,
        score_entity,
    ],
)
