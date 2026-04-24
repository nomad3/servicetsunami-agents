"""Agent Router — routes messages to CLI platforms.

Phase 1: Deterministic routing (tenant default + agent affinity).
Phase 3: RL-driven routing added on top.
"""
import logging
import os
import uuid
import random
from typing import Optional, Tuple, Dict, Any, List

from sqlalchemy.orm import Session
from sqlalchemy import text

from app.db.safe_ops import safe_rollback
from app.models.tenant_features import TenantFeatures
from app.models.tenant_branding import TenantBranding
from app.models.agent import Agent as AgentModel
from app.services.cli_session_manager import run_agent_session
from app.services import rl_experience_service
from app.services.memory_recall import build_memory_context_with_git
from app.services import safety_trust
from app.services import luna_presence_service
from app.services.embedding_service import match_intent
from app.services.local_inference import generate_agent_response_sync
from app.services.tool_groups import TIER_LIMITS
from app.memory.feature_flag import is_v2_enabled
from app.services.agent_identity import resolve_primary_agent_slug

logger = logging.getLogger(__name__)


def _build_memory_context(
    db, tenant_id, message, *,
    session_entity_names, domains, max_entities, max_observations,
    include_relations, include_episodes, agent_slug, chat_session_id=None,
    user_id=None,
):
    """V2 → memory.recall(); V1 → legacy build_memory_context_with_git."""
    try:
        if is_v2_enabled(tenant_id):
            from app.memory import recall
            from app.memory.types import RecallRequest
            req = RecallRequest(
                tenant_id=tenant_id,
                agent_slug=agent_slug or "luna",
                query=message,
                user_id=user_id,
                chat_session_id=uuid.UUID(chat_session_id) if chat_session_id else None,
                total_token_budget=8000,
            )
            resp = recall(db, req)
            return _recall_response_to_legacy_dict(resp)

        return build_memory_context_with_git(
            db, tenant_id, message,
            session_entity_names=session_entity_names,
            domains=domains,
            max_entities=max_entities,
            max_observations=max_observations,
            include_relations=include_relations,
            include_episodes=include_episodes,
        )
    except Exception as e:
        logger.warning("Memory context build failed: %s", e)
        safe_rollback(db)
        return None


def _recall_response_to_legacy_dict(resp) -> dict:
    """Convert typed RecallResponse to the dict shape the CLI prompt builder expects."""
    # Group observations by entity name
    obs_by_name = {}
    entity_map = {e.id: e.name for e in resp.entities}
    
    for o in resp.observations:
        name = entity_map.get(o.entity_id)
        if name:
            if name not in obs_by_name:
                obs_by_name[name] = []
            obs_by_name[name].append({
                "text": o.content,
                "sentiment": "neutral",
                "source_ref": getattr(o, "source_ref", "")
            })

    return {
        "recalled_entity_names": [e.name for e in resp.entities],
        "relevant_entities": [
            {
                "name": e.name,
                "type": e.category or "general",
                "description": e.description,
                "similarity": e.similarity
            } 
            for e in resp.entities
        ],
        "relevant_memories": [],  # memories are absorbed into entities in V2
        "relevant_relations": [
            {"from": r.from_entity, "to": r.to_entity, "type": r.relation_type} 
            for r in resp.relations
        ],
        "entity_observations": obs_by_name,
        "recent_episodes": [
            {
                "summary": ep.summary,
                "date": ep.created_at.strftime("%Y-%m-%d %H:%M") if ep.created_at else "",
                "mood": "neutral"
            } 
            for ep in resp.episodes
        ],
        "commitments": [
            {
                "title": c.title,
                "state": c.state,
                "due_at": c.due_at.strftime("%Y-%m-%d %H:%M") if c.due_at else "No deadline",
                "priority": c.priority
            }
            for c in resp.commitments
        ],
        "goals": [
            {
                "title": g.title,
                "state": g.state,
                "progress": g.progress_pct,
                "priority": g.priority
            }
            for g in resp.goals
        ],
        "past_conversations": [
            {
                "role": cv.role,
                "content": cv.content,
                "date": cv.created_at.strftime("%Y-%m-%d %H:%M") if cv.created_at else ""
            }
            for cv in resp.past_conversations
        ],
        "anticipatory_context": "",
        "contradictions": [],
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
    msg_lower = message.lower()
    for task_type, keywords in _TASK_TYPE_KEYWORDS.items():
        if any(kw in msg_lower for kw in keywords):
            return task_type
    return "general"


# Short-message local path threshold (chars ≈ 20 tokens)
_LOCAL_PATH_MAX_CHARS = 100


def _should_use_local_path(intent: dict | None, message: str, pin_to_cli: bool) -> bool:
    if pin_to_cli:
        return False
    if intent is not None:
        return False
    return len(message) <= _LOCAL_PATH_MAX_CHARS


def _format_memory_for_local(memory_context: dict | None) -> str:
    """Format memory context dict as a brief string for local inference context injection."""
    if not memory_context:
        return ""
    entities = memory_context.get("relevant_entities") or []
    if not entities:
        return ""
    
    lines = ["Relevant context:"]
    # Take top 3 entities for local context (matches test expectations)
    for ent in entities[:3]:
        name = ent.get("name", "")
        etype = ent.get("type", "")
        desc = ent.get("description", "")
        if name:
            line = f"- {name} ({etype})"
            if desc:
                # Include more description for local context
                line += f": {desc[:200]}"
            lines.append(line)
            
            # Add observations for this entity if present
            observations = memory_context.get("entity_observations", {}).get(name, [])
            for obs in observations[:2]:  # Top 2 observations per entity
                lines.append(f"  * {obs['text'][:200]}")
                
    return "\n".join(lines) if len(lines) > 1 else ""


def get_platform_performance(db: Session, tenant_id: uuid.UUID) -> List[Dict[str, Any]]:
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
    try:
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
    except Exception:
        safe_rollback(db)
        return []


def dispatch_coalition(
    tenant_id: uuid.UUID,
    chat_session_id: str,
    task_description: str,
) -> None:
    """Fire-and-forget CoalitionWorkflow dispatch."""
    import asyncio
    import threading
    from temporalio.client import Client
    from app.core.config import settings

    def _runner():
        try:
            async def _go():
                client = await Client.connect(settings.TEMPORAL_ADDRESS)
                await client.start_workflow(
                    "CoalitionWorkflow",
                    arg={
                        "tenant_id": str(tenant_id),
                        "chat_session_id": chat_session_id,
                        "task_description": task_description,
                    },
                    id=f"coalition-{chat_session_id}-{uuid.uuid4().hex[:8]}",
                    task_queue="agentprovision-orchestration",
                )
            asyncio.run(_go())
        except Exception as e:
            logger.warning("CoalitionWorkflow dispatch failed: %s", e)

    threading.Thread(target=_runner, daemon=True).start()


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
    # Apply channel-based agent default if not explicitly specified
    if not agent_slug:
        agent_slug = resolve_primary_agent_slug(db, tenant_id)

    # 1. Load tenant features
    try:
        features = db.query(TenantFeatures).filter(
            TenantFeatures.tenant_id == tenant_id
        ).first()
    except Exception:
        safe_rollback(db)
        features = None

    # Default platform is gemini_cli
    platform = "gemini_cli"
    if features and hasattr(features, 'default_cli_platform') and features.default_cli_platform:
        platform = features.default_cli_platform

    # When the tenant has a CLI subscription (gemini_cli, claude_code, codex),
    # always route through it — don't fall back to local Gemma 4 for short
    # messages. Local inference is for free-tier tenants with no subscription.
    _pin_to_claude = platform in {"gemini_cli", "claude_code", "codex"}

    # 2. Get trust profile
    try:
        trust_profile = safety_trust.get_agent_trust_profile(
            db,
            tenant_id,
            agent_slug,
            auto_create=True,
        )
    except Exception:
        safe_rollback(db)
        trust_profile = None

    inferred_type = _infer_task_type(message)

    # 0. Presence session scoping: use chat session ID so concurrent
    # requests don't clobber each other's state.
    _presence_sid = str((db_session_memory or {}).get("chat_session_id", ""))

    # 3. Intent matching
    # Coalition auto-trigger was removed 2026-04-24: it double-spawned the Gemini
    # CLI on every data/reports/github/shell intent match, doubling user-perceived
    # latency for a response that was never shown to the user. @coalition prefix
    # and POST /collaborations/dispatch remain the explicit entry points.
    try:
        intent = match_intent(message)
    except Exception as e:
        logger.debug("match_intent failed: %s — defaulting to full tier", e)
        intent = None

    if intent:
        agent_tier = intent["tier"]
        intent_tool_groups = intent["tools"]
        is_mutation = intent["mutation"]
        if is_mutation:
            agent_tier = "full"
    else:
        agent_tier = "full"
        intent_tool_groups = None
        is_mutation = False

    # 4. Agent selection
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
            safe_rollback(db)

    # 5. RL exploration & routing
    exploration_mode = os.environ.get("EXPLORATION_MODE", "off")
    exploration_rate = float(os.environ.get("EXPLORATION_RATE", "0.0"))
    routing_source = "default"

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
            if dp_config.exploration_rate is not None:
                exploration_rate = float(dp_config.exploration_rate)
    except Exception:
        safe_rollback(db)

    if exploration_mode != "off" and random.random() < exploration_rate:
        if exploration_mode == "codex":
            platform = "codex"
            routing_source = "exploration_codex"
        elif exploration_mode == "balanced":
            _VALID_EXPLORE = {"claude_code", "codex", "gemini_cli"}
            try:
                from app.services.rl_routing import get_best_platform
                rec = get_best_platform(db, tenant_id, inferred_type)
                if rec.alternatives:
                    valid = [a for a in rec.alternatives if a["platform"] in _VALID_EXPLORE]
                    if valid:
                        least = min(valid, key=lambda a: a["total"])
                        platform = least["platform"]
                        routing_source = "exploration_balanced"
            except Exception:
                safe_rollback(db)
    else:
        try:
            from app.services.rl_routing import get_routing_recommendation
            rl_rec = get_routing_recommendation(
                db, tenant_id, message,
                task_type=inferred_type,
                current_platform=platform,
                current_agent=agent_slug,
            )
            _VALID_CLI = {"claude_code", "codex", "gemini_cli"}
            if rl_rec.platform and rl_rec.platform in _VALID_CLI and rl_rec.platform_confidence >= 0.4:
                platform = rl_rec.platform
                routing_source = "rl_platform"
        except Exception as e:
            logger.debug("RL routing lookup failed: %s", e)
            safe_rollback(db)

    # 6. Policy rollout
    rollout_experiment_id = None
    try:
        from app.services import policy_rollout_service
        rollout = policy_rollout_service.get_active_rollout(db, tenant_id, "chat_response")
        if rollout:
            apply_policy, is_treatment = policy_rollout_service.should_apply_rollout(rollout)
            rollout_experiment_id = rollout["experiment_id"]
            if is_treatment and apply_policy:
                routing_source = "rollout_treatment"
                proposed = rollout.get("proposed_policy", {})
                if "platform" in proposed: platform = proposed["platform"]
                if "agent_slug" in proposed: agent_slug = proposed["agent_slug"]
    except Exception as e:
        logger.debug("Policy rollout check failed: %s", e)
        safe_rollback(db)

    # 7. Build memory context
    pre_built_memory_context = None
    session_entity_names = (db_session_memory or {}).get("recalled_entity_names")
    limits = TIER_LIMITS.get(agent_tier, TIER_LIMITS["full"])
    
    try:
        pre_built_memory_context = _build_memory_context(
            db, tenant_id, message,
            session_entity_names=session_entity_names,
            domains=agent_memory_domains,
            max_entities=limits["entities"],
            max_observations=limits["observations_per_entity"],
            include_relations=limits["include_relations"],
            include_episodes=limits["include_episodes"],
            agent_slug=agent_slug,
            chat_session_id=_presence_sid,
            user_id=user_id,
        )
        if pre_built_memory_context and pre_built_memory_context.get("relevant_entities"):
            recalled_entities = pre_built_memory_context["relevant_entities"]
    except Exception:
        logger.debug("Memory context build failed — routing without entity context")
        safe_rollback(db)

    # 8. Short-message local path
    if _should_use_local_path(intent, message, _pin_to_claude):
        user_name = None
        try:
            from app.models.user import User
            user = db.query(User).filter(User.id == user_id).first()
            if user:
                user_name = user.full_name
        except Exception:
            safe_rollback(db)

        _memory_summary = _format_memory_for_local(pre_built_memory_context)
        _local_response = generate_agent_response_sync(
            message=message,
            conversation_summary=conversation_summary,
            memory_context=_memory_summary,
            agent_slug=agent_slug,
            skill_body=f"The user you are speaking with is {user_name}." if user_name else ""
        )
        if _local_response:
            _tier_trajectory_id = uuid.uuid4()
            try:
                safe_rollback(db)
                rl_experience_service.log_experience(
                    db, tenant_id=tenant_id, trajectory_id=_tier_trajectory_id,
                    step_index=0, decision_point="tier_selection",
                    state={"user_message": message[:200], "task_type": inferred_type},
                    action={"tier": "local", "platform": "local_inference"},
                    state_text=f"task_type: {inferred_type}, message_len: {len(message)}",
                )
            except Exception:
                safe_rollback(db)
            
            _local_meta = {
                "platform": "local_inference", 
                "agent_tier": "local",
                "recalled_entity_names": pre_built_memory_context.get("recalled_entity_names", []) if pre_built_memory_context else []
            }
            return _local_response, _local_meta

    # 9. Log RL experience for agent_routing decision
    trajectory_id = uuid.uuid4()
    try:
        safe_rollback(db)
        rl_experience_service.log_experience(
            db, tenant_id=tenant_id, trajectory_id=trajectory_id,
            step_index=0, decision_point="agent_routing",
            state={"user_message": message[:200], "task_type": inferred_type},
            action={"platform": platform, "agent_slug": agent_slug, "routing_source": routing_source},
            state_text=f"task_type: {inferred_type}, channel: {channel}",
        )
    except Exception:
        safe_rollback(db)

    # 10. Presence update
    try:
        luna_presence_service.update_state(
            tenant_id, state="thinking", tool_status="running",
            session_id=_presence_sid,
        )
    except Exception:
        pass

    # 11. Execute
    try:
        response_text, metadata = run_agent_session(
            db, tenant_id=tenant_id, user_id=user_id,
            platform=platform, agent_slug=agent_slug,
            message=message, channel=channel,
            sender_phone=sender_phone, conversation_summary=conversation_summary,
            image_b64=image_b64, image_mime=image_mime,
            db_session_memory=db_session_memory,
            pre_built_memory_context=pre_built_memory_context,
            agent_tier=agent_tier,
            agent_tool_groups=agent_tool_groups,
            agent_memory_domains=agent_memory_domains,
        )
    except Exception:
        try:
            luna_presence_service.update_state(tenant_id, state="error", session_id=_presence_sid)
        except Exception:
            pass
        raise

    # 12. Final updates
    metadata = metadata or {}
    if pre_built_memory_context:
        metadata["recalled_entity_names"] = pre_built_memory_context.get("recalled_entity_names", [])

    try:
        state = "responding" if response_text else "idle"
        luna_presence_service.update_state(tenant_id, state=state, session_id=_presence_sid)
    except Exception:
        pass

    return response_text, metadata
