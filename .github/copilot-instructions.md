# Copilot Instructions for AgentProvision

AgentProvision is an **AI agent orchestration platform** that routes tasks to Claude Code CLI, Codex, and Gemini CLI via Temporal workflows. It serves 81 MCP tools, maintains a knowledge graph with pgvector, and auto-scores responses with local Qwen models for reinforcement learning.

## Quick Start

```bash
# Start all services locally
DB_PORT=8003 API_PORT=8001 WEB_PORT=8002 docker-compose up --build

# Services available at:
# - Web: http://localhost:8002
# - API: http://localhost:8001
# - Temporal UI: http://localhost:8233
# - Demo login: test@example.com / password
```

## Build, Test, and Lint Commands

### Python (API & Code-Worker)

```bash
# Setup
cd apps/api
pip install -r requirements.txt

# Run all tests
pytest

# Run specific test file
pytest tests/test_api.py

# Run single test
pytest tests/test_api.py::test_login -v

# Lint code
ruff check app

# Fix linting issues
ruff check app --fix
```

### React (Web Frontend)

```bash
# Setup
cd apps/web
npm install

# Run tests (watch mode)
npm test

# Run tests once (CI mode)
npm test -- --ci --watchAll=false

# Run specific test file
npm test -- WizardStepper.test.js

# Build for production
npm run build

# Start dev server
npm start
```

### Monorepo (All Services)

```bash
# Install and build all
pnpm install && pnpm build && pnpm lint
```

## Architecture Overview

### Core Stack

- **Backend**: FastAPI (Python 3.11) at `apps/api` (port 8001)
- **Frontend**: React 18 SPA at `apps/web` (port 8002)
- **Code Execution**: Claude Code CLI Temporal worker at `apps/code-worker`
- **MCP Tools**: FastMCP server at `apps/mcp-server` (ports 8086-8087)
- **Orchestration**: Temporal at port 7233
- **Database**: PostgreSQL with pgvector at port 8003
- **Local ML**: Ollama at port 11434 (Qwen models for scoring, extraction, summarization)
- **Tunneling**: Cloudflare Tunnel (agentprovision.com + agentprovision.com)

### Request Flow

```
User Input (Web/WhatsApp/API)
  ↓
FastAPI Chat Service
  ↓
Agent Router (Python, deterministic routing — zero LLM cost)
  ↓
Temporal Workflow Dispatcher
  ↓
Code-Worker Pod (Claude Code CLI with tenant's OAuth token)
  ↓
MCP Tool Server (81 tools)
  ↓
Response → Auto Quality Scorer (local Qwen, 6-dim rubric) → RL Experience
```

### Service Organization

**`apps/api`** (FastAPI backend):
- **Models** (`app/models/`): SQLAlchemy ORM with `tenant_id` for multi-tenancy
- **Services** (`app/services/`): Business logic, CRUD, LLM calls, embeddings
- **Routes** (`app/api/v1/`): RESTful endpoints with dependency injection
- **Workers** (`app/workers/`): Temporal workflow definitions
- **Workflows** (`app/workflows/`): Temporal activities and orchestration
- **Skills** (`app/skills/`): Skill marketplace (native/community/custom tiers)

**`apps/web`** (React SPA):
- **Pages** (`src/pages/`): Dashboard, Chat, Agents, Workflows, Memory (Knowledge Base), Integrations, Settings
- **Components** (`src/components/`): Reusable UI (Layout, TaskTimeline, NotificationBell, etc.)
- **Bootstrap 5 + React Bootstrap**: Glassmorphic "Ocean Theme" with dark mode support

**`apps/code-worker`** (Temporal worker):
- Autonomous Python/Node.js agent
- Executes `claude` commands with tenant's OAuth token (subscription-based, not API credits)
- Creates feature branches, commits, and opens PRs with full audit trail
- Task queue: `agentprovision-code`

**`apps/mcp-server`** (MCP protocol):
- 81 tools across 15 categories: Knowledge, Email, Calendar, Jira, GitHub, Ads, Data, Sales, Competitor, Monitor, Reports, Analytics, Skills, Shell, Drive
- Served via FastMCP (Anthropic's MCP standard)

## Key Conventions & Patterns

### Multi-Tenancy

**CRITICAL**: Every database query must filter by `tenant_id` to enforce isolation. All models inherit `tenant_id: UUID`.

```python
# ✅ Correct: Filter by tenant
agents = db.query(Agent).filter(Agent.tenant_id == current_user.tenant_id).all()

# ❌ WRONG: No tenant filter
agents = db.query(Agent).all()
```

### Authentication

- JWT-based: `Authorization: Bearer <token>` header
- Routes extract user via `get_current_user()` dependency (in `app/api/v1/deps.py`)
- All protected endpoints require user context
- Demo credentials: `test@example.com` / `password`

### Database Initialization

On API startup, `apps/api/app/main.py` calls `init_db()` which:
1. Creates tables (idempotent)
2. Seeds demo data
3. Uses SQLAlchemy synchronous sessions (despite `asyncpg` driver)

### Multi-LLM Provider Routing

- **Integration Registry + Credential Vault pattern**: Tenants select LLM provider (Claude Code, Codex, Gemini) via integrations page
- **OAuth token storage**: Credentials are Fernet-encrypted in the database
- **Agent Router**: Deterministic routing (zero LLM cost) maps user intents/channels to agent skills
- **Fallback**: Local Qwen model (Luna persona) when no subscription connected

### Knowledge Graph & Vector Search

- Entities (`knowledge_entity.py`) and relations (`knowledge_relation.py`) stored in PostgreSQL
- **pgvector embeddings**: 768-dim vectors via `nomic-embed-text-v1.5` (local, open-source)
- **Centralized in `embedding_service.py`**: Functions `embed_text()`, `embed_and_store()`, `search_similar()`, `recall()`
- Used by: knowledge operations, chat context, memory activities, RL experiences, skill registry (auto-trigger matching)

### Auto Quality Scoring & RL

Every response is automatically scored by local Qwen across 6 dimensions (100 pts total):

| Dimension | Pts | Measures |
|-----------|-----|----------|
| Accuracy | 25 | Factual correctness, no hallucinations |
| Helpfulness | 20 | Addresses actual user need, actionable |
| Tool Usage | 20 | Appropriate MCP tool selection |
| Memory Usage | 15 | Knowledge graph recall, context building |
| Efficiency | 10 | Concise, fast response |
| Context Awareness | 10 | Conversation continuity |

Scores logged as **RL experiences** (`rl_experience` table) with reward components for continuous improvement. Zero cloud cost — fully local inference.

### Service Structure Pattern

Each service extends the `BaseService` pattern:

```python
from app.services.base import BaseService

class AgentService(BaseService):
    model = Agent
    
    def create(self, db: Session, tenant_id: UUID, **kwargs):
        # Always filter by tenant
        obj = self.model(tenant_id=tenant_id, **kwargs)
        db.add(obj)
        db.commit()
        return obj
```

### Import Order (Python)

```python
# 1. Standard library
import uuid
from datetime import datetime

# 2. Third-party
from fastapi import FastAPI
from sqlalchemy import Column, String
from pydantic import BaseModel

# 3. Local app
from app.db.session import SessionLocal
from app.models.agent import Agent
```

### React Component Structure

```jsx
// PascalCase for components
export function WizardStepper({ steps, onComplete }) {
  const [currentStep, setCurrentStep] = useState(0);
  
  // Hooks, event handlers
  const handleNext = () => setCurrentStep(prev => prev + 1);
  
  return (
    <div className="wizard-container">
      {/* JSX */}
    </div>
  );
}

// camelCase for services
export const agentService = {
  list: () => axios.get('/api/v1/agents'),
  create: (data) => axios.post('/api/v1/agents', data),
};
```

### Error Handling

**API**:
- Return proper HTTP status codes with error details
- Validation errors: `422 Unprocessable Entity`
- Auth errors: `401 Unauthorized` or `403 Forbidden`
- Not found: `404 Not Found`

**Frontend**:
- Try/catch around API calls
- Display user-friendly error messages
- Log full errors to console for debugging

### Database Migrations

Manual SQL scripts in `apps/api/migrations/` (not Alembic). See `migrations/README.md` for runbook.

### Temporal Workflows

**Task Queues**:
- `agentprovision-orchestration`: TaskExecution, ChannelHealthMonitor, FollowUp, InboxMonitor, CompetitorMonitor
- `agentprovision-code`: Code-worker (Claude Code CLI execution)
- `agentprovision-postgres`: DatasetSync, KnowledgeExtraction, AgentKitExecution

Workflow structure:
```python
@workflow.defn
class CodeTaskWorkflow:
    @workflow.run
    async def run(self, task: CodeTask) -> str:
        # Dispatch to activities
        result = await workflow.execute_activity(execute_code_task, task)
        return result
```

### Skill Marketplace

Three-tier system:
1. **Native**: Built-in skills (sql_query, calculator, entity_extraction, knowledge_search, report_generation)
2. **Community**: Imported from GitHub repos (supports GWS SKILL.md format + Claude Code superpowers)
3. **Custom**: Per-tenant skills created/edited in UI with versioning

Engines: `python`, `shell`, `markdown`, `tool` (class registry)

### Adding a New Resource

1. **Model**: `apps/api/app/models/{resource}.py` with `tenant_id` FK
2. **Schema**: `apps/api/app/schemas/` with `{Resource}Create`, `{Resource}Update`, `{Resource}InDB`
3. **Service**: `apps/api/app/services/{resources}.py` extending `BaseService`
4. **Routes**: `apps/api/app/api/v1/{resources}.py` with dependency injection
5. **Frontend**: Add page in `apps/web/src/pages/` and route in `App.js`
6. **Helm**: Update `helm/values/` if Kubernetes resources needed

## Configuration & Environment

### Docker Compose Ports

```bash
API_PORT=8001      # FastAPI backend
WEB_PORT=8002      # React frontend
DB_PORT=8003       # PostgreSQL
MCP_PORT=8086      # MCP server
                   # MCP tools on 8087
```

### API Environment (`apps/api/.env`)

```bash
ANTHROPIC_API_KEY=sk-ant-...
DATABASE_URL=postgresql://postgres:postgres@db:5432/agentprovision
SECRET_KEY=<your-jwt-secret>
TEMPORAL_ADDRESS=temporal:7233
MCP_SERVER_URL=http://mcp-server:8000
ENCRYPTION_KEY=<fernet-key>  # python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
```

### Web Environment (`apps/web/.env.local`)

```bash
REACT_APP_API_BASE_URL=http://localhost:8001
```

### Code-Worker Configuration

```bash
GITHUB_TOKEN=ghp_xxxxx                      # GitHub PAT
API_INTERNAL_KEY=xxxxx                      # Internal API auth
API_BASE_URL=http://agentprovision-api      # Internal service URL
TEMPORAL_ADDRESS=temporal:7233
# CLAUDE_CODE_OAUTH_TOKEN set dynamically per-task from tenant's vault
```

## Important Files & Directories

| Path | Purpose |
|------|---------|
| `apps/api/app/main.py` | API entry point, initialization |
| `apps/api/app/models/` | SQLAlchemy ORM models (all with `tenant_id`) |
| `apps/api/app/services/` | Business logic, CRUD, API calls |
| `apps/api/app/api/v1/` | FastAPI routers and endpoints |
| `apps/api/app/workflows/` | Temporal workflow definitions |
| `apps/api/app/workers/` | Temporal worker registration |
| `apps/web/src/pages/` | React page components |
| `apps/web/src/components/` | Reusable UI components |
| `apps/code-worker/` | Claude Code CLI Temporal worker |
| `apps/mcp-server/` | MCP tool definitions |
| `docker-compose.yml` | Local development stack |
| `helm/` | Kubernetes Helm charts |
| `infra/terraform/` | AWS infrastructure as code |

## Testing Strategy

### Unit Tests

Place tests in same directory as code:
```
apps/api/tests/test_services.py
apps/api/tests/test_api.py
apps/web/src/components/__tests__/WizardStepper.test.js
```

### Running Tests Locally

```bash
# Full suite
pytest

# Single file
pytest tests/test_api.py -v

# Single test function
pytest tests/test_api.py::test_login -v

# With coverage
pytest --cov=app tests/
```

### E2E Testing

```bash
BASE_URL=http://localhost:8001 ./scripts/e2e_test_production.sh
```

## Deployment

### Local Development
```bash
docker-compose up --build
```

### Production (Kubernetes + GitHub Actions)
- See `.github/workflows/` for CI/CD pipelines
- Deploys via Helm charts to GKE
- Credentials stored in GCP Secret Manager
- Terraform for AWS infrastructure

## Common Tasks

### Add a Database Model

1. Create `apps/api/app/models/my_resource.py`:
```python
from sqlalchemy import Column, String
from app.db.base import Base
import uuid

class MyResource(Base):
    __tablename__ = "my_resource"
    id = Column(UUID, primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID, ForeignKey("tenant.id"), nullable=False)
    name = Column(String, nullable=False)
```

2. Add to `apps/api/app/models/__init__.py`

3. Create migration SQL in `apps/api/migrations/`

4. Create service in `apps/api/app/services/my_resources.py`

5. Create schema in `apps/api/app/schemas/my_resource.py`

6. Add routes in `apps/api/app/api/v1/my_resources.py`

### Add a React Page

1. Create `apps/web/src/pages/MyResourcePage.js`
2. Add route in `apps/web/src/App.js`
3. Add nav item in `apps/web/src/components/Layout.js`

### Connect to MCP Tool

MCP tools are auto-exposed to agent CLIs via config in `session_manager.py`. No extra registration needed — just call the tool from within Claude Code or another CLI agent.

### Debug Temporal Workflow

- View Temporal UI at http://localhost:8233
- See workflow history, failed activities, and execution traces
- Check worker logs: `docker-compose logs api-worker`

## Reference Documentation

- **Full architecture**: `CLAUDE.md` (comprehensive, covers models, services, workflows, deployment)
- **Agent structure**: `AGENTS.md` (agent hierarchy, tools, code style guidelines)
- **Gemini integration**: `GEMINI.md` (Gemini CLI setup and integration)
- **README**: `README.md` (high-level overview, quick start)

---

**Last Updated**: 2026-03-25

For questions about codebase structure or patterns, refer to `CLAUDE.md` which contains exhaustive architecture and service documentation.
