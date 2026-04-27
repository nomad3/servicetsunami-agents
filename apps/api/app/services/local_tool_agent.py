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
# Prefer MCP_SERVER_URL (the canonical name set by docker-compose); fall back
# to MCP_TOOLS_URL for legacy callers. Both should point at the FastMCP
# server's SSE root (e.g. http://mcp-tools:8086).
MCP_TOOLS_URL = os.environ.get("MCP_SERVER_URL") or os.environ.get(
    "MCP_TOOLS_URL", "http://mcp-tools:8086"
)
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
# MCP tool call via the SSE protocol — the FastMCP server only exposes /sse,
# not a JSON-RPC POST endpoint. Reuses the same primitive PR-A's external
# agent adapter (`external_agent_adapter._mcp_sse_call`) lands on, so there
# is one MCP-SSE call path across the platform.
# ---------------------------------------------------------------------------

def _call_mcp_tool(
    tool_name: str,
    arguments: Dict[str, Any],
    tenant_id: str,
) -> Dict[str, Any]:
    """Execute a tool against the internal MCP server over SSE."""
    arguments = {**arguments, "tenant_id": tenant_id}
    sse_url = MCP_TOOLS_URL.rstrip("/")
    if not sse_url.endswith("/sse"):
        sse_url = sse_url + "/sse"

    headers = {
        "X-Internal-Key": MCP_INTERNAL_KEY,
        "X-Tenant-Id": tenant_id,
    }

    start = time.time()
    try:
        # Lazy-import the SDK so api startup doesn't pay the cost.
        import asyncio
        from mcp import ClientSession
        from mcp.client.sse import sse_client

        async def _do() -> Dict[str, Any]:
            async with sse_client(sse_url, headers=headers) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(tool_name, arguments=arguments)
                    return _flatten_call_tool_result(result)

        # The local_tool_agent runs from the synchronous chat path. If a
        # FastAPI handler ever wraps this in an async loop we'd need the
        # _run_async thread-pool dance from external_agent_adapter, but
        # today the call site is sync.
        out = asyncio.run(asyncio.wait_for(_do(), timeout=float(TOOL_CALL_TIMEOUT)))
        out["duration_ms"] = int((time.time() - start) * 1000)
        return out
    except Exception as e:
        duration_ms = int((time.time() - start) * 1000)
        logger.warning("MCP tool call failed: %s(%s) — %s", tool_name, arguments, e)
        return {"error": str(e), "duration_ms": duration_ms}


def _flatten_call_tool_result(result: Any) -> Dict[str, Any]:
    """Reduce an mcp ``CallToolResult`` to ``{result: ...}`` or ``{error: ...}``.

    The SDK returns ``content`` as a list of typed blocks (TextContent,
    ImageContent, ...). For the local_tool_agent we only consume the first
    text block; non-text blocks fall back to repr so nothing silently drops.
    """
    if getattr(result, "isError", False):
        msg = getattr(result, "content", None) or "remote MCP tool returned an error"
        return {"error": _stringify_blocks(msg)}
    content = getattr(result, "content", None)
    blocks = content if isinstance(content, list) else []
    for item in blocks:
        text = getattr(item, "text", None)
        if isinstance(text, str):
            try:
                return {"result": json.loads(text)}
            except (json.JSONDecodeError, ValueError):
                return {"result": text}
    return {"result": _stringify_blocks(content)}


def _stringify_blocks(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            text = getattr(item, "text", None)
            parts.append(text if isinstance(text, str) else repr(item))
        return "\n".join(parts)
    return str(content)


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
        # Tier-1 #3: pin the model in Ollama memory between requests so
        # we don't re-pay the 30-60s model load after every quiet period.
        # Default is 5 minutes; we extend so a chat session that pauses
        # for 10-15 min between turns doesn't get cold-loaded again.
        # Model is ~14GB and this Mac M4 has 45GB unified memory, so
        # holding it indefinitely is fine. Bench observation: first
        # request after the api restart hit 70s warmup with no other
        # explanation; pinning the model here addresses that.
        "keep_alive": "30m",
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
    # Phase A.1 sub-instrumentation. Wall time inside this function is the
    # bulk of every chat-turn latency on the local-Gemma path; we want to
    # know how it splits between Ollama inference and MCP tool calls.
    _llm_ms_total = 0
    _tool_ms_total = 0
    _round_count = 0
    _bench_t0 = time.monotonic()

    metadata: Dict[str, Any] = {
        "platform": "local_gemma_tools",
        "model": LOCAL_TOOL_MODEL,
        "fallback": True,
        "agent_slug": agent_slug,
        "tools_used": [],
        "tool_rounds": 0,
        # populated at every return so the caller's `metadata['timings']`
        # gains a `local_*` family of stages.
        "timings": {},
    }

    def _emit_timings(extra: Optional[Dict[str, int]] = None) -> None:
        elapsed = int((time.monotonic() - _bench_t0) * 1000)
        metadata["timings"] = {
            "local_total_ms": elapsed,
            "local_llm_ms": _llm_ms_total,
            "local_tool_ms": _tool_ms_total,
            "local_overhead_ms": max(0, elapsed - _llm_ms_total - _tool_ms_total),
            "local_rounds": _round_count,
            **(extra or {}),
        }

    # Build system prompt from skill_body. Trim aggressively — Gemma 4 on
    # M4 prefills at ~100–200 tok/s, so every kilobyte of system prompt
    # adds ~0.5–1 s to *every* tool-calling round (2–3 rounds per turn).
    # Bench v4 measured 13–20 s per round; this trim halves the base
    # system prompt and is expected to save 3–5 s per round.
    system = skill_body.strip()[:1000] if skill_body else ""
    if not system:
        system = (
            "You are an AI assistant with access to tools. "
            "Reply in the user's language. Be concise."
        )
    # Local-path anti-hallucination rules — condensed from the cloud-CLI
    # ANTI_HALLUCINATION_PREAMBLE (1873 chars) to ~500 chars. The cloud
    # path has bigger context budgets and can afford the longer text;
    # here the same rules in fewer tokens.
    _LOCAL_AH_PREAMBLE = (
        "## Anti-hallucination rules (apply every turn)\n"
        "1. Names, prices, IDs, dates, times, addresses must come from "
        "(a) the conversation above, (b) memory/recall blocks below, or "
        "(c) a tool you call THIS turn. Never invent them.\n"
        "2. If a tool returns nothing or fails, say so plainly. "
        "Don't substitute plausible alternatives.\n"
        "3. Never say 'done', 'sent', 'scheduled', 'booked' unless you "
        "called the action tool successfully this turn.\n"
        "4. Honest 'I couldn't reach the system' beats a guessed answer."
    )
    system = _LOCAL_AH_PREAMBLE + "\n\n" + system + (
        "\n\nUse tools when needed; reply directly when not. Be brief."
    )

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
            _round_count = round_num + 1
            _llm_t0 = time.monotonic()
            resp = _ollama_chat(messages, tools)
            _llm_ms_total += int((time.monotonic() - _llm_t0) * 1000)
            if not resp:
                _emit_timings()
                return None, metadata

            msg = resp.get("message", {})
            tool_calls = msg.get("tool_calls")

            # No tool calls — model is done, return text
            if not tool_calls:
                text = _clean_response(msg.get("content", ""))
                if text:
                    metadata["tool_rounds"] = round_num
                    _emit_timings()
                    return text, metadata
                _emit_timings()
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
                _tool_t0 = time.monotonic()
                result = _call_mcp_tool(tool_name, arguments, str(tenant_id))
                _tool_ms_total += int((time.monotonic() - _tool_t0) * 1000)
                metadata["tools_used"].append(tool_name)

                # Trim tool results aggressively. Round 2's prefill cost
                # scales linearly with this content — cutting from 4 KB to
                # 1 KB saves ~750 tokens × 5 ms/token ≈ 4 s on the next
                # Gemma round. Most knowledge / search tools return
                # listings whose first 1 KB carries the headline answer.
                messages.append({
                    "role": "tool",
                    "content": json.dumps(result, default=str)[:1000],
                })

            metadata["tool_rounds"] = round_num + 1

        # Exhausted rounds — do one final call without tools to get a summary
        messages.append({
            "role": "user",
            "content": "Now summarize the tool results above and give a final answer to the user. Do not call any more tools.",
        })
        _llm_t0 = time.monotonic()
        resp = _ollama_chat(messages, tools=[])
        _llm_ms_total += int((time.monotonic() - _llm_t0) * 1000)
        if resp:
            text = _clean_response(resp.get("message", {}).get("content", ""))
            if text:
                _emit_timings({"local_summary_call": 1})
                return text, metadata

        _emit_timings({"local_summary_call": 1, "local_summary_empty": 1})
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
