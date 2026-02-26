# Self-Modifying Agents — Shell Access + Git Deploy

**Goal:** Give ADK agents full shell access within their container and the ability to deploy code changes (new tools, connectors, agent definitions) via git push, triggering CI/CD automatically.

**Architecture:** A new `dev_ops` sub-agent gets `execute_shell` and `deploy_changes` tools. Shell commands run via `subprocess.run()` inside the ADK container. Code changes are committed and pushed to git, triggering the existing `adk-deploy.yaml` GitHub Actions workflow. Changes take effect after the ~3 min deploy cycle.

## New Tools

### `tools/shell_tools.py`

**`execute_shell(command, working_dir, timeout)`**
- Runs any shell command via `subprocess.run(command, shell=True, capture_output=True)`
- Parameters:
  - `command` (str): The shell command to execute
  - `working_dir` (str, default="/app"): Working directory
  - `timeout` (int, default=60, max=300): Seconds before kill
- Returns: `{"stdout": str, "stderr": str, "return_code": int, "command": str}`
- No command filtering — full access within container boundaries

**`deploy_changes(commit_message, files)`**
- Stages, commits, and pushes code changes to the git repo
- Parameters:
  - `commit_message` (str): Git commit message
  - `files` (list[str], optional): Specific files to stage. If None, stages all changes.
- Returns: `{"status": "pushed", "commit_sha": str, "files_changed": list[str], "deploy_triggered": bool}`
- Pushes to `main` branch; CI/CD auto-triggers for `apps/adk-server/**` changes
- Records the deploy event as a knowledge observation for audit trail

## New Agent — `dev_ops`

Added to the supervisor as the 7th specialist sub-agent.

- **Name**: `dev_ops`
- **Model**: Same as other agents (`settings.adk_model`)
- **Purpose**: Code modifications, tool creation, dependency management, system admin
- **Tools**: `execute_shell`, `deploy_changes`, `search_knowledge`, `record_observation`
- **Instruction focus**:
  - Create new tools by writing Python async functions in `tools/`
  - Create new agents by adding files in `servicetsunami_supervisor/`
  - Install Python packages with `pip install`
  - Inspect logs, debug issues, run tests
  - Always test code locally (`python -c "import ..."`) before deploying
  - Record what was changed and why in the knowledge graph

## Container Changes

### Dockerfile
- Install `git` package (currently not in the image)
- Set git user identity at build time

### Helm Values (`servicetsunami-adk.yaml`)
- New env vars from k8s secret `adk-git-credentials`:
  - `GITHUB_TOKEN`: GitHub PAT with repo push access
  - `GIT_AUTHOR_NAME`: "ServiceTsunami Agent"
  - `GIT_AUTHOR_EMAIL`: "agent@servicetsunami.com"
- Init container or entrypoint script to configure git remote with token

### Git Configuration (entrypoint)
```bash
git config --global user.name "$GIT_AUTHOR_NAME"
git config --global user.email "$GIT_AUTHOR_EMAIL"
git remote set-url origin https://x-access-token:${GITHUB_TOKEN}@github.com/nomad3/servicetsunami-agents.git
```

## Supervisor Routing Update

Add to `agent.py` supervisor instruction:
- Code modifications, new tools, pip installs → `dev_ops`
- "Create a tool/connector/agent for X" → `dev_ops`
- Shell commands, system debugging, log inspection → `dev_ops`
- Infrastructure questions → `dev_ops`

## Security Boundaries

- Agent runs as non-root UID 1000 inside k8s pod
- Shell access is confined to the container (no host access)
- Container has 1 CPU / 1GB memory limits
- Git push is to a specific repo with a scoped PAT
- Network: only internal k8s services + github.com for push
- All changes are version-controlled (git history = full audit trail)
- Knowledge observations record what the agent changed and why

## Deploy Flow

1. User asks agent to "add a tool that fetches weather data"
2. Supervisor routes to `dev_ops`
3. `dev_ops` uses `execute_shell` to explore existing tools, understand patterns
4. `dev_ops` writes the new tool file (`tools/weather_tools.py`)
5. `dev_ops` updates the relevant agent's `tools=[]` list
6. `dev_ops` tests: `python -c "from tools.weather_tools import fetch_weather"`
7. `dev_ops` calls `deploy_changes("feat: add weather tool", ["tools/weather_tools.py", "servicetsunami_supervisor/web_researcher.py"])`
8. Git push triggers `adk-deploy.yaml` → Docker build → Helm upgrade → new pod with weather tool
9. Next ADK session has the new tool available
