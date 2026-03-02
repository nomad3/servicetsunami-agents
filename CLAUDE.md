# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

ServiceTsunami is an enterprise-grade agentic orchestration platform built as a monorepo. It provides multi-tenant control plane capabilities for managing AI agents, data pipelines, skill execution, and deployments. Features enterprise-grade credential management, traceability, and LLM-agnostic execution. Deploys exclusively via Kubernetes (GKE) using Helm charts and GitHub Actions.

## Architecture

### Monorepo Structure

This is a **Turborepo monorepo** managed with `pnpm` workspaces:

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
  - Multi-agent orchestration with supervisor pattern
  - Sub-agents: data_analyst, report_generator, knowledge_manager
  - Tools for data, analytics, knowledge, and actions
  - Connects to MCP server for Databricks operations

- **`apps/mcp-server`**: Model Context Protocol server for data integration (Python 3.11)
  - MCP-compliant server following Anthropic's specification
  - 9 tools: PostgreSQL connections, data ingestion, Databricks queries
  - Bronze/Silver/Gold data layer architecture via Databricks Unity Catalog

- **`helm/`**: Kubernetes Helm charts
  - `charts/microservice/`: Reusable base chart for all services
  - `values/`: Per-service configuration (api, web, worker, adk, temporal, redis, postgresql)

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

**Multi-LLM Router**: Supports multiple LLM providers (Anthropic, OpenAI, DeepSeek, etc.) with per-tenant configuration and cost-based routing. Models: `llm_provider.py`, `llm_model.py`, `llm_config.py`. Chat falls back to static templates if no API key is set.

**Multi-Agent Orchestration**: Agents are organized into a hierarchical multi-team structure. The **Root Supervisor** routes to 5 top-level teams, each with its own sub-supervisor:
- **Personal Assistant Team**: "Luna", WhatsApp-native business co-pilot for high-level tasks.
- **Dev Team**: Self-modifying team with a strict 5-step cycle (**Architect** → **Coder** → **Tester** → **DevOps** → **User Agent**). Agents have shell access and can autonomously modify code, run tests, and deploy via git.
- **Data Team**: **Data Analyst**, **Report Generator**, **Knowledge Manager**. Handles SQL, analytics, and knowledge graph.
- **Sales Team**: **Sales Agent** (deal management), **Customer Support** (inquiry handling).
- **Marketing Team**: **Web Researcher** for market intelligence and prospect discovery.
- **Specialized Industry Agents**: **HealthPets** platform agents (**Cardiac Analyst**, **Billing Agent**, **Vet Supervisor**), **Deal Team** agents (**Deal Analyst**, **Deal Researcher**, **Outreach Specialist**).

**Enterprise Orchestration Engine**:
- **Task Dispatcher**: Intelligent agent selection and task lifecycle management.
- **Entity Validator**: High-fidelity validation of extracted knowledge entities against tenant-specific schemas.
- **Credential Vault**: Fernet-encrypted storage for skill API keys and tokens.
- **Skill Router**: Dynamic routing of agent tasks to external skill integrations.

**Knowledge Graph**: Entities (`knowledge_entity.py`) and relations (`knowledge_relation.py`) form a knowledge graph. Supports **Lead Scoring** via configurable rubrics and the `LeadScoringTool`. Knowledge is extracted via `KnowledgeExtractionWorkflow`. pgvector is not used; search falls back to text-based `ILIKE`.

**UI/UX (Ocean Theme)**:
- **Design System**: Glassmorphic "Ocean Theme" with support for high-contrast light and dark modes.
- **Aesthetics**: Radial gradients, backdrop blurs (20px-30px), and "Metric Tiles" for data visualization.
- **Animations**: Subtle Y-axis transitions (6px-8px) on hover for interactive elements.
- **Custom Components**: `TaskTimeline` for execution traces and `SkillsConfigPanel` for integration management.

**Temporal workflows**: Durable workflow execution across three task queues:
- `servicetsunami-orchestration`: `TaskExecutionWorkflow`, `ChannelHealthMonitorWorkflow`, `FollowUpWorkflow`.
- `servicetsunami-databricks`: `DatasetSyncWorkflow`, `KnowledgeExtractionWorkflow`, `AgentKitExecutionWorkflow`, `DataSourceSyncWorkflow`.
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
- `skill_config.py`: Per-tenant skill enablement, approval gates, rate limits, LLM override
- `skill_credential.py`: Encrypted API keys/tokens for skill integrations

### Services (`apps/api/app/services/`)

Business logic layer (one service per model):
- `base.py`: Generic CRUD base service
- `llm.py`: Claude AI integration with fallback handling
- `context_manager.py`: Token counting, conversation summarization
- `tool_executor.py`: Tool execution framework (SQL Query, Calculator, Data Summary, Entity Extraction, Knowledge Search)
- `chat.py`, `enhanced_chat.py`: LLM-powered chat with tool and multi-agent support
- `adk_client.py`, `mcp_client.py`: Google ADK and MCP server clients
- `knowledge.py`, `knowledge_extraction.py`: Knowledge graph operations and extraction
- `branding.py`, `features.py`, `tenant_analytics.py`: Tenant customization services
- `skill_configs.py`: Skill configuration CRUD service
- `orchestration/`: Orchestration services package
  - `skill_router.py`: Skill execution router (stub — backend not yet wired)
  - `credential_vault.py`: Fernet-encrypted credential storage with CRUD helpers
  - `task_dispatcher.py`: Agent selection and task dispatch
- Pattern: `{resource}s.py` (e.g., `agents.py`, `datasets.py`, `agent_groups.py`, `vector_stores.py`)

### Workers (`apps/api/app/workers/`)

Temporal workers for async processing:
- `orchestration_worker.py`: TaskExecutionWorkflow + ChannelHealthMonitorWorkflow + FollowUpWorkflow (queue: `servicetsunami-orchestration`)
- `databricks_worker.py`: DatasetSync, KnowledgeExtraction, AgentKitExecution, DataSourceSync workflows (queue: `servicetsunami-databricks`)
- `scheduler_worker.py`: Automated pipeline execution (cron/interval scheduling, polls every 60s)

### Routes (`apps/api/app/api/v1/`)

FastAPI routers mounted at `/api/v1`. All routes use dependency injection via `deps.py` for database sessions and current user extraction.

## Web Frontend Structure

### Pages (`apps/web/src/pages/`)

Organized in 3-section navigation:
- **INSIGHTS**: `DashboardPage.js`, `DatasetsPage.js`
- **AI OPERATIONS**: `ChatPage.js`, `AgentsPage.js`, `WorkflowsPage.js`, `MemoryPage.js`
- **WORKSPACE**: `IntegrationsPage.js`, `NotebooksPage.js`, `VectorStoresPage.js`, `ToolsPage.js`
- **SETTINGS**: `SettingsPage.js`, `LLMSettingsPage.js`, `BrandingPage.js`
- **AUTH**: `RegisterPage.js`, `AgentWizardPage.js`

### Components (`apps/web/src/components/`)

- `Layout.js`: Authenticated layout with glassmorphic dark theme sidebar
- `wizard/`: Agent creation wizard (5-step flow with localStorage draft persistence). 8 templates including Research Agent, Lead Generation, and Knowledge Manager. 5 tools: sql_query, calculator, data_summary, entity_extraction, knowledge_search
- `TaskTimeline.js`: Execution trace timeline with step icons and duration badges
- `SkillsConfigPanel.js`: Skill enablement grid with dynamic credential forms from registry and test execution button

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
```

### Web Configuration (`apps/web/.env.local`)

```
REACT_APP_API_BASE_URL=http://localhost:8001
```

Uses `REACT_APP_` prefix (Create React App requirement).

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

## Additional Documentation

- `docs/KUBERNETES_DEPLOYMENT.md`: Full Kubernetes deployment runbook
- `docs/plans/`: Implementation plans and design documents
  - `2025-02-13-enterprise-orchestration-engine-design.md`: Orchestration engine design document
  - `2025-02-13-enterprise-orchestration-engine-plan.md`: 18-task implementation plan
  - `2026-02-20-whatsapp-agent-integration-platform-design.md`: WhatsApp agent + external app integration
  - `2026-02-20-lead-scoring-skill-design.md`: LLM-powered lead scoring with configurable rubrics
  - `2025-12-18-automations-temporal-plan.md`: Automations with Temporal connectors and scheduling
- `LLM_INTEGRATION_README.md`, `TOOL_FRAMEWORK_README.md`, `DATABRICKS_SYNC_README.md`: Feature docs
