"""Sales automation MCP tools.

Sales automation and pipeline management tools.
Tools for lead qualification, outreach drafting, pipeline management,
proposal generation, and follow-up scheduling. All operations route
through the internal API which accesses the knowledge graph.
"""
import logging
from datetime import datetime
from typing import Optional

import httpx
from mcp.server.fastmcp import Context

from src.mcp_app import mcp
from src.mcp_auth import resolve_tenant_id

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_api_base_url() -> str:
    from src.config import settings
    return settings.API_BASE_URL.rstrip("/")


def _get_internal_key() -> str:
    from src.config import settings
    return settings.API_INTERNAL_KEY


async def _get_entity(entity_id: str, tenant_id: str, include_relations: bool = False) -> Optional[dict]:
    """Fetch a knowledge entity from the internal API."""
    api_base_url = _get_api_base_url()
    internal_key = _get_internal_key()
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{api_base_url}/api/v1/knowledge/entities/{entity_id}/internal",
                headers={"X-Internal-Key": internal_key},
                params={
                    "tenant_id": tenant_id,
                    "include_relations": str(include_relations).lower(),
                },
            )
            if resp.status_code == 200:
                return resp.json()
            logger.warning("Entity %s not found: %s", entity_id, resp.status_code)
    except Exception:
        logger.exception("Failed to fetch entity %s", entity_id)
    return None


async def _update_entity(entity_id: str, tenant_id: str, updates: dict, reason: str = "") -> bool:
    """Update a knowledge entity via the internal API."""
    api_base_url = _get_api_base_url()
    internal_key = _get_internal_key()
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.patch(
                f"{api_base_url}/api/v1/knowledge/entities/{entity_id}/internal",
                headers={"X-Internal-Key": internal_key},
                json={"tenant_id": tenant_id, "reason": reason, **updates},
            )
            return resp.status_code in (200, 204)
    except Exception:
        logger.exception("Failed to update entity %s", entity_id)
    return False


async def _search_entities(tenant_id: str, query: str, limit: int = 50) -> list:
    """Search knowledge entities via the internal API."""
    api_base_url = _get_api_base_url()
    internal_key = _get_internal_key()
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{api_base_url}/api/v1/knowledge/entities/internal/search",
                headers={"X-Internal-Key": internal_key},
                params={"tenant_id": tenant_id, "q": query, "limit": limit},
            )
            if resp.status_code == 200:
                result = resp.json()
                return result if isinstance(result, list) else result.get("entities", [])
    except Exception:
        logger.exception("Failed to search entities")
    return []


def _assess_budget(props: dict) -> dict:
    funding = props.get("funding_data", {})
    has_funding = bool(funding.get("total_raised") or funding.get("last_round"))
    return {
        "score": 70 if has_funding else 30,
        "evidence": f"Funding: {funding.get('total_raised', 'unknown')}",
        "assessment": "Has funding" if has_funding else "Funding unknown",
    }


def _assess_authority(props: dict) -> dict:
    contacts = props.get("contacts", [])
    has_decision_maker = any(
        c.get("role", "").lower() in ("ceo", "cto", "vp", "director", "head", "founder")
        for c in contacts
    ) if contacts else False
    return {
        "score": 80 if has_decision_maker else 40,
        "evidence": f"{len(contacts)} contacts identified",
        "assessment": "Decision maker identified" if has_decision_maker else "No decision maker found",
    }


def _assess_need(props: dict) -> dict:
    hiring = props.get("hiring_data", {})
    tech = props.get("tech_stack", [])
    signals = bool(hiring) or bool(tech)
    return {
        "score": 70 if signals else 30,
        "evidence": f"Hiring: {bool(hiring)}, Tech stack: {len(tech)} items",
        "assessment": "Active signals detected" if signals else "No clear need signals",
    }


def _assess_timeline(props: dict) -> dict:
    news = props.get("recent_news", [])
    has_urgency = len(news) > 0
    return {
        "score": 60 if has_urgency else 30,
        "evidence": f"{len(news)} recent news items",
        "assessment": "Recent activity suggests active timeline" if has_urgency else "No urgency signals",
    }


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def qualify_lead(
    entity_id: str,
    tenant_id: str = "",
    ctx: Context = None,
) -> dict:
    """Qualify a lead using BANT framework (Budget, Authority, Need, Timeline).

    Fetches entity context from the knowledge graph, evaluates qualification
    criteria, and updates the entity properties with qualification results.

    Args:
        entity_id: UUID of the lead entity to qualify. Required.
        tenant_id: Tenant UUID (resolved from session if omitted).
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with budget, authority, need, timeline assessments, overall
        qualified boolean, and summary.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"error": "tenant_id is required."}
    if not entity_id:
        return {"error": "entity_id is required."}

    entity = await _get_entity(entity_id, tid, include_relations=True)
    if not entity:
        return {"error": f"Entity {entity_id} not found"}

    props = entity.get("properties", {})
    qualification = {
        "budget": _assess_budget(props),
        "authority": _assess_authority(props),
        "need": _assess_need(props),
        "timeline": _assess_timeline(props),
    }
    scores = [v["score"] for v in qualification.values()]
    avg_score = sum(scores) / len(scores) if scores else 0
    qualified = avg_score >= 50

    qualification["qualified"] = qualified
    qualification["score"] = round(avg_score)
    qualification["summary"] = (
        f"{'Qualified' if qualified else 'Not qualified'} "
        f"(score: {round(avg_score)}/100)"
    )

    updated_props = {**props, "qualification": qualification, "qualified": qualified}
    if qualified and not props.get("pipeline_stage"):
        updated_props["pipeline_stage"] = "qualified"

    await _update_entity(entity_id, tid, {"properties": updated_props}, reason="BANT qualification")

    return {"status": "success", **qualification}


@mcp.tool()
async def draft_outreach(
    entity_id: str,
    channel: str = "email",
    tone: str = "professional",
    tenant_id: str = "",
    ctx: Context = None,
) -> dict:
    """Draft a personalized outreach message for a lead.

    Generates a message template based on the entity's properties, qualification
    status, and the specified channel format. The LLM should personalize the body
    using the provided context.

    Args:
        entity_id: UUID of the lead/contact entity. Required.
        channel: Message channel - "email", "whatsapp", or "linkedin".
        tone: Message tone - "professional", "casual", or "formal".
        tenant_id: Tenant UUID (resolved from session if omitted).
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with subject (for email), body template, channel, and entity context.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"error": "tenant_id is required."}
    if not entity_id:
        return {"error": "entity_id is required."}

    entity = await _get_entity(entity_id, tid, include_relations=True)
    if not entity:
        return {"error": f"Entity {entity_id} not found"}

    name = entity.get("name", "there")
    props = entity.get("properties", {})
    description = entity.get("description", "")

    context_parts = [f"Company/Contact: {name}"]
    if description:
        context_parts.append(f"About: {description}")
    if props.get("tech_stack"):
        context_parts.append(f"Tech stack: {', '.join(props['tech_stack'][:5])}")
    if props.get("hiring_data"):
        context_parts.append(f"Hiring: {props['hiring_data']}")
    if props.get("qualification"):
        qual = props["qualification"]
        context_parts.append(f"Qualification: {qual.get('summary', 'N/A')}")

    context = "\n".join(context_parts)

    if channel == "email":
        return {
            "status": "success",
            "channel": "email",
            "subject": f"Quick question for {name}",
            "body": f"Hi {name},\n\n[Personalize based on: {context}]\n\nBest regards",
            "entity_name": name,
            "context": context,
            "note": "Draft template — personalize the body using the provided context.",
        }
    elif channel == "whatsapp":
        return {
            "status": "success",
            "channel": "whatsapp",
            "body": f"Hi {name}! [Personalize based on: {context}]",
            "entity_name": name,
            "context": context,
            "note": "Short, conversational format for WhatsApp.",
        }
    else:
        return {
            "status": "success",
            "channel": channel,
            "body": f"Hi {name}, [Personalize based on: {context}]",
            "entity_name": name,
            "context": context,
        }


@mcp.tool()
async def update_pipeline_stage(
    entity_id: str,
    new_stage: str,
    tenant_id: str = "",
    reason: str = "",
    ctx: Context = None,
) -> dict:
    """Move a lead entity to a new pipeline stage.

    Updates the entity's pipeline_stage property and records the transition
    as an observation for audit trail.

    Args:
        entity_id: UUID of the entity. Required.
        new_stage: Target stage name (e.g., "qualified", "proposal", "closed_won"). Required.
        tenant_id: Tenant UUID (resolved from session if omitted).
        reason: Why the stage is changing.
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with entity_id, previous_stage, new_stage, updated_at.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"error": "tenant_id is required."}
    if not entity_id or not new_stage:
        return {"error": "entity_id and new_stage are required."}

    entity = await _get_entity(entity_id, tid, include_relations=False)
    if not entity:
        return {"error": f"Entity {entity_id} not found"}

    props = entity.get("properties", {})
    previous_stage = props.get("pipeline_stage", "none")

    stage_history = props.get("stage_history", [])
    stage_history.append({
        "from": previous_stage,
        "to": new_stage,
        "reason": reason,
        "at": datetime.utcnow().isoformat(),
    })
    updated_props = {**props, "pipeline_stage": new_stage, "stage_history": stage_history}

    await _update_entity(
        entity_id, tid,
        {"properties": updated_props},
        reason=f"Pipeline stage: {previous_stage} → {new_stage}. {reason}",
    )

    return {
        "status": "success",
        "entity_id": entity_id,
        "entity_name": entity.get("name"),
        "previous_stage": previous_stage,
        "new_stage": new_stage,
        "updated_at": datetime.utcnow().isoformat(),
    }


@mcp.tool()
async def get_pipeline_summary(
    tenant_id: str = "",
    category: str = "lead",
    ctx: Context = None,
) -> dict:
    """Get aggregate pipeline metrics across all leads for a tenant.

    Queries knowledge entities to count leads at each pipeline stage
    and calculate basic conversion metrics.

    Args:
        tenant_id: Tenant UUID (resolved from session if omitted).
        category: Entity category to summarize (default: "lead").
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with stages breakdown, total_leads, and stage counts.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"error": "tenant_id is required."}

    entities = await _search_entities(tid, query=category, limit=500)

    stage_counts: dict = {}
    total = 0
    for entity in entities:
        if entity.get("category") != category:
            continue
        total += 1
        stage = entity.get("properties", {}).get("pipeline_stage", "unassigned")
        stage_counts[stage] = stage_counts.get(stage, 0) + 1

    stages = [
        {"stage": stage, "count": count}
        for stage, count in sorted(stage_counts.items(), key=lambda x: -x[1])
    ]

    return {
        "status": "success",
        "total_leads": total,
        "stages": stages,
        "category": category,
    }


@mcp.tool()
async def generate_proposal(
    entity_id: str,
    tenant_id: str = "",
    product_ids: str = "",
    ctx: Context = None,
) -> dict:
    """Generate a proposal outline for a lead based on their profile and products.

    Fetches the lead entity and relevant product/service entities from the
    knowledge graph, then structures a proposal outline for the LLM to expand.

    Args:
        entity_id: UUID of the lead entity. Required.
        tenant_id: Tenant UUID (resolved from session if omitted).
        product_ids: Comma-separated product entity UUIDs. If omitted,
            searches the knowledge graph for product entities.
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with title, sections, products, and entity context.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"error": "tenant_id is required."}
    if not entity_id:
        return {"error": "entity_id is required."}

    entity = await _get_entity(entity_id, tid, include_relations=True)
    if not entity:
        return {"error": f"Entity {entity_id} not found"}

    products = []
    if product_ids:
        for pid in [p.strip() for p in product_ids.split(",") if p.strip()]:
            prod = await _get_entity(pid, tid, include_relations=False)
            if prod:
                products.append(prod)
    else:
        all_entities = await _search_entities(tid, query="product service offering", limit=10)
        products = [p for p in all_entities if p.get("category") in ("product", "service", None)]

    return {
        "status": "success",
        "title": f"Proposal for {entity.get('name')}",
        "lead": {
            "name": entity.get("name"),
            "type": entity.get("entity_type"),
            "description": entity.get("description"),
            "properties": entity.get("properties", {}),
        },
        "products": [
            {
                "name": p.get("name"),
                "description": p.get("description"),
                "properties": p.get("properties", {}),
            }
            for p in products[:5]
        ],
        "sections": [
            "Executive Summary",
            "Understanding Your Needs",
            "Proposed Solution",
            "Pricing & Timeline",
            "Next Steps",
        ],
        "note": "Structured outline — expand each section into full prose based on lead and product context.",
    }


@mcp.tool()
async def schedule_followup(
    entity_id: str,
    action: str,
    tenant_id: str = "",
    delay_hours: int = 24,
    message: str = "",
    ctx: Context = None,
) -> dict:
    """Schedule a follow-up action for a lead via Temporal workflow.

    Creates a delayed task that will execute after the specified number
    of hours. Actions include sending messages, updating pipeline stage,
    or creating reminders.

    Args:
        entity_id: UUID of the entity to follow up with. Required.
        action: Action type - "send_whatsapp", "update_stage", or "remind". Required.
        tenant_id: Tenant UUID (resolved from session if omitted).
        delay_hours: Hours to wait before executing (default: 24).
        message: Message content for send actions, or stage name for update_stage.
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with workflow_id, scheduled_for, action, entity_id.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    if not tid:
        return {"error": "tenant_id is required."}
    if not entity_id or not action:
        return {"error": "entity_id and action are required."}

    api_base_url = _get_api_base_url()
    internal_key = _get_internal_key()

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{api_base_url}/api/v1/workflows/followup",
                headers={"X-Internal-Key": internal_key},
                json={
                    "entity_id": entity_id,
                    "tenant_id": tid,
                    "action": action,
                    "delay_hours": delay_hours,
                    "message": message,
                },
            )
            resp.raise_for_status()
            return {"status": "success", **resp.json()}
    except Exception as e:
        logger.error("schedule_followup failed: %s", e)
        return {
            "status": "scheduled_locally",
            "entity_id": entity_id,
            "action": action,
            "delay_hours": delay_hours,
            "note": f"Temporal scheduling failed ({e}), follow up manually.",
        }
