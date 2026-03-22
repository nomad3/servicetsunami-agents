# Local Tool Agent — Curated MCP Tool Calling for Free-Tier Tenants

**Date**: 2026-03-22 (revised 2026-03-23)
**Status**: In Progress
**Goal**: Give tenants without a Claude/Codex subscription access to curated MCP tools via a local model (qwen3:4b) through Ollama's native tool calling API.

## Context

- Codex CLI `--oss --local-provider ollama` does NOT wire MCP tool calls — models output tool calls as plain text
- Ollama's `/api/chat` endpoint natively supports `tools` parameter with proper structured `tool_calls` responses
- Existing `mcp_server_connectors.py` already has JSON-RPC `tools/call` and `tools/list` patterns we reuse
- Internal MCP server at `http://mcp-tools:8000/mcp`

## Model

**qwen3:4b** — 2.5GB, ~4GB RAM. Tool calling confirmed 2026-03-22. Already pulled.

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
- **Hard limits**:
  - Max 3 tool call rounds per message
  - Max 5 tools per turn
  - 30s timeout per tool call
  - Explicit fallback on malformed tool_calls
  - Tool allowlist by channel

## Curated Tool Registry

| Category | Tools | Requires Integration |
|----------|-------|---------------------|
| **knowledge** | knowledge_search, knowledge_list_entities, knowledge_create_entity, knowledge_create_observation | None (always available) |
| **email** | email_search, email_read, email_send | google_gmail |
| **calendar** | calendar_list_events, calendar_create_event | google_calendar |
| **jira** | jira_search_issues, jira_get_issue, jira_create_issue | jira |
| **reports** | report_generate | None (always available) |

~13 tools max exposed. Filtered down based on tenant's connected integrations.

## Tasks

### 1. ~~Pull qwen3:4b~~ DONE (2026-03-22)

### 2. Build local_tool_agent.py
- File: `apps/api/app/services/local_tool_agent.py`
- Curated tool registry as typed dict in code
- MCP JSON-RPC `tools/call` reusing pattern from mcp_server_connectors.py
- Ollama `/api/chat` with `tools` parameter
- Agent loop with max 3 rounds, 5 tools/turn, 30s timeout
- Preserves agent_slug and skill_body as system prompt
- Returns (response_text, metadata) or (None, metadata) on failure

### 3. Wire into cli_session_manager.py fallback chain
- Fallback order: local_tool_agent → generate_agent_response_sync → error
- Metadata: platform=local_qwen_tools, fallback=true, tools_used=[...]

### 4. Test end-to-end
- QwenTest tenant (no credentials)
- Knowledge search, entity creation
- Verify tool calls execute and results return
- Monitor resource usage

## Out of Scope
- Dynamic tool discovery (use curated registry)
- Streaming responses
- Models >7B
- Codex CLI --oss approach (proven broken)
