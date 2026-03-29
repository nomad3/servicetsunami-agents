"""CLI Session Manager — handles full lifecycle of a CLI agent session."""

import json
import logging
import os
import uuid
from datetime import timedelta
from typing import Any, Dict, Optional, Tuple

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.integration_config import IntegrationConfig
from app.services.memory_recall import build_memory_context_with_git
from app.services.orchestration.credential_vault import retrieve_credentials_for_skill
from app.services.skill_manager import skill_manager

logger = logging.getLogger(__name__)

SUPPORTED_CLI_PLATFORMS = {"claude_code", "codex", "gemini_cli"}


def generate_cli_instructions(
    skill_body: str,
    tenant_name: str,
    user_name: str,
    channel: str,
    conversation_summary: str,
    memory_context: Dict[str, Any],
    agent_slug: str = "luna",
) -> str:
    """Generate provider-neutral instruction markdown from agent skill + tenant context."""
    lines: list[str] = []

    lines.append("# CRITICAL RULES")
    lines.append("")
    lines.append(f"Your tenant_id is: {tenant_name}")
    lines.append(f"When calling ANY MCP tool, ALWAYS pass tenant_id=\"{tenant_name}\".")
    lines.append(f"Session: tenant={tenant_name} user={user_name} channel={channel}")
    lines.append("")
    # Identity section: use identity profile if available, otherwise default to Luna
    identity_profile = (memory_context.get("self_model") or {}).get("identity_context")
    if identity_profile:
        lines.append("## IDENTITY")
        lines.append(f"Your user-facing identity is {agent_slug}.")
        lines.append("The underlying execution runtime may be Codex, Claude Code, or Copilot CLI, but that is not your identity.")
        lines.append(f"If the user asks who you are, answer that you are {agent_slug}.")
        lines.append(f"If the user asks about the underlying model or runtime, explain it plainly: you are {agent_slug} running on the tenant's configured CLI platform.")
        lines.append("Do not introduce yourself as Codex, Claude, Claude Code, or 'the code agent' unless the user is explicitly asking about infrastructure.")
        lines.append("")
    else:
        lines.append("## IDENTITY")
        lines.append("Your user-facing identity is Luna.")
        lines.append("The underlying execution runtime may be Codex or Claude Code, but that is not your identity.")
        lines.append("If the user asks who you are, answer that you are Luna.")
        lines.append("If the user asks about the underlying model or runtime, explain it plainly: you are Luna running on the tenant's configured CLI platform.")
        lines.append("Do not introduce yourself as Codex, Claude, Claude Code, or 'the code agent' unless the user is explicitly asking about infrastructure.")
        lines.append("")
    lines.append("## Memory & Context Priority")
    lines.append("1. FIRST check the conversation history above — if the answer is in recent messages, use it directly.")
    lines.append("2. THEN check the Relevant Entities / Memories sections below for recalled context.")
    lines.append("3. ONLY IF neither contains the answer, call find_entities or search_knowledge MCP tools.")
    lines.append("NEVER say 'I don't have information' when the answer is visible in the conversation above.")
    lines.append(f"You are {agent_slug}, an AI agent with full access to email, calendar, knowledge graph, Jira, and code tools.")
    lines.append("")

    # Time and calendar context
    time_ctx = memory_context.get("time_context", {})
    upcoming = memory_context.get("upcoming_events", [])

    if time_ctx or upcoming:
        lines.append("## Today's Context")
        lines.append("")
        if time_ctx.get("greeting_hint"):
            lines.append(f"- {time_ctx['greeting_hint']}")
        if upcoming:
            lines.append("- Upcoming:")
            for evt in upcoming:
                lines.append(f"  - {evt['time']}: {evt['title']}")
        lines.append("")

    lines.append("# Agent Instructions")
    lines.append("")
    lines.append(skill_body.strip())
    lines.append("")

    if conversation_summary:
        lines.append("# Last Few Messages (for immediate context)")
        lines.append("")
        lines.append(conversation_summary.strip()[-1500:])
        lines.append("")

    relevant_entities = memory_context.get("relevant_entities", [])
    relevant_memories = memory_context.get("relevant_memories", [])
    relevant_relations = memory_context.get("relevant_relations", [])

    entity_observations = memory_context.get("entity_observations", {})

    if relevant_entities:
        lines.append("## Relevant Entities")
        lines.append("")
        for entity in relevant_entities:
            name = entity.get("name", "")
            etype = entity.get("type", "")
            desc = entity.get("description", "")
            sim = entity.get("similarity")
            summary = f"**{name}** ({etype})"
            if desc:
                summary += f": {desc}"
            lines.append(f"- {summary}")
            # Inject observations (facts) for this entity
            obs = entity_observations.get(name, [])
            for o in obs[:3]:
                sentiment = o.get('sentiment', '')
                sentiment_tag = f" [{sentiment}]" if sentiment and sentiment != "neutral" else ""
                source = o.get('source_ref', '')
                source_tag = f" (from {source})" if source else ""
                lines.append(f"  - {o.get('text', '')}{sentiment_tag}{source_tag}")
        lines.append("")

    # Separate dream-learned patterns from regular memories
    dream_memories = [m for m in relevant_memories if m.get("content", "").startswith("[Auto-dream]")]
    regular_memories = [m for m in relevant_memories if not m.get("content", "").startswith("[Auto-dream]")]

    if regular_memories:
        lines.append("## Relevant Memories")
        lines.append("")
        for memory in regular_memories:
            mtype = memory.get("type", "")
            content = memory.get("content", "")
            lines.append(f"- [{mtype}] {content}")
        lines.append("")

    if dream_memories:
        lines.append("## Learned Patterns (from RL consolidation)")
        lines.append("These patterns were learned from analyzing your past performance:")
        lines.append("")
        for memory in dream_memories:
            content = memory.get("content", "").replace("[Auto-dream] ", "")
            lines.append(f"- {content}")
        lines.append("")

    if relevant_relations:
        lines.append("## Relevant Relations")
        lines.append("")
        for relation in relevant_relations:
            frm = relation.get("from", "")
            to = relation.get("to", "")
            rtype = relation.get("type", "")
            lines.append(f"- {frm} --{rtype}--> {to}")
        lines.append("")

    contradictions = memory_context.get("contradictions", [])
    if contradictions:
        lines.append("## Conflicting Information (verify with user)")
        lines.append("")
        for c in contradictions:
            lines.append(f"- **{c['entity']}**: {c['attribute']} was '{c.get('current', {}).get('type', '?')}' but new info says '{c.get('conflicting', {}).get('type', '?')}' -- {c.get('reason', '')}")
        lines.append("")

    episodes = memory_context.get("recent_episodes", [])
    if episodes:
        lines.append("## Recent Conversations")
        lines.append("")
        for ep in episodes:
            date = ep.get("date", "")
            mood = ep.get("mood", "")
            source = ep.get("source", "")
            mood_tag = f" [{mood}]" if mood and mood != "neutral" else ""
            source_tag = f" via {source}" if source else ""
            lines.append(f"- {date}{source_tag}{mood_tag}: {ep.get('summary', '')}")
        lines.append("")

    # User preferences (learned from feedback)
    try:
        from app.db.session import SessionLocal as _SL
        from app.models.user_preference import UserPreference
        _pdb = _SL()
        try:
            import uuid as _uuid
            _tid = _uuid.UUID(tenant_name) if len(tenant_name) > 30 else None
            if _tid:
                # Filter to tenant-level preferences (user_id IS NULL) to avoid
                # leaking one user's preferences to another user on the same tenant
                prefs = _pdb.query(UserPreference).filter(
                    UserPreference.tenant_id == _tid,
                    UserPreference.user_id.is_(None),
                    UserPreference.confidence >= 0.3,
                ).order_by(UserPreference.confidence.desc()).limit(10).all()
                if prefs:
                    lines.append("## User Preferences (learned from feedback)")
                    lines.append("")
                    for p in prefs:
                        lines.append(f"- {p.preference_type}: {p.value} (confidence: {p.confidence:.0%})")
                    lines.append("")
        finally:
            _pdb.close()
    except Exception:
        pass

    git_context = memory_context.get("git_context", [])
    if git_context:
        lines.append("## Recent Git Context")
        lines.append("")
        for item in git_context:
            gtype = item.get("type", "")
            gtext = item.get("text", "")
            gdate = item.get("date", "")[:10]
            lines.append(f"- [{gtype}] {gtext} ({gdate})")
        lines.append("")

    # Self-model context: identity, goals, commitments
    self_model = memory_context.get("self_model", {})
    identity_context = self_model.get("identity_context")
    active_goals = self_model.get("active_goals", [])
    open_commitments = self_model.get("open_commitments", [])

    if identity_context:
        lines.append(identity_context)
        lines.append("")

    if active_goals:
        lines.append("## Active Goals")
        lines.append("")
        for g in active_goals:
            state_tag = f"[{g.get('state', 'active')}]"
            priority = g.get("priority", "")
            lines.append(f"- {state_tag} **{g.get('title', '')}** (priority: {priority})")
            if g.get("progress_summary"):
                lines.append(f"  Progress: {g['progress_summary']}")
        lines.append("")

    if open_commitments:
        lines.append("## Open Commitments")
        lines.append("")
        for c in open_commitments:
            due = f" (due: {c['due_at'][:10]})" if c.get("due_at") else ""
            lines.append(f"- **{c.get('title', '')}**{due}")
        lines.append("")

    # World model context: current state, unstable assumptions, causal patterns
    world_model = memory_context.get("world_model", {})
    state_context = world_model.get("state_context")
    unstable_assertions = world_model.get("unstable_assertions", [])
    causal_patterns = world_model.get("causal_patterns", [])

    if state_context:
        lines.append("## Current World State")
        lines.append("")
        lines.append(state_context)
        lines.append("")

    if unstable_assertions:
        lines.append("## Assumptions Needing Verification")
        lines.append("")
        lines.append("These facts have low confidence or are aging. Verify before relying on them:")
        for ua in unstable_assertions:
            lines.append(f"- **{ua['subject']}.{ua['attribute']}** = {ua['value']} (confidence: {ua['confidence']})")
        lines.append("")

    if causal_patterns:
        lines.append("## Known Causal Patterns")
        lines.append("")
        lines.append("Actions and their observed outcomes (use for planning):")
        for cp in causal_patterns:
            lines.append(f"- {cp['cause']} → {cp['effect']} (confidence: {cp['confidence']}, seen {cp['observations']}x)")
        lines.append("")

    return "\n".join(lines)


def generate_mcp_config(tenant_id: str, internal_key: str) -> dict:
    """Generate MCP config JSON for a CLI session."""
    mcp_tools_url = os.environ.get("MCP_TOOLS_URL", "http://mcp-tools:8000")
    mcp_url = f"{mcp_tools_url}/mcp"

    return {
        "mcpServers": {
            "servicetsunami": {
                "type": "http",
                "url": mcp_url,
                "headers": {
                    "X-Internal-Key": internal_key,
                    "X-Tenant-Id": str(tenant_id),
                },
            }
        }
    }


def _get_cli_platform_credentials(
    db: Session,
    tenant_id: uuid.UUID,
    integration_name: str,
) -> Dict[str, Any]:
    """Retrieve active credentials for a tenant CLI platform."""
    config = (
        db.query(IntegrationConfig)
        .filter(
            IntegrationConfig.tenant_id == tenant_id,
            IntegrationConfig.integration_name == integration_name,
            IntegrationConfig.enabled.is_(True),
        )
        .first()
    )
    if not config:
        logger.warning("No active %s integration config for tenant %s", integration_name, tenant_id)
        return {}

    creds = retrieve_credentials_for_skill(db, config.id, tenant_id)
    if not creds:
        logger.warning("No credentials found for %s integration, tenant %s", integration_name, tenant_id)
    return creds


def run_agent_session(
    db: Session,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    platform: str,
    agent_slug: str,
    message: str,
    channel: str,
    sender_phone: Optional[str],
    conversation_summary: str,
    image_b64: str = "",
    image_mime: str = "",
    db_session_memory: Dict = None,
    pre_built_memory_context: Dict = None,
) -> Tuple[Optional[str], Dict]:
    """Run a full stateless CLI agent session through the configured platform."""
    metadata: Dict[str, Any] = {
        "platform": platform,
        "agent_slug": agent_slug,
        "tenant_id": str(tenant_id),
        "user_id": str(user_id),
        "channel": channel,
        "error": None,
    }

    if platform not in SUPPORTED_CLI_PLATFORMS:
        err = f"Unsupported CLI platform '{platform}'"
        logger.error(err)
        metadata["error"] = err
        return None, metadata

    skill = skill_manager.get_skill_by_slug(agent_slug, str(tenant_id))
    if not skill and agent_slug != "luna":
        logger.info("Skill '%s' not found, falling back to 'luna'", agent_slug)
        skill = skill_manager.get_skill_by_slug("luna", str(tenant_id))
        if skill:
            agent_slug = "luna"
    if not skill:
        err = f"No agent skill found (tried '{agent_slug}' and 'luna')"
        logger.error(err)
        metadata["error"] = err
        return None, metadata

    skill_body = skill.description or ""

    credentials = _get_cli_platform_credentials(db, tenant_id, platform)
    session_token = credentials.get("session_token")
    auth_json = credentials.get("auth_json")

    oauth_token = credentials.get("oauth_token")
    subscription_missing = (
        (platform == "claude_code" and not session_token)
        or (platform == "codex" and not (session_token or auth_json))
        or (platform == "gemini_cli" and not (oauth_token or session_token))
    )
    if subscription_missing:
        logger.warning(
            "No %s credential for tenant %s — falling back to local agent",
            platform, tenant_id,
        )
        # 1. Try local tool agent (curated MCP tools via Ollama)
        try:
            from app.services import local_tool_agent
            connected = [
                r[0] for r in db.query(IntegrationConfig.integration_name)
                .filter(IntegrationConfig.tenant_id == tenant_id, IntegrationConfig.enabled.is_(True))
                .all()
            ]
            tool_response, tool_meta = local_tool_agent.run(
                message=message,
                tenant_id=tenant_id,
                skill_body=skill_body,
                agent_slug=agent_slug,
                conversation_summary=conversation_summary,
                connected_integrations=connected,
            )
            if tool_response:
                metadata.update(tool_meta)
                return tool_response, metadata
            logger.info("Local tool agent returned no response — falling back to plain text")
        except Exception as exc:
            logger.warning("Local tool agent failed: %s — falling back to plain text", exc)

        # 2. Fall back to plain text response (no tools)
        from app.services.local_inference import generate_agent_response_sync
        local_response = generate_agent_response_sync(
            message=message,
            conversation_summary=conversation_summary,
            skill_body=skill_body,
            agent_slug=agent_slug,
        )
        if local_response:
            metadata["platform"] = "local_qwen"
            metadata["fallback"] = True
            return local_response, metadata

        # 3. Friendly error
        platform_label = {"claude_code": "Claude Code", "codex": "Codex", "gemini_cli": "Gemini CLI"}.get(platform, platform)
        err = (
            f"{platform_label} subscription is not connected "
            "and the local model is unavailable. Please connect your account in Settings → Integrations."
        )
        metadata["error"] = err
        return None, metadata

    # Extract session entity names from prior turns for recall boosting
    session_entity_names = (db_session_memory or {}).get("recalled_entity_names")

    if pre_built_memory_context is not None:
        memory_context = pre_built_memory_context
    else:
        try:
            memory_context = build_memory_context_with_git(
                db, tenant_id, message,
                session_entity_names=session_entity_names,
            )
        except Exception as exc:
            logger.warning("Memory recall failed for tenant %s: %s", tenant_id, exc)
            memory_context = {}

    # Inject self-model context: identity profile, active goals, open commitments
    try:
        from app.services import agent_identity_service, goal_service, commitment_service
        self_model: Dict[str, Any] = {}
        identity_md = agent_identity_service.build_runtime_identity_context(db, tenant_id, agent_slug)
        if identity_md:
            self_model["identity_context"] = identity_md
        active_goals = goal_service.list_active_goals_for_agent(db, tenant_id, agent_slug)
        if active_goals:
            self_model["active_goals"] = [
                {"title": g.title, "state": g.state, "priority": g.priority, "progress_summary": g.progress_summary}
                for g in active_goals
            ]
        open_commitments = commitment_service.list_open_commitments_for_agent(db, tenant_id, agent_slug)
        if open_commitments:
            self_model["open_commitments"] = [
                {"title": c.title, "due_at": c.due_at.isoformat() if c.due_at else None}
                for c in open_commitments
            ]
        if self_model:
            memory_context["self_model"] = self_model
    except Exception as exc:
        logger.debug("Self-model injection failed for %s: %s", agent_slug, exc)

    # Inject world state context: snapshots, unstable assertions, causal patterns
    try:
        from app.services import world_state_service, causal_edge_service

        world_model: Dict[str, Any] = {}

        # Get snapshots for entities mentioned in the message or recalled entities
        recalled = memory_context.get("relevant_entities", [])
        subject_slugs = []
        for ent in recalled[:5]:
            name = ent.get("name", "") if isinstance(ent, dict) else getattr(ent, "name", "")
            if name:
                subject_slugs.append(name.lower().replace(" ", "_"))
        if subject_slugs:
            state_md = world_state_service.build_world_state_context(db, tenant_id, subject_slugs)
            if state_md:
                world_model["state_context"] = state_md

        # Get unstable assertions scoped to recalled subjects only
        if subject_slugs:
            all_unstable = world_state_service.get_unstable_assertions(
                db, tenant_id, confidence_threshold=0.5, limit=20
            )
            unstable = [a for a in all_unstable if a.subject_slug in subject_slugs][:5]
            if unstable:
                world_model["unstable_assertions"] = [
                    {
                        "subject": a.subject_slug,
                        "attribute": a.attribute_path,
                        "value": a.value_json,
                        "confidence": round(a.confidence, 2),
                    }
                    for a in unstable
                ]

        # Get causal patterns: confirmed first (strongest), then corroborated
        top_patterns = causal_edge_service.list_causal_edges(
            db, tenant_id=tenant_id, status="confirmed", limit=3
        )
        top_patterns += causal_edge_service.list_causal_edges(
            db, tenant_id=tenant_id, status="corroborated", limit=max(0, 5 - len(top_patterns))
        )
        if top_patterns:
            world_model["causal_patterns"] = [
                {
                    "cause": e.cause_summary,
                    "effect": e.effect_summary,
                    "confidence": round(e.confidence, 2),
                    "observations": e.observation_count,
                }
                for e in top_patterns
            ]

        if world_model:
            memory_context["world_model"] = world_model
    except Exception as exc:
        logger.debug("World model injection failed for %s: %s", agent_slug, exc)

    # Thread recalled entity names into metadata for session memory persistence
    recalled_names = memory_context.get("recalled_entity_names", [])
    if recalled_names:
        metadata["recalled_entity_names"] = recalled_names

    tenant_name = str(tenant_id)
    user_name = sender_phone or str(user_id)
    instruction_md_content = generate_cli_instructions(
        skill_body=skill_body,
        tenant_name=tenant_name,
        user_name=user_name,
        channel=channel,
        conversation_summary=conversation_summary,
        memory_context=memory_context,
        agent_slug=agent_slug,
    )

    internal_key = settings.MCP_API_KEY or "dev_mcp_key"
    mcp_config = generate_mcp_config(str(tenant_id), internal_key)

    logger.info(
        "Dispatching ChatCliWorkflow: platform=%s skill=%s tenant=%s channel=%s",
        platform, agent_slug, str(tenant_id)[:8], channel,
    )

    import asyncio
    from dataclasses import dataclass as _dc

    from temporalio.client import Client as TemporalClient

    try:
        temporal_address = os.environ.get("TEMPORAL_ADDRESS", "temporal:7233")

        async def _run_workflow():
            client = await TemporalClient.connect(temporal_address)

            @_dc
            class _ChatCliInput:
                platform: str
                message: str
                tenant_id: str
                instruction_md_content: str = ""
                mcp_config: str = ""
                image_b64: str = ""
                image_mime: str = ""
                session_id: str = ""

            existing_session_id = (
                (db_session_memory or {}).get(f"{platform}_cli_session_id")
                or (db_session_memory or {}).get("claude_cli_session_id", "")  # legacy key
                or (db_session_memory or {}).get("cli_session_id", "")
            )

            task_input = _ChatCliInput(
                platform=platform,
                message=message,
                tenant_id=str(tenant_id),
                instruction_md_content=instruction_md_content,
                mcp_config=json.dumps(mcp_config),
                image_b64=image_b64,
                image_mime=image_mime,
                session_id=existing_session_id,
            )

            return await client.execute_workflow(
                "ChatCliWorkflow",
                task_input,
                id=f"chat-cli-{uuid.uuid4()}",
                task_queue="servicetsunami-code",
                execution_timeout=timedelta(minutes=180),
            )

        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None

        if running_loop is not None and running_loop.is_running():
            # Already inside an async context (e.g. Temporal worker).
            # Run the workflow in a separate thread with its own loop.
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                result = pool.submit(lambda: asyncio.run(_run_workflow())).result(timeout=200)
        else:
            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(_run_workflow())
            finally:
                loop.close()

        if isinstance(result, dict):
            success = result.get("success", False)
            response_text = result.get("response_text", "")
            error = result.get("error")
            meta = result.get("metadata") or {}
        else:
            success = getattr(result, "success", False)
            response_text = getattr(result, "response_text", "")
            error = getattr(result, "error", None)
            meta = getattr(result, "metadata", None) or {}

        if success and response_text:
            if isinstance(meta, dict):
                input_tokens = meta.get("input_tokens") or 0
                output_tokens = meta.get("output_tokens") or 0
                try:
                    meta["tokens_used"] = int(input_tokens) + int(output_tokens)
                except (TypeError, ValueError):
                    pass
                if meta.get("cost") is None and meta.get("cost_usd") is not None:
                    meta["cost"] = meta.get("cost_usd")
                metadata.update(meta)
            return response_text, metadata

        metadata["error"] = error or "CLI workflow returned empty response"
        logger.warning(
            "ChatCliWorkflow result: success=%s error=%s response_len=%s",
            success, error, len(response_text) if response_text else 0,
        )
        return None, metadata
    except Exception as exc:
        logger.exception("ChatCliWorkflow dispatch failed")
        metadata["error"] = str(exc)
        return None, metadata
