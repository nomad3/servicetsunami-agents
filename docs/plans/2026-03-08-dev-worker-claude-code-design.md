# Dev Worker: Claude Code Integration Design

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the 5-agent ADK dev team (architect → coder → tester → dev_ops → user_agent) with a single `dev_agent` that delegates coding tasks to Claude Code CLI running in an isolated Kubernetes pod, communicating via Temporal workflows.

**Architecture:** A dedicated dev worker pod runs Claude Code CLI authenticated via Claude Pro/Max subscription token. The ADK `dev_agent` starts a `DevTaskWorkflow` on a `servicetsunami-dev` Temporal queue. Claude Code handles the full development cycle autonomously — reads code, implements, tests, commits to a feature branch, and creates a PR. The user reviews and merges.

**Tech Stack:** Claude Code CLI (Node.js), Python 3.11 (Temporal worker), Temporal, Kubernetes, Helm, GitHub Actions.

---

## Architecture Diagram

```
User (chat/WhatsApp)
  → Root Supervisor (ADK)
    → dev_agent (ADK leaf agent)
      → starts DevTaskWorkflow (Temporal, queue: servicetsunami-dev)
        → dev-worker pod picks up task
          → git pull origin main
          → git checkout -b dev/task-<short-id>
          → claude -p "<task description>" --output-format json --allowedTools "Edit,Write,Bash,Read,Glob,Grep"
          → git push origin dev/task-<short-id>
          → gh pr create --title "..." --body "..."
        → returns {pr_url, summary, branch, files_changed}
      → dev_agent reports PR URL + summary to user
```

## Components

### 1. Dev Worker Pod (`apps/dev-worker/`)

**Dockerfile:**
- Base: `python:3.11-slim`
- Install: Node.js 20 LTS (for Claude Code), git, gh CLI
- Install: `npm install -g @anthropic-ai/claude-code`
- Install: Python deps (temporalio, pydantic)
- Entrypoint: `python -m worker`

**Startup sequence:**
1. `git clone https://<GITHUB_TOKEN>@github.com/nomad3/servicetsunami-agents.git /workspace`
2. Start Temporal worker on `servicetsunami-dev` queue
3. Per-task: fetch tenant's Claude session token via internal API → `claude setup-token` → execute

**Worker code (`apps/dev-worker/worker.py`):**
- Temporal worker with one workflow + one activity
- `DevTaskWorkflow`: receives task description, tenant_id, optional context
- `execute_dev_task` activity:
  1. `cd /workspace && git fetch origin && git checkout main && git pull`
  2. `git checkout -b dev/task-<uuid[:8]>`
  3. Run Claude Code: `claude -p "<task>" --output-format json --allowedTools "Edit,Write,Bash,Read,Glob,Grep"`
  4. `git push origin dev/task-<id>`
  5. `gh pr create --title "<title>" --body "<summary>"`
  6. Return `{pr_url, summary, branch, files_changed, claude_output}`

**Timeouts:**
- Activity timeout: 15 minutes (Claude Code sessions can be long)
- Heartbeat: every 60 seconds
- Retry: 1 retry on failure

### 2. ADK dev_agent (replaces 5 agents)

**File:** `apps/adk-server/servicetsunami_supervisor/dev_agent.py`

Single leaf agent with one tool: `start_dev_task(task_description: str) -> dict`

The tool:
1. Calls the API's `/api/v1/dev-tasks` endpoint (or starts Temporal workflow directly)
2. Waits for completion (polls or uses Temporal query)
3. Returns the result (PR URL, summary)

**Instructions:** Tell the user what's happening ("I'm starting a dev task for X, Claude Code will implement it and create a PR"). Report back with the PR URL when done.

### 3. Root Supervisor Update

**File:** `apps/adk-server/servicetsunami_supervisor/agent.py`

- Remove `dev_team` import, replace with `dev_agent`
- Update routing: "dev_agent" handles all code/tool/feature requests
- Update `sub_agents` list

### 4. Temporal Integration

**Queue:** `servicetsunami-dev` (new dedicated queue)

**Workflow:** `DevTaskWorkflow`
- Input: `DevTaskInput(task_description: str, tenant_id: str, context: Optional[str])`
- Single activity: `execute_dev_task`
- Shows up in Workflows Executions page

**No changes to existing workers** — the dev worker is its own separate Temporal worker process.

### 5. Authentication & Secrets

**Per-tenant tokens (from Integrations page):**
- Claude session token → stored encrypted in `integration_credentials` table via credential vault
- Dev worker fetches at runtime: `GET /api/v1/oauth/internal/token/claude_code?tenant_id=...`
- Each tenant provides their own Claude Pro/Max subscription token

**GCP Secret Manager (infrastructure only):**
- `servicetsunami-github-token` — already exists (for git clone + push + gh CLI)
- `servicetsunami-api-internal-key` — already exists (for dev worker → API auth)

**ExternalSecrets in Helm values:**
```yaml
externalSecret:
  data:
    - secretKey: GITHUB_TOKEN
      remoteRef:
        key: servicetsunami-github-token
    - secretKey: API_INTERNAL_KEY
      remoteRef:
        key: servicetsunami-api-internal-key
```

**Token refresh:** Claude subscription tokens last weeks. User pastes a new one in the Integrations page when expired. The card shows connection status (connected/expired).

### 6. Git Flow

1. Claude Code creates branch: `dev/task-<uuid[:8]>`
2. Commits with descriptive messages
3. Pushes branch to origin
4. Creates PR via `gh pr create`
5. CI runs on the branch (existing workflows)
6. User reviews and merges to main
7. Deploy triggers on merge (existing CI/CD)

### 7. Helm Values (`helm/values/servicetsunami-dev-worker.yaml`)

Follows the worker pattern (no HTTP service, no probes):
```yaml
nameOverride: "servicetsunami-dev-worker"
container:
  command: ["python", "-m", "worker"]
replicaCount: 1
resources:
  requests: {cpu: 200m, memory: 512Mi}
  limits: {cpu: 1000m, memory: 2Gi}  # Claude Code needs more memory
livenessProbe: {enabled: false}
readinessProbe: {enabled: false}
service: {type: ClusterIP, port: 80}
```

No Cloud SQL proxy needed (dev worker doesn't access the database).

### 8. GitHub Actions (`dev-worker-deploy.yaml`)

Follows the ADK deploy pattern:
- Triggers on push to `apps/dev-worker/**`, `helm/values/servicetsunami-dev-worker.yaml`
- Builds Docker image → pushes to GCR → deploys via Helm
- Image: `gcr.io/ai-agency-479516/servicetsunami-dev-worker`

### 9. Integrations Page — Claude Code Card

Claude Code appears on the Integrations page as a token-paste integration (like Slack/Notion, not OAuth).

**Backend — `SKILL_CREDENTIAL_SCHEMAS` in `skill_configs.py`:**
```python
"claude_code": {
    "display_name": "Claude Code",
    "description": "Autonomous coding agent — implements features, fixes bugs, creates PRs",
    "icon": "FaTerminal",
    "credentials": [
        {"key": "session_token", "label": "Session Token", "type": "password", "required": True,
         "help": "Run 'claude setup-token' in your terminal, then paste the token here"},
    ],
},
```

**Frontend — `IntegrationsPanel.js`:**
- Add `FaTerminal` to `ICON_MAP`
- Add `claude_code: '#D97706'` to `SKILL_COLORS` (amber, matches Claude branding)
- The existing credential form rendering handles the rest — user clicks the card, expands, pastes token, saves

**Token flow:**
1. User runs `claude setup-token` locally (or copies from browser session)
2. Pastes the session token in the Integrations page Claude Code card
3. Token is encrypted via `credential_vault.store_credential()` and stored in `integration_credentials` table
4. Dev worker activity fetches the token at runtime via internal API: `GET /api/v1/oauth/internal/token/claude_code?tenant_id=...`
5. Dev worker runs `claude setup-token` with the fetched token before each task

**Multi-tenant:** Each tenant provides their own Claude subscription token. The dev worker fetches the correct token per `tenant_id` at task execution time, not at pod startup.

**This changes the authentication model:**
- ~~GCP Secret Manager for `CLAUDE_SESSION_TOKEN`~~ — no longer needed
- Token comes from the integration_credentials table via the internal API
- Dev worker only needs `API_INTERNAL_KEY` to call the token endpoint
- Helm values simplified: no `servicetsunami-claude-session-token` external secret

## What Gets Removed

- `apps/adk-server/servicetsunami_supervisor/architect.py`
- `apps/adk-server/servicetsunami_supervisor/coder.py`
- `apps/adk-server/servicetsunami_supervisor/tester.py`
- `apps/adk-server/servicetsunami_supervisor/dev_ops.py`
- `apps/adk-server/servicetsunami_supervisor/user_agent.py`
- `apps/adk-server/servicetsunami_supervisor/dev_team.py`
- Imports/references from `__init__.py` and `agent.py`

## What Stays

- `tools/shell_tools.py` — still used by other agents (Luna, etc.)
- `tools/shell_tools.deploy_changes()` — could be useful for hotfixes
- All other teams unchanged

## Security Boundaries

| Component | Has Access To | Does NOT Have Access To |
|-----------|---------------|------------------------|
| Dev worker | Git repo, GitHub token, API internal key (to fetch Claude token per-task) | DB, encryption keys, OAuth tokens, customer data |
| ADK service | DB (read), all agent tools | Git push (removed with dev_ops) |
| Orchestration worker | DB, encryption keys, Gmail/Calendar tokens | Git repo, Claude Code |

## File Structure

```
apps/dev-worker/
├── Dockerfile
├── requirements.txt        # temporalio, pydantic
├── worker.py               # Temporal worker + activities
├── workflows.py            # DevTaskWorkflow definition
└── entrypoint.sh           # git clone + claude setup-token + start worker
```

## Verification

1. Deploy dev worker pod, verify it starts and connects to Temporal
2. Send a dev task via WhatsApp/chat: "Add a health check endpoint to the MCP server"
3. Verify Claude Code runs, creates branch, opens PR
4. Check PR in GitHub — should have proper commits, tests
5. Check Workflows page — DevTaskWorkflow should appear with status
6. Merge PR — verify CI/CD deploys normally
