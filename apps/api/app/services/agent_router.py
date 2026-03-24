"""Agent Router — routes messages to CLI platforms.

Phase 1: Deterministic routing (tenant default + agent affinity).
Phase 3: RL-driven routing added on top.
"""
import logging
import os
import uuid
from typing import Optional, Tuple, Dict, Any, List

from sqlalchemy.orm import Session
from sqlalchemy import text

from app.models.tenant_features import TenantFeatures
from app.services.cli_session_manager import run_agent_session
from app.services import rl_experience_service
from app.services.memory_recall import build_memory_context_with_git
from app.services import safety_trust

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
    """Infer task type from message keywords. Qwen classification runs async post-routing."""
    # Keyword matching only — never block the hot path with Ollama calls
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

    trust_profile = safety_trust.get_agent_trust_profile(
        db,
        tenant_id,
        agent_slug,
        auto_create=True,
    )

    # Infer task type for RL context
    inferred_type = _infer_task_type(message)

    # ── RL exploration: route to underexplored platforms for training data ──
    # EXPLORATION_MODE controls the training strategy:
    #   "off"      — no exploration, use default/RL (production)
    #   "codex"    — route EXPLORATION_RATE % to codex for data collection
    #   "balanced" — route to whichever platform has fewest experiences
    import random
    exploration_mode = os.environ.get("EXPLORATION_MODE", "off")
    exploration_rate = float(os.environ.get("EXPLORATION_RATE", "0.7"))
    routing_source = "default"

    if exploration_mode != "off" and random.random() < exploration_rate:
        if exploration_mode == "codex":
            logger.info("RL exploration: routing to codex (training mode, rate=%.0f%%)", exploration_rate * 100)
            platform = "codex"
            routing_source = "exploration_codex"
        elif exploration_mode == "balanced":
            # Pick the platform with fewest scored experiences
            try:
                from app.services.rl_routing import get_best_platform
                rec = get_best_platform(db, tenant_id, inferred_type)
                if rec.alternatives:
                    # Find platform with lowest total
                    least = min(rec.alternatives, key=lambda a: a["total"])
                    platform = least["platform"]
                    routing_source = "exploration_balanced"
                    logger.info("RL exploration: routing to %s (least data: %d experiences)", platform, least["total"])
            except Exception:
                pass
    else:
        # ── RL-learned routing: override platform if strong signal ──
        try:
            from app.services.rl_routing import get_routing_recommendation
            rl_rec = get_routing_recommendation(
                db, tenant_id, message,
                task_type=inferred_type,
                current_platform=platform,
                current_agent=agent_slug,
            )
            if rl_rec.platform and rl_rec.platform_confidence >= 0.4:
                if rl_rec.platform != platform:
                    logger.info(
                        "RL routing override: platform %s→%s (confidence=%.2f, %s)",
                        platform, rl_rec.platform, rl_rec.platform_confidence, rl_rec.platform_reasoning,
                    )
                    platform = rl_rec.platform
                else:
                    logger.info(
                        "RL routing confirmed: %s (confidence=%.2f, %s)",
                        platform, rl_rec.platform_confidence, rl_rec.platform_reasoning,
                    )
                routing_source = "rl_platform"
        except Exception as e:
            logger.debug("RL routing lookup failed: %s — using defaults", e)

    # ── Policy rollout: check if a live A/B experiment overrides routing ──
    rollout_experiment_id = None
    try:
        from app.services import policy_rollout_service
        rollout = policy_rollout_service.get_active_rollout(db, tenant_id, "chat_response")
        if rollout:
            if policy_rollout_service.should_apply_rollout(rollout):
                proposed = rollout.get("proposed_policy", {})
                if "platform" in proposed:
                    logger.info(
                        "Policy rollout: applying treatment platform=%s (experiment=%s, pct=%.0f%%)",
                        proposed["platform"], rollout["experiment_id"], rollout["rollout_pct"] * 100,
                    )
                    platform = proposed["platform"]
                    routing_source = "rollout_treatment"
                    rollout_experiment_id = rollout["experiment_id"]
            else:
                routing_source = "rollout_control"
                rollout_experiment_id = rollout["experiment_id"]
    except Exception as e:
        logger.debug("Policy rollout check failed: %s", e)

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
                "routing_source": routing_source,
                "agent_trust_score": round(float(trust_profile.trust_score), 3) if trust_profile else None,
                "agent_autonomy_tier": trust_profile.autonomy_tier if trust_profile else None,
            },
            state_text=state_text,
        )
    except Exception:
        logger.debug("Failed to log agent_routing RL experience — continuing")

    logger.info(
        "Routing: tenant=%s agent=%s platform=%s channel=%s task_type=%s entities=%d trust=%s tier=%s",
        str(tenant_id)[:8], agent_slug, platform, channel, inferred_type,
        len(recalled_entities) if recalled_entities else 0,
        round(float(trust_profile.trust_score), 3) if trust_profile else "n/a",
        trust_profile.autonomy_tier if trust_profile else "n/a",
    )

    # Execute on the selected platform
    if platform in ("claude_code", "gemini_cli", "codex"):
        response_text, metadata = run_agent_session(
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
        metadata = metadata or {}
        if trust_profile:
            metadata.setdefault("agent_trust_score", round(float(trust_profile.trust_score), 3))
            metadata.setdefault("agent_autonomy_tier", trust_profile.autonomy_tier)
            metadata.setdefault("agent_trust_confidence", round(float(trust_profile.confidence), 3))

        # Record rollout observation if a live experiment is active
        if rollout_experiment_id:
            try:
                from app.services import policy_rollout_service
                is_treatment = routing_source == "rollout_treatment"
                policy_rollout_service.record_rollout_observation(
                    db, tenant_id,
                    experiment_id=uuid.UUID(rollout_experiment_id),
                    is_treatment=is_treatment,
                    reward=None,  # Reward assigned later by auto-quality-scorer
                )
                metadata["rollout_experiment_id"] = rollout_experiment_id
                metadata["rollout_arm"] = "treatment" if is_treatment else "control"
            except Exception as e:
                logger.debug("Rollout observation failed: %s", e)

        return response_text, metadata

    # Future: gemini_cli and additional providers.
    return None, {"error": f"Platform '{platform}' not yet supported"}
