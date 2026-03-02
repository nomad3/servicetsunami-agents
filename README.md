# ServiceTsunami — Enterprise AI Lakehouse Platform

[![Production](https://img.shields.io/badge/production-servicetsunami.com-green)](https://servicetsunami.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue)](LICENSE)
[![GKE](https://img.shields.io/badge/GKE-Kubernetes-4285F4)](https://cloud.google.com/kubernetes-engine)
[![Turborepo](https://img.shields.io/badge/Turborepo-Monorepo-EF4444)](https://turbo.build)

Enterprise-grade unified data & AI lakehouse platform. Orchestrates AI agents, data pipelines, and analytics workloads with full multi-tenant isolation. Built as a **Turborepo monorepo** deploying to GKE via Helm and GitHub Actions.

**Production:** [servicetsunami.com](https://servicetsunami.com)

**Sub-platforms:** [DentalERP](https://scdp-front-prod.servicetsunami.com) · Into the Space

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│  React SPA (Bootstrap 5 · i18n · React Router v7)                │
│  Dashboard · Chat · Agents · Datasets · Pipelines · Integrations │
└──────────────────────────┬───────────────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────────────┐
│  FastAPI Backend (Python 3.11 · SQLAlchemy · JWT Multi-Tenant)   │
│  Auth · Chat · Workflows · Tool Executor · Analytics · Datasets  │
└──────┬───────────────────┬──────────────────┬────────────────────┘
       │                   │                  │
┌──────▼──────┐   ┌───────▼───────┐  ┌───────▼───────┐
│  ADK Server │   │  MCP Server   │  │  Temporal     │
│  (Google    │   │  (Databricks  │  │  (Workflow    │
│   ADK)      │   │   Integration)│  │   Engine)     │
│  Supervisor │   │  9 MCP Tools  │  │  Data Sync    │
│  + Agents   │   │  Unity Catalog│  │  Knowledge    │
└─────────────┘   └───────────────┘  └───────────────┘
                           │
              ┌────────────▼────────────┐
              │  Databricks Unity       │
              │  Catalog                │
              │  Bronze → Silver → Gold │
              └─────────────────────────┘
```

### Data Flow

```
User Upload / API Ingest
  → Temporal Workflow (reliable background processing)
    → Bronze Table (raw data in Unity Catalog)
      → Silver Table (automated cleaning + quality checks)
        → Gold Table (business-ready analytics)
          → Chat / Dashboard queries
```

---

## Tech Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| Frontend | React 18, JavaScript, Bootstrap 5, React Router v7, i18next | Dashboard, chat UI, dataset management |
| Backend | FastAPI, Python 3.11, SQLAlchemy, Pydantic, PostgreSQL | Multi-tenant REST API, auth, business logic |
| ADK Server | Google ADK, Python 3.11 | Multi-agent orchestration (supervisor pattern) |
| MCP Server | FastMCP, Python 3.11 | Databricks integration, data tools (9 MCP tools) |
| Workflows | Temporal | Durable execution: data sync, knowledge extraction |
| Data Layer | Databricks Unity Catalog, PostgreSQL, Redis | Medallion architecture, entities, caching |
| Infrastructure | GKE, Helm, GitHub Actions, Terraform (AWS) | Kubernetes deployment, CI/CD |

---

## Key Features

### Multi-Agent Orchestration (Google ADK)
- **Hierarchical Multi-Team Model**: Root supervisor routing to 5 top-level teams (Personal Assistant, Dev, Data, Sales, Marketing).
- **Self-Modifying Agents**: Dev team with strict 5-step cycle, shell access, and autonomous deployment capabilities.
- **Industry Specialization**: Pre-built agents for HealthPets (mobile cardiology), Remedia (pharmacy), and M&A Deal Pipelines.
- **Task Delegation**: Configurable autonomy, depth limits, and inter-agent communication (supervisor pattern).

### Multi-LLM Router
Smart routing across 5+ providers with cost optimization and automatic failover:

| Provider | Best For | Cost/1M tokens |
|----------|----------|----------------|
| DeepSeek | Code generation | $0.14 |
| Google Gemini | Multimodal analysis | $1.25 |
| GPT-4o | General tasks | $2.50 |
| Mistral | European compliance | $2.00 |
| Claude | Complex reasoning | $3.00 |

### Databricks Lakehouse Integration
- Automatic dataset sync to Unity Catalog via Temporal workflows.
- Bronze/Silver/Gold medallion architecture.
- MCP-compliant server with 9 tools for data operations.

### Durable Business Workflows (Temporal)
- **Deal Pipeline**: Discover → Score → Research → Outreach → Advance → Sync.
- **Remedia Order**: E-commerce lifecycle with WhatsApp notifications and payment monitoring.
- **HealthPets Billing**: Usage-based processing, invoice generation, and automated payments.
- **Knowledge Extraction**: LLM-powered entity and relation extraction from chat history.

### Three-Tier Memory System
- **Hot Context** (Redis) — Active session state, <1ms.
- **Semantic Memory** (Vector Store) — Past conversation embeddings, ~10ms.
- **Knowledge Graph** (PostgreSQL) — Entities, relationships, and **Lead Scoring** (rubric-based), ~50ms.

### Enterprise Multi-Tenancy
- Full tenant isolation via JWT-secured APIs.
- Per-tenant data segregation, LLM configs, feature flags.
- Whitelabel branding with custom domains and industry templates.

---

## Quick Start

### Prerequisites
- Docker & Docker Compose
- Node.js 18+ / pnpm
- Python 3.11+

### Development

```bash
# Install dependencies
pnpm install

# Start all services (custom ports to avoid conflicts)
DB_PORT=8003 API_PORT=8001 WEB_PORT=8002 docker-compose up --build

# Services:
#   API:         http://localhost:8001
#   Web:         http://localhost:8002
#   Database:    localhost:8003
#   MCP Server:  http://localhost:8086
#   ADK Server:  http://localhost:8085
#   Temporal UI: http://localhost:8233
```

### Access

- **Dashboard**: http://localhost:8002/dashboard
- **Demo Login**: `test@example.com` / `password` (or "Login as Demo User")

### Individual Services

```bash
# API
cd apps/api && pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# Web
cd apps/web && npm install && npm start

# MCP Server
cd apps/mcp-server && pip install -e ".[dev]"
python -m src.server

# ADK Server
cd apps/adk-server && pip install -r requirements.txt
python server.py

# Monorepo build
pnpm install && pnpm build && pnpm lint
```

### Testing

```bash
# API tests
cd apps/api && pytest -v

# Web tests
cd apps/web && npm test -- --ci --watchAll=false

# MCP Server tests
cd apps/mcp-server && pytest tests/ -v

# E2E (production-grade, 22 test cases)
BASE_URL=http://localhost:8001 ./scripts/e2e_test_production.sh
```

---

## Repository Structure

```
servicetsunami/
├── apps/
│   ├── api/                  # FastAPI backend (Python 3.11)
│   │   ├── app/
│   │   │   ├── api/v1/       # REST API endpoints
│   │   │   ├── models/       # SQLAlchemy models (all have tenant_id)
│   │   │   ├── services/     # Business logic (LLM, chat, tools, context)
│   │   │   └── core/         # Config, security, dependencies
│   │   └── tests/
│   ├── web/                  # React SPA (JavaScript, CRA)
│   │   └── src/
│   │       ├── pages/        # Dashboard, Chat, Agents, Datasets, Pipelines
│   │       ├── components/   # Layout (glassmorphic sidebar), Wizard
│   │       └── services/     # API clients
│   ├── adk-server/           # Google ADK multi-agent server
│   │   ├── servicetsunami_supervisor/  # Hierarchical Team Agents
│   │   ├── tools/            # Agent tools (data, shell, analytics, knowledge)
│   │   └── server.py
│   └── mcp-server/           # MCP server for Databricks integration
│       ├── src/tools/        # 9 MCP tools
│       └── tests/
├── helm/
│   ├── charts/microservice/  # Reusable Helm base chart
│   └── values/               # Per-service: api, web, worker, adk, temporal, redis, postgresql
├── infra/terraform/          # AWS IaC (EKS, Aurora, VPC)
├── scripts/                  # deploy.sh, e2e_test_production.sh
├── docs/                     # Plans, deployment runbook, archive
├── docker-compose.yml
├── turbo.json                # Turborepo config
└── pnpm-workspace.yaml
```

---

## Production Deployment

Deploys **exclusively via Kubernetes** (GKE) using Helm charts and GitHub Actions.

```bash
# Full stack deploy
gh workflow run deploy-all.yaml -f environment=prod

# ADK server only
gh workflow run adk-deploy.yaml -f environment=prod

# Watch rollout
kubectl get pods -n prod -w
kubectl rollout status deployment/servicetsunami-api -n prod
```

### Production Architecture

```
GKE Gateway → prod namespace
  ├── servicetsunami-web     (React SPA)
  ├── servicetsunami-api     (FastAPI)
  ├── servicetsunami-worker  (Temporal worker)
  ├── servicetsunami-adk     (Google ADK agents)
  ├── mcp-server             (Databricks integration)
  ├── temporal + temporal-web
  └── redis / postgresql (Cloud SQL proxy)
```

### GitHub Actions Workflows
- `deploy-all.yaml` — Full stack deployment
- `adk-deploy.yaml` — ADK server only
- `servicetsunami-api.yaml` — API service
- `servicetsunami-web.yaml` — Web frontend
- `servicetsunami-worker.yaml` — Temporal worker
- `kubernetes-infrastructure.yaml` — Initial infra setup

See `docs/KUBERNETES_DEPLOYMENT.md` for the full runbook.

---

## Recent Changes

- **Hierarchical Agent Teams** — New ADK structure with 5 specialized teams and Root Supervisor.
- **Self-Modifying Capabilities** — Dev team agents can now autonomously modify and deploy code.
- **Industry Vertical Support** — Native agents and workflows for HealthPets, Remedia, and Deal Pipeline.
- **Lead Scoring Rubrics** — Configurable LLM-powered entity scoring within the knowledge graph.
- **Pipeline Run Tracking** — Scheduled and on-demand pipeline execution with run history.
- **WhatsApp Integration Platform** — Multi-account WhatsApp orchestration for "Luna" and industry agents.

---

## API Reference

### Authentication
```bash
POST /api/v1/auth/register   # Create tenant + admin user
POST /api/v1/auth/login      # Returns JWT access token
```

### Core Endpoints (Bearer token required)
```bash
# Agents & Teams
GET/POST /api/v1/agents
GET/POST /api/v1/agent_groups

# Chat & Memory
POST /api/v1/chat/sessions
POST /api/v1/chat/sessions/{id}/messages

# Datasets & Pipelines
POST /api/v1/datasets/ingest
GET  /api/v1/datasets/{id}/databricks/status

# LLM Configuration
GET  /api/v1/llm/providers
POST /api/v1/llm/configs
```

---

## Contributing

1. Branch: `feature/amazing-feature`
2. Follow patterns in `AGENTS.md`
3. Test: `pnpm test && pnpm lint`
4. Conventional commits: `feat:`, `fix:`, `chore:`
5. Open a Pull Request

---

*Built with React · FastAPI · Google ADK · Temporal · Databricks · Turborepo · GKE*
