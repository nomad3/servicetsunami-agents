# AGENTS.md

## Build/Lint/Test Commands

### API (Python)

```bash
cd apps/api && pip install -r requirements.txt
pytest                    # Run all tests
pytest tests/test_api.py  # Run single test file
pytest -v                 # Verbose output
ruff check app            # Lint code
```

### Web (React)

```bash
cd apps/web && npm install
npm test                  # Run tests in watch mode
npm test -- --ci          # Run tests once
npm test -- WizardStepper.test.js  # Run single test
npm run build             # Build for production
```

### Monorepo

```bash
pnpm install && pnpm build && pnpm lint
```

## Agent Hierarchy (ADK)

The project uses a hierarchical multi-team structure in `apps/adk-server/servicetsunami_supervisor/`.

### 1. Root Supervisor
Routes user requests to the appropriate team supervisor. It does not have tools of its own.

### 2. Personal Assistant Team ("Luna")
- **personal_assistant**: WhatsApp-native business co-pilot. Handles high-level scheduling, reminders, and general business inquiries.

### 3. Code Agent
- **code_agent**: Autonomous coding agent powered by Claude Code CLI. Delegates tasks to a dedicated `code-worker` pod via Temporal (`servicetsunami-code` queue). Creates feature branches and PRs automatically.

### 4. Data Team
- **data_analyst**: Performs SQL queries and data analysis via MCP server.
- **report_generator**: Creates summaries and visualizations.
- **knowledge_manager**: Manages knowledge graph entities and relations.

### 5. Sales Team
- **sales_agent**: Manages deals, prospects, and pipeline state.
- **customer_support**: Handles inquiries, provides documentation, and troubleshoots.

### 6. Marketing Team
- **web_researcher**: Gathers market intelligence, competitor data, and finds prospects via MCP scraper.
- **marketing_analyst**: Manages ad campaigns across Meta/Google/TikTok, monitors competitors, searches public ad libraries, and provides cross-platform marketing intelligence.
- **knowledge_manager**: Manages knowledge graph entities, relations, lead scoring, and semantic search.

### 7. Prospecting Team
- **prospect_researcher**: Lead research and intelligence gathering (tech stack, hiring, funding).
- **prospect_scorer**: Lead scoring with BANT qualification and configurable rubrics.
- **prospect_outreach**: Outreach drafting, pipeline management, and proposals.

### 8. Specialized Industry Teams
- **Vet Supervisor (HealthPets)**: `cardiac_analyst`, `billing_agent`, `report_generator`.
- **Deal Team**: `deal_analyst`, `deal_researcher`, `outreach_specialist`.

### ADK Tools (`apps/adk-server/tools/`)
- `knowledge_tools.py`: Entity CRUD, relations, observations, semantic search
- `ads_tools.py`: Meta/Google/TikTok campaign management + public ad library search (12 functions)
- `competitor_tools.py`: Competitor entity management and comparison (5 functions)
- `google_tools.py`: Gmail and Calendar operations
- `jira_tools.py`: Jira project and issue management
- `github_tools.py`: GitHub repo, issue, and PR operations
- `monitor_tools.py`: Inbox monitor + competitor monitor control
- `data_tools.py`: SQL queries, dataset discovery, insights
- `sales_tools.py`: Lead qualification, follow-ups, pipeline
- `code_tools.py`: Claude Code CLI execution via Temporal
- `connector_tools.py`: Data source queries
- `shell_tools.py`: Shell command execution

## Code Style Guidelines

### Python (API & ADK)

- SQLAlchemy models with UUID primary keys, always include `tenant_id`.
- ADK agents should be defined in `apps/adk-server/servicetsunami_supervisor/`.
- Tools should be modularized in `apps/adk-server/tools/`.
- Import order: stdlib → third-party → local app.
- Pydantic schemas for API validation.

### JavaScript (Web)

- React functional components with hooks, Bootstrap 5 UI.
- Axios for API calls with JWT auth, localStorage for tokens.
- Components: PascalCase, Services: camelCase.

### Error Handling

- API: Proper HTTP status codes with error details.
- Frontend: Try/catch for API calls, user-friendly messages.
- Always validate tenant isolation in queries.

## Luna Assistant (OpenCode / Local LLM)

When running as Luna via OpenCode with local Gemma 4:

### Required Tool Parameters

ALL servicetsunami MCP tool calls MUST include:
- `tenant_id`: `"0728db77-504d-452d-8c5d-9c26993b1829"`

Never omit tenant_id — tools will fail without it.

### Personality

- Keep responses concise and natural
- You speak English and Spanish fluently
- Be warm but professional
