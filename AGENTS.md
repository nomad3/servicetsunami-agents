# AGENTS.md

> Quick reference for the agent-system layout. For full architecture see [`CLAUDE.md`](CLAUDE.md).

AgentProvision is a **memory-first, multi-tenant AI agent orchestration platform**. It does not ship its own LLM agent loops — it routes tasks to existing **CLI agent runtimes** (Claude Code, Codex, Gemini CLI, GitHub Copilot CLI) via Temporal workflows. Every tenant uses **its own subscription** through OAuth — zero API credits.

ADK was removed 2026-03-18. AgentKit was removed 2026-04-19 — chat sessions now bind to an **Agent** directly via `chat_sessions.agent_id`.

## Build / Lint / Test

### API (Python 3.11)

```bash
cd apps/api && pip install -r requirements.txt
pytest                          # full suite
pytest tests/test_api.py        # single file
pytest tests/test_api.py::test_login -v
ruff check app                  # lint
ruff check app --fix            # autofix
```

### Web (React 18, CRA)

```bash
cd apps/web && npm install
npm start                       # dev server (port 3000)
npm test                        # watch mode
npm test -- --ci --watchAll=false   # CI mode
npm test -- WizardStepper.test.js
npm run build
```

### Luna client (Tauri 2 + React)

```bash
cd apps/luna-client && npm install
npm run tauri dev               # desktop hot reload
cd src-tauri && cargo check
```

> Don't build Tauri locally for releases — push to `main`, the GitHub Actions pipeline (`.github/workflows/luna-client-build.yaml`) produces signed macOS ARM64 DMGs.

### MCP server

```bash
cd apps/mcp-server && pip install -e ".[dev]"
pytest tests/ -v
python -m src.server            # http://localhost:8086
```

### Rust services (embedding-service, memory-core)

```bash
cd apps/embedding-service && cargo check        # gRPC :50051
cd apps/memory-core && cargo check              # gRPC :50052
```

### Monorepo

```bash
pnpm install && pnpm build && pnpm lint
```

## Agents in this Repo

There are two distinct meanings of "agent" — keep them separate:

1. **CLI agent runtimes** — external processes invoked from `apps/code-worker`. They run as subprocesses with the tenant's OAuth token in env. Heartbeat ≤240s or Temporal cancels.
   | Runtime | Auth | Status |
   |---------|------|--------|
   | Claude Code | `CLAUDE_CODE_OAUTH_TOKEN` from vault | Live |
   | Codex (OpenAI) | `auth.json` from vault | Live |
   | Gemini CLI | OAuth creds from vault (`--skip-trust`) | Live |
   | GitHub Copilot CLI | OAuth token from vault | Live (#244, 2026-04-26) |

2. **Platform Agents** — tenant-defined records in the `agent` table. Each has identity (name, role, capabilities, persona_prompt), runtime config (`tool_groups`, `memory_domains`, `default_model_tier`), and ALM governance (`status`, `version`, `owner_user_id`, `team_id`). Chat sessions bind to one agent.

The **Agent Router** (`apps/api/app/services/agent_router.py`) maps an incoming message → Platform Agent → CLI runtime via deterministic rules + RL policy. Zero LLM cost.

## Default Tenant Agents

New tenants are seeded with a **Luna Supervisor** agent (general-purpose, WhatsApp-native co-pilot). Additional out-of-the-box roles available via wizard: Code Agent, Data Analyst, Knowledge Manager, Sales Agent, Customer Support, Marketing Analyst, Web Researcher, Cardiac Analyst (HealthPets), Billing Agent, Vet Supervisor.

External agents (OpenAI Assistants, MCP servers, webhook endpoints, Microsoft Copilot Studio, Azure AI Foundry) register via `external_agents` and dispatch through `external_agent_adapter.py`.

## Agent Lifecycle Management (ALM, shipped 2026-04-18)

Governance layer for production agents:

- **Lifecycle**: `draft → staging → production → deprecated` with `successor_agent_id` for graceful migration.
- **Versioning**: every promote snapshots config to `agent_versions`. `POST /agents/{id}/rollback/{version}`.
- **Audit log**: `agent_audit_log` rows for create/update/promote/deprecate/rollback/integration-change. `GET /agents/{id}/audit-log`.
- **Performance**: hourly Temporal rollup → `agent_performance_snapshots`. `GET /agents/{id}/performance`.
- **Policies**: `agent_policies` — rate limits, approval gates, allowed tools, blocked actions.
- **RBAC**: `agent_permissions` — owner/editor/viewer per user or team. Enforced by `deps.require_agent_permission`.
- **Registry**: Redis-backed capability discovery. `GET /agents/discover?capability=<x>`.
- **Heartbeat**: `POST /agents/{id}/heartbeat` for external/long-running agents.

UI: `AgentsPage` fleet view + `AgentDetailPage` (Overview / Performance / Audit / Versions / Integrations tabs).

## A2A Collaboration (shipped 2026-04-12, v2 2026-04-26)

Multi-agent coalitions over a shared **Blackboard**:

- `CoalitionWorkflow` (queue: `agentprovision-orchestration`) runs phased patterns: `gather_facts → hypothesize → prescribe`.
- Each agent turn is a `ChatCliWorkflow` child workflow on `agentprovision-code` queue.
- Patterns shipped: `incident_investigation`, `deal_brief`, `cardiology_case_review` (defined as `workflow_templates` JSON).
- Handoffs persist as `ChatMessage(context.kind="handoff")` + `WorkflowRun` — no separate `agent_messages` table.
- Live stream: Redis pub/sub → `GET /chat/sessions/{id}/events/stream` SSE → `CollaborationPanel`.
- **CLI-agnostic**: dispatches always go through RL routing, never hardcode a CLI.

## MCP Tools (90+)

Served by `apps/mcp-server` over MCP SSE on port 8086. Auth: `X-Internal-Key` + `X-Tenant-Id`. Categories: knowledge graph (11), email (6), calendar (2), drive (4), Jira (5), GitHub (8, multi-account aware), Copilot Studio (1), ads (12), data (4), sales (6), competitor (5), monitor (6), reports (2), analytics (3), dynamic workflows (8), skills (4), devices (2), shell (2), connectors (1), tenant-specific (~8).

### Calling MCP tools (Luna / OpenCode / local Gemma 4)

All tool calls **must include**:

```
tenant_id: "<tenant-uuid>"
```

Tools fail without it.

## Skill Marketplace v2 (shipped 2026-04-26)

File-based skills laid out across two folders on a shared volume:

- `_bundled/` — read-only, ships with the container.
- `_tenant/<uuid>/` — per-tenant skills (custom + community imports).
- Format: Claude-Code-style `SKILL.md` (frontmatter + instructions). Engines: `python` (script.py), `shell` (script.sh), `markdown` (prompt.md), `tool` (class registry).
- Audit: every change written to `library_revisions` (migration 110).
- Code-worker reads via the **`read_library_skill` MCP tool** (do not mount the library into the worker pod — that decision is intentional).
- Discovery: `update_skill`, `update_agent`, `read_library_skill` MCP tools.
- Semantic auto-trigger via pgvector embeddings.

## Dynamic Workflows

JSON-defined, interpreted at runtime by a single `DynamicWorkflowExecutor` Temporal workflow. Step types: `mcp_tool`, `agent`, `condition`, `for_each`, `parallel`, `wait`, `transform`, `human_approval`, `webhook_trigger`, `workflow`, `continue_as_new`, `cli_execute`, `internal_api`. Triggers: `cron`, `interval`, `webhook`, `event`, `manual`, `agent`. 26 native templates + Cardiac Report Generator (HealthPets). Visual builder at `/workflows/builder/:id`.

## Auto Quality Scoring & RL

Every response is auto-scored locally by **Gemma 4** via Ollama (native host, port 11434, M4 GPU ~57 tok/s) across a 6-dim rubric:

| Dim | Pts |
|-----|-----|
| Accuracy | 25 |
| Helpfulness | 20 |
| Tool Usage | 20 |
| Memory Usage | 15 |
| Efficiency | 10 |
| Context Awareness | 10 |

Scores logged as `rl_experience` with reward components, token/cost tracking, platform recommendation. Used to learn which CLI runtime performs best per task type. Side-effect tools, low scores (<40), fragile consensus, or 5% random sample additionally fan out to a **multi-provider review council** (Claude + Codex + Gemma 4) on the `agentprovision-code` queue.

## Local ML (zero cloud cost)

`apps/api/app/services/local_inference.py` routes all lightweight ML to native Ollama / Gemma 4: response generation (Luna fallback), summarization, knowledge extraction, inbox triage, competitor analysis, intent classification, MCP tool calling.

## Memory-First Path

`apps/api/app/memory/`:

- `recall.py` — pre-loads context into CLAUDE.md before each chat turn (1500ms hard timeout, pgvector semantic search, token budget).
- `record.py` — sync writes for observations, commitments, goals.
- `ingest.py` — bulk source-adapter event ingestion.
- `dispatch.py` — fires `PostChatMemoryWorkflow` after each turn.
- Feature flag: `USE_MEMORY_V2=true`.
- Rust gRPC services back the hot path: `embedding-service` (fastembed/ONNX, port 50051) and `memory-core` (port 50052). Phase 2 dual-read enabled.

## Code Style

### Python

- SQLAlchemy models with UUID PKs, **always** include `tenant_id`.
- Multi-tenant filter on every query: `db.query(Model).filter(Model.tenant_id == tenant_id)`.
- Pydantic for request/response schemas.
- Import order: stdlib → third-party → local app.
- Models in `app/models/`, services in `app/services/`, routers in `app/api/v1/`.

### React (Web)

- Functional components + hooks. Bootstrap 5 + React Bootstrap, glassmorphic Ocean theme.
- Components: `PascalCase`. Service singletons: `camelCase`.
- Axios with JWT bearer header (token in `localStorage`).

### Rust (Luna client + memory services)

- Tauri 2 plugins: `global-shortcut`, `updater`, tray API.

### Error handling

- API: proper status codes, error details. Validation: 422; auth: 401/403; missing: 404.
- Frontend: try/catch with user-friendly messages, full errors logged.
- Always validate tenant isolation in queries.

## Adding a Resource

1. **Model** — `apps/api/app/models/{resource}.py` with `tenant_id` FK.
2. **Schema** — `apps/api/app/schemas/{resource}.py` (`Create` / `Update` / `InDB`).
3. **Service** — `apps/api/app/services/{resources}.py` extending `BaseService`.
4. **Routes** — `apps/api/app/api/v1/{resources}.py`, mount in `routes.py`.
5. **Migration** — manual SQL in `apps/api/migrations/NNN_<slug>.sql`, then `INSERT INTO _migrations(filename) VALUES (...)`. (No Alembic.)
6. **Frontend** — page in `apps/web/src/pages/`, route in `App.js`, nav in `Layout.js`.
7. **Helm** — values in `helm/values/` if a new service is needed.

## Hard Rules

- **Never** commit to `main` — always feature branch + PR. Assign PRs to `nomade`.
- **Never** add `Co-Authored-By: Claude` or AI credits anywhere.
- **Never** add docs / plans / tests / scripts at the repo root — use dedicated folders (`docs/plans/`, `docs/report/`, `docs/changelog/`, `scripts/`).
- **Never** run destructive Docker / git commands without explicit approval.
- When making manual changes, **mirror them into Helm + Git + Terraform** to prevent drift.
- All `tenant_id` filtering on queries is mandatory; missing it is a multi-tenancy break.
