# apps/code-worker

Dedicated Temporal worker that executes **CLI agent runtimes** (Claude Code, Codex, Gemini CLI, GitHub Copilot CLI) for chat turns and code tasks. Runs in its own pod so the CLI subprocesses are isolated from the API.

For full architecture see [`../../CLAUDE.md`](../../CLAUDE.md).

## What it does

1. Subscribes to the `agentprovision-code` Temporal task queue.
2. For each task: fetches the tenant's OAuth token from the API (`GET /api/v1/oauth/internal/token/{integration}`), sets it as a per-subprocess env var, spawns the chosen CLI runtime.
3. CLI runs against the project, calls MCP tools, writes to disk if needed, returns output.
4. For code tasks: commits, pushes, opens a PR via `gh` with full audit trail.
5. Heartbeats every ≤240s or Temporal cancels the activity.

Workflows: `CodeTaskWorkflow`, `ChatCliWorkflow`, `ProviderReviewWorkflow`. Defined in `workflows.py`.

## CLI runtimes

| Runtime | Auth env | Notes |
|---------|----------|-------|
| Claude Code | Native Claude Code HOME session, or `CLAUDE_CODE_OAUTH_TOKEN` in legacy print mode | `CLAUDE_CODE_EXECUTION_MODE=interactive` avoids `claude -p`; `print` keeps the JSON path |
| Codex (OpenAI) | `auth.json` written from vault | per-subprocess |
| Gemini CLI | OAuth creds + `--skip-trust` + `GEMINI_CLI_TRUST_WORKSPACE` | trusted-folders gate workaround (#209) |
| GitHub Copilot CLI | OAuth token | `--json` output mode + usage tracking (#244) |

Routing across these runs through the API's `agent_router.py` with autodetect + quota fallback (#245). The worker doesn't choose a runtime — it executes whichever the workflow tells it to.

## Run locally

The worker is started as part of the docker compose stack (`code-worker` service). To iterate on it standalone:

```bash
cd apps/code-worker
pip install -r requirements.txt
TEMPORAL_ADDRESS=localhost:7233 \
API_BASE_URL=http://localhost:8000 \
API_INTERNAL_KEY=... \
GITHUB_TOKEN=... \
python worker.py
```

## Required env

| Var | Purpose |
|-----|---------|
| `API_BASE_URL` | API service URL (default: `http://api:8000`, fixed in #234) |
| `API_INTERNAL_KEY` | to fetch tenant OAuth tokens via `/api/v1/oauth/internal/...` |
| `GITHUB_TOKEN` | git push + PR ops |
| `TEMPORAL_ADDRESS` | `temporal:7233` |
| `MCP_TOOLS_URL` | MCP SSE endpoint for tool calls |
| `CLAUDE_CODE_EXECUTION_MODE` | `print` uses the legacy `claude -p --output-format json` path. `interactive` drives a native Claude Code TTY session through a PTY for subscription-auth workers. |
| `CLAUDE_CODE_INTERACTIVE_HOME` | `tenant` uses the tenant HOME for native auth. `worker` uses `/home/codeworker` so a pre-authenticated worker Claude Code session is visible to the PTY path. |
| `CLAUDE_CODE_WORKER_HOME` | Optional override for worker native-auth HOME. Defaults to `/home/codeworker`. |

CLI tokens are **not** in the pod env — they're set per-subprocess from the tenant's vault.

## Heartbeat discipline (hard rule)

`execute_chat_cli` is a **sync** Temporal activity run in a thread pool with a background heartbeat loop. If you write a new long-running activity:

- Heartbeat every ≤240s.
- For chat CLIs, heartbeat from the activity thread itself (#223).
- Don't filter audio MIME types into the CLI media path — CLIs don't handle audio. Transcribe first.

## PR traceability

Code-task PRs include a structured body: task description, CLI output summary, full commit log, files changed, AgentProvision Code Agent attribution. **Never** add `Co-Authored-By: Claude` or any AI credit.

## Container image

Built from `Dockerfile`. Has `git`, `gh`, `claude`, `codex`, `gemini`, `copilot`, `node` on PATH. Built and deployed via CI — don't build locally.
