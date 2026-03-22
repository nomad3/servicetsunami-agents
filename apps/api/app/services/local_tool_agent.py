"""Local tool-capable agent runtime using Ollama + MCP JSON-RPC.

Gives free-tier tenants (no Claude/Codex subscription) access to curated
MCP tools via qwen3:4b's native tool calling through Ollama's /api/chat.

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

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://ollama:11434")
MCP_TOOLS_URL = os.environ.get("MCP_TOOLS_URL", "http://mcp-tools:8000")
LOCAL_TOOL_MODEL = os.environ.get("LOCAL_TOOL_MODEL", "qwen3:4b")
MCP_INTERNAL_KEY = os.environ.get("MCP_API_KEY", "dev_mcp_key")

MAX_TOOL_ROUNDS = 3
MAX_TOOLS_PER_TURN = 5
TOOL_CALL_TIMEOUT = 30
OLLAMA_TIMEOUT = 180

# ---------------------------------------------------------------------------
# Curated tool registry — typed allowlist per category
# ---------------------------------------------------------------------------

_TOOL_REGISTRY: Dict[str, Dict[str, Any]] = {
    # Always available — no integration required
    "knowledge_search": {
        "category": "knowledge",
        "integration": None,
        "schema": {
            "type": "function",
            "function": {
                "name": "knowledge_search",
                "description": "Search the knowledge graph for entities (contacts, companies, deals, competitors). Returns matching entities with descriptions.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query text"},
                        "category": {
                            "type": "string",
                            "description": "Filter by entity category",
                            "enum": ["person", "company", "deal", "competitor", "lead", "contact"],
                        },
                        "limit": {"type": "integer", "description": "Max results (default 10)"},
                    },
                    "required": ["query"],
                },
            },
        },
    },
    "knowledge_list_entities": {
        "category": "knowledge",
        "integration": None,
        "schema": {
            "type": "function",
            "function": {
                "name": "knowledge_list_entities",
                "description": "List all entities in the knowledge graph, optionally filtered by category.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "category": {"type": "string", "description": "Filter by category"},
                        "limit": {"type": "integer", "description": "Max results (default 20)"},
                    },
                },
            },
        },
    },
    "knowledge_create_entity": {
        "category": "knowledge",
        "integration": None,
        "schema": {
            "type": "function",
            "function": {
                "name": "knowledge_create_entity",
                "description": "Create a new entity in the knowledge graph (person, company, deal, etc).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Entity name"},
                        "entity_type": {
                            "type": "string",
                            "description": "Type of entity",
                            "enum": ["person", "organization", "product", "location", "event", "opportunity", "concept"],
                        },
                        "category": {"type": "string", "description": "Category (lead, contact, customer, competitor, etc)"},
                        "description": {"type": "string", "description": "Brief description"},
                    },
                    "required": ["name", "entity_type"],
                },
            },
        },
    },
    "knowledge_create_observation": {
        "category": "knowledge",
        "integration": None,
        "schema": {
            "type": "function",
            "function": {
                "name": "knowledge_create_observation",
                "description": "Add a fact or observation about an existing entity.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "entity_name": {"type": "string", "description": "Name of the entity"},
                        "content": {"type": "string", "description": "The observation or fact"},
                        "observation_type": {
                            "type": "string",
                            "description": "Type of observation",
                            "enum": ["fact", "insight", "preference", "context"],
                        },
                    },
                    "required": ["entity_name", "content"],
                },
            },
        },
    },
    "report_generate": {
        "category": "reports",
        "integration": None,
        "schema": {
            "type": "function",
            "function": {
                "name": "report_generate",
                "description": "Generate an Excel report from data.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "report_type": {
                            "type": "string",
                            "description": "Type of report to generate",
                            "enum": ["summary", "detailed", "comparison"],
                        },
                        "title": {"type": "string", "description": "Report title"},
                    },
                    "required": ["report_type"],
                },
            },
        },
    },
    # Require google_gmail integration
    "email_search": {
        "category": "email",
        "integration": "google_gmail",
        "schema": {
            "type": "function",
            "function": {
                "name": "email_search",
                "description": "Search emails by query (from, subject, keywords). Returns recent matching emails.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Gmail search query"},
                        "max_results": {"type": "integer", "description": "Max emails to return (default 5)"},
                    },
                    "required": ["query"],
                },
            },
        },
    },
    "email_send": {
        "category": "email",
        "integration": "google_gmail",
        "schema": {
            "type": "function",
            "function": {
                "name": "email_send",
                "description": "Send an email.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "to": {"type": "string", "description": "Recipient email address"},
                        "subject": {"type": "string", "description": "Email subject"},
                        "body": {"type": "string", "description": "Email body text"},
                    },
                    "required": ["to", "subject", "body"],
                },
            },
        },
    },
    # Require google_calendar integration
    "calendar_list_events": {
        "category": "calendar",
        "integration": "google_calendar",
        "schema": {
            "type": "function",
            "function": {
                "name": "calendar_list_events",
                "description": "List upcoming calendar events.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "days_ahead": {"type": "integer", "description": "Number of days to look ahead (default 7)"},
                        "max_results": {"type": "integer", "description": "Max events to return (default 10)"},
                    },
                },
            },
        },
    },
    "calendar_create_event": {
        "category": "calendar",
        "integration": "google_calendar",
        "schema": {
            "type": "function",
            "function": {
                "name": "calendar_create_event",
                "description": "Create a new calendar event.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "summary": {"type": "string", "description": "Event title"},
                        "start_time": {"type": "string", "description": "Start time in ISO 8601 format"},
                        "end_time": {"type": "string", "description": "End time in ISO 8601 format"},
                        "description": {"type": "string", "description": "Event description"},
                    },
                    "required": ["summary", "start_time", "end_time"],
                },
            },
        },
    },
    # Require jira integration
    "jira_search_issues": {
        "category": "jira",
        "integration": "jira",
        "schema": {
            "type": "function",
            "function": {
                "name": "jira_search_issues",
                "description": "Search Jira issues using JQL or text query.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "JQL query or text search"},
                        "max_results": {"type": "integer", "description": "Max results (default 10)"},
                    },
                    "required": ["query"],
                },
            },
        },
    },
    "jira_create_issue": {
        "category": "jira",
        "integration": "jira",
        "schema": {
            "type": "function",
            "function": {
                "name": "jira_create_issue",
                "description": "Create a new Jira issue.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "project_key": {"type": "string", "description": "Jira project key (e.g. ST)"},
                        "summary": {"type": "string", "description": "Issue title"},
                        "description": {"type": "string", "description": "Issue description"},
                        "issue_type": {
                            "type": "string",
                            "description": "Issue type",
                            "enum": ["Task", "Bug", "Story", "Epic"],
                        },
                    },
                    "required": ["project_key", "summary"],
                },
            },
        },
    },
}


# ---------------------------------------------------------------------------
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
    """Send a chat request to Ollama with tool schemas. Returns the response dict."""
    body: Dict[str, Any] = {
        "model": LOCAL_TOOL_MODEL,
        "messages": messages,
        "stream": False,
    }
    if tools:
        body["tools"] = tools

    try:
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
        "platform": "local_qwen_tools",
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

    # Get curated tools for this tenant
    tools = _get_tools_for_tenant(tenant_id, connected_integrations)
    if not tools:
        logger.info("No tools available for tenant %s — skipping tool agent", tenant_id)
        return None, metadata

    # Build initial messages
    messages: List[Dict[str, Any]] = [{"role": "system", "content": system}]
    if conversation_summary:
        messages.append({
            "role": "system",
            "content": f"Recent conversation context:\n{conversation_summary.strip()[-800:]}",
        })
    messages.append({"role": "user", "content": message})

    # Agent loop — max rounds
    for round_num in range(MAX_TOOL_ROUNDS):
        resp = _ollama_chat(messages, tools)
        if not resp:
            return None, metadata

        msg = resp.get("message", {})
        tool_calls = msg.get("tool_calls")

        # No tool calls — model is done, return text
        if not tool_calls:
            text = msg.get("content", "").strip()
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

            logger.info("Local tool agent calling: %s(%s)", tool_name, json.dumps(arguments)[:200])
            result = _call_mcp_tool(tool_name, arguments, str(tenant_id))
            metadata["tools_used"].append(tool_name)

            messages.append({
                "role": "tool",
                "content": json.dumps(result, default=str)[:4000],
            })

        metadata["tool_rounds"] = round_num + 1

    # Exhausted rounds — do one final call without tools to get a summary
    resp = _ollama_chat(messages, tools=[])
    if resp:
        text = resp.get("message", {}).get("content", "").strip()
        if text:
            return text, metadata

    return None, metadata
