# apps/api

FastAPI backend — chat service, agent router, memory layer, RL, knowledge graph, skill marketplace, ALM. Multi-tenant. Source of truth for the platform's data model.

For full architecture see [`../../CLAUDE.md`](../../CLAUDE.md).

## Layout

```
app/
├── api/v1/            # FastAPI routers, mounted under /api/v1
├── core/              # config, security, deps
├── db/                # SQLAlchemy session + base
├── models/            # ORM models — every table carries tenant_id
├── schemas/           # Pydantic request/response contracts
├── services/          # business logic (one service per resource)
├── memory/            # memory-first package (recall / record / ingest / dispatch)
├── skills/            # bundled skills (also mirrored on the shared volume)
├── workers/           # Temporal worker registration
└── workflows/         # Temporal workflow + activity definitions
migrations/            # manual SQL migrations (no Alembic)
proto/                 # gRPC .proto contracts for embedding + memory-core
                       # (generated stubs land in app/generated/)
storage/               # local cache for uploads, attachments
scripts/               # one-off ops scripts
tests/                 # pytest
```

## Run locally

```bash
cd apps/api
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Or via the full stack: `docker compose up -d` from repo root.

## Test + lint

```bash
pytest                                # full suite
pytest tests/test_api.py              # one file
pytest tests/test_api.py::test_login -v
ruff check app                        # lint
ruff check app --fix
```

## Required env

All three are **required** — startup fails without them:

| Var | Purpose |
|-----|---------|
| `SECRET_KEY` | JWT signing (32+ byte hex) |
| `API_INTERNAL_KEY` | `/api/v1/*/internal/*` auth (32+ byte hex) |
| `MCP_API_KEY` | MCP server ↔ API (24+ byte hex) |
| `ENCRYPTION_KEY` | Fernet key for credential vault |
| `DATABASE_URL` | `postgresql://postgres:postgres@db:5432/agentprovision` |
| `TEMPORAL_ADDRESS` | `temporal:7233` |
| `MCP_SERVER_URL` | `http://mcp-tools:8000` (compose) / `http://agentprovision-mcp` (helm) |
| `ANTHROPIC_API_KEY`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | per-feature |

Generate hex secrets: `python -c "import secrets; print(secrets.token_hex(32))"`. Generate Fernet: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.

## Hard rules

- Every query **must** filter by `tenant_id`. No exceptions. `current_user.tenant_id` comes from the JWT via `deps.get_current_user`.
- Schemas live in `schemas/`, never inline in routes. Pydantic for everything crossing the API boundary.
- Heartbeat discipline for long-running Temporal activities: `heartbeat()` ≤240s or Temporal cancels.
- Memory recall (`memory/recall.py`) has a hard 1500ms cap on the chat hot path — do not extend it.

## Migrations

Manual SQL. Latest is `migrations/113_tenant_github_primary_account.sql`.

```bash
# Apply
PG=$(docker ps --format '{{.Names}}' | grep db-1)
docker exec -i $PG psql -U postgres agentprovision < migrations/NNN_<slug>.sql
docker exec -i $PG psql -U postgres agentprovision \
  -c "INSERT INTO _migrations(filename) VALUES ('NNN_<slug>.sql');"

# Force-add (global .gitignore catches *.sql)
git add -f migrations/NNN_*.sql
```

## Key services

- `agent_router.py` — deterministic + RL-augmented routing, zero LLM cost.
- `cli_session_manager.py` — generates CLAUDE.md per turn, dispatches CLI runtime via Temporal.
- `embedding_service.py` — `embed_text()` routes to Rust gRPC (`embedding-service:50051`) or sentence-transformers fallback.
- `auto_quality_scorer.py` — Gemma 4 / Ollama 6-dim scoring after every chat turn → `rl_experience` table.
- `external_agent_adapter.py` — OpenAI Assistants / webhook / MCP / Copilot Studio dispatch.
- `skill_manager.py` — Skills v2 file-based marketplace (`_bundled/` + `_tenant/<uuid>/`, `library_revisions` audit).

## Workers

Two worker modules live in this app:

- `workers/orchestration_worker.py` — `agentprovision-orchestration` queue (TaskExecution, ChannelHealthMonitor, FollowUp, InboxMonitor, CompetitorMonitor, TeamsMonitor, DynamicWorkflowExecutor, CoalitionWorkflow, AgentPerformanceSnapshot, plus DatasetSync / KnowledgeExtraction / DataSourceSync).
- `workers/scheduler_worker.py` — polls cron/interval pipelines every 60s.

The CLI worker runs in a separate service: see [`../code-worker/`](../code-worker/).
