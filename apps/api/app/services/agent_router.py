"""Agent Router — routes messages to CLI platforms.

Phase 1: Deterministic routing (tenant default + agent affinity).
Phase 3: RL-driven routing added on top.
"""
import logging
import os
import time
import uuid
import random
from typing import Optional, Tuple, Dict, Any, List

from sqlalchemy.orm import Session
from sqlalchemy import text, func

from app.db.safe_ops import safe_rollback
from app.models.tenant_features import TenantFeatures
from app.models.tenant_branding import TenantBranding
from app.models.agent import Agent as AgentModel
from app.services.cli_session_manager import run_agent_session
from app.services.cli_platform_resolver import (
    classify_error as _classify_cli_error,
    mark_cooldown as _mark_cli_cooldown,
    resolve_cli_chain as _resolve_cli_chain,
)
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


# ── Greeting fast-path (Tier-1 #1 of the latency reduction plan) ──
#
# Bench v4 measured a "hola luna" turn at 21 s, 100% Gemma 4 inference,
# 2 inference rounds — a 50-token greeting reply. The agent's persona
# enforces proactive memory recall on every turn, even ones that are
# just "say hi". For trivially short messages with greeting intent we
# can short-circuit BEFORE any LLM call.
#
# Heuristic: intent matched the canonical "greeting or small talk"
# class AND the message is ≤ this many characters AND contains no
# question marks (so we don't snap on "hola, qué citas tengo hoy?").
_GREETING_FAST_PATH_MAX_CHARS = 30


# Keyword fallback for when the embedding-service intent classifier
# couldn't initialize (cold-start race — see plan §A.3). Without this
# fallback the greeting fast-path was firing at 0% in production after
# every api restart that beat embedding-service to readiness.
_GREETING_KEYWORDS_ES = (
    "hola", "buenas", "buenos días", "buen día",
    "buenas tardes", "buenas noches", "qué tal", "que tal",
    "saludos", "ola", "ey",
)
_GREETING_KEYWORDS_EN = (
    "hi", "hello", "hey", "good morning", "good afternoon",
    "good evening", "howdy",
)


def _looks_like_greeting(message: str) -> bool:
    """Cheap keyword check: starts with one of the known greetings.

    Used both as the keyword fallback when intent is None AND as a
    confirming gate when intent is set — defends against intent
    misclassifications.
    """
    lower = (message or "").strip().lower()
    if not lower:
        return False
    for kw in _GREETING_KEYWORDS_ES + _GREETING_KEYWORDS_EN:
        if lower == kw or lower.startswith(kw + " ") or lower.startswith(kw + ",") or lower.startswith(kw + "!"):
            return True
    return False


def _greeting_template(intent: dict | None, message: str, agent_slug: str) -> str | None:
    """Return a templated greeting reply, or None if the message
    doesn't qualify for the fast-path.

    Fires when EITHER:
      - The intent classifier matched "greeting or small talk", OR
      - The classifier wasn't available (intent is None) and the
        message keyword-matches a known greeting.

    The keyword path is critical — embedding-service has a cold-start
    race that empties _intent_cache after every api restart (plan §A.3),
    so without this fallback the fast-path would be 0% effective in
    practice for ~60s after every deploy.
    """
    intent_name = (intent or {}).get("name") if intent else None
    if intent_name == "greeting or small talk":
        pass  # ok, intent matched
    elif intent_name is None and _looks_like_greeting(message):
        pass  # keyword fallback fired
    else:
        return None
    msg = (message or "").strip()
    if not msg or len(msg) > _GREETING_FAST_PATH_MAX_CHARS:
        return None
    if "?" in msg or "¿" in msg:
        return None
    # Spanish vs English heuristic by leading token. Cheap; the agent
    # persona's "respond in the same language" guidance covered this in
    # the LLM path; here we approximate.
    lower = msg.lower()
    is_spanish = any(lower.startswith(t) for t in (
        "hola", "qué tal", "que tal", "buenas", "buenos días", "buen día",
        "buenas tardes", "buenas noches", "saludos", "ey", "ola",
    ))
    name = "Luna"
    # Slug → friendly display name fallback (only used when the agent
    # isn't Luna — e.g. tenant has named their agent something else).
    if agent_slug and agent_slug != "luna":
        name = agent_slug.replace("_", " ").replace("-", " ").title()
    if is_spanish:
        return f"¡Hola! Soy {name}. ¿En qué te puedo ayudar?"
    return f"Hi! I'm {name}. How can I help?"


# Display labels for CLI platforms surfaced in the chat UI's routing
# footer. Keep these human-readable — the customer reads them directly.
# Internal platform identifiers stay snake_case in code; this is the
# mapping to the polished label they see under the assistant message.
_CLI_DISPLAY_LABELS: Dict[str, str] = {
    "claude_code": "Claude Code",
    "copilot_cli": "GitHub Copilot CLI",
    "codex": "Codex CLI",
    "gemini_cli": "Gemini CLI",
    "opencode": "OpenCode (local)",
    "local_gemma": "Local model",
    "template": "Template (no LLM)",
}

# Friendly summaries for fallback reasons surfaced in the chat UI.
# The internal classification ("quota" / "auth" / "missing_credential" /
# "exception") becomes a one-line user-facing explanation. Keep the
# internal classification strings in metadata too for ops dashboards.
_FALLBACK_REASON_LABELS: Dict[str, str] = {
    "quota": "rate limit / quota exceeded",
    "auth": "authentication failed",
    "missing_credential": "subscription not connected",
    "exception": "transient error",
}


def _build_routing_summary(
    *,
    served_by: Optional[str],
    requested: Optional[str],
    chain_length: int,
    fallback_reason: Optional[str],
) -> Dict[str, Any]:
    """Build a CURATED routing summary for the chat UI footer.

    Lands in ``ChatMessage.context.routing_summary`` and is rendered as
    a one-line note under the assistant message ("Served by GitHub
    Copilot CLI · 891 tokens · 14s"). Deliberately a small, polished
    subset — NOT the raw `attempted` chain (that was the PR #245
    review's concern about exposing internals). Operators get the full
    chain via structured logs; customers get just enough to build trust.

    Fields:
      - served_by_platform: snake_case platform id (machine-readable)
      - served_by: human-readable label ("GitHub Copilot CLI")
      - requested: original platform pre-fallback (when fallback fired)
      - fallback_reason: one of "quota" / "auth" / "missing_credential"
        / "exception" — only present when a fallback fired
      - fallback_explanation: friendly one-liner for the reason
      - chain_length: number of CLIs the resolver tried (≥1)
    """
    summary: Dict[str, Any] = {
        "served_by_platform": served_by,
        "served_by": _CLI_DISPLAY_LABELS.get(served_by or "", served_by or "—"),
        "chain_length": max(chain_length, 1),
    }
    if served_by and requested and served_by != requested:
        summary["requested_platform"] = requested
        summary["requested"] = _CLI_DISPLAY_LABELS.get(requested, requested)
        summary["fallback_reason"] = fallback_reason or "unknown"
        summary["fallback_explanation"] = _FALLBACK_REASON_LABELS.get(
            fallback_reason or "", "fell back to the next available CLI",
        )
    return summary


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
    agent_skill_slugs: list = None,
    conversation_summary: str = "",
    image_b64: str = "",
    image_mime: str = "",
    db_session_memory: dict = None,
    recalled_entities: list = None,
) -> Tuple[Optional[str], Dict[str, Any]]:
    # Apply channel-based agent default if not explicitly specified
    if not agent_slug:
        agent_slug = resolve_primary_agent_slug(db, tenant_id)
    # Default the skill list to a single-entry of the identity slug — keeps
    # legacy callers (workflows / dynamic_step / simulation) working without
    # signature-level changes.
    if not agent_skill_slugs:
        agent_skill_slugs = [agent_slug]

    # 1. Load tenant features
    try:
        features = db.query(TenantFeatures).filter(
            TenantFeatures.tenant_id == tenant_id
        ).first()
    except Exception:
        safe_rollback(db)
        features = None

    # Initial platform — let the resolver autodetect when no explicit
    # default_cli_platform is set. Was previously hardcoded to
    # ``"gemini_cli"`` as a floor; the resolver chain (PR #245) now
    # picks the right CLI based on which integrations the tenant has
    # actually wired, with `opencode` as the universal local fallback.
    # Holding `platform=None` here lets `_resolve_cli_chain` build a
    # purely autodetect-driven chain instead of artificially leading
    # with gemini_cli on every request — which used to cause a wasted
    # gemini attempt + chain skip on tenants who connected GitHub
    # Copilot but never set a default. (See smoke #3 in the PR #245
    # post-merge verification.)
    platform: Optional[str] = None
    if features and getattr(features, "default_cli_platform", None):
        platform = features.default_cli_platform

    # Per-agent CLI override. Imported agents from Microsoft Copilot Studio
    # and Azure AI Foundry are stored as native Agent rows with
    # `config.preferred_cli` set (typically to "copilot_cli" so they run
    # against the tenant's GitHub Copilot subscription). Honor that ahead
    # of the tenant default — admin-declared per-agent intent wins.
    #
    # Slug normalization note: `agent_slug` originates from
    # `resolve_primary_agent_slug` (hyphen-delimited) or downstream rewrites
    # (`name.lower().replace(" ", "-")`). Normalize both sides to a common
    # form (lowercase, hyphen-separated) so a hyphenated slug like
    # "copilot-studio-bot" matches the row "Copilot Studio Bot". Exact-match
    # in SQL — no ILIKE — to avoid `%` / `_` wildcard collisions on names
    # like `"Sales_Manager"` or hand-crafted slugs.
    try:
        normalized_slug = (
            agent_slug.lower().replace(" ", "-").replace("_", "-")
            if agent_slug else ""
        )
        if normalized_slug:
            normalized_name = func.replace(
                func.replace(func.lower(AgentModel.name), " ", "-"),
                "_", "-",
            )
            _agent_row = (
                db.query(AgentModel)
                .filter(
                    AgentModel.tenant_id == tenant_id,
                    normalized_name == normalized_slug,
                )
                .first()
            )
            if _agent_row is not None:
                cfg = _agent_row.config or {}
                preferred = cfg.get("preferred_cli")
                if preferred and preferred in {
                    "copilot_cli", "claude_code", "gemini_cli", "codex", "opencode"
                }:
                    platform = preferred
    except Exception as e:
        logger.warning(
            "per-agent preferred_cli override lookup failed for slug=%r tenant=%s: %s",
            agent_slug, tenant_id, e,
        )
        safe_rollback(db)

    # When the tenant has a CLI subscription (gemini_cli, claude_code, codex,
    # copilot_cli), always route through it — don't fall back to local Gemma 4
    # for short messages. Local inference is for free-tier tenants with no
    # subscription.
    #
    # With the gemini_cli floor removed (PR #252 autodetect), `platform`
    # may be None when no explicit default is set. Compute the resolver
    # chain ONCE and reuse it for both `_pin_to_cli` and the actual
    # dispatch loop later — was previously called twice per chat turn,
    # an avoidable +1 DB query and ~4 Redis EXISTS per call. Holistic
    # 2026-05-02 review C4.
    cli_chain: Optional[List[str]] = None
    if platform in {"gemini_cli", "claude_code", "codex", "copilot_cli"}:
        # Fast path — explicit paid CLI is already pinned, skip the
        # resolver probe entirely (we'll still call it below for the
        # actual chain at dispatch time, but `_pin_to_cli` doesn't need it).
        _pin_to_cli = True
    else:
        try:
            cli_chain = _resolve_cli_chain(db, tenant_id, explicit_platform=platform)
            _pin_to_cli = bool(cli_chain) and cli_chain[0] != "opencode"
        except Exception as e:
            logger.warning(
                "CLI chain probe (for pin-to-cli) failed for tenant=%s: %s",
                str(tenant_id)[:8], e,
            )
            # Conservative — when probing fails, prefer pinning if the
            # explicit platform LOOKS like a paid CLI; otherwise fall
            # through to local-path eligibility. M5 from holistic review:
            # don't silently downgrade a paid tenant to local Gemma on a
            # transient resolver hiccup.
            _pin_to_cli = platform in {
                "gemini_cli", "claude_code", "codex", "copilot_cli",
            }

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

    # Greeting fast-path (Tier-1 #1 from the latency reduction plan).
    # Bench v4 measured: a "hola luna" turn spent 21 s in 2 Gemma 4
    # rounds + 0.26 s in tool calls. The whole thing is "say hi back".
    # If intent matched "greeting or small talk" AND the message is
    # short enough that there's no real content to process, return a
    # pre-rendered template and skip Gemma entirely.
    template_response = _greeting_template(intent, message, agent_slug)
    if template_response is not None:
        return template_response, {
            "platform": "template",
            "agent_tier": "template",
            "agent_slug": agent_slug,
            "timings": {"template_match_ms": 0},
            "routing_summary": _build_routing_summary(
                served_by="template", requested=None, chain_length=1, fallback_reason=None,
            ),
        }

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

    # [chat-trace] recall is the most likely silent-hang point on the chat hot
    # path: it issues blocking gRPC + DB queries with internal deadlines, but
    # the outer call has no wall-clock guard. Bracket it with a timing log so
    # a future hang is observable instead of opaque.
    _recall_t0 = time.perf_counter()
    logger.info(
        "[chat-trace] recall: enter tenant=%s agent=%s",
        str(tenant_id)[:8], agent_slug or "luna",
    )
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
        logger.info(
            "[chat-trace] recall: return tenant=%s elapsed=%.0fms entities=%d",
            str(tenant_id)[:8], (time.perf_counter() - _recall_t0) * 1000,
            len((pre_built_memory_context or {}).get("relevant_entities") or []),
        )
    except Exception:
        logger.warning(
            "[chat-trace] recall: failed tenant=%s elapsed=%.0fms — routing without entity context",
            str(tenant_id)[:8], (time.perf_counter() - _recall_t0) * 1000,
        )
        safe_rollback(db)

    # 8. Short-message local path
    if _should_use_local_path(intent, message, _pin_to_cli):
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
                "recalled_entity_names": pre_built_memory_context.get("recalled_entity_names", []) if pre_built_memory_context else [],
                "routing_summary": _build_routing_summary(
                    served_by="local_gemma", requested=None, chain_length=1, fallback_reason=None,
                ),
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

    # 11. Execute — autodetect available CLIs, walk the chain on quota/auth.
    #
    # Resolution priority (built in resolve_cli_chain):
    #   1. The `platform` value resolved above (per-agent override → tenant
    #      default → upstream RL/exploration), IF the tenant has the
    #      credentials wired AND that CLI isn't in cooldown.
    #   2. Other CLIs the tenant has actually connected, in default order.
    #   3. `opencode` (local Gemma 4) as the universal floor.
    #
    # Cooldown rules (deliberately narrow — see PR #245 review):
    #   * `quota` and `auth` classifications mark a 10-min cooldown so
    #     subsequent turns skip the failing CLI immediately.
    #   * `missing_credential` skips the CLI on this turn but does NOT
    #     cool — config issues (revoked OAuth) resolve in seconds, and
    #     cooling would stretch a quick reconnect into 10 min of
    #     degraded replies.
    #   * Bare exceptions (Temporal CancelledError, network blips) skip
    #     to the next CLI but do NOT cool — a transient code-worker pod
    #     restart shouldn't mass-cool every tenant's preferred CLI.
    #   * Unclassified empty responses bubble up — we don't burn the
    #     tenant's other CLI quotas on what's likely a prompt bug.
    #
    # Chain telemetry (`cli_chain_attempted`, `cli_fallback_used`) goes
    # to the structured logger only — NOT into `metadata` — because
    # `metadata` is serialized verbatim into `ChatMessage.context` and
    # would expose internal routing decisions to end-users.
    # Reuse the chain computed earlier when probing for `_pin_to_cli`.
    # Only fall back to a fresh resolve if it wasn't computed (the fast
    # path branch — explicit paid CLI). Saves a redundant DB+Redis hit
    # per chat turn. C4 from the holistic 2026-05-02 review.
    if cli_chain is None:
        try:
            cli_chain = _resolve_cli_chain(db, tenant_id, explicit_platform=platform)
        except Exception as e:
            # Resolver failure must not block dispatch — fall back to the
            # single-platform legacy behavior.
            logger.warning(
                "CLI chain resolution failed for tenant=%s platform=%s: %s — using single-platform path",
                str(tenant_id)[:8], platform, e,
            )
            cli_chain = [platform] if platform else ["opencode"]

    response_text: Optional[str] = None
    metadata: Dict[str, Any] = {}
    last_error: Optional[str] = None
    last_err_class: Optional[str] = None
    attempted: List[str] = []

    try:
        for attempt_platform in cli_chain:
            attempted.append(attempt_platform)
            try:
                response_text, metadata = run_agent_session(
                    db, tenant_id=tenant_id, user_id=user_id,
                    platform=attempt_platform, agent_slug=agent_slug,
                    agent_skill_slugs=agent_skill_slugs,
                    message=message, channel=channel,
                    sender_phone=sender_phone, conversation_summary=conversation_summary,
                    image_b64=image_b64, image_mime=image_mime,
                    db_session_memory=db_session_memory,
                    pre_built_memory_context=pre_built_memory_context,
                    agent_tier=agent_tier,
                    agent_tool_groups=agent_tool_groups,
                    agent_memory_domains=agent_memory_domains,
                )
            except Exception as exc:
                # Hard exception (Temporal CancelledError, network blip).
                # Skip to the next CLI but do NOT cool — a transient
                # code-worker hiccup must not mass-degrade every tenant's
                # preferred CLI for 10 min. Next chat turn retries.
                last_error = f"{attempt_platform}: {exc}"
                last_err_class = "exception"
                logger.warning(
                    "CLI attempt raised (no cooldown set) — tenant=%s platform=%s err=%s",
                    str(tenant_id)[:8], attempt_platform, exc,
                )
                continue

            # Successful response — done. Log chain telemetry to ops logs;
            # also stamp a CURATED routing_summary on metadata (lands in
            # ChatMessage.context) so the chat UI can show "Served by X"
            # under the assistant message. The summary deliberately
            # excludes the raw `attempted` list (that was the PR #245
            # review's concern about exposing internals); it surfaces
            # only "served_by" + optional "fallback_from / fallback_reason"
            # so the user sees what happened to their turn without
            # bleeding routing strategy details.
            if response_text:
                if attempt_platform != platform or len(attempted) > 1:
                    logger.info(
                        "CLI chain resolved — tenant=%s requested=%s served_by=%s attempted=%s",
                        str(tenant_id)[:8], platform, attempt_platform, attempted,
                    )
                metadata = metadata or {}
                metadata["routing_summary"] = _build_routing_summary(
                    served_by=attempt_platform,
                    requested=platform,
                    chain_length=len(attempted),
                    fallback_reason=last_err_class if attempt_platform != platform else None,
                )
                break

            # No response text but no exception — classify the metadata.error.
            err = (metadata or {}).get("error") if isinstance(metadata, dict) else None
            err_class = _classify_cli_error(err)
            last_error = err or "empty response"
            last_err_class = err_class
            if err_class in {"quota", "auth"}:
                logger.info(
                    "CLI %s returned %s for tenant=%s — cooldown + chain skip: %r",
                    attempt_platform, err_class, str(tenant_id)[:8], err,
                )
                _mark_cli_cooldown(tenant_id, attempt_platform, reason=err_class)
                continue
            if err_class == "missing_credential":
                logger.info(
                    "CLI %s missing credential for tenant=%s — chain skip (no cooldown): %r",
                    attempt_platform, str(tenant_id)[:8], err,
                )
                continue

            # Unclassified empty response — don't blast through the tenant's
            # other CLI quotas; let the empty result surface.
            break

        if not response_text:
            metadata = metadata or {}
            metadata.setdefault("error", last_error or "all CLI fallbacks failed")
            logger.warning(
                "CLI chain exhausted — tenant=%s requested=%s attempted=%s last_error=%r",
                str(tenant_id)[:8], platform, attempted, last_error,
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
