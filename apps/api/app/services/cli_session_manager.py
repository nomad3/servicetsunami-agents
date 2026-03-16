"""CLI Session Manager — handles full lifecycle of a CLI agent session.

Loads skill, generates CLAUDE.md, generates MCP config, invokes Claude Code CLI
subprocess, parses response. Each call is stateless — no session persistence.
"""

import json
import logging
import os
import shutil
import subprocess
import tempfile
import uuid
from datetime import timedelta
from typing import Any, Dict, Optional, Tuple

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.integration_config import IntegrationConfig
from app.services.memory_recall import build_memory_context
from app.services.orchestration.credential_vault import retrieve_credentials_for_skill
from app.services.skill_manager import skill_manager

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# generate_claude_md
# ---------------------------------------------------------------------------

def generate_claude_md(
    skill_body: str,
    tenant_name: str,
    user_name: str,
    channel: str,
    conversation_summary: str,
    memory_context: Dict[str, Any],
) -> str:
    """Generate CLAUDE.md content from agent skill body + tenant context.

    The resulting markdown is written to the CLI session directory so Claude
    Code picks it up automatically as project-level instructions.
    """
    lines: list[str] = []

    lines.append("# Agent Instructions")
    lines.append("")
    lines.append(skill_body.strip())
    lines.append("")

    # Tenant context section
    lines.append("## Session Context")
    lines.append("")
    lines.append(f"- **Tenant:** {tenant_name}")
    lines.append(f"- **User:** {user_name}")
    lines.append(f"- **Channel:** {channel}")
    lines.append("")

    if conversation_summary:
        lines.append("## Conversation Summary")
        lines.append("")
        lines.append(conversation_summary.strip())
        lines.append("")

    # Memory context — entities, memories, relations
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

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# generate_mcp_config
# ---------------------------------------------------------------------------

def generate_mcp_config(tenant_id: str, internal_key: str) -> dict:
    """Generate MCP config JSON for a CLI session.

    Points to MCP_SERVER_URL/mcp with tenant authentication headers so the
    Claude Code CLI can reach ServiceTsunami's MCP tools.
    """
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


# ---------------------------------------------------------------------------
# invoke_claude_cli
# ---------------------------------------------------------------------------

def invoke_claude_cli(
    message: str,
    session_dir: str,
    oauth_token: str,
    timeout: int = 120,
) -> Tuple[Optional[str], Dict]:
    """Invoke Claude Code CLI as a stateless subprocess.

    Args:
        message: The user message / prompt to pass to the CLI.
        session_dir: Temporary directory containing CLAUDE.md and optionally mcp.json.
        oauth_token: Tenant's Claude Code OAuth session token.
        timeout: Subprocess timeout in seconds.

    Returns:
        Tuple of (response_text, metadata).
        response_text is None on failure; metadata contains token usage and
        any error information.
    """
    metadata: Dict[str, Any] = {
        "input_tokens": 0,
        "output_tokens": 0,
        "model": None,
        "error": None,
        "exit_code": None,
    }

    # Build the command
    cmd = [
        "claude",
        "-p", message,
        "--output-format", "json",
        "--project-dir", session_dir,
    ]

    # Add MCP config if present
    mcp_config_path = os.path.join(session_dir, "mcp.json")
    if os.path.exists(mcp_config_path):
        cmd.extend(["--mcp-config", mcp_config_path])

    env = {**os.environ, "CLAUDE_CODE_OAUTH_TOKEN": oauth_token}

    logger.info(
        "Invoking Claude CLI with project-dir=%s, timeout=%ds",
        session_dir, timeout,
    )

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except FileNotFoundError:
        err = "Claude Code CLI not found — ensure 'claude' is installed and on PATH"
        logger.error(err)
        metadata["error"] = err
        return None, metadata
    except subprocess.TimeoutExpired:
        err = f"Claude Code CLI timed out after {timeout}s"
        logger.error(err)
        metadata["error"] = err
        return None, metadata

    metadata["exit_code"] = result.returncode

    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()[:2000]
        logger.error(
            "Claude CLI exited with code %d: %s",
            result.returncode, err,
        )
        metadata["error"] = f"CLI exited with code {result.returncode}: {err}"
        return None, metadata

    raw_output = result.stdout.strip()
    if not raw_output:
        metadata["error"] = "Claude CLI produced no output"
        return None, metadata

    # Parse JSON output
    try:
        data = json.loads(raw_output)
    except json.JSONDecodeError as exc:
        logger.warning(
            "Failed to parse Claude CLI JSON output (%s); treating as plain text", exc
        )
        # Return raw stdout as-is if it can't be parsed
        return raw_output, metadata

    # Extract response text — Claude Code JSON output has a "result" field
    response_text = data.get("result") or data.get("response") or data.get("content")
    if not response_text and isinstance(data, dict):
        # Fallback: try to find any string value that looks like a response
        for key in ("text", "message", "output"):
            if isinstance(data.get(key), str):
                response_text = data[key]
                break

    # Extract usage metadata if present
    usage = data.get("usage") or {}
    if isinstance(usage, dict):
        metadata["input_tokens"] = usage.get("input_tokens", 0)
        metadata["output_tokens"] = usage.get("output_tokens", 0)

    metadata["model"] = data.get("model")

    if not response_text:
        # No recognisable response field — return the raw JSON string so callers
        # can decide what to do with it
        response_text = raw_output

    return response_text, metadata


# ---------------------------------------------------------------------------
# run_agent_session
# ---------------------------------------------------------------------------

def _get_claude_code_token(db: Session, tenant_id: uuid.UUID) -> Optional[str]:
    """Retrieve the tenant's Claude Code session token from the credential vault."""
    config = (
        db.query(IntegrationConfig)
        .filter(
            IntegrationConfig.tenant_id == tenant_id,
            IntegrationConfig.integration_name == "claude_code",
            IntegrationConfig.enabled.is_(True),
        )
        .first()
    )
    if not config:
        logger.warning(
            "No active claude_code integration config for tenant %s", tenant_id
        )
        return None

    creds = retrieve_credentials_for_skill(db, config.id, tenant_id)
    token = creds.get("session_token")
    if not token:
        logger.warning(
            "No session_token credential found for claude_code integration, tenant %s",
            tenant_id,
        )
    return token


def run_agent_session(
    db: Session,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    agent_slug: str,
    message: str,
    channel: str,
    sender_phone: Optional[str],
    conversation_summary: str,
) -> Tuple[Optional[str], Dict]:
    """Run a full stateless CLI agent session.

    Steps:
    1. Load agent skill from skill_manager by slug.
    2. Get tenant's claude_code OAuth token from the credential vault.
    3. Build memory context using build_memory_context().
    4. Create a temp directory, write CLAUDE.md and mcp.json.
    5. Call invoke_claude_cli().
    6. Cleanup the temp directory (always, in a finally block).
    7. Return (response_text, metadata).

    Args:
        db: SQLAlchemy database session.
        tenant_id: UUID of the tenant.
        user_id: UUID of the authenticated user.
        agent_slug: Slug of the agent skill to load.
        message: The user's message.
        channel: Channel name (e.g. "whatsapp", "chat").
        sender_phone: Sender's phone number (may be None for non-WhatsApp channels).
        conversation_summary: Brief summary of previous conversation turns.

    Returns:
        Tuple of (response_text, metadata). response_text is None on failure.
    """
    metadata: Dict[str, Any] = {
        "agent_slug": agent_slug,
        "tenant_id": str(tenant_id),
        "user_id": str(user_id),
        "channel": channel,
        "error": None,
    }

    # 1. Load agent skill (fallback to 'luna' if specific skill not found)
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

    skill_body = ""
    if skill.description:
        skill_body = skill.description

    # 2. Get OAuth token
    oauth_token = _get_claude_code_token(db, tenant_id)
    if not oauth_token:
        err = f"No Claude Code session token for tenant {tenant_id}"
        logger.error(err)
        metadata["error"] = err
        return None, metadata

    # 3. Build memory context
    try:
        memory_context = build_memory_context(db, tenant_id, message)
    except Exception as exc:
        logger.warning("Memory recall failed for tenant %s: %s", tenant_id, exc)
        memory_context = {}

    # 4. Build CLAUDE.md content and MCP config (strings, not files)
    tenant_name = str(tenant_id)
    user_name = sender_phone or str(user_id)

    claude_md_content = generate_claude_md(
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
        "Dispatching ChatCliWorkflow: skill=%s tenant=%s channel=%s",
        agent_slug, str(tenant_id)[:8], channel,
    )

    # 5. Dispatch to code-worker via Temporal
    import asyncio
    from temporalio.client import Client as TemporalClient

    try:
        temporal_address = os.environ.get("TEMPORAL_ADDRESS", "temporal:7233")

        async def _run_workflow():
            client = await TemporalClient.connect(temporal_address)
            # Import workflow types for type-safe dispatch
            from dataclasses import dataclass as _dc

            @_dc
            class _ChatCliInput:
                message: str
                tenant_id: str
                claude_md_content: str = ""
                mcp_config: str = ""

            task_input = _ChatCliInput(
                message=message,
                tenant_id=str(tenant_id),
                claude_md_content=claude_md_content,
                mcp_config=json.dumps(mcp_config),
            )

            result = await client.execute_workflow(
                "ChatCliWorkflow",
                task_input,
                id=f"chat-cli-{uuid.uuid4()}",
                task_queue="servicetsunami-code",
                execution_timeout=timedelta(minutes=10),
            )
            return result

        # Run the async workflow dispatch from sync context
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(_run_workflow())
        finally:
            loop.close()

        # Temporal may return a dataclass, dict, or other type
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
            metadata["platform"] = "claude_code"
            if isinstance(meta, dict):
                metadata.update(meta)
            return response_text, metadata
        else:
            metadata["error"] = error or "CLI workflow returned empty response"
            logger.warning("ChatCliWorkflow result: success=%s error=%s response_len=%s",
                          success, error, len(response_text) if response_text else 0)
            return None, metadata

    except Exception as e:
        logger.exception("ChatCliWorkflow dispatch failed")
        metadata["error"] = str(e)
        return None, metadata


    # NOTE: cleanup not needed — code-worker handles its own temp dirs
    if False:  # dead code, kept for reference
        try:
            shutil.rmtree(session_dir, ignore_errors=True)
        except Exception as exc:
            logger.warning("Failed to cleanup session dir %s: %s", session_dir, exc)
