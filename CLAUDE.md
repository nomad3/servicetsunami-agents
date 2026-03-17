# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

ServiceTsunami is an AI agent orchestration platform that routes tasks to **Claude Code CLI** (Opus 4.6) via Temporal workflows. Agents are defined as marketplace skills, tools are served via **MCP** (77 tools), and the platform learns from user feedback via RL. Runs from a laptop via **Cloudflare Tunnel** serving both `servicetsunami.com` and `agentprovision.com`.

**Key architecture**: Chat → Agent Router (Python, zero LLM cost) → Temporal → code-worker (Claude Code CLI with `--model opus`) → MCP tools (FastMCP, 77 tools) → response. The ADK server is legacy and being deprecated — the CLI orchestrator (`cli_orchestrator_enabled` feature flag) is the primary path.

## Architecture

### Monorepo Structure

Docker Compose stack with Cloudflare Tunnel:

- **`apps/api`** (port 8001): FastAPI backend — chat service, agent router, RL, knowledge graph, skill marketplace
- **`apps/code-worker`**: Claude Code CLI execution via Temporal. Has git, gh, claude, node. Fetches GitHub + Claude Code tokens from vault per-session.
- **`apps/mcp-server`** (port 8086): Original REST MCP server (Databricks/scraping)
- **`mcp-tools`** (port 8087): FastMCP with 77 tools (email, calendar, knowledge, jira, github, data, ads, competitor, monitor, sales, reports, shell, analytics, skills)
- **`apps/web`** (port 8002): React SPA with markdown rendering (react-markdown), Ocean theme
- **`apps/adk-server`** (port 8085): **Legacy — being deprecated**. 25 custom agents via Google ADK + LiteLLM.
- **`cloudflared`**: Cloudflare Tunnel — routes servicetsunami.com + agentprovision.com to local stack
- **`temporal`** (port 7233): Workflow engine for durable task execution
- **`db`** (port 8003): PostgreSQL + pgvector

Previously a Turborepo monorepo managed with `pnpm` workspaces:

- **`apps/api`**: FastAPI backend (Python 3.11)
  - Multi-tenant JWT-secured REST API
  - Synchronous SQLAlchemy with PostgreSQL (not async despite asyncpg driver)
  - Temporal workflow integration for orchestration
  - Seed data initialization on startup via `init_db.py`

- **`apps/web`**: React SPA (JavaScript, React 18, Create React App)
  - Bootstrap 5 + React Bootstrap UI
  - React Router v7 for navigation
  - i18next for internationalization
  - Authenticated console at `/dashboard/*`, marketing landing page at `/`

- **`apps/adk-server`**: Google Agent Development Kit (ADK) server (Python 3.11)
  - Multi-agent orchestration with supervisor pattern (25 agents across 5 teams)
  - LLM-agnostic via `before_model_callback` + LiteLLM — switches between Gemini/Anthropic/others per-request
  - Model callback registered on all 25 agent files individually (ADK doesn't propagate callbacks)
  - Local embeddings via nomic-embed-text-v1.5 (768-dim, no API key needed)
  - Tools for data, analytics, knowledge, email (Gmail attachments), calendar, Jira, and ads
  - Connects to MCP server for Databricks operations

- **`apps/code-worker`**: Claude Code CLI Temporal worker (Python 3.11 + Node.js 20)
  - Dedicated pod for autonomous coding tasks via Claude Code CLI
  - Authenticates using tenant's OAuth token via `CLAUDE_CODE_OAUTH_TOKEN` env var
  - Token fetched at runtime from API's internal endpoint (`/api/v1/oauth/internal/token/claude_code`)
  - Creates feature branches, commits changes, and opens PRs with full traceability
  - PR body includes: task description, Claude Code output summary, commit log, files changed
  - Temporal worker on `servicetsunami-code` queue

- **`apps/mcp-server`**: Model Context Protocol server for data integration (Python 3.11)
  - MCP-compliant server following Anthropic's specification
  - 9 tools: PostgreSQL connections, data ingestion, Databricks queries
  - Bronze/Silver/Gold data layer architecture via Databricks Unity Catalog

- **`helm/`**: Kubernetes Helm charts
  - `charts/microservice/`: Reusable base chart for all services
  - `values/`: Per-service configuration (api, web, worker, adk, code-worker, temporal, redis, postgresql)

- **`infra/terraform`**: Infrastructure as Code for AWS deployment (EKS, Aurora PostgreSQL, VPC)

- **`scripts`**: Utility scripts
  - `deploy.sh`: Legacy VM deployment (deprecated, use Kubernetes)
  - `e2e_test_production.sh`: End-to-end test suite (22 test cases)

### Key Architectural Patterns

**Multi-tenancy**: All data models include a `tenant_id` foreign key. The API enforces tenant isolation via JWT token validation. Users belong to tenants; all operations are scoped to the authenticated user's tenant. Tenants have associated branding (`tenant_branding.py`), feature flags/usage limits (`tenant_features.py`), and analytics (`tenant_analytics.py`).

**Authentication flow**:
1. `POST /api/v1/auth/register` creates tenant + admin user
2. `POST /api/v1/auth/login` returns JWT access token
3. All protected endpoints require `Authorization: Bearer <token>` header
4. Demo credentials: `test@example.com` / `password`

**Database initialization**: On API startup, `apps/api/app/main.py` calls `init_db()` which creates tables and seeds demo data.

**Multi-LLM Abstraction Layer**: Tenants choose their LLM provider (Anthropic Claude, Google Gemini, or any LiteLLM-supported provider) via the **integration registry + credential vault** pattern. Credentials are Fernet-encrypted. The chat service reads `tenant_features.active_llm_provider`, retrieves decrypted API key + model ID from the vault, and passes `llm_config` to ADK via `state_delta`. ADK's `before_model_callback` (in `config/model_callback.py`) sets `agent.model` to the LiteLLM format (e.g., `"anthropic/claude-opus-4-6"`), which ADK's `LLMRegistry` resolves to a `LiteLlm` instance. Agents stay as singletons; the model is overridden per-request. Free-text model IDs (no curated dropdowns). LLM Settings page (`LLMSettingsPage.js`) uses the same integration card pattern as other integrations.

**Multi-Agent Orchestration**: Agents are organized into a hierarchical multi-team structure. The **Root Supervisor** routes to 5 top-level teams, each with its own sub-supervisor. New tenants get a default "Luna Supervisor" AgentKit on registration.
- **Personal Assistant Team**: "Luna", WhatsApp-native business co-pilot for high-level tasks. Shows typing indicator (composing presence) while processing. Luna's personality is warm and conversational — sends short messages like real human texting.
- **Code Agent**: Autonomous coding agent powered by Claude Code CLI. Delegates tasks to a dedicated `code-worker` pod via Temporal (`servicetsunami-code` queue). Creates feature branches and PRs automatically. Replaces the old 5-agent dev team.
- **Data Team**: **Data Analyst**, **Report Generator**, **Knowledge Manager**. Handles SQL, analytics, and knowledge graph.
- **Sales Team**: **Sales Agent** (deal management), **Customer Support** (inquiry handling).
- **Marketing Team**: **Web Researcher** for market intelligence and prospect discovery. **Marketing Analyst** for ad campaign management (Meta/Google/TikTok), competitor monitoring, and cross-platform ad intelligence. **Knowledge Manager** for entity CRUD and knowledge graph.
- **Specialized Industry Agents**: **HealthPets** platform agents (**Cardiac Analyst**, **Billing Agent**, **Vet Supervisor**), **Deal Team** agents (**Deal Analyst**, **Deal Researcher**, **Outreach Specialist**).

**Enterprise Orchestration Engine**:
- **Task Dispatcher**: Intelligent agent selection and task lifecycle management.
- **Entity Validator**: High-fidelity validation of extracted knowledge entities against tenant-specific schemas.
- **Credential Vault**: Fernet-encrypted storage for integration API keys and tokens.
- **Skill Router**: Dynamic routing of agent tasks to external integrations.

**Knowledge Graph + Vector Search**: Entities (`knowledge_entity.py`) and relations (`knowledge_relation.py`) form a knowledge graph with pgvector-powered semantic search (768-dim embeddings via nomic-embed-text-v1.5). Supports **Lead Scoring** via configurable rubrics and the `LeadScoringTool`. Knowledge is extracted via `KnowledgeExtractionWorkflow`. Observations table (`knowledge_observations`) stores facts and insights. Entity history tracked in `knowledge_entity_history`. **Memory Activity** audit log (`memory_activities` table) tracks entity_created, entity_updated, relation_created, memory_created, and action_triggered events.

**Embedding System**: Local open-source embeddings via `nomic-ai/nomic-embed-text-v1.5` (768-dim, sentence-transformers). No API key needed — runs locally in both API and ADK containers. Centralized in `embedding_service.py` (API) and `vertex_vector.py` (ADK). Used by: knowledge graph, chat messages, memory activities, RL experiences, skill registry (auto-trigger matching), and email attachment content. Email attachments downloaded via Gmail API are automatically embedded for semantic search (`content_type='email_attachment'`).

**Skill Marketplace**: Three-tier file-based skill system with GitHub import:
- **Native tier**: Bundled skills shipped with the container (read-only): sql_query, calculator, data_summary, entity_extraction, knowledge_search, lead_scoring, report_generation
- **Community tier**: Imported from GitHub repos via `POST /skills/library/import-github`. Supports external formats (GWS `SKILL.md`, Claude Code superpowers). Auto-adapter normalizes frontmatter (semver→int, nested categories, engine mapping).
- **Custom tier**: Per-tenant skills created/edited in the UI with versioning and CHANGELOG
- **Engines**: `python` (script.py), `shell` (script.sh), `markdown` (prompt.md), `tool` (class registry)
- **Semantic search**: Skills are embedded via pgvector for auto-trigger matching
- **GitHub import**: Paste a repo URL → imports single skill or scans subdirectories. Supports GWS (92 skills), superpowers, or any repo with SKILL.md/skill.md files.

**UI/UX (Ocean Theme)**:
- **Design System**: Glassmorphic "Ocean Theme" with support for high-contrast light and dark modes.
- **Aesthetics**: Radial gradients, backdrop blurs (20px-30px), and "Metric Tiles" for data visualization.
- **Animations**: Subtle Y-axis transitions (6px-8px) on hover for interactive elements.
- **Custom Components**: `TaskTimeline` for execution traces and `IntegrationsPanel` for integration management.

**Notifications System**: `notification.py` model with sources (gmail, calendar, whatsapp, system), priorities (high/medium/low), read/dismissed tracking. API endpoints at `/api/v1/notifications`. Frontend `NotificationBell` component in Layout.

**Proactive Inbox Monitor**: `InboxMonitorWorkflow` — long-running per-tenant workflow using `continue_as_new` every 15 minutes. Monitors Gmail + Calendar, triages items with LLM + memory context, creates notifications, and extracts knowledge entities from important emails. Auto-starts when Google OAuth is connected. Queue: `servicetsunami-orchestration`. Activities: fetch_new_emails, fetch_upcoming_events, triage_items, create_notifications, extract_from_emails, log_monitor_cycle.

**Competitor Monitor**: `CompetitorMonitorWorkflow` — long-running per-tenant workflow using `continue_as_new` (default 24h cycle). Monitors competitor entities (category="competitor" in knowledge graph) by scraping websites/news via MCP scraper, checking public ad libraries (Meta Ad Library), analyzing changes, storing observations, and creating notifications. Queue: `servicetsunami-orchestration`. Activities: fetch_competitors, scrape_competitor_activity, check_ad_libraries, analyze_competitor_changes, store_competitor_observations, create_competitor_notifications.

**Marketing Intelligence & Ads Platform**: Integrates with Meta Ads, Google Ads, and TikTok Ads via manual API tokens stored in the integration registry. Tools in `apps/adk-server/tools/ads_tools.py` (12 functions) manage campaigns (list, insights, pause) and search public ad libraries. Competitor tools in `apps/adk-server/tools/competitor_tools.py` (5 functions) manage competitor entities in the knowledge graph. Luna has competitor tools directly; marketing_analyst agent has both ads + competitor tools.

**Temporal workflows**: Durable workflow execution across four task queues:
- `servicetsunami-orchestration`: `TaskExecutionWorkflow`, `ChannelHealthMonitorWorkflow`, `FollowUpWorkflow`, `InboxMonitorWorkflow`, `CompetitorMonitorWorkflow`.
- `servicetsunami-databricks`: `DatasetSyncWorkflow`, `KnowledgeExtractionWorkflow`, `AgentKitExecutionWorkflow`, `DataSourceSyncWorkflow`.
- `servicetsunami-code`: `CodeTaskWorkflow` (Claude Code CLI execution in isolated code-worker pod).
- `servicetsunami-business`: Industry-specific flows:
  - `DealPipelineWorkflow`: Discover → Score → Research → Outreach → Advance → Sync (6 steps).
  - `RemediaOrderWorkflow`: Create order → Confirm (WhatsApp) → Monitor payment → Notify delivery.
  - `MonthlyBillingWorkflow`: Process usage → Generate invoices → Trigger payments (HealthPets).
- `scheduler_worker.py`: Polls every 60s for cron/interval-based pipeline runs.

**Pipeline Run Tracking**: `pipeline_run.py` model tracks pipeline execution history with status, duration, and error details. The scheduler worker handles automated pipeline execution.

**Databricks Integration**: Datasets sync to Unity Catalog via MCP server (Bronze/Silver/Gold layers). Status tracked in dataset metadata (`sync_status`: pending/syncing/synced/failed).

**Database Migrations**: Manual SQL scripts in `apps/api/migrations/` (not Alembic). See `migrations/README.md` for instructions.

## Development Commands

### Local Development (Docker Compose)

```bash
# Start all services with custom ports
DB_PORT=8003 API_PORT=8001 WEB_PORT=8002 docker-compose up --build

# Services: API (8001), Web (8002), DB (8003), MCP (8086), ADK (8085), Temporal (7233/8233)

# View logs
docker-compose logs -f api
docker-compose logs -f web

# Connect to PostgreSQL
docker-compose exec db psql -U postgres servicetsunami
```

### API Development

```bash
cd apps/api
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Testing (pytest configured with asyncio_mode=auto in root pytest.ini)
pytest                                # Run all tests
pytest tests/test_api.py              # Run specific file
pytest tests/test_api.py::test_login  # Run specific test
pytest -v                             # Verbose output

# Linting
ruff check app
```

### Web Development

```bash
cd apps/web
npm install
npm start                              # Dev server (port 3000)
npm test                               # Tests in watch mode
npm test -- --ci --watchAll=false      # CI mode (single run)
npm test -- WizardStepper.test.js      # Specific test file
npm run build                          # Production build
```

### MCP Server Development

```bash
cd apps/mcp-server
pip install -e ".[dev]"
pytest tests/ -v
python -m src.server                   # Runs on http://localhost:8085
```

### ADK Server Development

```bash
cd apps/adk-server
pip install -r requirements.txt
python server.py                       # Runs on http://localhost:8080
```

### Monorepo Commands

```bash
pnpm install && pnpm build && pnpm lint
```

## API Structure

### Models (`apps/api/app/models/`)

Core domain models (all inherit from SQLAlchemy Base, include `tenant_id` ForeignKey):
- `tenant.py`, `user.py`: Multi-tenancy and users
- `tenant_branding.py`, `tenant_features.py`, `tenant_analytics.py`: Whitelabel, feature flags, usage analytics
- `agent.py`, `agent_kit.py`: AI agent definitions and kits
- `agent_group.py`, `agent_relationship.py`, `agent_task.py`: Multi-agent teams and orchestration
- `agent_message.py`, `agent_skill.py`, `agent_memory.py`: Agent communication, skills, and memory
- `deployment.py`: Agent deployment tracking
- `data_source.py`, `data_pipeline.py`, `pipeline_run.py`: Data engineering and execution tracking
- `dataset.py`, `dataset_group.py`: Dataset management with DuckDB/Parquet support
- `tool.py`, `connector.py`: Tool and integration definitions
- `chat.py`: Chat sessions and messages
- `notebook.py`: Jupyter-style SQL notebooks
- `vector_store.py`: Vector store management
- `knowledge_entity.py`, `knowledge_relation.py`: Knowledge graph
- `llm_provider.py`, `llm_model.py`, `llm_config.py`: Multi-LLM provider configuration
- `execution_trace.py`: Step-by-step audit trail for task execution
- `integration_config.py`: Per-tenant integration enablement, approval gates, rate limits, LLM override
- `integration_credential.py`: Encrypted API keys/tokens for integrations (OAuth tokens, session tokens, API keys)
- `notification.py`: Proactive alerts from inbox monitor, system events
- `memory_activity.py`: Audit log for knowledge graph operations

### Services (`apps/api/app/services/`)

Business logic layer (one service per model):
- `base.py`: Generic CRUD base service
- `llm.py`: Claude AI integration with fallback handling
- `context_manager.py`: Token counting, conversation summarization
- `tool_executor.py`: Tool execution framework (SQL Query, Calculator, Data Summary, Entity Extraction, Knowledge Search)
- `chat.py`, `enhanced_chat.py`: LLM-powered chat with tool and multi-agent support. Chat session creation requires only Title (optional) + Agent Kit selection; auto-selects kit when only one exists. Reads tenant's `active_llm_provider` and passes `llm_config` to ADK via `state_delta` (primary + retry paths).
- `embedding_service.py`: Local embedding generation via nomic-embed-text-v1.5 (768-dim). Functions: `embed_text()`, `embed_and_store()`, `search_similar()`, `recall()`. Used by knowledge, chat, memory, RL, skills.
- `adk_client.py`, `mcp_client.py`: Google ADK and MCP server clients
- `knowledge.py`, `knowledge_extraction.py`: Knowledge graph operations and extraction
- `skill_manager.py`: Three-tier skill marketplace (native/community/custom). GitHub import with external format adapter (GWS SKILL.md support). Skill execution across 4 engines. Semantic auto-trigger matching.
- `skill_registry_service.py`: Sync file skills to DB + pgvector embeddings
- `whatsapp_service.py`: Neonize-based WhatsApp integration with persistent typing indicator (refreshes composing presence every 4s until response sent)
- `branding.py`, `features.py`, `tenant_analytics.py`: Tenant customization services
- `integration_configs.py`: Integration configuration CRUD service
- `orchestration/`: Orchestration services package
  - `credential_vault.py`: Fernet-encrypted credential storage with CRUD helpers
  - `task_dispatcher.py`: Agent selection and task dispatch
- Pattern: `{resource}s.py` (e.g., `agents.py`, `datasets.py`, `agent_groups.py`, `vector_stores.py`)

### Workers (`apps/api/app/workers/`)

Temporal workers for async processing:
- `orchestration_worker.py`: TaskExecutionWorkflow, ChannelHealthMonitorWorkflow, FollowUpWorkflow, InboxMonitorWorkflow, CompetitorMonitorWorkflow (queue: `servicetsunami-orchestration`)
- `databricks_worker.py`: DatasetSync, KnowledgeExtraction, AgentKitExecution, DataSourceSync workflows (queue: `servicetsunami-databricks`)
- `scheduler_worker.py`: Automated pipeline execution (cron/interval scheduling, polls every 60s)

### Routes (`apps/api/app/api/v1/`)

FastAPI routers mounted at `/api/v1`. All routes use dependency injection via `deps.py` for database sessions and current user extraction.

## Web Frontend Structure

### Pages (`apps/web/src/pages/`)

Organized in 3-section navigation:
- **INSIGHTS**: `DashboardPage.js`, `DatasetsPage.js`
- **AI OPERATIONS**: `ChatPage.js`, `AgentsPage.js`, `WorkflowsPage.js`, `MemoryPage.js` (labeled "Knowledge Base" in sidebar)
- **WORKSPACE**: `IntegrationsPage.js`, `NotebooksPage.js`, `VectorStoresPage.js`, `ToolsPage.js`
- **SETTINGS**: `SettingsPage.js`, `LLMSettingsPage.js`, `BrandingPage.js`
- **AUTH**: `RegisterPage.js`, `AgentWizardPage.js`

### Components (`apps/web/src/components/`)

- `Layout.js`: Authenticated layout with glassmorphic dark theme sidebar, includes `NotificationBell` component
- `wizard/`: Agent creation wizard (5-step flow with localStorage draft persistence). 8 templates (compact text layout). Skills step includes report_generation tool. 6 tools: sql_query, calculator, data_summary, entity_extraction, knowledge_search, report_generation
- `TaskTimeline.js`: Execution trace timeline with step icons and duration badges
- `IntegrationsPanel.js`: Integration enablement grid with dynamic credential forms from registry and test execution button
- `NotificationBell.js`: Real-time notification indicator with dropdown for inbox monitor alerts

## Environment Configuration

### Docker Compose Ports (root `.env`)

```
API_PORT=8001    # FastAPI backend
WEB_PORT=8002    # React frontend
DB_PORT=8003     # PostgreSQL
MCP_PORT=8086    # MCP server
ADK_PORT=8085    # ADK server
```

### API Configuration (`apps/api/.env`)

Loaded via pydantic-settings. See `apps/api/app/core/config.py`:

```bash
# Required
ANTHROPIC_API_KEY=sk-ant-api03-xxxxxxxxxxxxx
DATABASE_URL=postgresql://postgres:postgres@db:5432/servicetsunami
SECRET_KEY=your-jwt-secret

# Temporal
TEMPORAL_ADDRESS=temporal:7233  # Use localhost:7233 for local dev

# MCP/Databricks
MCP_SERVER_URL=http://mcp-server:8000
DATABRICKS_SYNC_ENABLED=true

# ADK
ADK_BASE_URL=http://adk-server:8080
ADK_APP_NAME=servicetsunami_supervisor

# Credential Vault
ENCRYPTION_KEY=<fernet-key>  # Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Google OAuth (required for Inbox Monitor)
GOOGLE_CLIENT_ID=xxx
GOOGLE_CLIENT_SECRET=xxx
```

**Note**: The orchestration Helm worker needs the same secrets as the API (ENCRYPTION_KEY, ANTHROPIC_API_KEY, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET).

### Web Configuration (`apps/web/.env.local`)

```
REACT_APP_API_BASE_URL=http://localhost:8001
```

Uses `REACT_APP_` prefix (Create React App requirement).

### Code Worker Configuration

The code-worker authenticates to Claude Code CLI using per-tenant OAuth tokens (subscription-based, not API credits).

```bash
# Required (from GCP Secret Manager via ExternalSecret)
GITHUB_TOKEN=ghp_xxxxx              # GitHub PAT for git operations and PR creation
API_INTERNAL_KEY=xxxxx              # Key for internal API endpoints (token fetch)

# Set by ConfigMap
API_BASE_URL=http://servicetsunami-api  # Internal API service URL
TEMPORAL_ADDRESS=temporal:7233          # Temporal server address

# Set dynamically per-task (not in pod env)
# CLAUDE_CODE_OAUTH_TOKEN is set per-subprocess from the tenant's stored credential
```

**Authentication flow:**
1. ADK `code_agent` calls `start_code_task` tool → Temporal workflow starts on `servicetsunami-code` queue
2. Code-worker activity fetches tenant's OAuth token: `GET /api/v1/oauth/internal/token/claude_code?tenant_id=<uuid>`
3. Token passed as `CLAUDE_CODE_OAUTH_TOKEN` env var to `claude -p` subprocess
4. Claude Code CLI runs with tenant's subscription (Pro/Max), not API credits
5. Changes committed, pushed, PR created via `gh pr create` with full traceability

## Deployment

### Kubernetes (Production - GKE)

Production deploys exclusively via Kubernetes using Helm charts and GitHub Actions.

```bash
# Deploy all services
gh workflow run deploy-all.yaml -f deploy_infrastructure=false -f environment=prod

# Deploy ADK server when agent logic changes
gh workflow run adk-deploy.yaml -f deploy=true -f environment=prod

# Watch rollout status
kubectl get pods -n prod -w
kubectl rollout status deployment/servicetsunami-api -n prod
kubectl rollout status deployment/servicetsunami-adk -n prod

# Validate Helm releases
helm list -n prod | grep servicetsunami
helm status servicetsunami-adk -n prod

# Rollback if needed
helm rollback servicetsunami-api -n prod
```

**GitHub Actions Workflows** (`.github/workflows/`):
- `deploy-all.yaml`: Full stack deployment (API, Web, Worker, ADK, Temporal, Redis, PostgreSQL)
- `adk-deploy.yaml`: ADK server only (auto-triggers on push to `apps/adk-server/**`)
- `servicetsunami-api.yaml`: API service
- `servicetsunami-web.yaml`: Web frontend
- `servicetsunami-worker.yaml`: Temporal worker
- `kubernetes-infrastructure.yaml`: Initial infra setup
- `kubernetes-shared.yaml`: Shared resources (Ingress, ManagedCertificates)

**Required GCP Secrets** (Secret Manager):
- `servicetsunami-secret-key`, `servicetsunami-database-url`
- `servicetsunami-anthropic-api-key`, `servicetsunami-mcp-api-key`

See `docs/KUBERNETES_DEPLOYMENT.md` for full runbook.

### E2E Testing

```bash
# Test against any environment
BASE_URL=http://localhost:8001 ./scripts/e2e_test_production.sh
BASE_URL=https://servicetsunami.com ./scripts/e2e_test_production.sh
```


## Important Patterns

### Adding a New Resource

1. **Model**: `apps/api/app/models/` - SQLAlchemy model with `tenant_id` ForeignKey
2. **Schema**: `apps/api/app/schemas/` - `{Resource}Create`, `{Resource}Update`, `{Resource}InDB`
3. **Service**: `apps/api/app/services/` - Extend `base.py` CRUD, ensure tenant isolation
4. **Routes**: `apps/api/app/api/v1/` - Mount in `routes.py`
5. **Frontend**: `apps/web/src/pages/` - Add route in `App.js`, nav in `Layout.js`
6. **Helm**: Update `helm/values/` if new service needs Kubernetes resources

### Multi-tenant Query Pattern

```python
# Always filter by tenant
def get_agents(db: Session, tenant_id: uuid.UUID):
    return db.query(Agent).filter(Agent.tenant_id == tenant_id).all()
```

### Infrastructure Sync Rule

When making manual changes, always replicate them to Helm, Git, and Terraform to prevent drift.

### Code Agent PR Traceability

PRs created by the code agent include structured body with full audit trail:
- **Summary**: Auto-generated description
- **Task**: Original task description from the user
- **Claude Code Output**: Summary of what Claude Code did
- **Commits**: Full commit log with short hashes
- **Files Changed**: Bulleted list of modified files
- **Footer**: ServiceTsunami Code Agent attribution

## Additional Documentation

- `docs/KUBERNETES_DEPLOYMENT.md`: Full Kubernetes deployment runbook
- `docs/plans/`: Implementation plans and design documents
  - `2025-02-13-enterprise-orchestration-engine-design.md`: Orchestration engine design document
  - `2025-02-13-enterprise-orchestration-engine-plan.md`: 18-task implementation plan
  - `2026-02-20-whatsapp-agent-integration-platform-design.md`: WhatsApp agent + external app integration
  - `2026-02-20-lead-scoring-skill-design.md`: LLM-powered lead scoring with configurable rubrics
  - `2025-12-18-automations-temporal-plan.md`: Automations with Temporal connectors and scheduling
  - `2026-03-06-proactive-inbox-monitor.md`: Proactive inbox monitor workflow design
  - `2026-03-06-memory-system-design.md`: Memory activity audit and knowledge graph design
  - `2026-03-07-orchestration-worker-architecture.md`: Orchestration worker design (Temporal worker for Gmail/Calendar/WhatsApp workflows)
  - `2026-03-10-marketing-intelligence-ads-platform-design.md`: Marketing intelligence, competitor monitoring, Meta/Google/TikTok ad integrations
  - `2026-03-10-marketing-intelligence-ads-platform-plan.md`: Implementation plan for marketing intelligence feature
  - `2026-03-13-multi-model-abstraction-layer-design.md`: Multi-LLM provider switching via integration registry + ADK before_model_callback
  - `2026-03-13-multi-model-abstraction-layer-plan.md`: 10-task implementation plan for multi-model support
- `LLM_INTEGRATION_README.md`, `TOOL_FRAMEWORK_README.md`, `DATABRICKS_SYNC_README.md`: Feature docs
