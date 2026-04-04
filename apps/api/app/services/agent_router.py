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
from app.services import luna_presence_service

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

    # Pin to Claude Code when the session has an active --resume session ID.
    # Switching to Codex (one-shot) mid-conversation breaks context continuity.
    _mem = db_session_memory or {}
    _has_claude_session = _mem.get("claude_code_cli_session_id") or _mem.get("claude_cli_session_id")
    if _has_claude_session:
        platform = "claude_code"
        _pin_to_claude = True
    else:
        _pin_to_claude = False

    trust_profile = safety_trust.get_agent_trust_profile(
        db,
        tenant_id,
        agent_slug,
        auto_create=True,
    )

    # Infer task type for RL context
    inferred_type = _infer_task_type(message)

    # ── RL exploration: route to underexplored platforms for training data ──
    # Per-decision-point config overrides global env vars when available
    import random
    exploration_mode = os.environ.get("EXPLORATION_MODE", "off")
    exploration_rate = float(os.environ.get("EXPLORATION_RATE", "0.1"))
    routing_source = "default"

    # Check per-decision-point exploration config (set by autonomous learning)
    try:
        dp_config = db.execute(
            text("""
                SELECT exploration_rate, exploration_mode, target_platforms
                FROM decision_point_config
                WHERE tenant_id = CAST(:tid AS uuid) AND decision_point = 'chat_response'
                ORDER BY updated_at DESC LIMIT 1
            """),
            {"tid": str(tenant_id)},
        ).first()
        if dp_config:
            exploration_mode = dp_config.exploration_mode or exploration_mode
            exploration_rate = float(dp_config.exploration_rate or exploration_rate)
    except Exception:
        pass  # Table may not exist yet

    if exploration_mode != "off" and random.random() < exploration_rate and not _pin_to_claude:
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
    elif not _pin_to_claude:
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
            apply_policy, is_treatment = policy_rollout_service.should_apply_rollout(rollout)
            rollout_experiment_id = rollout["experiment_id"]
            if is_treatment and apply_policy and not _pin_to_claude:
                routing_source = "rollout_treatment"
                proposed = rollout.get("proposed_policy", {})
                if "platform" in proposed:
                    platform = proposed["platform"]
                if "agent_slug" in proposed:
                    agent_slug = proposed["agent_slug"]
                logger.info(
                    "Policy rollout: applying treatment (experiment=%s, pct=%.0f%%)",
                    rollout["experiment_id"], rollout["rollout_pct"] * 100,
                )
            else:
                routing_source = "rollout_control"
    except Exception as e:
        logger.debug("Policy rollout check failed: %s", e)

    # Build memory context early so recalled entities enrich routing RL state
    pre_built_memory_context = None
    session_entity_names = (db_session_memory or {}).get("recalled_entity_names")
    if not recalled_entities:
        try:
            pre_built_memory_context = build_memory_context_with_git(
                db, tenant_id, message,
                session_entity_names=session_entity_names,
            )
            if pre_built_memory_context and pre_built_memory_context.get("relevant_entities"):
                recalled_entities = pre_built_memory_context["relevant_entities"]
        except Exception:
            logger.debug("Early memory recall failed — routing without entity context")
    elif recalled_entities and not pre_built_memory_context:
        # Entities were passed in externally but memory context was not pre-built.
        # Build it now so cli_session_manager does not rebuild (double recall).
        try:
            pre_built_memory_context = build_memory_context_with_git(
                db=db, tenant_id=tenant_id, message=message,
                session_entity_names=session_entity_names,
            )
        except Exception:
            logger.debug("Memory context build for external recalled_entities failed — continuing")

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

    # Playful mood for short casual messages
    if inferred_type == "general" and len(message) < 50:
        try:
            luna_presence_service.update_state(tenant_id, mood="playful")
        except Exception:
            pass

    logger.info(
        "Routing: tenant=%s agent=%s platform=%s channel=%s task_type=%s entities=%d trust=%s tier=%s",
        str(tenant_id)[:8], agent_slug, platform, channel, inferred_type,
        len(recalled_entities) if recalled_entities else 0,
        round(float(trust_profile.trust_score), 3) if trust_profile else "n/a",
        trust_profile.autonomy_tier if trust_profile else "n/a",
    )

    # Execute on the selected platform
    if platform in ("claude_code", "gemini_cli", "codex"):
        # Presence session scoping: use chat session ID so concurrent
        # requests don't clobber each other's state.
        _presence_sid = str((db_session_memory or {}).get("chat_session_id", ""))
        try:
            _presence_state = "focused" if inferred_type == "code" else "thinking"
            luna_presence_service.update_state(
                tenant_id, state=_presence_state, tool_status="running",
                session_id=_presence_sid,
            )
        except Exception:
            pass
        try:
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
        except Exception:
            # CLI failure — set error state briefly, then idle
            try:
                luna_presence_service.update_state(
                    tenant_id, state="error", tool_status="error",
                    session_id=_presence_sid,
                )
            except Exception:
                pass
            raise
        # Update presence: responding or idle
        try:
            if response_text:
                luna_presence_service.update_state(
                    tenant_id, state="responding", tool_status="idle",
                    session_id=_presence_sid,
                )
            else:
                luna_presence_service.update_state(
                    tenant_id, state="idle", tool_status="idle",
                    mood="empathetic", session_id=_presence_sid,
                )
        except Exception:
            pass

        metadata = metadata or {}
        if trust_profile:
            metadata.setdefault("agent_trust_score", round(float(trust_profile.trust_score), 3))
            metadata.setdefault("agent_autonomy_tier", trust_profile.autonomy_tier)
            metadata.setdefault("agent_trust_confidence", round(float(trust_profile.confidence), 3))

        # Tag rollout metadata so the async scorer can record the observation
        # with the scored reward (single recording point, no double-counting)
        if rollout_experiment_id:
            metadata["rollout_experiment_id"] = rollout_experiment_id
            metadata["rollout_arm"] = "treatment" if routing_source == "rollout_treatment" else "control"

        # Thread routing trajectory so scorer can backfill the reward
        metadata["routing_trajectory_id"] = str(trajectory_id)

        return response_text, metadata

    # Future: gemini_cli and additional providers.
    return None, {"error": f"Platform '{platform}' not yet supported"}
