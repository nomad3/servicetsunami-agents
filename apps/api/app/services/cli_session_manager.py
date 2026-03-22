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

SUPPORTED_CLI_PLATFORMS = {"claude_code", "codex"}


def generate_cli_instructions(
    skill_body: str,
    tenant_name: str,
    user_name: str,
    channel: str,
    conversation_summary: str,
    memory_context: Dict[str, Any],
) -> str:
    """Generate provider-neutral instruction markdown from agent skill + tenant context."""
    lines: list[str] = []

    lines.append("# CRITICAL RULES")
    lines.append("")
    lines.append(f"Your tenant_id is: {tenant_name}")
    lines.append(f"When calling ANY MCP tool, ALWAYS pass tenant_id=\"{tenant_name}\".")
    lines.append(f"Session: tenant={tenant_name} user={user_name} channel={channel}")
    lines.append("")
    lines.append("## IDENTITY")
    lines.append("Your user-facing identity is Luna.")
    lines.append("The underlying execution runtime may be Codex or Claude Code, but that is not your identity.")
    lines.append("If the user asks who you are, answer that you are Luna.")
    lines.append("If the user asks about the underlying model or runtime, explain it plainly: you are Luna running on the tenant's configured CLI platform.")
    lines.append("Do not introduce yourself as Codex, Claude, Claude Code, or 'the code agent' unless the user is explicitly asking about infrastructure.")
    lines.append("")
    lines.append("## MANDATORY: Check Memory Before Every Response")
    lines.append("Before answering ANY question, you MUST call find_entities and search_knowledge.")
    lines.append("NEVER say 'I don't have information' without checking your MCP tools first.")
    lines.append("You are Luna, an AI chief of staff with full access to email, calendar, knowledge graph, Jira, and code tools.")
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

    if relevant_entities:
        lines.append("## Relevant Entities")
        lines.append("")
        for entity in relevant_entities:
            name = entity.get("name", "")
            etype = entity.get("type", "")
            desc = entity.get("description", "")
            summary = f"**{name}** ({etype})"
            if desc:
                summary += f": {desc}"
            lines.append(f"- {summary}")
        lines.append("")

    if relevant_memories:
        lines.append("## Relevant Memories")
        lines.append("")
        for memory in relevant_memories:
            mtype = memory.get("type", "")
            content = memory.get("content", "")
            lines.append(f"- [{mtype}] {content}")
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

    subscription_missing = (
        (platform == "claude_code" and not session_token)
        or (platform == "codex" and not (session_token or auth_json))
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
        err = (
            f"{'Claude Code' if platform == 'claude_code' else 'Codex'} subscription is not connected "
            "and the local model is unavailable. Please connect your account in Settings → Integrations."
        )
        metadata["error"] = err
        return None, metadata

    if pre_built_memory_context is not None:
        memory_context = pre_built_memory_context
    else:
        try:
            memory_context = build_memory_context_with_git(db, tenant_id, message)
        except Exception as exc:
            logger.warning("Memory recall failed for tenant %s: %s", tenant_id, exc)
            memory_context = {}

    tenant_name = str(tenant_id)
    user_name = sender_phone or str(user_id)
    instruction_md_content = generate_cli_instructions(
        skill_body=skill_body,
        tenant_name=tenant_name,
        user_name=user_name,
        channel=channel,
        conversation_summary=conversation_summary,
        memory_context=memory_context,
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
                execution_timeout=timedelta(minutes=45),
            )

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
