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
from app.models.agent import Agent as AgentModel
from app.services.cli_session_manager import run_agent_session
from app.services import rl_experience_service
from app.services.memory_recall import build_memory_context_with_git
from app.services import safety_trust
from app.services import luna_presence_service
from app.services.embedding_service import match_intent
from app.services.local_inference import generate_agent_response_sync
from app.services.tool_groups import TIER_LIMITS

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
    """Infer task type from message keywords. Gemma 4 classification runs async post-routing."""
    # Keyword matching only — never block the hot path with Ollama calls
    msg_lower = message.lower()
    for task_type, keywords in _TASK_TYPE_KEYWORDS.items():
        if any(kw in msg_lower for kw in keywords):
            return task_type
    return "general"


# Short-message local path threshold (chars ≈ 20 tokens)
_LOCAL_PATH_MAX_CHARS = 100


def _should_use_local_path(intent: dict | None, message: str, pin_to_cli: bool) -> bool:
    """Return True when a message should be handled by local Ollama (no CLI spin-up).

    Conditions:
    - No intent match (intent is None) — semantic routing found nothing above threshold
    - Short message (≤ _LOCAL_PATH_MAX_CHARS chars) — heuristic for conversational/simple
    - Session is NOT pinned to an active CLI session (context continuity takes priority)

    The message-length heuristic is language-agnostic: a short message in any language
    gets the fast path. RL learns the optimal threshold from quality scores over time.
    """
    if pin_to_cli:
        return False
    if intent is not None:
        return False
    return len(message) <= _LOCAL_PATH_MAX_CHARS


def _format_memory_for_local(memory_context: dict | None) -> str:
    """Format memory context dict as a brief string for local inference context injection.

    Extracts up to 3 relevant entities from the pre-built memory context and returns
    a compact text block suitable for the conversation_summary parameter of
    generate_agent_response_sync().
    """
    if not memory_context:
        return ""
    entities = memory_context.get("relevant_entities") or []
    if not entities:
        return ""
    lines = ["Relevant context:"]
    for ent in entities[:3]:
        name = ent.get("name", "") if isinstance(ent, dict) else getattr(ent, "name", "")
        etype = ent.get("entity_type", "") if isinstance(ent, dict) else getattr(ent, "entity_type", "")
        desc = ent.get("description", "") if isinstance(ent, dict) else getattr(ent, "description", "")
        if name:
            line = f"- {name} ({etype})"
            if desc:
                line += f": {desc[:80]}"
            lines.append(line)
    return "\n".join(lines) if len(lines) > 1 else ""


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

    # ── Intent-based tier selection (embedding match) ──
    try:
        intent = match_intent(message)
    except Exception as e:
        logger.debug("match_intent failed: %s — defaulting to full tier", e)
        intent = None

    if intent:
        agent_tier = intent["tier"]
        intent_tool_groups = intent["tools"]
        is_mutation = intent["mutation"]
        # Mutations always go to full tier for safety
        if is_mutation:
            agent_tier = "full"
    else:
        # No match or embedding unavailable — safe default
        agent_tier = "full"
        intent_tool_groups = None
        is_mutation = False

    # ── Select responding agent by tool_groups overlap ──
    responding_agent = None
    agent_tool_groups = None
    agent_memory_domains = None

    if intent_tool_groups:
        try:
            tenant_agents = db.query(AgentModel).filter(
                AgentModel.tenant_id == tenant_id,
                AgentModel.tool_groups.isnot(None),
            ).all()

            best_overlap = 0
            for agent_candidate in tenant_agents:
                if agent_candidate.tool_groups:
                    overlap = len(set(intent_tool_groups) & set(agent_candidate.tool_groups))
                    if overlap > best_overlap:
                        best_overlap = overlap
                        responding_agent = agent_candidate

            if responding_agent and best_overlap > 0:
                agent_slug = responding_agent.name.lower().replace(" ", "-")
                agent_tier = responding_agent.default_model_tier or agent_tier
                agent_tool_groups = responding_agent.tool_groups
                agent_memory_domains = responding_agent.memory_domains
        except Exception as e:
            logger.warning("Agent selection by tool_groups failed: %s", e)

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

    # ── Light tier uses Haiku via Claude CLI (fast + tools), not OpenCode ──
    # OpenCode/Gemma4 is fallback-only (when Claude+Codex are out of credits).
    # The --model haiku flag on Claude CLI gives us speed without losing tool quality.

    # Build memory context with agent-scoped parameters.
    # The tier limits and memory_domains from agent selection (above) drive
    # how much context we load — a Light-tier booking agent gets 3 entities
    # from its domains instead of 10 from everywhere.
    pre_built_memory_context = None
    session_entity_names = (db_session_memory or {}).get("recalled_entity_names")
    limits = TIER_LIMITS.get(agent_tier, TIER_LIMITS["full"])
    if not recalled_entities:
        try:
            pre_built_memory_context = build_memory_context_with_git(
                db, tenant_id, message,
                session_entity_names=session_entity_names,
                domains=agent_memory_domains,
                max_entities=limits["entities"],
                max_observations=limits["observations_per_entity"],
                include_relations=limits["include_relations"],
                include_episodes=limits["include_episodes"],
            )
            if pre_built_memory_context and pre_built_memory_context.get("relevant_entities"):
                recalled_entities = pre_built_memory_context["relevant_entities"]
        except Exception:
            logger.debug("Early memory recall failed — routing without entity context")
    elif recalled_entities and not pre_built_memory_context:
        try:
            pre_built_memory_context = build_memory_context_with_git(
                db=db, tenant_id=tenant_id, message=message,
                session_entity_names=session_entity_names,
                domains=agent_memory_domains,
                max_entities=limits["entities"],
                max_observations=limits["observations_per_entity"],
                include_relations=limits["include_relations"],
                include_episodes=limits["include_episodes"],
            )
        except Exception:
            logger.debug("Memory context build for external recalled_entities failed — continuing")

    # ── Short-message local path ──
    # When semantic intent matching fails for short messages (e.g. non-English
    # greetings like "hola", "bonjour"), avoid spinning up a full CLI session.
    # Use local Ollama inference directly — 2-5s instead of 65-75s.
    # RL learns the quality of this tier decision via tier_selection experiences.
    if _should_use_local_path(intent, message, _pin_to_claude):
        _memory_summary = _format_memory_for_local(pre_built_memory_context)
        _local_response = generate_agent_response_sync(
            message=message,
            conversation_summary=(_memory_summary + "\n\n" + conversation_summary).strip(),
            agent_slug=agent_slug,
        )
        if _local_response:
            # Log tier_selection RL experience so the policy engine can learn
            _tier_trajectory_id = None
            try:
                _tier_trajectory_id = uuid.uuid4()
                rl_experience_service.log_experience(
                    db,
                    tenant_id=tenant_id,
                    trajectory_id=_tier_trajectory_id,
                    step_index=0,
                    decision_point="tier_selection",
                    state={
                        "user_message": message[:200],
                        "channel": channel,
                        "message_len": len(message),
                        "intent_matched": False,
                        "task_type": inferred_type,
                    },
                    action={
                        "tier": "local",
                        "platform": "local_inference",
                        "reason": "short_no_intent",
                    },
                    state_text=f"task_type: {inferred_type}, channel: {channel}, "
                               f"message_len: {len(message)}, intent_matched: false",
                )
            except Exception:
                logger.debug("Failed to log tier_selection RL experience — continuing")
                _tier_trajectory_id = None  # ensure no orphaned reference

            _local_meta = {
                "platform": "local_inference",
                "agent_tier": "local",
                "tool_groups": [],
                "routing_trajectory_id": str(_tier_trajectory_id) if _tier_trajectory_id else None,
            }
            if trust_profile:
                _local_meta["agent_trust_score"] = round(float(trust_profile.trust_score), 3)
                _local_meta["agent_autonomy_tier"] = trust_profile.autonomy_tier

            logger.info(
                "Local path: tenant=%s message_len=%d response_len=%d",
                str(tenant_id)[:8], len(message), len(_local_response),
            )
            return _local_response, _local_meta
        # If local inference fails (Ollama down), fall through to full CLI
        logger.warning("Local inference failed for short message — falling through to CLI")

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
                "model_tier": agent_tier,
                "tool_groups": intent_tool_groups or [],
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
    if platform in ("claude_code", "gemini_cli", "codex", "opencode"):
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
                agent_tier=agent_tier,
                agent_tool_groups=agent_tool_groups,
                agent_memory_domains=agent_memory_domains,
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

        # Expose tier routing info for downstream logging (chat service, scorer)
        metadata["agent_tier"] = agent_tier
        metadata["tool_groups"] = intent_tool_groups or []

        return response_text, metadata

    # Future: gemini_cli and additional providers.
    return None, {"error": f"Platform '{platform}' not yet supported"}
