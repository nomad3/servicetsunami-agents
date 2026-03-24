# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

ServiceTsunami is an AI agent orchestration platform that routes tasks to **Claude Code CLI** (Sonnet by default, configurable via `CLAUDE_CODE_MODEL`) via Temporal workflows. Agents are defined as marketplace skills, tools are served via **MCP** (81 tools), and the platform learns from user feedback via RL. Runs from a laptop via **Cloudflare Tunnel** serving both `servicetsunami.com` and `agentprovision.com`.

**Key architecture**: Chat → Agent Router (Python, zero LLM cost) → Temporal → code-worker (Claude Code CLI with `--model sonnet`) → MCP tools (FastMCP, 81 tools) → response. Every response is auto-scored by a local Qwen council (3 reviewers, 6-dimension rubric) and selectively reviewed by a multi-provider council (Claude + Codex + Qwen in parallel via Temporal). All scores logged as RL experiences for continuous improvement and platform routing optimization.

## Architecture

### Monorepo Structure

Docker Compose stack with Cloudflare Tunnel:

- **`apps/api`** (port 8001): FastAPI backend — chat service, agent router, RL, knowledge graph, skill marketplace
- **`apps/code-worker`**: Claude Code CLI execution via Temporal. Has git, gh, claude, node. Fetches GitHub + Claude Code tokens from vault per-session.
- **`apps/mcp-server`** (port 8086): Original REST MCP server (Databricks/scraping)
- **`mcp-tools`** (port 8087): FastMCP with 81 tools (email, calendar, knowledge, jira, github, data, ads, competitor, monitor, sales, reports, shell, analytics, skills, drive)
- **`apps/web`** (port 8002): React SPA with markdown rendering (react-markdown), Ocean theme
- **`cloudflared`**: Cloudflare Tunnel — routes servicetsunami.com + agentprovision.com to local stack
- **`temporal`** (port 7233): Workflow engine for durable task execution
- **`ollama`** (port 11434): Local LLM runtime — hosts Qwen models for auto-scoring, RL, knowledge extraction, conversation summarization, and free-tier fallback responses
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
  - `values/`: Per-service configuration (api, web, worker, code-worker, temporal, redis, postgresql)

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

**Multi-LLM Abstraction Layer**: Tenants choose their LLM provider via the **integration registry + credential vault** pattern. Credentials are Fernet-encrypted. The CLI orchestrator reads `tenant_features.default_cli_platform` to route to the appropriate CLI agent (Claude Code, Gemini CLI, Codex CLI). LLM Settings page (`LLMSettingsPage.js`) uses the same integration card pattern as other integrations.

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

**Embedding System**: Local open-source embeddings via `nomic-ai/nomic-embed-text-v1.5` (768-dim, sentence-transformers). No API key needed — runs locally in API and MCP containers. Centralized in `embedding_service.py`. Used by: knowledge graph, chat messages, memory activities, RL experiences, skill registry (auto-trigger matching), and email attachment content. Email attachments downloaded via Gmail API are automatically embedded for semantic search (`content_type='email_attachment'`).

**Auto Quality Scoring & RL**: Every agent response is automatically scored by a local Qwen model (`qwen2.5-coder:1.5b` via Ollama) across a 6-dimension rubric (100 points total):
- **Accuracy** (25pts): Factual correctness, no hallucinations
- **Helpfulness** (20pts): Addresses actual user need, actionable
- **Tool Usage** (20pts): Appropriate MCP tool selection and usage
- **Memory Usage** (15pts): Knowledge graph recall, context building
- **Efficiency** (10pts): Concise, fast, no padding
- **Context Awareness** (10pts): Conversation continuity, history usage
Scores are logged as RL experiences (`rl_experience` table) with reward components, cost tracking (tokens/cost per quality point), and platform recommendation. The scoring runs async after each response via `auto_quality_scorer.py` using the `agent_response_quality` rubric from `scoring_rubrics.py`. Zero cloud cost — fully local inference. Includes leave-one-out fragility detection (flags when removing one reviewer would flip consensus).

**Multi-Provider Review Council**: Async Temporal workflow (`ProviderReviewWorkflow` on `servicetsunami-code` queue) where Claude, Codex, and Qwen each independently review the same response. Triggers on: side-effect tools, fragile local consensus, low scores (<40), or 5% random sample (`PROVIDER_COUNCIL_SAMPLE_RATE` env var). Each provider returns score (0-100), verdict, issues, suggestions. Meta-adjudicator computes agreement (over ALL reviewers including failed), detects disagreements, and recommends best platform. Results merged into RL experience via `POST /api/v1/rl/internal/provider-council`. Provider failures are isolated (`_safe_review` wrapper) — one timeout doesn't abort the others.

**Inference Bulkhead**: Foreground (user-blocking) and background (scoring/consensus) Ollama calls are isolated via `_foreground_active` threading.Event. Background scoring skips when foreground is active — degrade scorer first, never the user path. Local tool agent is read-only (search tools only, no mutations). Pre-execution safety gate blocks side-effect tools for local model.

**Reinforcement Learning System**: RL experiences track agent decisions across multiple decision points (`chat_response`, `code_task`, `agent_routing`). Each experience stores state, action, reward (0-1 scale from quality score), and reward components (the 6-dimension breakdown plus provider council results when available). Services: `rl_experience_service.py` (CRUD + querying), `rl_reward_service.py` (reward assignment), `rl_policy_engine.py` (policy updates). The `RLPolicyUpdateWorkflow` (Temporal) periodically retrains policies from accumulated experiences. Embeddings of state text enable semantic similarity search over past decisions.

**Local ML Inference** (`local_inference.py`): All lightweight ML tasks run locally via Ollama (zero cloud cost):
- `generate_luna_response_sync()`: Free-tier fallback when no CLI subscription connected
- `summarize_conversation_sync()`: Conversation summarization (replaces Anthropic calls in context_manager)
- `extract_knowledge_sync()`: Entity/relation extraction from content
- `triage_inbox_items()`: Email/calendar triage for inbox monitor
- `analyze_competitor_data()`: Competitor monitoring analysis
- `classify_task_type()`: Message intent classification for agent routing
- Models: `qwen2.5-coder:0.5b` (default fast), `qwen2.5-coder:1.5b` (quality scoring), `qwen3:1.7b` (tool calling + provider review, `think:false` for direct JSON output)

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

**Marketing Intelligence & Ads Platform**: Integrates with Meta Ads, Google Ads, and TikTok Ads via manual API tokens stored in the integration registry. MCP tools in `apps/mcp-server/src/mcp_tools/ads.py` (12 tools) manage campaigns (list, insights, pause) and search public ad libraries. Competitor tools in `competitor.py` (5 tools) manage competitor entities in the knowledge graph.

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

# Services: API (8001), Web (8002), DB (8003), MCP (8086/8087), Temporal (7233/8233)

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
- `rl_experience.py`: Reinforcement learning experiences with decision_point, state, action, reward, reward_components (JSONB), reward_source, embedded state_text
- `safety_policy.py`: `TenantActionPolicy` (tenant overrides), `SafetyEvidencePack` (enforcement audit trail, 30d TTL), `AgentTrustProfile` (per-agent trust scores + autonomy tiers)
- `goal_record.py`: Durable goals with state machine (proposed→active→blocked→completed/abandoned), hierarchical parent goals, success criteria, deadlines
- `commitment_record.py`: Agent promises with state machine (open→in_progress→fulfilled/broken/cancelled), source tracking, due dates, goal linkage
- `agent_identity_profile.py`: Per-agent operating profiles — role, mandate, domain boundaries, tool access, risk posture, communication style, learned strengths/weaknesses
- `world_state.py`: `WorldStateAssertion` (normalized claims with confidence, provenance, TTL, supersession), `WorldStateSnapshot` (auto-projected current state per entity)

### Services (`apps/api/app/services/`)

Business logic layer (one service per model):
- `base.py`: Generic CRUD base service
- `llm.py`: Claude AI integration with fallback handling
- `context_manager.py`: Token counting, conversation summarization
- `tool_executor.py`: Tool execution framework (SQL Query, Calculator, Data Summary, Entity Extraction, Knowledge Search)
- `chat.py`, `enhanced_chat.py`: LLM-powered chat with CLI orchestrator. Chat session creation requires only Title (optional) + Agent Kit selection; auto-selects kit when only one exists.
- `cli_session_manager.py`: CLI orchestrator session lifecycle — generates CLAUDE.md, MCP config, dispatches Temporal workflows.
- `agent_router.py`: Deterministic agent routing (zero LLM cost) — maps channels/intents to skills.
- `embedding_service.py`: Local embedding generation via nomic-embed-text-v1.5 (768-dim). Functions: `embed_text()`, `embed_and_store()`, `search_similar()`, `recall()`. Used by knowledge, chat, memory, RL, skills.
- `mcp_client.py`: MCP server client
- `knowledge.py`, `knowledge_extraction.py`: Knowledge graph operations and extraction
- `skill_manager.py`: Three-tier skill marketplace (native/community/custom). GitHub import with external format adapter (GWS SKILL.md support). Skill execution across 4 engines. Semantic auto-trigger matching.
- `skill_registry_service.py`: Sync file skills to DB + pgvector embeddings
- `whatsapp_service.py`: Neonize-based WhatsApp integration with persistent typing indicator (refreshes composing presence every 4s until response sent)
- `branding.py`, `features.py`, `tenant_analytics.py`: Tenant customization services
- `integration_configs.py`: Integration configuration CRUD service
- `auto_quality_scorer.py`: Async post-response scoring via local Qwen model (6-dimension rubric → RL experience)
- `scoring_rubrics.py`: Configurable scoring rubrics registry (agent_response_quality rubric)
- `rl_experience_service.py`: RL experience CRUD, querying, and semantic search
- `rl_reward_service.py`: Reward assignment and aggregation for RL experiences
- `rl_policy_engine.py`: Policy updates from accumulated RL experiences
- `local_inference.py`: Local Ollama-based inference for scoring, summarization, extraction, triage (zero cloud cost)
- `orchestration/`: Orchestration services package
  - `credential_vault.py`: Fernet-encrypted credential storage with CRUD helpers
  - `task_dispatcher.py`: Agent selection and task dispatch
- `safety_policies.py`: Unified risk catalog (111 actions), evaluation, tenant overrides with ceiling enforcement
- `safety_enforcement.py`: Central enforcement, evidence packs, autonomy tier restrictions, automated channel escalation
- `safety_trust.py`: Trust scoring from RL + provider council, autonomy tier derivation, auto-refresh stale profiles (6h)
- `goal_service.py`: Goal CRUD with state machine transitions, cross-tenant validation, hierarchical goals
- `commitment_service.py`: Commitment CRUD with state machine, overdue detection, goal linkage validation
- `agent_identity_service.py`: Identity profile CRUD, `build_runtime_identity_context()` for CLI prompt injection
- `world_state_service.py`: `assert_state()` (corroborate/supersede), TTL expiry, atomic snapshot projection, `build_world_state_context()`
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
MCP_PORT=8086    # MCP server (Databricks)
                 # MCP tools on 8087 (FastMCP, 81 tools)
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
1. Chat service dispatches code task → Temporal workflow starts on `servicetsunami-code` queue
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

# Watch rollout status
kubectl get pods -n prod -w
kubectl rollout status deployment/servicetsunami-api -n prod

# Validate Helm releases
helm list -n prod | grep servicetsunami

# Rollback if needed
helm rollback servicetsunami-api -n prod
```

**GitHub Actions Workflows** (`.github/workflows/`):
- `deploy-all.yaml`: Full stack deployment (API, Web, Worker, Code-Worker, Temporal, Redis, PostgreSQL)
- `code-worker-deploy.yaml`: Code-worker only (auto-triggers on push to `apps/code-worker/**`)
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

## AGI Roadmap — Brain Architecture

The platform is evolving from a reactive assistant into a durable agent system through six capability gaps. The "brain" is the combination of safety governance, self-model persistence, world state grounding, and reinforcement learning that makes agents self-consistent across sessions.

```
┌─────────────────────────────────────────────────────────────┐
│                     AGENT BRAIN                             │
│                                                             │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ SAFETY LAYER (Gap 05) — Foundation for all autonomy   │  │
│  │  • 111 governed actions, 5 risk classes               │  │
│  │  • Evidence packs, tenant overrides, ceiling enforce  │  │
│  │  • Trust scores → autonomy tiers per agent            │  │
│  └───────────────────────────────────────────────────────┘  │
│                          ▲                                  │
│  ┌──────────────────┐    │    ┌────────────────────────┐   │
│  │ SELF-MODEL        │    │    │ WORLD MODEL            │   │
│  │ (Gap 02)          │    │    │ (Gap 01)               │   │
│  │                   │    │    │                        │   │
│  │ Identity profiles │    │    │ Assertions + snapshots │   │
│  │ Goals + deadlines │    │    │ Confidence + TTL       │   │
│  │ Commitments       │    │    │ Corroboration chain    │   │
│  │ Strengths/weak    │    │    │ Supersession tracking  │   │
│  └────────┬──────────┘    │    └───────────┬────────────┘   │
│           │               │                │                │
│           ▼               │                ▼                │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ REINFORCEMENT LEARNING — Continuous improvement loop  │  │
│  │  • Auto quality scorer (6-dim, 100pts)                │  │
│  │  • Provider council (Claude+Codex+Qwen, 20% sample)  │  │
│  │  • RL experiences → trust scores → routing decisions  │  │
│  │  • Exploration: 70% Codex / 30% Claude Code           │  │
│  └───────────────────────────────────────────────────────┘  │
│                          │                                  │
│  ┌───────────────────────▼───────────────────────────────┐  │
│  │ ORCHESTRATION — Temporal durable execution            │  │
│  │  • CLI routing (Claude/Codex/Gemini + fallback)      │  │
│  │  • Goal review workflow (6h cycle, stale detection)   │  │
│  │  • Inbox/competitor monitors (continue_as_new)        │  │
│  │  • Dynamic workflows with safety-gated steps         │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

### Implementation Status

```
Gap 05: Safety & Trust          ████████████████████ COMPLETE
  Phase 1: Risk taxonomy         ████ PR #28
  Phase 2: Enforcement           ████ PR #29
  Phase 3: Trust autonomy        ████ PR #30
  Bypass fixes                   ████ PR #31

Gap 02: Self-Model & Goals      ████████████████████ COMPLETE
  Phase 1: Goals + commitments   ████ PR #32
  Phase 2: Identity profiles     ████ PR #33
  Phase 3: Goal review workflow  ████ PR #34

Gap 01: World Model             █████░░░░░░░░░░░░░░░ Phase 1 done
  Phase 1: Assertions            ████ PR #35
  Phase 2: Conflict/freshness    ░░░░ next
  Phase 3: Causal graph          ░░░░
  Phase 4: State-first prompts   ░░░░

Gap 03: Long-Horizon Planning   ░░░░░░░░░░░░░░░░░░░░ Not started
Gap 06: Society of Agents       ░░░░░░░░░░░░░░░░░░░░ Not started
Gap 04: Self-Improvement        ░░░░░░░░░░░░░░░░░░░░ Not started
```

### RL Feedback Loop

```
User message
    │
    ▼
Agent Router ──────────────────────────────┐
    │ (trust profile + RL routing)         │
    ▼                                      │
CLI Execution ─────────────────────┐       │
    │ (Claude/Codex/Gemini)        │       │
    ▼                              │       │
Response ──────────────────┐       │       │
    │                      │       │       │
    ▼                      ▼       ▼       ▼
Auto Quality Scorer    RL Experience Logged
    │ (Qwen, 6-dim)       (state, action, reward)
    │                          │
    ▼                          ▼
Provider Council (20%)    Trust Recompute (6h)
    │ (Claude+Codex+Qwen)     │
    │                          ▼
    ▼                     Autonomy Tier Update
RL Experience Updated         │
    │                         ▼
    └─────────────────► Next routing decision
```

### AGI Design Documents

- `docs/plans/2026-03-24-agi-roadmap-summary.md`: Full roadmap with ASCII diagrams
- `docs/plans/2026-03-24-agi-gap-01-world-model-grounding-design.md`: World model design
- `docs/plans/2026-03-24-agi-gap-02-self-model-and-goal-persistence-design.md`: Self-model design
- `docs/plans/2026-03-24-agi-gap-03-long-horizon-autonomy-design.md`: Planning design
- `docs/plans/2026-03-24-agi-gap-04-self-improvement-and-experimentation-design.md`: Self-improvement design
- `docs/plans/2026-03-24-agi-gap-05-safety-governance-and-trust-design.md`: Safety design
- `docs/plans/2026-03-24-agi-gap-06-collective-intelligence-and-society-of-agents-design.md`: Multi-agent design
- `docs/plans/2026-03-24-copilot-cli-integration-design.md`: GitHub Copilot CLI integration (planned)

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
  - `2026-03-13-multi-model-abstraction-layer-design.md`: Multi-LLM provider switching via integration registry
  - `2026-03-13-multi-model-abstraction-layer-plan.md`: 10-task implementation plan for multi-model support
  - `2026-03-22-local-ollama-mcp-bridge-plan.md`: Local Qwen3:4b → Ollama tool calling → MCP bridge for free-tier tenants
- `LLM_INTEGRATION_README.md`, `TOOL_FRAMEWORK_README.md`, `DATABRICKS_SYNC_README.md`: Feature docs
