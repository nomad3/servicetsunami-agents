"""Agent Router — routes messages to CLI platforms.

Phase 1: Deterministic routing (tenant default + agent affinity).
Phase 3: RL-driven routing added on top.
"""
import logging
import uuid
from typing import Optional, Tuple, Dict, Any, List

from sqlalchemy.orm import Session
from sqlalchemy import text

from app.models.tenant_features import TenantFeatures
from app.services.cli_session_manager import run_agent_session
from app.services import rl_experience_service
from app.services.memory_recall import build_memory_context_with_git

logger = logging.getLogger(__name__)

# Default agent for each channel
CHANNEL_AGENT_MAP = {
    "whatsapp": "luna",
    "web": "luna",
}

# Simple keyword-based task type inference
_TASK_TYPE_KEYWORDS = {
    "code": ["code", "implement", "fix", "bug", "pr", "commit", "deploy", "refactor"],
    "data": ["query", "sql", "dataset", "analytics", "report", "chart", "dashboard"],
    "sales": ["deal", "pipeline", "lead", "prospect", "outreach", "crm"],
    "marketing": ["campaign", "ad", "competitor", "seo", "social", "content"],
    "knowledge": ["entity", "knowledge", "graph", "relation", "memory"],
    "general": [],
}


def _infer_task_type(message: str) -> str:
    """Infer task type from message keywords."""
    msg_lower = message.lower()
    for task_type, keywords in _TASK_TYPE_KEYWORDS.items():
        if any(kw in msg_lower for kw in keywords):
            return task_type
    return "general"


def get_platform_performance(db: Session, tenant_id: uuid.UUID) -> List[Dict[str, Any]]:
    """Query rl_experiences for agent_routing, grouped by platform action, compute positive_pct."""
    sql = text("""
        SELECT
            action->>'platform' AS platform,
            COUNT(*) AS total,
            AVG(reward) AS avg_reward,
            COUNT(*) FILTER (WHERE reward > 0) AS positive_count
        FROM rl_experiences
        WHERE tenant_id = CAST(:tid AS uuid)
          AND decision_point = 'agent_routing'
          AND reward IS NOT NULL
          AND archived_at IS NULL
        GROUP BY action->>'platform'
        HAVING COUNT(*) >= 3
        ORDER BY AVG(reward) DESC
    """)
    rows = db.execute(sql, {"tid": str(tenant_id)}).fetchall()
    return [
        {
            "platform": r.platform or "unknown",
            "total": r.total,
            "avg_reward": round(float(r.avg_reward or 0), 3),
            "positive_pct": round(r.positive_count * 100.0 / r.total, 1) if r.total > 0 else 0.0,
        }
        for r in rows
    ]


def route_and_execute(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    message: str,
    channel: str = "web",
    sender_phone: str = None,
    agent_slug: str = None,
    conversation_summary: str = "",
    image_b64: str = "",
    image_mime: str = "",
    db_session_memory: dict = None,
    recalled_entities: list = None,
) -> Tuple[Optional[str], Dict[str, Any]]:
    """Route message to the appropriate CLI platform and execute.

    Args:
        db: SQLAlchemy database session.
        tenant_id: UUID of the tenant.
        user_id: UUID of the authenticated user.
        message: The user's message to process.
        channel: Communication channel (default "web").
        sender_phone: Sender's phone number (relevant for WhatsApp channel).
        agent_slug: Explicit agent slug.
        conversation_summary: Brief summary of prior conversation context.
        image_b64: Base64-encoded image data.
        image_mime: MIME type of the image.
        db_session_memory: Session memory dict.
        recalled_entities: Optional list of recalled KG entities for context enrichment.

    Returns:
        Tuple of (response_text, metadata).
    """
    # Apply channel-based agent default if not explicitly specified
    if not agent_slug:
        agent_slug = CHANNEL_AGENT_MAP.get(channel, "luna")

    # Load tenant features to determine the CLI platform preference
    features = db.query(TenantFeatures).filter(
        TenantFeatures.tenant_id == tenant_id
    ).first()

    # Default platform is claude_code; allow per-tenant override via features
    platform = "claude_code"
    if features and hasattr(features, 'default_cli_platform') and features.default_cli_platform:
        platform = features.default_cli_platform

    # Infer task type for RL context
    inferred_type = _infer_task_type(message)

    # Build memory context early so recalled entities enrich routing RL state
    pre_built_memory_context = None
    if not recalled_entities:
        try:
            pre_built_memory_context = build_memory_context_with_git(db, tenant_id, message)
            if pre_built_memory_context and pre_built_memory_context.get("relevant_entities"):
                recalled_entities = pre_built_memory_context["relevant_entities"]
        except Exception:
            logger.debug("Early memory recall failed — routing without entity context")

    # Build enriched state_text for RL logging
    state_parts = [f"task_type: {inferred_type}, channel: {channel}"]

    if recalled_entities:
        # Cap to 5 entities
        capped = recalled_entities[:5]
        entity_strs = []
        categories = set()
        for ent in capped:
            name = ent.get("name", "unknown") if isinstance(ent, dict) else getattr(ent, "name", "unknown")
            etype = ent.get("entity_type", "") if isinstance(ent, dict) else getattr(ent, "entity_type", "")
            cat = ent.get("category", "") if isinstance(ent, dict) else getattr(ent, "category", "")
            entity_strs.append(f"{name}({etype}/{cat})")
            if cat:
                categories.add(cat)
        state_parts.append(f"known_entities: [{', '.join(entity_strs)}]")
        state_parts.append(f"entity_categories: [{', '.join(sorted(categories))}]")

    # Add platform performance history
    try:
        perf = get_platform_performance(db, tenant_id)
        if perf:
            perf_strs = [f"{p['platform']}:{p['positive_pct']}%" for p in perf]
            state_parts.append(f"platform_history: [{', '.join(perf_strs)}]")
    except Exception:
        logger.debug("Failed to fetch platform performance — skipping")

    state_text = ", ".join(state_parts)

    # Log RL experience for agent_routing decision
    trajectory_id = uuid.uuid4()
    try:
        rl_experience_service.log_experience(
            db,
            tenant_id=tenant_id,
            trajectory_id=trajectory_id,
            step_index=0,
            decision_point="agent_routing",
            state={
                "user_message": message[:200],
                "channel": channel,
                "agent_slug": agent_slug,
                "task_type": inferred_type,
                "entity_count": len(recalled_entities) if recalled_entities else 0,
            },
            action={
                "platform": platform,
                "agent_slug": agent_slug,
            },
            state_text=state_text,
        )
    except Exception:
        logger.debug("Failed to log agent_routing RL experience — continuing")

    logger.info(
        "Routing: tenant=%s agent=%s platform=%s channel=%s task_type=%s entities=%d",
        str(tenant_id)[:8], agent_slug, platform, channel, inferred_type,
        len(recalled_entities) if recalled_entities else 0,
    )

    # Execute on the selected platform
    if platform in {"claude_code", "codex", "gemini_cli"}:
        return run_agent_session(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            platform=platform,
            agent_slug=agent_slug,
            message=message,
            channel=channel,
            sender_phone=sender_phone,
            conversation_summary=conversation_summary,
            image_b64=image_b64,
            image_mime=image_mime,
            db_session_memory=db_session_memory,
            pre_built_memory_context=pre_built_memory_context,
        )

    return None, {"error": f"Platform '{platform}' not yet supported"}
