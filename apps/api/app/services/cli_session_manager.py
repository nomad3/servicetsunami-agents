"""CLI Session Manager — handles full lifecycle of a CLI agent session."""

import json
import logging
import os
import uuid
from datetime import timedelta
from typing import Any, Dict, Optional, Tuple

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.safe_ops import safe_rollback
from app.models.integration_config import IntegrationConfig
from app.models.mcp_server_connector import MCPServerConnector
from app.services.memory_recall import build_memory_context_with_git
from app.services.orchestration.credential_vault import retrieve_credentials_for_skill
from app.services.skill_manager import skill_manager
from app.services.agent_identity import resolve_primary_agent_slug
from app.services.tool_groups import TIER_LIMITS, TIER_MODEL_MAP, format_allowed_tools, resolve_tool_names

logger = logging.getLogger(__name__)

SUPPORTED_CLI_PLATFORMS = {"claude_code", "codex", "gemini_cli", "copilot_cli", "qwen_code", "opencode"}


# Universal anti-hallucination preamble. Lifted from aremko's "REGLA DE ORO"
# pattern to a platform-wide rule. Imported by both the CLI hot path
# (generate_cli_instructions) and the local-Gemma fallback path
# (local_tool_agent.run + local_inference.generate_agent_response_sync) so
# tenants without a CLI subscription get the same protection.
ANTI_HALLUCINATION_PREAMBLE = """## Anti-Hallucination Discipline (universal rule, every turn)
You will fabricate data unless you actively prevent yourself from doing so. Apply these rules ON EVERY RESPONSE:

**1. Tool grounding before specifics.** Before stating any of the following — a person's name, a product/service/SKU, a price, a date, a duration, a dosage, a slot/time, a metric, an address, a code, a quotation — you MUST have one of:
  (a) seen it in the conversation history above,
  (b) seen it in the Relevant Entities / Memories block below,
  (c) called an MCP tool in this same turn that returned it.
If none of these are true, do not invent it. Either call the appropriate tool, or say 'I don't have that — let me check' and call it, or hedge explicitly ('based on what I have in memory, but worth verifying').

**2. Never invent alternatives.** If a tool returns nothing or fails, do NOT fill the gap with plausible-sounding substitutes. The worst failure mode is 'I couldn't find X, but you could try Y or Z' where Y and Z are made up. Say 'I couldn't find X — would you like me to try a different date / parameter / source?' instead.

**3. Common fabrication patterns to NEVER produce:**
- Service / product names that 'sound like' the brand but aren't in the catalog.
- Prices, durations, dosages, IDs without a tool result in this turn.
- User-specific data (their address, phone, last order) without recall or a tool call.
- Quotation marks around invented quotes ('we promise...', 'as we discussed...').
- 'Done!' or 'I scheduled it' confirmations without actually invoking the action tool.

**4. Honest failure beats confident fabrication.** 'I couldn't reach the system — let me retry' is always better than a guessed answer.

**5. If your skill body lists tools you MUST call before responding, follow that list strictly.** Do not summarize what the tool would return — call it."""


def _build_today_briefing(memory_context: Dict[str, Any], include_goals: bool, include_commitments: bool) -> list[str]:
    """Render a compact operational briefing from anticipatory context."""
    lines: list[str] = []
    time_ctx = memory_context.get("time_context", {})
    upcoming = memory_context.get("upcoming_events", [])
    self_model = memory_context.get("self_model", {})
    active_goals = self_model.get("active_goals", []) if include_goals else []
    open_commitments = self_model.get("open_commitments", []) if include_commitments else []
    recent_episodes = memory_context.get("recent_episodes", [])

    if not any([time_ctx, upcoming, active_goals, open_commitments, recent_episodes]):
        return lines

    lines.append("## Today's Context")
    lines.append("")

    greeting_hint = time_ctx.get("greeting_hint")
    if greeting_hint:
        lines.append(f"- {greeting_hint}")

    if upcoming:
        if len(upcoming) == 1:
            lines.append(f"- You have 1 upcoming event in the next 4 hours: {upcoming[0]['time']}: {upcoming[0]['title']}")
        else:
            lines.append(f"- You have {len(upcoming)} upcoming events in the next 4 hours.")
            for evt in upcoming:
                lines.append(f"  - {evt['time']}: {evt['title']}")

    if active_goals:
        blocked_goals = sum(1 for goal in active_goals if goal.get("state") == "blocked")
        summary = f"- There {'is' if len(active_goals) == 1 else 'are'} {len(active_goals)} active goal{'s' if len(active_goals) != 1 else ''}"
        if blocked_goals:
            summary += f", including {blocked_goals} blocked"
        lines.append(summary + ".")

    if open_commitments:
        summary = f"- There {'is' if len(open_commitments) == 1 else 'are'} {len(open_commitments)} open commitment{'s' if len(open_commitments) != 1 else ''}"
        lines.append(summary + ".")

    if recent_episodes and time_ctx.get("time_of_day") == "morning":
        latest = recent_episodes[0]
        lines.append(f"- Recent thread to keep in mind: {latest.get('summary', '')}")

    lines.append("")
    return lines


def generate_cli_instructions(
    skill_body: str,
    tenant_name: str,
    user_name: str,
    channel: str,
    conversation_summary: str,
    memory_context: Dict[str, Any],
    agent_slug: str = "luna",
    tier: str = "full",
    connected_integrations: list | None = None,
) -> str:
    """Generate provider-neutral instruction markdown from agent skill + tenant context."""
    limits = TIER_LIMITS.get(tier, TIER_LIMITS["full"])
    lines: list[str] = []

    lines.append("# CRITICAL RULES")
    lines.append("")
    lines.append(f"Your tenant_id is: {tenant_name}")
    lines.append(f"When calling ANY MCP tool, ALWAYS pass tenant_id=\"{tenant_name}\".")
    lines.append("MCP tool names in skill descriptions may omit the server prefix for readability.")
    lines.append("Always call tools using their FULL registered name as shown in your tools list.")
    lines.append("Prefix rules — verified from production logs (2026-04-25):")
    lines.append("- Gemini CLI registers MCP tools as `mcp_agentprovision_<tool_name>` (single underscore between mcp/server/tool).")
    lines.append("- Claude Code registers them as `mcp__agentprovision__<tool_name>` (double underscore).")
    lines.append("If a tool described as `foo_bar` returns `not found`, retry with `mcp_agentprovision_foo_bar` (Gemini) or `mcp__agentprovision__foo_bar` (Claude Code). Never retry with `default_api:foo_bar` — that namespace does not exist on this platform.")
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
    lines.append("")
    lines.append("## Uncertainty Signaling (Gap 4)")
    lines.append("Apply calibrated confidence in every response:")
    lines.append("- If you KNOW something from tools/data: state it directly.")
    lines.append("- If you're INFERRING or GUESSING: say 'I think...', 'This might be...', or 'Worth verifying, but...'")
    lines.append("- If something is TIME-SENSITIVE (prices, availability, live data): always flag it as potentially stale.")
    lines.append("- NEVER present a guess as a fact. One clear hedge is enough — don't over-qualify every sentence.")
    lines.append(f"You are {agent_slug}, an AI agent with full access to email, calendar, knowledge graph, Jira, and code tools.")
    lines.append("")

    # Connected integrations — load-bearing context. Without this, the
    # agent has no positive signal that gmail / calendar / github are
    # actually wired up for THIS tenant, so it defaults to "let me have
    # you re-authorize" / "I don't have permission to access X" even
    # when credentials exist (2026-05-04 incident: Luna kept asking the
    # user to reconnect Gmail despite 204 active gmail credentials in
    # integration_credentials).
    if connected_integrations:
        lines.append("## Connected Integrations")
        lines.append("These tenant integrations are CONNECTED and READY TO USE. Do not ask the user to (re)authorize unless a tool call actually fails with an auth error:")
        # De-duplicate by (integration_name, account_email) so multi-credential rows don't spam the list.
        seen: set = set()
        for ci in connected_integrations:
            if not isinstance(ci, dict):
                continue
            name = ci.get("integration_name") or ci.get("name")
            email = ci.get("account_email") or ci.get("email") or ""
            enabled = ci.get("enabled", True)
            if not name or not enabled:
                continue
            key = (name, email)
            if key in seen:
                continue
            seen.add(key)
            if email:
                lines.append(f"- **{name}** — account: `{email}`")
            else:
                lines.append(f"- **{name}**")
        lines.append("")
        lines.append("If the user references one of these accounts, proceed directly with the tool call. Treat absence from this list as truly missing (then it is appropriate to ask the user to connect it).")
        lines.append("")

    lines.append(ANTI_HALLUCINATION_PREAMBLE)
    lines.append("")

    # Gap 5: Temporal awareness (local time, active hours, last seen)
    temporal_ctx = memory_context.get("temporal_context", "")
    if temporal_ctx:
        lines.append(temporal_ctx)
        lines.append("")


    lines.append("# Agent Instructions")
    lines.append("")
    lines.append(skill_body.strip())
    lines.append("")

    if conversation_summary:
        # Pass the full chat history through. The upstream builder in
        # chat.py:_generate_agentic_response (~line 285) caps to 50,000 chars
        # / ~12K tokens of newest-first messages BEFORE this point. Slicing
        # again here was a long-standing bug — the previous `[-1500:]` cut
        # the budget to ~3% (3-5 short messages) and caused turn-to-turn
        # context loss in long WhatsApp conversations.
        lines.append("# Conversation History")
        lines.append("")
        lines.append(conversation_summary.strip())
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
        lines.append("## Recent Episode Summaries")
        lines.append("")
        for ep in episodes:
            date = ep.get("date", "")
            lines.append(f"- {date}: {ep.get('summary', '')}")
        lines.append("")

    past_cv = memory_context.get("past_conversations", [])
    if past_cv:
        lines.append("## Relevant Past Message Excerpts")
        lines.append("")
        for cv in past_cv:
            date = cv.get("date", "")
            role = cv.get("role", "user").upper()
            content = cv.get("content", "")
            lines.append(f"[{date}] {role}: {content}")
        lines.append("")

    commitments = memory_context.get("commitments", [])
    if commitments:
        lines.append("## Pending Commitments")
        lines.append("These are promises you have made that are still open:")
        lines.append("")
        for c in commitments:
            lines.append(f"- **{c['title']}** (State: {c['state']}, Due: {c['due_at']}, Priority: {c['priority']})")
        lines.append("")

    goals = memory_context.get("goals", [])
    if goals:
        lines.append("## Active Goals")
        lines.append("These are your current high-level objectives:")
        lines.append("")
        for g in goals:
            lines.append(f"- **{g['title']}** (State: {g['state']}, Progress: {g['progress']}%, Priority: {g['priority']})")
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

    lines.extend(
        _build_today_briefing(
            memory_context,
            include_goals=limits["include_goals"],
            include_commitments=limits["include_commitments"],
        )
    )

    if identity_context:
        lines.append(identity_context)
        lines.append("")

    if limits["include_goals"] and active_goals:
        lines.append("## Active Goals")
        lines.append("")
        for g in active_goals:
            state_tag = f"[{g.get('state', 'active')}]"
            priority = g.get("priority", "")
            lines.append(f"- {state_tag} **{g.get('title', '')}** (priority: {priority})")
            if g.get("progress_summary"):
                lines.append(f"  Progress: {g['progress_summary']}")
        lines.append("")

    if limits["include_commitments"] and open_commitments:
        lines.append("## Open Commitments")
        lines.append("")
        for c in open_commitments:
            due = f" (due: {c['due_at'][:10]})" if c.get("due_at") else ""
            lines.append(f"- **{c.get('title', '')}**{due}")
        lines.append("")

    # Brain gap context injection (Gap 1 morning briefing, Gap 2 behavioral,
    # Gap 3 stakes) removed. These blocks were hardcoded to always inject into
    # every system prompt, polluting context with topics Luna had already
    # resolved. Memory recall, commitment awareness, and behavioral learning
    # should flow through the existing RL/memory/knowledge-graph pipeline
    # (semantic recall via embeddings, tool-based commitment creation),
    # not via hardcoded text injection on every message.

    # World model context: current state, unstable assumptions, causal patterns
    world_model = memory_context.get("world_model", {})
    state_context = world_model.get("state_context")
    unstable_assertions = world_model.get("unstable_assertions", [])
    causal_patterns = world_model.get("causal_patterns", [])

    if limits["include_world_state"] and state_context:
        lines.append("## Current World State")
        lines.append("")
        lines.append(state_context)
        lines.append("")

    if limits["include_world_state"] and unstable_assertions:
        lines.append("## Assumptions Needing Verification")
        lines.append("")
        lines.append("These facts have low confidence or are aging. Verify before relying on them:")
        for ua in unstable_assertions:
            lines.append(f"- **{ua['subject']}.{ua['attribute']}** = {ua['value']} (confidence: {ua['confidence']})")
        lines.append("")

    if limits["include_world_state"] and causal_patterns:
        lines.append("## Known Causal Patterns")
        lines.append("")
        lines.append("Actions and their observed outcomes (use for planning):")
        for cp in causal_patterns:
            lines.append(f"- {cp['cause']} → {cp['effect']} (confidence: {cp['confidence']}, seen {cp['observations']}x)")
        lines.append("")

    return "\n".join(lines)


def generate_mcp_config(
    tenant_id: str,
    internal_key: str,
    db: Session = None,
    user_id: Optional[str] = None,
    agent_token: Optional[str] = None,
) -> dict:
    """Generate MCP config JSON for a CLI session.

    Includes the built-in AgentProvision MCP server plus any external MCP servers
    connected to this tenant via MCPServerConnector. ``user_id`` is forwarded
    as ``X-User-Id`` so chat-side tools that mutate skills/agents can attribute
    the change to the actor.

    Phase 4: when ``agent_token`` is provided (resilient-executor flag ON),
    we add an ``Authorization: Bearer <agent_token>`` header to the
    ``agentprovision`` server entry so the leaf authenticates via the
    third auth tier (agent-token) rather than relying on X-Internal-Key.
    The internal key + tenant header are still set for backward compat
    during the cutover; the MCP server precedence rule in
    ``mcp_auth.resolve_auth_context`` makes agent_token authoritative.
    """
    # Helm sets MCP_TOOLS_URL explicitly; docker-compose only sets
    # MCP_SERVER_URL — fall back to that so the default works in both.
    mcp_tools_url = os.environ.get(
        "MCP_TOOLS_URL",
        os.environ.get("MCP_SERVER_URL", "http://mcp-tools:8086"),
    )
    mcp_url = f"{mcp_tools_url}/sse"

    headers = {
        "X-Internal-Key": internal_key,
        "X-Tenant-Id": str(tenant_id),
    }
    if user_id:
        headers["X-User-Id"] = str(user_id)
    if agent_token:
        # Phase 4 — third auth tier. Server-side
        # ``mcp_auth.resolve_auth_context`` gives this precedence over
        # the X-Tenant-Id header + X-Internal-Key.
        headers["Authorization"] = f"Bearer {agent_token}"

    config = {
        "mcpServers": {
            "agentprovision": {
                # FastMCP runs in SSE mode (see apps/mcp-server/src/mcp_serve.py)
                # because gemini-cli's HTTP MCP client doesn't negotiate the
                # streamable-http Accept header correctly.
                "type": "sse",
                "url": mcp_url,
                "headers": headers,
            }
        }
    }

    # Inject tenant's external MCP server connectors
    if db:
        try:
            connectors = (
                db.query(MCPServerConnector)
                .filter(
                    MCPServerConnector.tenant_id == tenant_id,
                    MCPServerConnector.status == "connected",
                    MCPServerConnector.enabled.is_(True),
                )
                .all()
            )
            # Map connector transport to CLI config type
            transport_map = {"streamable-http": "http", "sse": "sse"}

            for conn in connectors:
                # Skip stdio connectors — they need command config, not URL
                if conn.transport == "stdio":
                    logger.warning("Skipping stdio connector '%s' — not supported in CLI MCP config", conn.name)
                    continue

                server_entry = {
                    "type": transport_map.get(conn.transport, "http"),
                    "url": conn.server_url,
                }
                # Add auth headers — mirror the connector service's auth behavior
                headers = {}
                if conn.auth_type == "bearer" and conn.auth_token:
                    header_name = conn.auth_header or "Authorization"
                    headers[header_name] = f"Bearer {conn.auth_token}"
                elif conn.auth_type == "api_key" and conn.auth_token:
                    header_name = conn.auth_header or "X-API-Key"
                    headers[header_name] = conn.auth_token
                elif conn.auth_type == "basic" and conn.auth_token:
                    header_name = conn.auth_header or "Authorization"
                    headers[header_name] = f"Basic {conn.auth_token}"
                if conn.custom_headers:
                    headers.update(conn.custom_headers)
                if headers:
                    server_entry["headers"] = headers

                # Use connector name as the MCP server key (slugified)
                server_key = conn.name.lower().replace(" ", "-").replace("_", "-")
                config["mcpServers"][server_key] = server_entry
                logger.info("Injected external MCP server '%s' (%s) for tenant %s", conn.name, conn.server_url, str(tenant_id)[:8])
        except Exception as e:
            safe_rollback(db)
            logger.warning("Failed to load tenant MCP connectors: %s", e, exc_info=True)

    return config


def _get_cli_platform_credentials(
    db: Session,
    tenant_id: uuid.UUID,
    integration_name: str,
) -> Dict[str, Any]:
    """Retrieve active credentials for a tenant CLI platform.
    
    Falls back to other compatible Google integrations for gemini_cli
    if the primary one is missing or has inactive credentials.
    """
    from app.models.integration_credential import IntegrationCredential

    search_names = [integration_name]
    if integration_name == "gemini_cli":
        search_names.extend(["gmail", "google_drive", "google_calendar"])
    elif integration_name == "copilot_cli":
        # Copilot CLI authenticates with the same GitHub OAuth token the
        # `github` integration stores. Tenants don't (and can't) connect a
        # standalone "copilot_cli" provider — they connect github once and
        # the Copilot subscription rides the same OAuth token via
        # COPILOT_GITHUB_TOKEN at runtime (see code-worker workflows.py).
        search_names.append("github")

    for name in search_names:
        config = (
            db.query(IntegrationConfig)
            .filter(
                IntegrationConfig.tenant_id == tenant_id,
                IntegrationConfig.integration_name == name,
                IntegrationConfig.enabled.is_(True),
            )
            .first()
        )
        if not config:
            continue

        # Check if this config has at least one active credential
        active_cred = db.query(IntegrationCredential).filter(
            IntegrationCredential.integration_config_id == config.id,
            IntegrationCredential.status == "active"
        ).first()
        
        if not active_cred:
            logger.debug("Integration %s found for tenant %s but has no active credentials", name, tenant_id)
            continue

        creds = retrieve_credentials_for_skill(db, config.id, tenant_id)
        if creds:
            return creds

    logger.warning("No active %s (or compatible) integration with credentials for tenant %s", integration_name, tenant_id)
    return {}


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
    agent_tier: str = "full",
    agent_tool_groups: list = None,
    agent_memory_domains: list = None,
    agent_skill_slugs: list = None,
    attempt: int = 1,
) -> Tuple[Optional[str], Dict]:
    """Public entry — runs the legacy session and fires shadow comparison.

    Phase 2 cutover: when ``tenant_features.use_resilient_executor`` is
    FALSE (default), this delegates to the byte-identical legacy
    implementation in ``_run_agent_session_legacy`` and then fires
    ``maybe_run_shadow`` against a stubbed ResilientExecutor that
    replays the legacy outcome (no real second dispatch). When the
    flag is TRUE, ``agent_router`` builds an ExecutionRequest directly
    and calls ResilientExecutor.execute(req) — this function is not
    on that path. Either way, shadow can NEVER poison the response
    (try/except around the call).
    """
    response_text, metadata = _run_agent_session_legacy(
        db, tenant_id=tenant_id, user_id=user_id,
        platform=platform, agent_slug=agent_slug,
        message=message, channel=channel,
        sender_phone=sender_phone, conversation_summary=conversation_summary,
        image_b64=image_b64, image_mime=image_mime,
        db_session_memory=db_session_memory,
        pre_built_memory_context=pre_built_memory_context,
        agent_tier=agent_tier, agent_tool_groups=agent_tool_groups,
        agent_memory_domains=agent_memory_domains,
        agent_skill_slugs=agent_skill_slugs,
        attempt=attempt,
    )

    # Phase 2 shadow comparison — wrapped in try/except so the shadow
    # path can never poison the response. Lazy-imported to avoid a
    # cycle at module load.
    try:
        from app.services.cli_orchestrator_shadow import maybe_run_shadow
        maybe_run_shadow(
            db=db,
            tenant_id=tenant_id,
            platform=platform,
            response_text=response_text,
            metadata=metadata,
        )
    except BaseException:  # noqa: BLE001
        logger.debug("shadow comparison wrapper swallowed exception", exc_info=True)

    return response_text, metadata


def _run_agent_session_legacy(
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
    agent_tier: str = "full",
    agent_tool_groups: list = None,
    agent_memory_domains: list = None,
    agent_skill_slugs: list = None,
    attempt: int = 1,
) -> Tuple[Optional[str], Dict]:
    """Legacy run_agent_session — body unchanged from Phase 1.6.

    Renamed from ``run_agent_session`` to ``_run_agent_session_legacy``
    in Phase 2 step 8 so the public ``run_agent_session`` can wrap the
    legacy outcome with a shadow-comparison fire. The body of THIS
    function is byte-identical to the prior public function — every
    return path, every variable name, every log message, every guard
    is preserved.

    See ``run_agent_session`` above for the cutover wrapping.

    `agent_skill_slugs` is the ordered list of skill slugs to compose into
    CLAUDE.md. The first element is the agent's identity skill (its body
    drives persona); subsequent elements are additional capability bundles
    appended after the identity body. When None, falls back to
    `[agent_slug]` for legacy compatibility.
    """
    # Phase A.1 of the latency reduction plan: per-stage timers persisted
    # into ``metadata['timings']`` so the chat layer's ExecutionTrace gets
    # them under ``details``. The bench script reads them from there.
    import time as _stage_time
    _stage_t0 = _stage_time.monotonic()
    timings: Dict[str, int] = {}
    def _mark(stage: str) -> None:
        nonlocal _stage_t0
        now = _stage_time.monotonic()
        timings[stage] = int((now - _stage_t0) * 1000)
        _stage_t0 = now

    metadata: Dict[str, Any] = {
        "platform": platform,
        "agent_slug": agent_slug,
        "tenant_id": str(tenant_id),
        "user_id": str(user_id),
        "channel": channel,
        "error": None,
        "timings": timings,
    }

    if platform not in SUPPORTED_CLI_PLATFORMS:
        err = f"Unsupported CLI platform '{platform}'"
        logger.error(err)
        metadata["error"] = err
        return None, metadata

    _mark("setup")

    primary_slug = resolve_primary_agent_slug(db, tenant_id)
    skill = skill_manager.get_skill_by_slug(agent_slug, str(tenant_id))
    if not skill and agent_slug != primary_slug:
        logger.info("Skill '%s' not found, falling back to primary '%s'", agent_slug, primary_slug)
        skill = skill_manager.get_skill_by_slug(primary_slug, str(tenant_id))
        if skill:
            agent_slug = primary_slug
    if not skill:
        err = f"No agent skill found (tried '{agent_slug}' and '{primary_slug}')"
        logger.error(err)
        metadata["error"] = err
        return None, metadata

    skill_body = skill.description or ""

    # Compose additional skill bodies (PR2). The identity slug already
    # provided `skill_body`; append the rest of the declared list with a
    # clear section header so the model can tell them apart.
    #
    # Dedup against the identity slug AND any earlier appearance — a
    # config like `skills: [luna, calculator, calculator]` composes
    # calculator exactly once.
    composed_slugs = list(agent_skill_slugs) if agent_skill_slugs else [agent_slug]
    seen: set[str] = {agent_slug}
    extra_slugs: list[str] = []
    for s in composed_slugs[1:]:
        if not s or s in seen:
            continue
        seen.add(s)
        extra_slugs.append(s)
    if extra_slugs:
        appended_bodies: list[str] = []
        appended_slugs: list[str] = []
        for extra_slug in extra_slugs:
            extra_skill = skill_manager.get_skill_by_slug(extra_slug, str(tenant_id))
            if not extra_skill or not extra_skill.description:
                logger.warning(
                    "Composed skill '%s' not found / empty for tenant %s — skipping",
                    extra_slug, str(tenant_id)[:8],
                )
                continue
            appended_bodies.append(
                f"\n\n## Additional Skill: {extra_skill.name}\n\n{extra_skill.description.strip()}"
            )
            appended_slugs.append(extra_slug)
        if appended_bodies:
            skill_body = (skill_body or "").rstrip() + "".join(appended_bodies)
            logger.info(
                "Composed %d additional skills onto identity %s: %s",
                len(appended_bodies), agent_slug, ", ".join(appended_slugs),
            )
    metadata["composed_skills"] = composed_slugs

    # OpenCode uses local Gemma 4 — no credentials needed
    if platform == "opencode":
        credentials = {}
        subscription_missing = False
    else:
        credentials = _get_cli_platform_credentials(db, tenant_id, platform)
    session_token = credentials.get("session_token")
    auth_json = credentials.get("auth_json")

    oauth_token = credentials.get("oauth_token")
    # claude_code supports two credential shapes: OAuth `session_token`
    # (from the `/start` + `/submit-code` flow) and Anthropic Console
    # `api_key` (from `/api-key`). Either is sufficient — the downstream
    # CLI invocation reads whichever is active. Without this branch the
    # API-key path stores a credential the gate refuses to see and we
    # fall back to the local agent regardless.
    claude_api_key = credentials.get("api_key")
    if platform != "opencode":
        subscription_missing = (
            (platform == "claude_code" and not (session_token or claude_api_key))
            or (platform == "codex" and not (session_token or auth_json))
            or (platform == "gemini_cli" and not (oauth_token or session_token))
            # Copilot CLI authenticates with the GitHub OAuth token (gho_…)
            # exposed via COPILOT_GITHUB_TOKEN in code-worker. Without it
            # the CLI runs anonymously and Copilot replies with auth errors.
            or (platform == "copilot_cli" and not oauth_token)
        )
    else:
        subscription_missing = False
    if subscription_missing:
        logger.warning(
            "No %s credential for tenant %s — falling back to local agent",
            platform, tenant_id,
        )
        _mark("cli_credentials_missing")
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
            _mark("local_tool_agent")
            if tool_response:
                # Preserve our timings dict on metadata.update.
                meta_timings = tool_meta.pop("timings", None) if isinstance(tool_meta, dict) else None
                metadata.update(tool_meta)
                if meta_timings:
                    metadata.setdefault("timings", {}).update(meta_timings)
                else:
                    metadata["timings"] = timings
                return tool_response, metadata
            logger.info("Local tool agent returned no response — falling back to plain text")
        except Exception as exc:
            # Roll back so the IntegrationConfig query above (or any DB work
            # local_tool_agent did) cannot poison the session for the plain-text
            # fallback below or the caller's downstream commits. See PR #349 —
            # the missing rollback on a sibling handler is what cascaded into
            # InFailedSqlTransaction across an entire FastAPI request.
            safe_rollback(db)
            logger.warning("Local tool agent failed: %s — falling back to plain text", exc)

        # 2. Fall back to plain text response (no tools)
        from app.services.local_inference import generate_agent_response_sync
        local_response = generate_agent_response_sync(
            message=message,
            conversation_summary=conversation_summary,
            skill_body=skill_body,
            agent_slug=agent_slug,
        )
        _mark("local_inference_plain")
        if local_response:
            metadata["platform"] = "local_gemma"
            metadata["fallback"] = True
            metadata["timings"] = timings
            return local_response, metadata

        # 3. Friendly error
        platform_label = {
            "claude_code": "Claude Code",
            "codex": "Codex",
            "gemini_cli": "Gemini CLI",
            "copilot_cli": "GitHub Copilot CLI",
            "qwen_code": "Qwen Code",
        }.get(platform, platform)
        err = (
            f"{platform_label} subscription is not connected "
            "and the local model is unavailable. Please connect your account in Settings → Integrations."
        )
        metadata["error"] = err
        return None, metadata

    # Extract session entity names from prior turns for recall boosting
    session_entity_names = (db_session_memory or {}).get("recalled_entity_names")

    limits = TIER_LIMITS.get(agent_tier, TIER_LIMITS["full"])

    _mark("skill_compose")

    if pre_built_memory_context is not None:
        memory_context = pre_built_memory_context
    else:
        logger.warning(
            "Memory context not pre-built by router — rebuilding (should not happen in normal flow) tenant=%s",
            str(tenant_id)[:8],
        )
        try:
            memory_context = build_memory_context_with_git(
                db=db,
                tenant_id=tenant_id,
                user_message=message,
                session_entity_names=session_entity_names,
                domains=agent_memory_domains,
                max_entities=limits["entities"],
                max_observations=limits["observations_per_entity"],
                include_relations=limits["include_relations"],
                include_episodes=limits["include_episodes"],
            )
        except Exception as exc:
            logger.warning("Memory recall failed for tenant %s: %s", tenant_id, exc)
            # Critical: rollback. build_memory_context_with_git runs queries
            # against pgvector + entity tables; when any of them fail (e.g.
            # earlier txn already aborted, or an asyncpg type-coerce error
            # on a NULL column), psycopg2 leaves the session in a poisoned
            # state. Without this rollback, the very next ORM query in this
            # dispatch (mcp_server_connectors load at chat_cli build, or
            # the agent-token / cli session lookups) cascades into
            # psycopg2.errors.InFailedSqlTransaction. Sister fix to PRs #352
            # (dispatch-level rollback) and #361 (chat-side rollback) — the
            # rebuild fallback was the last unguarded catch site.
            safe_rollback(db)
            memory_context = {}

    _mark("memory_recall")

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
        # Self-model queries hit agent_identities / goals / commitments. Any
        # failure (NULL columns, missing rows, sibling-handler poison) leaves
        # psycopg2's txn aborted. Without rollback, the *next* block (world
        # model) re-poisons it after every catch site, and generate_mcp_config
        # at line 506 cascades. Watchdog caught this 2026-05-11 — caught + fixed
        # in PR alongside PR #363, which patched only the user_obj lookup below.
        safe_rollback(db)

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
        # See safe_rollback rationale on the self-model handler above —
        # world_state + causal_edge queries hit the same poisonable txn.
        safe_rollback(db)

    # Thread recalled entity names into metadata for session memory persistence
    recalled_names = memory_context.get("recalled_entity_names", [])
    if recalled_names:
        metadata["recalled_entity_names"] = recalled_names

    # Gap 5: Inject temporal awareness context (user's local time, active hours, last seen)
    try:
        from app.services.temporal_awareness import build_temporal_system_context
        temporal_ctx = build_temporal_system_context(db, tenant_id=tenant_id, user_id=user_id)
        if temporal_ctx:
            memory_context["temporal_context"] = temporal_ctx
    except Exception as exc:
        logger.debug("Temporal context injection failed: %s", exc)
        # Last unguarded handler in the memory-context pipeline. After this,
        # the explicit pre-clear below ran for the user_obj lookup but a
        # poisoned txn from here would still cascade because the comment
        # at "Clear any poisoned DB state" assumes a clean slate it didn't
        # actually have. Closes the same class as the self/world rollback fix.
        safe_rollback(db)

    # Clear any poisoned DB state before we start querying
    safe_rollback(db)

    tenant_name = str(tenant_id)
    # Resolve actual user name from DB instead of passing a UUID
    user_name = sender_phone
    if not user_name:
        try:
            from app.models.user import User
            user_obj = db.query(User).filter(User.id == user_id).first()
            user_name = (user_obj.full_name if user_obj and user_obj.full_name else None) or str(user_id)
        except Exception:
            safe_rollback(db)
            user_name = str(user_id)
    # Pull the tenant's connected integrations so the agent's CLAUDE.md
    # carries an explicit "## Connected Integrations" section. Without
    # this, models default to asking the user to (re)authorize gmail /
    # calendar / github even when credentials are present (the 2026-05-04
    # symptom that prompted this fix). Exception-tolerant: if the query
    # fails, we fall back to the generic prompt rather than crash chat.
    connected_integrations: list = []
    try:
        rows = (
            db.query(
                IntegrationConfig.integration_name,
                IntegrationConfig.account_email,
                IntegrationConfig.enabled,
            )
            .filter(
                IntegrationConfig.tenant_id == tenant_id,
                IntegrationConfig.enabled.is_(True),
            )
            .all()
        )
        connected_integrations = [
            {"integration_name": r[0], "account_email": r[1], "enabled": r[2]}
            for r in rows
        ]
    except Exception:
        safe_rollback(db)
        connected_integrations = []

    instruction_md_content = generate_cli_instructions(
        skill_body=skill_body,
        tenant_name=tenant_name,
        user_name=user_name,
        channel=channel,
        conversation_summary=conversation_summary,
        memory_context=memory_context,
        agent_slug=agent_slug,
        tier=agent_tier,
        connected_integrations=connected_integrations,
    )

    # Phase 1 PR C — emotion engine prompt-side style injection.
    # Best-effort: read the session's most recent affect_vector and append
    # a short addendum. Returns "" when no affect is recorded or state is
    # neutral, so the prompt is byte-identical in those cases.
    try:
        from app.services.emotion_engine_io import build_affect_addendum_for_session

        affect_session_id_str = (
            (db_session_memory or {}).get("chat_session_id", "") or ""
        )
        affect_session_id = (
            uuid.UUID(affect_session_id_str) if affect_session_id_str else None
        )
        affect_addendum = build_affect_addendum_for_session(
            db,
            session_id=affect_session_id,
            tenant_id=tenant_id,
        )
        if affect_addendum:
            instruction_md_content = instruction_md_content + affect_addendum
    except Exception:
        # NEVER let the emotion layer break prompt assembly.
        pass

    internal_key = settings.MCP_API_KEY or "dev_mcp_key"

    # ── Phase 4 commit 3 — agent-token mint (gated by resilient flag) ──
    # When use_resilient_executor is TRUE, mint an agent-scoped JWT and
    # plumb it through generate_mcp_config so the leaf authenticates via
    # the third auth tier on apps/mcp-server. When FALSE (the default
    # during cutover), agent_token is None and behavior is byte-identical
    # to Phase 3 — the leaf still uses X-Internal-Key + X-Tenant-Id.
    agent_token: Optional[str] = None
    try:
        from app.services.cli_orchestrator_shadow import read_flags
        use_resilient, _ = read_flags(db, tenant_id)
    except Exception:  # noqa: BLE001
        use_resilient = False

    if use_resilient:
        try:
            from app.models.agent import Agent
            from app.services.agent_token import mint_agent_token

            # `resolve_tool_names` is already imported at module scope (line 20).
            # Re-importing it here turned it into a function-local in the parent
            # `dispatch_chat_cli` scope, which then shadowed the module global
            # for the nested `_run_workflow` closure further down. When
            # `use_resilient` was False, the local was never bound and the
            # closure lookup raised:
            #   NameError: cannot access free variable 'resolve_tool_names'
            #   where it is not associated with a value in enclosing scope
            # — which silently aborted ChatCliWorkflow dispatch for every
            # tenant where the resilient flag wasn't set (Luna in WhatsApp,
            # most notably). Don't re-import.

            # Agent has no `slug` column; the chat hot path passes a slug
            # form like "luna". Match case-insensitively against Agent.name
            # which is the closest analogue (display name).
            from sqlalchemy import func as _sa_func

            agent_row = (
                db.query(Agent)
                .filter(
                    Agent.tenant_id == tenant_id,
                    _sa_func.lower(Agent.name) == agent_slug.lower(),
                )
                .first()
            )
            if agent_row is not None:
                # Scope claim from agent.tool_groups (per plan correction
                # #2). resolve_tool_names returns None when tool_groups
                # is None, meaning "all tools" — propagate that to the
                # claim so the server-side scope check is a no-op.
                scope = resolve_tool_names(agent_row.tool_groups)
                # task_id: the chat hot path doesn't have a persisted
                # AgentTask row (the chat workflow doesn't create one),
                # so we mint a synthetic one. The MCP server's audit
                # boundary (apps/mcp-server/src/tool_audit.py) writes
                # this id to the ``tool_calls`` table only — there is
                # no FK to agent_tasks and no execution_trace row is
                # written for chat-driven leafs in this phase.
                # Phase 4.5+ will persist a synthetic AgentTask row
                # (kind="chat") to close the audit-trace gap.
                synth_task_id = str(uuid.uuid4())
                parent_chain = tuple(
                    str(x) for x in (
                        (db_session_memory or {}).get("parent_chain") or ()
                    )
                )
                agent_token = mint_agent_token(
                    tenant_id=str(tenant_id),
                    agent_id=str(agent_row.id),
                    task_id=synth_task_id,
                    parent_workflow_id=None,  # set per-run by Temporal
                    scope=scope,
                    parent_chain=parent_chain,
                )
        except Exception as exc:  # noqa: BLE001
            # Mint failure is non-fatal — fall back to legacy auth path.
            # WARN, not DEBUG: silent fallback at INFO+ would mask JWT
            # secret rotation bugs, malformed tool_groups data, or DB
            # outages on every chat turn. Mirror worker-side severity
            # (apps/code-worker/app/workflows.py:574) — Phase 4 review C3.
            #
            # Rollback is required: the try-block ran `db.query(Agent)` and
            # `read_flags(db, ...)`. A NameError or DB hiccup mid-block
            # (this is exactly the failure mode behind PR #349) leaves the
            # session in InFailedSqlTransaction. The very next line calls
            # generate_mcp_config(..., db=db) which would otherwise cascade.
            safe_rollback(db)
            logger.warning(
                "agent_token mint failed (falling back to legacy auth): %s",
                exc, exc_info=True,
            )
            agent_token = None

    mcp_config = generate_mcp_config(
        str(tenant_id), internal_key, db=db,
        user_id=str(user_id), agent_token=agent_token,
    )

    _mark("prompt_render")

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
                model: str = ""  # model slug e.g., "claude-haiku-4-5-20251001"
                allowed_tools: str = ""  # comma-separated MCP tool names
                # Plan 2026-05-16-terminal-full-cli-output §4.1: the worker
                # needs the agentprovision chat_session_id (NOT the CLI's
                # native session_id) so it can POST stream chunks back to
                # /api/v2/internal/sessions/{id}/events. `attempt` is the
                # 1-based index in the cli chain — stamped on every chunk.
                chat_session_id: str = ""
                attempt: int = 1

            existing_session_id = (
                (db_session_memory or {}).get(f"{platform}_cli_session_id")
                or (db_session_memory or {}).get("claude_cli_session_id", "")  # legacy key
                or (db_session_memory or {}).get("cli_session_id", "")
            )

            model_slug = TIER_MODEL_MAP.get(agent_tier, {}).get(platform, "")
            tool_names = resolve_tool_names(agent_tool_groups)
            # Pass the target CLI platform so the formatter emits the
            # correct MCP namespace shape (Gemini uses single underscores,
            # everything else uses double underscores). Without this,
            # Gemini-routed turns silently filtered tools matched only by
            # `mcp__*` shape — including Higgsfield (#572 BLOCKER fix).
            allowed_tools_str = (
                format_allowed_tools(tool_names, cli_platform=platform)
                if tool_names
                else ""
            )

            chat_session_id_for_stream = str(
                (db_session_memory or {}).get("chat_session_id", "") or ""
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
                model=model_slug,
                allowed_tools=allowed_tools_str,
                chat_session_id=chat_session_id_for_stream,
                attempt=attempt,
            )

            return await client.execute_workflow(
                "ChatCliWorkflow",
                task_input,
                id=f"chat-cli-{uuid.uuid4()}",
                task_queue="agentprovision-code",
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
                # Increased timeout to 600s (10 min) to allow for complex tasks like PDF ingestion
                result = pool.submit(lambda: asyncio.run(_run_workflow())).result(timeout=600)
        else:
            # `asyncio.run` (vs manual new_event_loop/close) drains pending
            # tasks before closing — manual close was leaving httpx aclose()
            # tasks orphaned, surfacing later as
            # `RuntimeError: Event loop is closed` on GC.
            result = asyncio.run(_run_workflow())

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

        _mark("cli_dispatch")

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
                # Preserve our timings dict — meta.update would clobber it.
                meta_timings = meta.pop("timings", None)
                metadata.update(meta)
                if meta_timings:
                    metadata.setdefault("timings", {}).update(meta_timings)
                else:
                    metadata["timings"] = timings
            return response_text, metadata

        metadata["error"] = error or "CLI workflow returned empty response"
        logger.warning(
            "ChatCliWorkflow result: success=%s error=%s response_len=%s",
            success, error, len(response_text) if response_text else 0,
        )
        _record_tool_failure_affect(db, db_session_memory, tenant_id, severity=0.5)
        return None, metadata
    except Exception as exc:
        # Roll back so the caller (chat.py route_and_execute) can keep
        # running its own db.commit() calls — _append_message, ExecutionTrace
        # write, session memory_context update — without cascading into
        # InFailedSqlTransaction. This is the exact site that PR #349
        # diagnosed: a NameError raised inside the nested _run_workflow
        # closure was caught here without a rollback, which poisoned every
        # subsequent query on the same FastAPI request session for hours
        # of WhatsApp traffic.
        safe_rollback(db)
        logger.exception("ChatCliWorkflow dispatch failed")
        metadata["error"] = str(exc)
        _record_tool_failure_affect(db, db_session_memory, tenant_id, severity=1.0)
        return None, metadata


def _record_tool_failure_affect(
    db,
    db_session_memory,
    tenant_id,
    *,
    severity: float,
) -> None:
    """Emotion-engine wire-in for cli_session_manager failure paths.

    Best-effort: NEVER raises into the caller. Failures here log a debug
    line and return; the emotion layer must not break chat. Phase 2 of
    the emotions engine (see docs/plans/2026-05-19-emotions-engine-prototype-design.md).

    severity convention:
      - 0.5 = graceful failure (workflow returned empty/error). Moderate.
      - 1.0 = hard exception (workflow raised). Maximum.
    """
    try:
        from app.services.emotion_engine_io import record_session_tool_failure

        session_id_str = (db_session_memory or {}).get("chat_session_id") or ""
        if not session_id_str:
            return
        session_id = uuid.UUID(session_id_str)
        record_session_tool_failure(
            db,
            session_id=session_id,
            tenant_id=tenant_id,
            severity=severity,
        )
    except Exception:  # noqa: BLE001 — emotion layer never crashes chat
        logger.debug(
            "emotion_engine tool_failure wire-in raised; suppressed.",
            exc_info=True,
        )
