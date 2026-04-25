"""Local tool-capable agent runtime using Ollama + MCP JSON-RPC.

Gives free-tier tenants (no Claude/Codex subscription) access to curated
MCP tools via Gemma 4's native tool calling through Ollama's /api/chat.

Preserves the selected agent's skill_body as system prompt — not hardcoded
to any single persona.
"""

import json
import logging
import os
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.db.session import SessionLocal
from app.schemas.safety_policy import ActionType, PolicyDecision, SafetyEnforcementRequest
from app.services import safety_enforcement

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
MCP_TOOLS_URL = os.environ.get("MCP_TOOLS_URL", "http://mcp-tools:8000")
LOCAL_TOOL_MODEL = os.environ.get("LOCAL_TOOL_MODEL", "gemma4")
MCP_INTERNAL_KEY = os.environ.get("MCP_API_KEY", "dev_mcp_key")

MAX_TOOL_ROUNDS = 3
MAX_TOOLS_PER_TURN = 5
TOOL_CALL_TIMEOUT = 30
OLLAMA_TIMEOUT = 300

# ---------------------------------------------------------------------------
# Curated tool registry — typed allowlist per category
# ---------------------------------------------------------------------------

_TOOL_REGISTRY: Dict[str, Dict[str, Any]] = {
    # ── Knowledge (always available) ──
    "search_knowledge": {
        "category": "knowledge",
        "integration": None,
        "schema": {
            "type": "function",
            "function": {
                "name": "search_knowledge",
                "description": "Search the knowledge graph for entities (contacts, companies, deals, competitors).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query text"},
                        "top_k": {"type": "integer", "description": "Max results (default 10)"},
                    },
                    "required": ["query"],
                },
            },
        },
    },
    "find_entities": {
        "category": "knowledge",
        "integration": None,
        "schema": {
            "type": "function",
            "function": {
                "name": "find_entities",
                "description": "Find entities in the knowledge graph by name or type.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query"},
                        "entity_types": {"type": "string", "description": "Comma-separated types: person,organization,product,location"},
                        "limit": {"type": "integer", "description": "Max results (default 20)"},
                    },
                    "required": ["query"],
                },
            },
        },
    },
    # NOTE: create_entity and record_observation removed — local model is read-only.
    # These mutations require a subscribed CLI (Claude/Codex) for trust.
    # ── Email (requires gmail) ──
    "search_emails": {
        "category": "email",
        "integration": "gmail",
        "schema": {
            "type": "function",
            "function": {
                "name": "search_emails",
                "description": "Search emails by query (from, subject, keywords).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Gmail search query"},
                        "max_results": {"type": "integer", "description": "Max emails (default 5)"},
                    },
                },
            },
        },
    },
    # NOTE: send_email removed — local model is read-only, no side effects.
    # ── Calendar (requires google_calendar) ──
    "list_calendar_events": {
        "category": "calendar",
        "integration": "google_calendar",
        "schema": {
            "type": "function",
            "function": {
                "name": "list_calendar_events",
                "description": "List upcoming calendar events.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "days_ahead": {"type": "integer", "description": "Days ahead (default 7)"},
                        "max_results": {"type": "integer", "description": "Max events (default 10)"},
                    },
                },
            },
        },
    },
    # ── Jira (requires jira) ──
    "search_jira_issues": {
        "category": "jira",
        "integration": "jira",
        "schema": {
            "type": "function",
            "function": {
                "name": "search_jira_issues",
                "description": "Search Jira issues.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "jql": {"type": "string", "description": "JQL query string"},
                        "max_results": {"type": "integer", "description": "Max results (default 10)"},
                    },
                },
            },
        },
    },
    # NOTE: create_jira_issue removed — local model is read-only, no side effects.
}


# Tool filtering by tenant integrations
# ---------------------------------------------------------------------------

def _get_tools_for_tenant(
    tenant_id: uuid.UUID,
    connected_integrations: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Return Ollama-format tool schemas filtered by tenant's connected integrations."""
    tools = []
    for tool_name, tool_info in _TOOL_REGISTRY.items():
        required = tool_info["integration"]
        if required is None:
            tools.append(tool_info["schema"])
        elif connected_integrations and required in connected_integrations:
            tools.append(tool_info["schema"])
    return tools


# ---------------------------------------------------------------------------
# MCP JSON-RPC tool call (reuses pattern from mcp_server_connectors.py)
# ---------------------------------------------------------------------------

def _call_mcp_tool(
    tool_name: str,
    arguments: Dict[str, Any],
    tenant_id: str,
) -> Dict[str, Any]:
    """Execute a tool via JSON-RPC tools/call against the internal MCP server."""
    # Auto-inject tenant_id — MCP tools require it
    arguments = {**arguments, "tenant_id": tenant_id}

    rpc_body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments,
        },
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-Internal-Key": MCP_INTERNAL_KEY,
        "X-Tenant-Id": tenant_id,
    }
    url = f"{MCP_TOOLS_URL}/mcp"

    start = time.time()
    try:
        with httpx.Client(timeout=float(TOOL_CALL_TIMEOUT)) as client:
            resp = client.post(url, json=rpc_body, headers=headers)
        duration_ms = int((time.time() - start) * 1000)

        if resp.status_code >= 400:
            return {"error": f"HTTP {resp.status_code}: {resp.text[:300]}", "duration_ms": duration_ms}

        data = resp.json()
        result = data.get("result", {})

        # Extract text content from MCP response
        content = result.get("content", [])
        if content:
            for item in content:
                if item.get("type") == "text":
                    try:
                        return {"result": json.loads(item["text"]), "duration_ms": duration_ms}
                    except (json.JSONDecodeError, KeyError):
                        return {"result": item.get("text", ""), "duration_ms": duration_ms}
            return {"result": content, "duration_ms": duration_ms}
        return {"result": result, "duration_ms": duration_ms}

    except Exception as e:
        duration_ms = int((time.time() - start) * 1000)
        logger.warning("MCP tool call failed: %s(%s) — %s", tool_name, arguments, e)
        return {"error": str(e), "duration_ms": duration_ms}


# ---------------------------------------------------------------------------
# Ollama chat with tool calling
# ---------------------------------------------------------------------------

def _ollama_chat(
    messages: List[Dict[str, Any]],
    tools: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Send a chat request to Ollama with tool schemas. Returns the response dict.
    Uses the foreground sync lock — user-blocking calls have GPU priority."""
    from app.services.local_inference import _ollama_sync_lock

    body: Dict[str, Any] = {
        "model": LOCAL_TOOL_MODEL,
        "messages": messages,
        "stream": False,
        "options": {"num_ctx": 4096},  # keep context small for speed
    }
    if tools:
        body["tools"] = tools

    try:
        with _ollama_sync_lock:  # foreground priority — blocks background scoring
            with httpx.Client(timeout=float(OLLAMA_TIMEOUT)) as client:
                resp = client.post(f"{OLLAMA_BASE_URL}/api/chat", json=body)
        if resp.status_code != 200:
            logger.error("Ollama chat failed: HTTP %s — %s", resp.status_code, resp.text[:300])
            return None
        return resp.json()
    except Exception as e:
        logger.error("Ollama chat error: %s", e)
        return None


# ---------------------------------------------------------------------------
# Main agent loop
# ---------------------------------------------------------------------------

def run(
    message: str,
    tenant_id: uuid.UUID,
    skill_body: str = "",
    agent_slug: str = "luna",
    conversation_summary: str = "",
    connected_integrations: Optional[List[str]] = None,
) -> Tuple[Optional[str], Dict[str, Any]]:
    """Run the local tool agent for a single user message.

    Returns (response_text, metadata) or (None, metadata) on failure.
    Preserves agent_slug and skill_body — not hardcoded to any persona.
    """
    metadata: Dict[str, Any] = {
        "platform": "local_gemma_tools",
        "model": LOCAL_TOOL_MODEL,
        "fallback": True,
        "agent_slug": agent_slug,
        "tools_used": [],
        "tool_rounds": 0,
    }

    # Build system prompt from skill_body
    system = skill_body.strip()[:2000] if skill_body else ""
    if not system:
        system = (
            "You are an AI assistant with access to tools. "
            "Use tools when they would help answer the user's question. "
            "Respond in the same language the user writes in."
        )
    system += (
        "\n\nYou have access to tools. Use them when needed to answer accurately. "
        "If no tool is needed, respond directly. Be concise."
    )
    # Universal anti-hallucination preamble — same rules as the CLI hot path.
    # Imported lazily to avoid circular imports.
    from app.services.cli_session_manager import ANTI_HALLUCINATION_PREAMBLE
    system = ANTI_HALLUCINATION_PREAMBLE + "\n\n" + system

    # Get curated tools for this tenant
    tools = _get_tools_for_tenant(tenant_id, connected_integrations)
    if not tools:
        logger.info("No tools available for tenant %s — skipping tool agent", tenant_id)
        return None, metadata
    policy_db = SessionLocal()

    # Build initial messages
    messages: List[Dict[str, Any]] = [{"role": "system", "content": system}]
    if conversation_summary:
        messages.append({
            "role": "system",
            "content": f"Recent conversation context:\n{conversation_summary.strip()[-800:]}",
        })
    messages.append({"role": "user", "content": message})

    # Agent loop — max rounds
    try:
        for round_num in range(MAX_TOOL_ROUNDS):
            resp = _ollama_chat(messages, tools)
            if not resp:
                return None, metadata

            msg = resp.get("message", {})
            tool_calls = msg.get("tool_calls")

            # No tool calls — model is done, return text
            if not tool_calls:
                text = _clean_response(msg.get("content", ""))
                if text:
                    metadata["tool_rounds"] = round_num
                    return text, metadata
                return None, metadata

            # Enforce per-turn tool limit
            tool_calls = tool_calls[:MAX_TOOLS_PER_TURN]

            # Add assistant message with tool calls to conversation
            messages.append(msg)

            # Execute each tool call
            for tc in tool_calls:
                fn = tc.get("function", {})
                tool_name = fn.get("name", "")
                arguments = fn.get("arguments", {})

                # Validate tool is in our allowlist
                if tool_name not in _TOOL_REGISTRY:
                    logger.warning("Model requested unlisted tool: %s — skipping", tool_name)
                    messages.append({
                        "role": "tool",
                        "content": json.dumps({"error": f"Tool '{tool_name}' is not available"}),
                    })
                    continue

                # Validate arguments is a dict
                if not isinstance(arguments, dict):
                    logger.warning("Malformed tool arguments for %s: %s", tool_name, type(arguments))
                    messages.append({
                        "role": "tool",
                        "content": json.dumps({"error": "Invalid arguments format"}),
                    })
                    continue

                enforcement = safety_enforcement.enforce_action(
                    policy_db,
                    tenant_id=tenant_id,
                    request=SafetyEnforcementRequest(
                        action_type=ActionType.MCP_TOOL,
                        action_name=tool_name,
                        channel="local_agent",
                        proposed_action={"arguments": arguments},
                        assumptions=["Local-tool fallback is an unsupervised runtime."],
                        uncertainty_notes=["No human confirmation is available inline for the local agent."],
                        context_summary=f"Local tool request from agent '{agent_slug}'.",
                        context_ref={"agent_slug": agent_slug, "message_excerpt": message[:200]},
                        expected_downside=f"Tool '{tool_name}' could operate beyond read-only bounds if misclassified.",
                        agent_slug=agent_slug,
                    ),
                )
                if enforcement.decision not in (PolicyDecision.ALLOW, PolicyDecision.ALLOW_WITH_LOGGING):
                    logger.warning(
                        "BLOCKED governed tool %s for local agent — %s (evidence_pack_id=%s)",
                        tool_name,
                        enforcement.rationale,
                        enforcement.evidence_pack_id,
                    )
                    messages.append({
                        "role": "tool",
                        "content": json.dumps({
                            "error": f"Tool '{tool_name}' is blocked for the local runtime. {enforcement.rationale} Connect Claude Code or Codex in Settings → Integrations for supervised execution.",
                            "evidence_pack_id": str(enforcement.evidence_pack_id) if enforcement.evidence_pack_id else None,
                        }),
                    })
                    continue

                logger.info("Local tool agent calling: %s(%s)", tool_name, json.dumps(arguments)[:200])
                result = _call_mcp_tool(tool_name, arguments, str(tenant_id))
                metadata["tools_used"].append(tool_name)

                messages.append({
                    "role": "tool",
                    "content": json.dumps(result, default=str)[:4000],
                })

            metadata["tool_rounds"] = round_num + 1

        # Exhausted rounds — do one final call without tools to get a summary
        messages.append({
            "role": "user",
            "content": "Now summarize the tool results above and give a final answer to the user. Do not call any more tools.",
        })
        resp = _ollama_chat(messages, tools=[])
        if resp:
            text = _clean_response(resp.get("message", {}).get("content", ""))
            if text:
                return text, metadata

        return None, metadata
    finally:
        policy_db.close()


def _clean_response(text: str) -> str:
    """Strip model artifacts like <tool_call>, <think>, etc from response."""
    import re
    if not text:
        return ""
    # Remove <tool_call>...</tool_call> blocks
    text = re.sub(r"<tool_call>.*?</tool_call>", "", text, flags=re.DOTALL)
    # Remove <think>...</think> blocks
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    # Remove unclosed tags
    text = re.sub(r"</?(?:tool_call|think)>", "", text)
    return text.strip()
