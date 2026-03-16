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

### 2. Unified MCP Server as Tool Marketplace (Anthropic Convention)

The MCP server follows the same three-tier marketplace pattern as skills. Tools are markdown definition files — importable from GitHub, community-shareable, and user-customizable.

**Three tiers:**
```
tools/
├── native/              # Built-in, ship with container (read-only)
│   ├── email/tool.md
│   ├── calendar/tool.md
│   ├── knowledge/tool.md
│   ├── data/tool.md
│   └── ...
├── community/           # Imported from GitHub / MCP Registry
│   ├── slack/tool.md
│   ├── notion/tool.md
│   ├── stripe/tool.md
│   └── ...
└── tenant_{id}/         # Per-tenant custom tools
    ├── internal_api/tool.md
    └── ...
```

**Tool definition format** (same YAML frontmatter + markdown as skills):
```yaml
# tools/native/email/tool.md
---
name: search_emails
engine: mcp_tool
category: communication
auth_type: oauth
integration: gmail
input_schema:
  query: { type: string, required: true, description: "Gmail search query" }
  max_results: { type: integer, default: 10 }
  account_email: { type: string, default: "" }
output_schema:
  emails: { type: array }
  total: { type: integer }
---
Search Gmail or Outlook inbox using standard search operators.
Requires OAuth token from credential vault.
```

**Import from GitHub** (same endpoint as skills):
- `POST /tools/import-github {"repo_url": "https://github.com/modelcontextprotocol/servers/tree/main/src/slack"}`
- Supports MCP Registry at `registry.modelcontextprotocol.io`
- Auto-adapter normalizes external MCP server formats to our tool.md schema

**Create custom tools in UI:**
- Tenant defines tool name, description, input/output schema, auth type
- Saved as `tools/tenant_{id}/{slug}/tool.md`
- Live immediately — MCP server hot-reloads

**Shared backend with skills:**
- `SkillManager` extended to handle `engine: mcp_tool`
- Same version, fork, edit, CHANGELOG flow
- Same pgvector embedding for semantic auto-trigger matching
- Tools page in frontend already exists — extended with import/create

**Dynamic MCP server:**
- On startup, scans tool marketplace directories
- Registers `@mcp.tool()` for each tool.md definition
- Per-tenant: tenant sees native + community + their custom tools
- New tools added at runtime without restart

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

Two execution modes: **fast path** (conversational) and **async path** (heavy tasks).

**Fast path (chat messages — WhatsApp, web):**
1. User sends message (WhatsApp/Web)
2. Chat Service → Agent Router (Python, no LLM)
3. Session Manager loads agent skill, generates platform-specific config
4. **Direct subprocess** from API process (no Temporal):
   ```bash
   claude -p "user message" \
     --output-format json \
     --mcp-config /tmp/sessions/{id}/mcp.json \
     --project-dir /tmp/sessions/{id}/
   ```
5. CLI connects to MCP server, uses tools, returns response
6. Chat Service saves response, embeds it, logs RL experience
7. Latency target: <15s for WhatsApp, <10s for web chat

**Async path (heavy tasks — code, deep scan, reports):**
1. Agent Router detects heavy task (code generation, bulk email scan, report)
2. Dispatches `AgentSessionWorkflow` to Temporal (same code-worker pattern)
3. Temporal activity runs CLI with longer timeout (15 min)
4. Result returned via webhook/poll to chat service
5. WhatsApp typing indicator stays active until completion

**Stateless CLI invocations (each call is independent):**
- CLI does NOT manage conversation history across calls
- ServiceTsunami injects context into each prompt:
  ```
  [Conversation summary from last 6 messages]
  [Relevant knowledge graph entities recalled via embeddings]
  [Current user message]
  ```
- This is the same pattern as the existing context rotation in `chat.py`
- No dependency on `--resume` or persistent CLI sessions
- Pod restarts, scaling events, and CLI updates have zero impact

**Session rotation:** When injected context exceeds 80% of CLI's context window, summarize and trim. Same cumulative token tracking as current implementation.

**Key principle:** CLIs are stateless tools invoked per-message. ServiceTsunami owns conversation history, memory, and context injection. CLIs own the LLM call, tool execution, and response generation.

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

## Existing Code Worker

The existing `apps/code-worker/` stays and becomes the blueprint for all CLI activities. It already proves the pattern:
- Temporal workflow + activity for CLI subprocess execution
- Per-tenant OAuth token fetched at runtime from credential vault
- `claude -p` invocation with `--output-format json`
- Git worktree isolation, PR creation, full audit trail

The pivot extends this by adding:
- `GeminiCliActivity` — same pattern, different CLI binary + auth env var
- `CodexCliActivity` — same pattern, different CLI binary + auth env var
- Fast-path direct subprocess for conversational messages (skip Temporal)
- MCP config generation per session

## CLI Platform Auth (One-Click Integration)

All three CLIs support subscription-based OAuth — no API credits needed. Follows the same integration card pattern as Gmail, GitHub, and Jira.

| CLI | OAuth Flow | Env Var | Subscription Tiers |
|---|---|---|---|
| Claude Code | Anthropic OAuth | `CLAUDE_CODE_OAUTH_TOKEN` | Pro, Max, Team |
| Codex | "Sign in with ChatGPT" | `OPENAI_CODEX_TOKEN` | Plus, Pro, Team, Enterprise |
| Gemini CLI | "Sign in with Google" | `GEMINI_AUTH_TOKEN` | Free tier, AI Pro, AI Ultra |

**User flow (same as existing integrations):**
1. User opens **Integrations** page
2. Clicks "Connect Claude Code" / "Connect Codex" / "Connect Gemini CLI"
3. OAuth redirect → user logs in with their subscription account
4. Token stored in credential vault (Fernet encrypted, per-tenant)
5. Agent Router uses their subscription for CLI calls — zero API cost

**Integration registry entries** (added to `INTEGRATION_CREDENTIAL_SCHEMAS`):
- `claude_code` — already exists (code-worker uses it)
- `openai_codex` — new, OAuth flow to ChatGPT
- `gemini_cli` — new, OAuth flow to Google (extends existing Google OAuth)

**Headless execution:** For K8s workers that can't open a browser, tokens are fetched from the vault at runtime (same as code-worker: `GET /api/v1/oauth/internal/token/{integration_name}`).

## Platform MCP Compatibility

Before adding a CLI platform, validate MCP support:
- **Claude Code CLI**: Full MCP support via `--mcp-config`. Confirmed.
- **Gemini CLI**: Validate `--mcp-config` support. If not available, use Gemini's native function calling with a shim that translates MCP tool definitions.
- **Codex CLI**: Validate MCP support. If not available, same shim approach.

Phase 1 ships with Claude Code CLI only. Gemini and Codex are added after MCP support is confirmed. If a platform lacks MCP, build a lightweight adapter that translates MCP tools to the platform's native tool format.

## Design Decisions (from spec review)

**Stateless CLI invocations over persistent sessions:** Each CLI call is independent. No `--resume`, no session persistence, no pod affinity. ServiceTsunami owns history and injects context per-call. Simplest, most resilient pattern.

**Fast path + async path over Temporal-only:** Conversational messages use direct subprocess (<15s). Heavy tasks use Temporal workflows (up to 15min). Avoids Temporal dispatch overhead for chat.

**Incremental MCP migration over big-bang rewrite:** Phase 1 adds FastMCP tools alongside existing MCP server. Tools are ported one group at a time. Existing Databricks tools stay untouched.

**Deterministic routing before RL routing:** Phase 1-2 use tenant default + agent affinity. RL exploration added in Phase 3 once baseline metrics exist per platform.

**3-platform cost catalog over 329-model catalog:** Start with pricing for Claude, Gemini, Codex models only. Expand later.

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
