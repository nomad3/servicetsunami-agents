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

### 3. Dev Team (Self-Modifying)
Operates on a strict 5-step development cycle:
- **architect**: Explores codebase, designs solutions, and writes specifications.
- **coder**: Implements code changes using `shell_tools`.
- **tester**: Runs tests and verifies implementation.
- **dev_ops**: Deploys changes via git.
- **user_agent**: Validates the final result against user requirements.
*Note: Dev agents have shell access via `execute_shell`.*

### 4. Data Team
- **data_analyst**: Performs SQL queries and data analysis via MCP server.
- **report_generator**: Creates summaries and visualizations.
- **knowledge_manager**: Manages knowledge graph entities and relations.

### 5. Sales Team
- **sales_agent**: Manages deals, prospects, and pipeline state.
- **customer_support**: Handles inquiries, provides documentation, and troubleshoots.

### 6. Marketing Team
- **web_researcher**: Gathers market intelligence, competitor data, and finds prospects.

### 7. Specialized Industry Teams
- **HealthPets**: `cardiac_analyst`, `billing_agent`, `vet_supervisor`.
- **Deal Team**: `deal_analyst`, `deal_researcher`, `outreach_specialist`.

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
