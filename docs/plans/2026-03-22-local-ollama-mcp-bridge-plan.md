# Local Tool Agent — Curated MCP Tool Calling for Free-Tier Tenants

**Date**: 2026-03-22 (revised 2026-03-23)
**Status**: In Progress — code complete, blocked on model inference speed
**Goal**: Give tenants without a Claude/Codex subscription access to curated MCP tools via a local model (qwen3:4b) through Ollama's native tool calling API.

## Context

- Codex CLI `--oss --local-provider ollama` does NOT wire MCP tool calls — models output tool calls as plain text
- Ollama's `/api/chat` endpoint natively supports `tools` parameter with proper structured `tool_calls` responses
- Existing `mcp_server_connectors.py` already has JSON-RPC `tools/call` and `tools/list` patterns we reuse
- Internal MCP server at `http://mcp-tools:8000/mcp`

## Model

**qwen3:4b** — 2.5GB, ~4GB RAM. Tool calling confirmed 2026-03-22. Already pulled.

**Known issue**: With 10 tool schemas, each call takes 47-80s on laptop. First call after cold start exceeds 300s timeout. May need faster hardware or smaller model.

## Architecture

```
User message (no subscription)
  → cli_session_manager.py detects missing credentials
  → Fallback chain:
      1. local_tool_agent.run() — curated tools, agent-preserving
      2. generate_agent_response_sync() — plain text fallback
      3. friendly error message
  → local_tool_agent.run():
      1. Build curated tool schemas (allowlist filtered by tenant integrations)
      2. Call Ollama /api/chat with message + skill system prompt + tools
      3. If tool_calls → execute via JSON-RPC tools/call to /mcp
      4. Feed results back → next round (max 3 rounds)
      5. Return final text
```

## Design Constraints

- **Agent-preserving**: Uses selected agent's skill_body as system prompt, not hardcoded Luna
- **Curated tool registry**: Typed allowlist per category, not all 81 tools
- **Tenant-aware filtering**: Only expose tools for integrations the tenant has connected
- **Reuse existing MCP call code**: JSON-RPC tools/call pattern from mcp_server_connectors.py
- **Auto-injects tenant_id**: MCP tools require tenant_id — injected automatically before each call
- **Hard limits**:
  - Max 3 tool call rounds per message
  - Max 5 tools per turn
  - 30s timeout per tool call, 300s Ollama timeout
  - Explicit fallback on malformed tool_calls
  - Context window limited to 4096 tokens for speed

## Curated Tool Registry (actual MCP names)

| Category | MCP Tool Name | Requires Integration |
|----------|--------------|---------------------|
| **knowledge** | search_knowledge | None |
| **knowledge** | find_entities | None |
| **knowledge** | create_entity | None |
| **knowledge** | record_observation | None |
| **email** | search_emails | gmail |
| **email** | send_email | gmail |
| **calendar** | list_calendar_events | google_calendar |
| **jira** | search_jira_issues | jira |
| **jira** | create_jira_issue | jira |

~9 tools max. Filtered by tenant's connected integrations.

## Status

### Done
- qwen3:4b pulled and tool calling confirmed
- local_tool_agent.py built with correct MCP tool names/schemas
- Fallback chain wired: tool agent → plain text → error
- Agent-preserving (skill_body as system prompt)
- tenant_id auto-injected into MCP calls

### Blocked
- qwen3:4b inference too slow on M-series laptop with 10 tool schemas (~5min per call)
- Need either: fewer tools (2-3), smaller model (qwen3:1.7b), or beefier hardware
