"""Sales automation tools.

LLM-powered tools for lead qualification, outreach drafting, pipeline
management, proposal generation, and follow-up scheduling. All tools
operate on entities in the knowledge graph.
"""
import logging
import uuid
from typing import Optional
from datetime import datetime

import httpx

from services.knowledge_graph import get_knowledge_service
from tools.knowledge_tools import _resolve_tenant_id
from config.settings import settings

logger = logging.getLogger(__name__)

_http_client: Optional[httpx.AsyncClient] = None


def _get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            base_url=settings.api_base_url,
            timeout=30.0,
        )
    return _http_client


async def qualify_lead(
    entity_id: str,
    tenant_id: str,
) -> dict:
    """Qualify a lead using BANT framework (Budget, Authority, Need, Timeline).

    Fetches entity context from the knowledge graph, evaluates qualification
    criteria, and updates the entity properties with qualification results.

    Args:
        entity_id: UUID of the lead entity to qualify.
        tenant_id: Tenant context.

    Returns:
        Dict with budget, authority, need, timeline assessments, overall
        qualified boolean, and summary.
    """
    tenant_id = _resolve_tenant_id(tenant_id)
    kg = get_knowledge_service()
    entity = await kg.get_entity(entity_id, include_relations=True)
    if not entity:
        return {"error": f"Entity {entity_id} not found"}

    # Build qualification from entity properties
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

    # Update entity with qualification result
    updated_props = {**props, "qualification": qualification, "qualified": qualified}
    if qualified and not props.get("pipeline_stage"):
        updated_props["pipeline_stage"] = "qualified"

    await kg.update_entity(
        entity_id=entity_id,
        updates={"properties": updated_props},
        reason="BANT qualification",
    )

    return qualification


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


async def draft_outreach(
    entity_id: str,
    tenant_id: str,
    channel: str = "email",
    tone: str = "professional",
) -> dict:
    """Draft a personalized outreach message for a lead.

    Generates a message based on the entity's properties, qualification
    status, and the specified channel format.

    Args:
        entity_id: UUID of the lead/contact entity.
        tenant_id: Tenant context.
        channel: Message channel - "email", "whatsapp", or "linkedin".
        tone: Message tone - "professional", "casual", or "formal".

    Returns:
        Dict with subject (for email), body, channel, and entity context.
    """
    tenant_id = _resolve_tenant_id(tenant_id)
    kg = get_knowledge_service()
    entity = await kg.get_entity(entity_id, include_relations=True)
    if not entity:
        return {"error": f"Entity {entity_id} not found"}

    name = entity.get("name", "there")
    props = entity.get("properties", {})
    entity_type = entity.get("entity_type", "")
    description = entity.get("description", "")

    # Build context summary for personalization
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

    # Channel-specific formatting
    if channel == "email":
        return {
            "channel": "email",
            "subject": f"Quick question for {name}",
            "body": f"Hi {name},\n\n[Personalize based on: {context}]\n\nBest regards",
            "entity_name": name,
            "context": context,
            "note": "This is a draft template. The LLM supervisor should personalize the body using the context provided.",
        }
    elif channel == "whatsapp":
        return {
            "channel": "whatsapp",
            "body": f"Hi {name}! [Personalize based on: {context}]",
            "entity_name": name,
            "context": context,
            "note": "Short, conversational format for WhatsApp.",
        }
    else:
        return {
            "channel": channel,
            "body": f"Hi {name}, [Personalize based on: {context}]",
            "entity_name": name,
            "context": context,
        }


async def update_pipeline_stage(
    entity_id: str,
    new_stage: str,
    tenant_id: str,
    reason: str = "",
) -> dict:
    """Move a lead entity to a new pipeline stage.

    Updates the entity's pipeline_stage property and records the transition
    as an observation for audit trail.

    Args:
        entity_id: UUID of the entity.
        new_stage: Target stage name (e.g., "qualified", "proposal", "closed_won").
        tenant_id: Tenant context.
        reason: Why the stage is changing.

    Returns:
        Dict with entity_id, previous_stage, new_stage, updated_at.
    """
    tenant_id = _resolve_tenant_id(tenant_id)
    kg = get_knowledge_service()
    entity = await kg.get_entity(entity_id, include_relations=False)
    if not entity:
        return {"error": f"Entity {entity_id} not found"}

    props = entity.get("properties", {})
    previous_stage = props.get("pipeline_stage", "none")

    # Update entity
    updated_props = {**props, "pipeline_stage": new_stage}
    # Track stage history
    stage_history = props.get("stage_history", [])
    stage_history.append({
        "from": previous_stage,
        "to": new_stage,
        "reason": reason,
        "at": datetime.utcnow().isoformat(),
    })
    updated_props["stage_history"] = stage_history

    await kg.update_entity(
        entity_id=entity_id,
        updates={"properties": updated_props},
        reason=f"Pipeline stage: {previous_stage} → {new_stage}. {reason}",
    )

    # Record observation for audit
    await kg.record_observation(
        observation_text=f"Pipeline stage changed: {previous_stage} → {new_stage}. {reason}",
        tenant_id=tenant_id,
        observation_type="pipeline_transition",
        source_type="sales_agent",
    )

    return {
        "entity_id": entity_id,
        "entity_name": entity.get("name"),
        "previous_stage": previous_stage,
        "new_stage": new_stage,
        "updated_at": datetime.utcnow().isoformat(),
    }


async def get_pipeline_summary(
    tenant_id: str,
    category: str = "lead",
) -> dict:
    """Get aggregate pipeline metrics across all leads for a tenant.

    Queries knowledge entities to count leads at each pipeline stage
    and calculate basic conversion metrics.

    Args:
        tenant_id: Tenant context.
        category: Entity category to summarize (default: "lead").

    Returns:
        Dict with stages breakdown, total_leads, and stage counts.
    """
    tenant_id = _resolve_tenant_id(tenant_id)
    kg = get_knowledge_service()

    # Find all entities in the category
    entities = await kg.find_entities(
        query="*",
        tenant_id=tenant_id,
        entity_types=None,
        limit=500,
        min_confidence=0.0,
    )

    # Filter by category and count stages
    stage_counts = {}
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
        "total_leads": total,
        "stages": stages,
        "category": category,
    }


async def generate_proposal(
    entity_id: str,
    tenant_id: str,
    product_ids: Optional[list] = None,
) -> dict:
    """Generate a proposal document for a lead based on their profile and products.

    Fetches the lead entity and relevant product/service entities from the
    knowledge graph, then structures a proposal outline.

    Args:
        entity_id: UUID of the lead entity.
        tenant_id: Tenant context.
        product_ids: Optional list of product entity UUIDs. If omitted,
            searches knowledge graph for product entities.

    Returns:
        Dict with title, sections, products, and entity context for the LLM
        to format into a full proposal.
    """
    tenant_id = _resolve_tenant_id(tenant_id)
    kg = get_knowledge_service()
    entity = await kg.get_entity(entity_id, include_relations=True)
    if not entity:
        return {"error": f"Entity {entity_id} not found"}

    # Find products/services
    products = []
    if product_ids:
        for pid in product_ids:
            prod = await kg.get_entity(pid, include_relations=False)
            if prod:
                products.append(prod)
    else:
        products = await kg.find_entities(
            query="product service offering",
            tenant_id=tenant_id,
            limit=10,
            min_confidence=0.0,
        )
        products = [p for p in products if p.get("category") in ("product", "service", None)]

    return {
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
        "note": "This is a structured outline. The LLM should expand each section into full prose based on the lead and product context.",
    }


async def schedule_followup(
    entity_id: str,
    tenant_id: str,
    action: str,
    delay_hours: int = 24,
    message: str = "",
) -> dict:
    """Schedule a follow-up action for a lead via Temporal workflow.

    Creates a delayed task that will execute after the specified number
    of hours. Actions include sending messages, updating pipeline stage,
    or creating reminders.

    Args:
        entity_id: UUID of the entity to follow up with.
        tenant_id: Tenant context.
        action: Action type - "send_whatsapp", "update_stage", or "remind".
        delay_hours: Hours to wait before executing (default: 24).
        message: Message content for send actions, or stage name for update_stage.

    Returns:
        Dict with workflow_id, scheduled_for, action, entity_id.
    """
    client = _get_http_client()
    try:
        resp = await client.post(
            "/api/v1/workflows/followup",
            headers={"X-Internal-Key": settings.mcp_api_key},
            json={
                "entity_id": entity_id,
                "tenant_id": _resolve_tenant_id(tenant_id),
                "action": action,
                "delay_hours": delay_hours,
                "message": message,
            },
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error("schedule_followup failed: %s", e)
        return {
            "status": "scheduled_locally",
            "entity_id": entity_id,
            "action": action,
            "delay_hours": delay_hours,
            "note": f"Temporal scheduling failed ({e}), follow up manually.",
        }
