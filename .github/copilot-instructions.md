# Copilot Instructions for AgentProvision

AgentProvision is a **memory-first, multi-tenant AI agent orchestration platform**. It routes tasks to existing CLI agent runtimes (**Claude Code, Codex, Gemini CLI, GitHub Copilot CLI**) via Temporal workflows, serves **90+ MCP tools**, maintains a knowledge graph in pgvector (768-dim, `nomic-embed-text-v1.5`), and auto-scores responses with **local Gemma 4** (Ollama) for reinforcement learning.

> **Source of truth for architecture:** [`CLAUDE.md`](../CLAUDE.md). Quick agent reference: [`AGENTS.md`](../AGENTS.md). This file is the entry point for GitHub Copilot.

## Quick Start

```bash
# 1. Configure secrets (all three are REQUIRED — startup fails without them)
cp apps/api/.env.example apps/api/.env
# Generate with: python -c "import secrets; print(secrets.token_hex(32))"
# Edit apps/api/.env to set SECRET_KEY, API_INTERNAL_KEY, MCP_API_KEY

# 2. Start the stack (docker-compose is the primary local runtime)
docker compose up -d

# 3. Apply migrations
PG=$(docker ps --format '{{.Names}}' | grep db-1)
for f in apps/api/migrations/*.sql; do
  docker exec -i $PG psql -U postgres agentprovision < "$f"
done

# Endpoints (host ports, mapped through .env)
# Web:        http://localhost:8002              (or https://agentprovision.com via Cloudflare tunnel)
# API:        http://localhost:8000              (or https://agentprovision.com/api/v1/)
# Luna:       http://localhost:8009              (or https://luna.agentprovision.com)
# Temporal:   http://localhost:8233
# Demo login: test@example.com / password
```

> Production-style K8s deployment (Rancher Desktop + Helm) is documented in [`docs/KUBERNETES_DEPLOYMENT.md`](../docs/KUBERNETES_DEPLOYMENT.md) and [`scripts/deploy_k8s_local.sh`](../scripts/deploy_k8s_local.sh).

## Build / Test / Lint

### Python (API + workers + MCP server)

```bash
cd apps/api && pip install -r requirements.txt
pytest                                  # full suite
pytest tests/test_api.py                # single file
pytest tests/test_api.py::test_login -v # single test
ruff check app                          # lint
ruff check app --fix                    # autofix
```

### React Web Frontend

```bash
cd apps/web && npm install
npm start                                  # dev server (port 3000)
npm test                                   # watch mode
npm test -- --ci --watchAll=false          # CI mode
npm run build                              # production bundle
```

### Luna Client (Tauri 2)

```bash
cd apps/luna-client && npm install
npm run tauri dev                          # desktop hot reload
cd src-tauri && cargo check                # Rust type check
```

> Don't build production Tauri DMGs locally — push to `main` and let GitHub Actions build via the `luna-client-build` workflow.

### Monorepo

```bash
pnpm install && pnpm build && pnpm lint
```

## Architecture Overview

### Core Stack

| Service | Path | Port | Purpose |
|---------|------|------|---------|
| API | `apps/api` | 8000 | FastAPI backend, all REST + SSE endpoints |
| Web | `apps/web` | 8002 (host) → 80 (nginx) | React 18 SPA |
| Luna | `apps/luna-client` | 8009 (host) | Tauri 2 desktop + PWA |
| MCP server | `apps/mcp-server` | 8086 | FastMCP, 90+ tools |
| Code worker | `apps/code-worker` | — | Temporal worker on `agentprovision-code` queue |
| Embedding service | `apps/embedding-service` | 50051 | Rust gRPC, fastembed/ONNX |
| Memory core | `apps/memory-core` | 50052 | Rust gRPC, recall + record |
| Temporal | — | 7233 | Workflow engine |
| PostgreSQL | — | 5432 | pgvector/pgvector:pg13 |
| Redis | — | 6379 | Pub/sub + agent registry |
| Ollama | host:11434 | — | Native Gemma 4 (M4 GPU) |
| Cloudflare tunnel | in-cluster pod | — | `agentprovision.com` ingress |

### Request Flow

```
User input  (Web · WhatsApp · Luna desktop · API · Microsoft Teams)
     │
     ▼
FastAPI Chat Service
     │  ┌──────────── Memory Recall (pgvector, 1500ms hard cap) ────────┐
     │  └──────────── Pre-loads context into CLAUDE.md ────────────────┘
     ▼
Agent Router (Python, deterministic, RL-augmented — zero LLM cost)
     │
     ▼
Temporal: ChatCliWorkflow (queue: agentprovision-code)
     │
     ▼
Code Worker pod
     │  ┌── Claude Code · Codex · Gemini CLI · GitHub Copilot CLI ──┐
     │  └── tenant OAuth from vault → set per-subprocess env ──────┘
     ▼
MCP Tool Server (FastMCP SSE :8086, X-Internal-Key auth)
     │
     ▼
Response  →  Auto Quality Scorer (Gemma 4, 6-dim rubric)  →  RL experience
        ↓
        PostChatMemoryWorkflow (entity extraction, async)
```

### Service Organization

`apps/api`:
- **Models** (`app/models/`) — SQLAlchemy ORM, every model carries `tenant_id`. Includes ALM tables (`agent_versions`, `agent_audit_log`, `agent_policies`, `agent_permissions`, `agent_performance_snapshots`, `external_agents`).
- **Services** (`app/services/`) — business logic, CRUD, embeddings, RL, agent router, A2A coalition.
- **Routes** (`app/api/v1/`) — REST + SSE endpoints, dependency-injected.
- **Workers** (`app/workers/`) — Temporal worker registration (orchestration, postgres, scheduler).
- **Workflows** (`app/workflows/`) — Temporal workflow definitions including `DynamicWorkflowExecutor`, `CoalitionWorkflow`, `InboxMonitorWorkflow`, `CompetitorMonitorWorkflow`, `TeamsMonitorWorkflow` (#250).
- **Memory** (`app/memory/`) — recall / record / ingest / dispatch package.
- **Skills** (`app/skills/`) — file-based marketplace (`_bundled/` + `_tenant/<uuid>/`).

`apps/web` — `pages/` (DashboardPage, ChatPage, AgentsPage, AgentDetailPage, WorkflowsPage, MemoryPage, IntegrationsPage, NotebooksPage, SettingsPage, BrandingPage, LLMSettingsPage), `components/` (Layout, NotificationBell, TaskTimeline, IntegrationsPanel, CollaborationPanel, workflows/*, wizard/*).

`apps/code-worker` — Python + Node.js. Owns the CLI subprocess lifecycle. Heartbeat ≤240s. Multi-account-aware GitHub MCP wiring (#249).

`apps/mcp-server` — Tools across knowledge, email, calendar, drive, Jira, GitHub, ads, data, sales, competitor, monitor, reports, analytics, workflows, skills, devices, shell, connectors, tenant-specific, Copilot Studio.

## Key Conventions & Patterns

### Multi-Tenancy (CRITICAL)

Every database query must filter by `tenant_id`. All models inherit a `tenant_id: UUID` FK.

```python
# ✅ Correct
agents = db.query(Agent).filter(Agent.tenant_id == current_user.tenant_id).all()

# ❌ Multi-tenancy break
agents = db.query(Agent).all()
```

### Authentication

- JWT-based: `Authorization: Bearer <token>` header.
- `get_current_user()` dependency (`app/api/v1/deps.py`) extracts the user.
- Tokens expire ~30min — re-login during long sessions.
- Demo: `test@example.com` / `password`.

### Required Secrets (no defaults — hardened 2026-04-18)

Startup `ValidationError` if missing:
- `SECRET_KEY` (32+ byte hex) — JWT signing.
- `API_INTERNAL_KEY` (32+ byte hex) — for `/api/v1/*/internal/*` endpoints (now blocked from public internet at the Cloudflare tunnel since #207).
- `MCP_API_KEY` (24+ byte hex) — for MCP server ↔ API calls.
- `ENCRYPTION_KEY` (Fernet) — credential vault.

> Footgun: in `docker-compose.yml`, `environment:` overrides `env_file`. After rotating a key, recreate services: `docker compose up -d --force-recreate api code-worker orchestration-worker mcp-tools`.

### CLI Runtime Routing

- **Per-tenant default** via `tenant_features.default_cli_platform` (Settings → Integrations).
- **Autodetect + quota fallback chain** (#245) — auto-routes to a working CLI when the preferred one is rate-limited or unavailable.
- **Subscription-based OAuth** — credentials stored Fernet-encrypted in the integration vault, fetched at runtime, never logged.

### Agent Lifecycle Management (ALM, shipped 2026-04-18)

`draft → staging → production → deprecated` with `successor_agent_id`. Versioned snapshots in `agent_versions`, audit in `agent_audit_log`, hourly performance rollup in `agent_performance_snapshots`, RBAC in `agent_permissions`, governance rules in `agent_policies`. Redis-backed capability registry. External agents (OpenAI Assistants, MCP servers, webhooks, Copilot Studio, Azure AI Foundry) via `external_agents` + adapter service.

### A2A Collaboration (shipped 2026-04-12, v2 2026-04-26)

Multi-agent coalitions over a shared **Blackboard**. `CoalitionWorkflow` runs phased patterns (`gather_facts → hypothesize → prescribe`). Each agent turn is a `ChatCliWorkflow` child workflow. Live SSE stream powers `CollaborationPanel`. Patterns ship as `workflow_templates` JSON. Handoffs persist as `ChatMessage(context.kind="handoff")` + `WorkflowRun` — no separate `agent_messages` table. **CLI-agnostic** — never hardcode a CLI in pattern definitions.

### Knowledge Graph + pgvector

Entities (`knowledge_entity.py`) + relations (`knowledge_relation.py`) + observations (`knowledge_observations`) + history (`knowledge_entity_history`). Centralized embeddings via `embedding_service.embed_text()` — routes to Rust gRPC (port 50051) or Python sentence-transformers fallback. 768-dim, `nomic-embed-text-v1.5`. Used by knowledge, chat, memory, RL, skill auto-trigger.

### Auto Quality Scoring & RL

Gemma 4 scores every response across 6 dimensions (100 pts):

| Dim | Pts | Measures |
|-----|-----|----------|
| Accuracy | 25 | Factual correctness, no hallucinations |
| Helpfulness | 20 | Addresses actual user need |
| Tool Usage | 20 | Appropriate MCP tool selection |
| Memory Usage | 15 | Knowledge graph recall |
| Efficiency | 10 | Concise, fast |
| Context Awareness | 10 | Conversation continuity |

Logged as `rl_experience` with cost tracking and platform recommendations. Side-effect tools / low scores / fragile consensus / 5% sample fan out to a **multi-provider review council** (Claude + Codex + Gemma 4) on the `agentprovision-code` queue.

### Service Pattern

```python
from app.services.base import BaseService

class AgentService(BaseService):
    model = Agent

    def create(self, db: Session, tenant_id: UUID, **kwargs):
        obj = self.model(tenant_id=tenant_id, **kwargs)
        db.add(obj); db.commit()
        return obj
```

### Python Imports

```python
# 1. Standard library
import uuid
from datetime import datetime

# 2. Third-party
from fastapi import FastAPI
from sqlalchemy import Column, String

# 3. Local app
from app.db.session import SessionLocal
from app.models.agent import Agent
```

### React Components

```jsx
// PascalCase components
export function WizardStepper({ steps, onComplete }) { ... }

// camelCase service singletons
export const agentService = {
  list: () => axios.get('/api/v1/agents'),
};
```

### Error Handling

- API: 422 (validation), 401/403 (auth), 404 (missing), proper error detail.
- Frontend: try/catch + user-friendly messages, full error to console.
- Always validate tenant isolation in queries.

### Database Migrations (manual, no Alembic)

```bash
# 1. Add SQL file
apps/api/migrations/NNN_<slug>.sql        # NNN = next number
git add -f apps/api/migrations/NNN_*.sql  # *.sql is in global .gitignore — force add

# 2. Apply against the DB pod
PG=$(docker ps --format '{{.Names}}' | grep db-1)
docker exec -i $PG psql -U postgres agentprovision < apps/api/migrations/NNN_<slug>.sql

# 3. Record it
docker exec -i $PG psql -U postgres agentprovision \
  -c "INSERT INTO _migrations(filename) VALUES ('NNN_<slug>.sql');"
```

### Temporal Task Queues

| Queue | Workflows |
|-------|-----------|
| `agentprovision-orchestration` | TaskExecution, ChannelHealthMonitor, FollowUp, InboxMonitor, CompetitorMonitor, **TeamsMonitor** (#250), DynamicWorkflowExecutor, CoalitionWorkflow, AgentPerformanceSnapshot |
| `agentprovision-code` | CodeTaskWorkflow, ChatCliWorkflow, ProviderReviewWorkflow |
| `agentprovision-business` | DealPipeline, RemediaOrder, MonthlyBilling |

> **Heartbeat discipline**: long-running CLI activities must `heartbeat()` ≤240s or Temporal cancels. `execute_chat_cli` is a sync activity in a thread pool with a background heartbeat loop.

### Skill Marketplace v2 (shipped 2026-04-26)

Two-folder file layout:
- `_bundled/` — read-only, ships with the container.
- `_tenant/<uuid>/` — per-tenant, custom + community.
- Format: Claude-Code-style `SKILL.md` (frontmatter + instructions + optional `script.py`/`script.sh`/`prompt.md`).
- Audit: every change → `library_revisions` row (migration 110).
- Code-worker access: via `read_library_skill` MCP tool. Don't mount the library into the worker pod.
- Discovery via MCP: `update_skill`, `update_agent`, `read_library_skill`.

### Adding a New Resource

1. **Model** — `apps/api/app/models/{resource}.py` with `tenant_id` FK.
2. **Schema** — `apps/api/app/schemas/{resource}.py`.
3. **Service** — `apps/api/app/services/{resources}.py` extending `BaseService`.
4. **Routes** — `apps/api/app/api/v1/{resources}.py`, mount in `routes.py`.
5. **Migration** — manual SQL (see above).
6. **Frontend** — page in `apps/web/src/pages/`, route in `App.js`, nav in `Layout.js`.
7. **Helm** — values in `helm/values/` if a new service is needed.

## Configuration & Environment

### API `.env` (`apps/api/.env`)

```bash
SECRET_KEY=<jwt-signing-key>           # 32+ byte hex (REQUIRED)
API_INTERNAL_KEY=<internal-svc-key>    # 32+ byte hex (REQUIRED)
MCP_API_KEY=<mcp-key>                  # 24+ byte hex (REQUIRED)
ENCRYPTION_KEY=<fernet-key>            # python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

ANTHROPIC_API_KEY=sk-ant-...
DATABASE_URL=postgresql://postgres:postgres@db:5432/agentprovision
TEMPORAL_ADDRESS=temporal:7233
MCP_SERVER_URL=http://mcp-tools:8000   # internal compose hostname (helm uses agentprovision-mcp, see #238)

GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
```

### Web `.env.local` (`apps/web/.env.local`)

```bash
REACT_APP_API_BASE_URL=http://localhost:8000
```

### Code-Worker

- `GITHUB_TOKEN` — git + PR ops (from secrets).
- `API_INTERNAL_KEY` — to fetch tenant OAuth tokens.
- `API_BASE_URL=http://api:8000` — internal compose URL (default since #234).
- `CLAUDE_CODE_OAUTH_TOKEN` / Codex `auth.json` / Gemini creds / Copilot token — set per-subprocess from the tenant's vault, never in pod env.

## Hard Rules

- **Never** commit to `main` — feature branch + PR. Assign PRs to `nomade`.
- **Never** add `Co-Authored-By: Claude` (or any AI credit) to commits, PRs, or comments.
- **Never** add docs / plans / tests / scripts at the repo root — use dedicated folders.
- **Never** build production Tauri DMGs locally — push to main, let CI build.
- **Never** run destructive Docker / git commands without approval.
- When making manual changes, **mirror them into Helm + Git + Terraform** to prevent drift.
- All `tenant_id` filtering is mandatory.

## Reference Documentation

- [`CLAUDE.md`](../CLAUDE.md) — full architecture, models, services, dev commands, patterns. Source of truth.
- [`AGENTS.md`](../AGENTS.md) — agent-system layout (CLI runtimes vs platform agents, ALM, A2A).
- [`docs/changelog/`](../docs/changelog/) — weekly digests.
- [`docs/plans/`](../docs/plans/) — design docs and implementation plans.
- [`docs/report/`](../docs/report/) — security audits, pentest verifications.
- [`docs/KUBERNETES_DEPLOYMENT.md`](../docs/KUBERNETES_DEPLOYMENT.md) — K8s runbook.
- [`README.md`](../README.md) — high-level overview, quick start.

---

**Last updated:** 2026-05-03
