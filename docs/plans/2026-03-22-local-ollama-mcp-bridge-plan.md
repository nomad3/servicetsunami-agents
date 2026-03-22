# Local Ollama MCP Bridge — Tool Calling for Free-Tier Tenants

**Date**: 2026-03-22
**Status**: Planned
**Goal**: Give tenants without a Claude/Codex subscription access to MCP tools via a lightweight local model through Ollama's native tool calling API.

## Context

- Codex CLI `--oss --local-provider ollama` does NOT wire MCP tool calls to local models — models output tool calls as plain text
- Ollama's `/api/chat` endpoint natively supports a `tools` parameter with function calling (separate from Codex CLI)
- We need a thin bridge: Ollama `/api/chat` with tools ↔ our MCP server (81 tools at `http://mcp-tools:8000/mcp`)

## Recommended Model

**qwen3:4b** — 2.5GB download, ~4GB RAM
- Native tool calling CONFIRMED working via Ollama `/api/chat` with `tools` param
- Produces proper structured `tool_calls` (not text) — tested 2026-03-22
- Reportedly rivals Qwen2.5-72B performance despite 4B params
- Built-in thinking/reasoning before tool selection
- Already pulled on local Ollama

Fallback options: `qwen3:1.7b` (~1.1GB), `ministral:3b` (~2GB)

## Architecture

```
User message (no subscription)
  → cli_session_manager.py detects missing credentials
  → calls local_tool_agent() instead of Temporal workflow
  → local_tool_agent:
      1. Fetches available MCP tools from mcp-tools server (GET /mcp/tools or similar)
      2. Converts MCP tool schemas → Ollama tool format
      3. Calls Ollama /api/chat with message + tools
      4. If model returns tool_calls → executes them against MCP server
      5. Feeds tool results back to model for final response
      6. Returns final text to user
```

## Tasks

### 1. ~~Pull qwen3:4b model~~ DONE
- Already pulled and tested (2026-03-22)
- Tool calling confirmed: returns structured `tool_calls` with correct args
- First load ~148s (cold), subsequent calls much faster

### 2. Build MCP tool schema converter
- File: `apps/api/app/services/local_tool_agent.py`
- Fetch tool list from MCP server (`http://mcp-tools:8000/mcp` — check endpoint)
- Convert MCP tool JSON schema → Ollama's tool calling format:
  ```json
  {
    "type": "function",
    "function": {
      "name": "knowledge_search",
      "description": "Search the knowledge graph",
      "parameters": { "type": "object", "properties": {...} }
    }
  }
  ```
- Cache tool schemas (don't fetch on every message)

### 3. Build the agent loop
- In `local_tool_agent.py`:
  - Send user message + tool schemas to Ollama `/api/chat`
  - Parse response for `tool_calls`
  - If tool_calls present: execute each against MCP server, collect results
  - Send tool results back to model as follow-up messages
  - Max 3 tool call rounds to prevent loops
  - Return final assistant text
- Use `qwen3:4b` by default, configurable via `LOCAL_TOOL_MODEL` env var

### 4. Wire into cli_session_manager.py fallback
- Replace `generate_luna_response_sync()` call with `local_tool_agent()` call
- Keep `generate_luna_response_sync()` as fallback if tool agent fails
- Metadata should include: `platform=local_qwen`, `fallback=true`, `tools_used=[...]`

### 5. Subset tool selection
- 81 tools is too many for a 3B model's context window
- Select a subset of ~10-15 most useful tools for chat:
  - `knowledge_search`, `knowledge_list_entities`, `knowledge_create_entity`
  - `memory_search`, `memory_list_activities`
  - `email_search`, `email_send` (if Google connected)
  - `calendar_list_events` (if Google connected)
  - `jira_search_issues`, `jira_create_issue` (if Jira connected)
  - `shell_execute` (basic commands)
  - `report_generate`
- Filter based on which integrations the tenant has connected

### 6. Test end-to-end
- Create test tenant with no Claude/Codex credentials
- Send messages that should trigger tool use:
  - "Search my knowledge base for contacts"
  - "What meetings do I have today?"
  - "Create a new entity for Acme Corp"
- Verify tool calls execute and results return correctly
- Monitor laptop resource usage during inference

## Out of Scope
- Codex CLI `--oss` mode (proven to not work for MCP tool calling)
- Models larger than 7B (laptop thermal constraints)
- Streaming responses (keep it simple, batch response)

## References
- [Ollama Tool Calling Docs](https://docs.ollama.com/capabilities/tool-calling)
- [Ollama MCP Bridge](https://github.com/patruff/ollama-mcp-bridge) — reference implementation
- [Qwen 2.5 Function Calling](https://qwen.readthedocs.io/en/latest/framework/function_call.html)
