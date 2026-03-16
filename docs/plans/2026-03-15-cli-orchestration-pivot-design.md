# CLI Orchestration Pivot — Design Spec

> ServiceTsunami pivots from custom ADK agents to an orchestration layer on top of existing agent platforms (Claude Code CLI, Gemini CLI, Codex CLI).

**Date:** 2026-03-15
**Status:** Approved
**Author:** Simon Aguilera + Claude

---

## Motivation

Agent platforms (Claude Code, Gemini CLI, Codex) handle context windows, memory, tool calling, and rate limits natively. Building custom agents on top of ADK is expensive (burned $100+ in API credits in one day), fragile (context overflow, rate limit retries), and duplicates work these platforms already do. ServiceTsunami's value is the orchestration layer: routing, multi-tenancy, integrations, knowledge graph, skill marketplace, and RL learning — not the LLM agent itself.

## Target CLI Platforms

| Platform | Auth | Use Case |
|----------|------|----------|
| Claude Code CLI | OAuth token (subscription) | General assistant, code, complex reasoning |
| Gemini CLI | Google API key (free tier) | Data analysis, bulk operations, cost-sensitive |
| Codex CLI | OpenAI API key | Code generation, technical tasks |

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  Channels: WhatsApp (Neonize) · Web Chat · API                  │
└──────────────────────────┬──────────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────────┐
│  FastAPI Backend                                                 │
│  ┌─────────────┐ ┌──────────────┐ ┌──────────────────────────┐  │
│  │ Chat Service │ │ Agent Router │ │ Session Manager          │  │
│  │ (entry point)│ │ (RL + tenant │ │ (CLI session lifecycle,  │  │
│  │             │ │  default)    │ │  persistence, rotation)  │  │
│  └──────┬──────┘ └──────┬───────┘ └──────────────────────────┘  │
│         │               │                                        │
│  ┌──────▼───────────────▼───────────────────────────────────┐   │
│  │  Temporal Client — dispatches AgentSessionWorkflow        │   │
│  └──────────────────────┬───────────────────────────────────┘   │
└─────────────────────────┼───────────────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────────────┐
│  Temporal Workers (servicetsunami-agents queue)                   │
│                                                                   │
│  ┌────────────────┐ ┌────────────────┐ ┌──────────────────┐     │
│  │ Claude Code    │ │ Gemini CLI     │ │ Codex CLI        │     │
│  │ Activity       │ │ Activity       │ │ Activity         │     │
│  │ claude -p      │ │ gemini -p      │ │ codex --prompt   │     │
│  │ + OAUTH_TOKEN  │ │ + GOOGLE_KEY   │ │ + OPENAI_KEY     │     │
│  └───────┬────────┘ └───────┬────────┘ └────────┬─────────┘     │
│          └──────────────────┼────────────────────┘               │
│                             │                                    │
│                    ┌────────▼────────┐                           │
│                    │  Unified MCP    │                           │
│                    │  Server         │                           │
│                    │ (FastMCP, HTTP) │                           │
│                    └─────────────────┘                           │
└──────────────────────────────────────────────────────────────────┘
```

## Component Design

### 1. Agent Router

Pure Python routing — zero LLM cost. Replaces ADK root supervisor.

**Routing logic:**
1. Check `tenant_features.default_cli_platform`
2. Query RL policy for learned platform preference by task type
3. Look up agent's `platform_affinity` from skill marketplace
4. Check tenant has valid credentials for chosen platform
5. Fall back to `fallback_platform` if primary unavailable
6. Dispatch to Temporal with `{ platform, agent_skill_slug, message, tenant_creds }`

**RL integration:**
- Each dispatch logs `agent_routing` experience: `{ platform, agent, task_type }`
- User feedback (thumbs up/down) propagates reward
- Learning page shows platform-level performance comparison
- Router converges to best platform per task type over time

### 2. Unified MCP Server (Anthropic Convention)

Single MCP server using `mcp` Python SDK (`FastMCP`), Streamable HTTP transport.

**Stack:** `FastMCP(stateless_http=True, json_response=True)`

**Structure:**
```
apps/mcp-server/
├── src/
│   ├── server.py              # FastMCP instance + HTTP transport
│   ├── auth.py                # Tenant auth from request headers
│   ├── tools/
│   │   ├── email.py           # search_emails, read_email, send_email, deep_scan, download_attachment
│   │   ├── calendar.py        # list_events, create_event
│   │   ├── jira.py            # search/get/create/update issues
│   │   ├── knowledge.py       # entity CRUD, relations, observations, semantic search
│   │   ├── data.py            # query_sql, discover_datasets, generate_insights
│   │   ├── github.py          # repo operations
│   │   ├── ads.py             # meta/google/tiktok campaigns
│   │   ├── competitor.py      # competitor monitoring
│   │   ├── skills.py          # skill marketplace CRUD
│   │   └── reports.py         # Excel report generation
│   ├── resources/
│   │   ├── knowledge.py       # Knowledge graph as MCP resources
│   │   └── tenant.py          # Tenant config, features
│   └── prompts/
│       └── agents.py          # Agent instruction templates from skill marketplace
├── pyproject.toml
└── Dockerfile
```

**Tool pattern:**
```python
from mcp.server.fastmcp import FastMCP, Context

mcp = FastMCP("ServiceTsunami", stateless_http=True, json_response=True)

@mcp.tool()
async def search_emails(query: str, max_results: int = 10, account_email: str = "", ctx: Context = None) -> dict:
    """Search Gmail or Outlook inbox."""
    tenant_id = ctx.request_context.get("tenant_id")
    # ... same logic as current google_tools.py
```

**Tenant auth:** CLI sessions connect with headers `X-Tenant-Id` and `X-Internal-Key`. MCP server validates and scopes all operations.

**Existing Databricks tools stay** — new tools added alongside in same server.

### 3. CLI Session Lifecycle

**New conversation:**
1. User sends message (WhatsApp/Web)
2. Chat Service → Agent Router (Python, no LLM)
3. Session Manager creates session record, loads agent skill, generates platform-specific config file (CLAUDE.md/GEMINI.md/CODEX.md) + MCP config
4. Dispatches `AgentSessionWorkflow` to Temporal
5. Temporal activity runs CLI subprocess:
   ```bash
   claude -p "user message" \
     --output-format json \
     --mcp-config /tmp/sessions/{id}/mcp.json \
     --project-dir /tmp/sessions/{id}/
   ```
6. CLI connects to MCP server, uses tools, returns response
7. Activity returns response text → Chat Service saves, embeds, logs RL

**Continuing conversation:** Same CLI invoked with `--resume {session_id}`. CLI handles conversation history natively.

**Session rotation:** When CLI returns context overflow error or token threshold exceeded, Session Manager archives old session, creates new one with `conversation_summary` injected into the instruction file.

**Key principle:** CLI handles context windows, memory, and tool calling. We only manage routing, tools (MCP), instructions (skills), tenant isolation, and observability.

### 4. Agent Skill → Platform File Generation

Each agent is a skill (`engine: agent`) in the marketplace.

**Skill format:**
```yaml
# skills/agents/luna/skill.md
---
name: Luna
engine: agent
platform_affinity: claude_code
fallback_platform: gemini_cli
category: personal_assistant
tags: [whatsapp, copilot, business]
---
You are Luna — AI chief of staff on the ServiceTsunami platform.
## Personality
Warm, conversational, brief. Send short messages like texting a friend.
## Tools Available
- search_emails, read_email, send_email, deep_scan_emails, download_attachment
- list_calendar_events, create_calendar_event
- search_jira_issues, create_jira_issue, update_jira_issue
- create_entity, find_entities, update_entity, create_relation, record_observation
- query_sql, discover_datasets
## Behaviors
- ALWAYS extract entities from every interaction
- Use deep_scan_emails for bulk operations
...
```

**Generation:** Template function loads skill.md body, injects tenant context (name, user, channel), writes to session directory as CLAUDE.md / GEMINI.md / CODEX.md. No LLM needed.

**Migration:** Extract existing 25 agent instruction strings from Python files into `skills/agents/{name}/skill.md`. Each becomes a marketplace skill with `engine: agent` and `platform_affinity`.

### 5. Observability Layer (Lightweight LangWatch Alternative)

Built on existing RL system + execution traces. Uses `langevals` (Apache 2.0) for evaluators.

**New components:**
```
apps/api/app/services/observability/
├── trace_collector.py    # Structured spans (parent/child) per CLI invocation
├── cost_catalog.py       # 329+ model pricing from open data
├── evaluators.py         # Quality evaluators using langevals (Apache 2.0)
└── dashboard_service.py  # Timeseries aggregations
```

**Trace model:**
```python
class AgentSpan:
    id, parent_span_id         # Nested span tree
    session_id, tenant_id
    platform: str              # claude_code | gemini_cli | codex_cli
    agent_slug: str            # luna, data_analyst, etc.
    span_type: str             # llm_call | tool_call | routing | total
    input_tokens, output_tokens, cost_usd
    duration_ms
    status: str                # success | error | timeout
    metadata: JSONB
```

**Evaluators** (async, post-response, like knowledge extraction):
- `sentiment` — user satisfaction trends
- `off_topic` — detect off-rails responses
- `pii_detection` — flag sensitive data
- `answer_relevancy` — response usefulness

Scores feed into `rl_experiences.reward` as implicit signal.

**Cost catalog:** 329+ model pricing covering all CLI platforms. Replaces hardcoded 2-provider dict.

**Dashboard:** Extends Workflows page with cost per platform, quality scores per agent, platform comparison, token breakdown.

## Migration Strategy

Incremental per agent. ADK stays as fallback throughout. Feature flag `cli_orchestrator_enabled` per tenant.

### Phase 1 — Luna on Claude Code CLI (Week 1-2)
- Extract Luna → `skills/agents/luna/skill.md`
- Build `AgentSessionWorkflow` + `ClaudeCodeActivity`
- Build unified MCP server (email + calendar + knowledge tools)
- Wire WhatsApp → Agent Router → Temporal → Claude Code CLI
- Feature flag off by default. Test locally.
- ADK handles web chat and all other agents.

### Phase 2 — All agents as skills + Gemini CLI (Week 3-4)
- Extract 24 remaining agents → skills
- Build `GeminiCliActivity`
- Platform file generation (CLAUDE.md, GEMINI.md)
- Agent Router with tenant default + RL routing
- Web chat switches to CLI orchestrator
- ADK becomes fallback only.

### Phase 3 — Codex CLI + Observability (Week 5-6)
- Build `CodexCliActivity`
- Port remaining MCP tools (jira, github, ads, competitor, data, reports)
- Build observability layer (spans, cost catalog, evaluators)
- Extend Learning page with platform comparison
- Dashboard: cost/quality per CLI platform.

### Phase 4 — ADK removal + Production (Week 7-8)
- All tenants on CLI orchestrator
- Remove ADK server from deployment
- Full observability + RL-driven routing live
- Production deploy to GKE.

**Rollback:** At every phase, `cli_orchestrator_enabled = false` reverts to ADK. Knowledge graph, RL, skills, credentials all in PostgreSQL — independent of agent executor.

## What Stays vs What Goes

| Stays (value layer) | Goes (replaced by CLIs) |
|---|---|
| Agent Router (new, Python) | ADK root supervisor |
| Unified MCP Server (FastMCP) | ADK tool functions |
| Skill Marketplace (agent definitions) | Hardcoded Python agent files |
| Knowledge Graph + pgvector | — |
| RL System + Learning Page | — |
| Credential Vault (Fernet) | — |
| WhatsApp Gateway (Neonize) | — |
| Local Embeddings (nomic-embed) | — |
| Temporal Workflows | — (extended, not replaced) |
| Session Manager (new) | ADK session management |
| Observability Layer (new) | Basic execution traces |
| Cost Catalog (new) | Hardcoded 2-provider pricing |
| Multi-tenant isolation | — |

## Tech Stack Summary

| Component | Technology |
|---|---|
| CLI Platforms | Claude Code CLI, Gemini CLI, Codex CLI |
| Orchestration | Temporal (workflows + activities) |
| Tool Protocol | MCP (FastMCP, Streamable HTTP) |
| Agent Definitions | Skill Marketplace (SKILL.md → CLAUDE.md/GEMINI.md/CODEX.md) |
| Routing | Python + RL policy (zero LLM cost) |
| Embeddings | nomic-embed-text-v1.5 (local, 768-dim) |
| Vector Search | pgvector |
| Evaluation | langevals (Apache 2.0) |
| Database | PostgreSQL |
| Messaging | WhatsApp via Neonize |
| Infrastructure | GKE + Helm + GitHub Actions |
