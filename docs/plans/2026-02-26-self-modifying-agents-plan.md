# Self-Modifying Agents Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Give ADK agents full shell access within their container and the ability to deploy code changes (new tools, connectors, agent definitions) via git push, triggering CI/CD automatically.

**Architecture:** A new `dev_ops` sub-agent gets `execute_shell` and `deploy_changes` tools. Shell commands run via `subprocess.run()` inside the ADK container. Code changes are committed and pushed to git, triggering the existing `adk-deploy.yaml` GitHub Actions workflow. Changes take effect after the ~3 min deploy cycle.

**Tech Stack:** Python 3.11, Google ADK, subprocess, git CLI, GitHub Actions, Helm/Kubernetes

**Design Doc:** `docs/plans/2026-02-26-self-modifying-agents-design.md`

---

### Task 1: Create `tools/shell_tools.py` — `execute_shell` tool

**Files:**
- Create: `apps/adk-server/tools/shell_tools.py`

**Step 1: Create the shell tools file with `execute_shell`**

```python
"""Shell tools for executing commands inside the ADK container.

Gives agents the ability to run arbitrary shell commands and deploy
code changes via git push, triggering the CI/CD pipeline.
"""
import asyncio
import logging
import os
import subprocess
from typing import Optional

logger = logging.getLogger(__name__)


async def execute_shell(
    command: str,
    working_dir: str = "/app",
    timeout: int = 60,
) -> dict:
    """Execute a shell command inside the ADK container.

    Runs any command via subprocess. Use for exploring code, installing
    packages, running tests, inspecting logs, or any system task.

    Args:
        command: The shell command to execute.
        working_dir: Working directory (default /app).
        timeout: Seconds before the command is killed (default 60, max 300).

    Returns:
        Dict with stdout, stderr, return_code, and the command that was run.
    """
    timeout = min(timeout, 300)

    logger.info("execute_shell: %s (cwd=%s, timeout=%ds)", command, working_dir, timeout)

    try:
        result = await asyncio.to_thread(
            subprocess.run,
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=working_dir,
        )
        stdout = result.stdout[-10000:] if len(result.stdout) > 10000 else result.stdout
        stderr = result.stderr[-5000:] if len(result.stderr) > 5000 else result.stderr

        return {
            "stdout": stdout,
            "stderr": stderr,
            "return_code": result.returncode,
            "command": command,
        }
    except subprocess.TimeoutExpired:
        return {
            "stdout": "",
            "stderr": f"Command timed out after {timeout}s",
            "return_code": -1,
            "command": command,
        }
    except Exception as e:
        return {
            "stdout": "",
            "stderr": str(e),
            "return_code": -1,
            "command": command,
        }
```

**Step 2: Verify the file is syntactically valid**

Run: `cd /Users/nomade/Documents/GitHub/servicetsunami-agents/apps/adk-server && python -c "from tools.shell_tools import execute_shell; print('OK')"`
Expected: `OK`

**Step 3: Commit**

```bash
git add apps/adk-server/tools/shell_tools.py
git commit -m "feat: add execute_shell tool for ADK agents"
```

---

### Task 2: Add `deploy_changes` to `tools/shell_tools.py`

**Files:**
- Modify: `apps/adk-server/tools/shell_tools.py`

**Step 1: Append the `deploy_changes` function**

Add after `execute_shell`:

```python
async def deploy_changes(
    commit_message: str,
    files: Optional[list] = None,
) -> dict:
    """Stage, commit, and push code changes to trigger a CI/CD deploy.

    Pushes to the main branch. The GitHub Actions workflow
    `adk-deploy.yaml` auto-triggers for changes under `apps/adk-server/`.
    Deploy takes ~3 minutes. The new code takes effect on the next pod restart.

    Args:
        commit_message: Git commit message describing the change.
        files: Specific files to stage. If None, stages all changes (`git add -A`).

    Returns:
        Dict with status, commit_sha, files_changed, and deploy_triggered flag.
    """
    cwd = "/app"
    logger.info("deploy_changes: %s (files=%s)", commit_message, files)

    try:
        # Stage files
        if files:
            for f in files:
                stage = subprocess.run(
                    ["git", "add", f], capture_output=True, text=True, cwd=cwd
                )
                if stage.returncode != 0:
                    return {"status": "error", "error": f"git add {f} failed: {stage.stderr}"}
        else:
            stage = subprocess.run(
                ["git", "add", "-A"], capture_output=True, text=True, cwd=cwd
            )
            if stage.returncode != 0:
                return {"status": "error", "error": f"git add -A failed: {stage.stderr}"}

        # Check if there are staged changes
        diff = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            capture_output=True, text=True, cwd=cwd,
        )
        changed_files = [f for f in diff.stdout.strip().split("\n") if f]
        if not changed_files:
            return {"status": "no_changes", "error": "No staged changes to commit"}

        # Commit
        commit = subprocess.run(
            ["git", "commit", "-m", commit_message],
            capture_output=True, text=True, cwd=cwd,
        )
        if commit.returncode != 0:
            return {"status": "error", "error": f"git commit failed: {commit.stderr}"}

        # Extract commit SHA
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, cwd=cwd,
        )
        commit_sha = sha.stdout.strip()

        # Push
        push = subprocess.run(
            ["git", "push", "origin", "main"],
            capture_output=True, text=True, cwd=cwd,
            timeout=60,
        )
        if push.returncode != 0:
            return {
                "status": "error",
                "error": f"git push failed: {push.stderr}",
                "commit_sha": commit_sha,
            }

        # Check if changes will trigger ADK deploy
        adk_paths = any(
            f.startswith("apps/adk-server/") or f.startswith("helm/values/servicetsunami-adk")
            for f in changed_files
        )

        return {
            "status": "pushed",
            "commit_sha": commit_sha,
            "files_changed": changed_files,
            "deploy_triggered": adk_paths,
        }
    except subprocess.TimeoutExpired:
        return {"status": "error", "error": "git push timed out after 60s"}
    except Exception as e:
        logger.exception("deploy_changes failed")
        return {"status": "error", "error": str(e)}
```

**Step 2: Verify both functions import correctly**

Run: `cd /Users/nomade/Documents/GitHub/servicetsunami-agents/apps/adk-server && python -c "from tools.shell_tools import execute_shell, deploy_changes; print('OK')"`
Expected: `OK`

**Step 3: Commit**

```bash
git add apps/adk-server/tools/shell_tools.py
git commit -m "feat: add deploy_changes tool for git push deploys"
```

---

### Task 3: Create `dev_ops` sub-agent

**Files:**
- Create: `apps/adk-server/servicetsunami_supervisor/dev_ops.py`

**Step 1: Create the dev_ops agent file**

```python
"""DevOps specialist agent.

Handles code modifications, tool creation, dependency management,
and system administration within the ADK container:
- Execute shell commands (explore, debug, test, install)
- Create new tools, agents, and connectors
- Deploy changes via git push (triggers CI/CD)
- Inspect logs and diagnose issues
"""
from google.adk.agents import Agent

from tools.shell_tools import execute_shell, deploy_changes
from tools.knowledge_tools import search_knowledge, record_observation
from config.settings import settings

dev_ops = Agent(
    name="dev_ops",
    model=settings.adk_model,
    instruction="""You are a DevOps and development specialist. You have full shell access inside the ADK container and can deploy code changes via git push.

IMPORTANT: For the tenant_id parameter in knowledge tools, use the value from the session state.
If you cannot access the session state, use "auto" as tenant_id and the system will resolve it.

Your capabilities:
- Execute any shell command inside the container (explore code, install packages, run tests, inspect logs)
- Create new tools by writing Python async functions in the tools/ directory
- Create new agents by adding files in servicetsunami_supervisor/
- Deploy changes by committing and pushing to git (triggers CI/CD, ~3 min deploy)
- Record what you changed and why in the knowledge graph

## Working directory layout (/app):
- tools/ — Tool modules (async Python functions). Each tool file exports functions used by agents.
- servicetsunami_supervisor/ — Agent definitions. Each file exports an Agent instance.
- servicetsunami_supervisor/agent.py — Root supervisor with sub_agents list and routing instructions.
- servicetsunami_supervisor/__init__.py — Exports all agents.
- config/settings.py — Environment configuration via pydantic-settings.
- server.py — FastAPI wrapper for ADK.
- requirements.txt — Python dependencies.

## How to create a new tool:

1. Explore existing tools for patterns:
   execute_shell("cat tools/connector_tools.py")

2. Write the new tool file:
   execute_shell("cat > tools/my_tool.py << 'PYEOF'\nasync def my_function(param: str) -> dict:\n    ...\nPYEOF")

3. Test it locally:
   execute_shell("python -c \\"from tools.my_tool import my_function; print('OK')\\"")

4. Add the tool to the relevant agent's tools=[] list in servicetsunami_supervisor/

5. Deploy:
   deploy_changes("feat: add my_tool", ["tools/my_tool.py", "servicetsunami_supervisor/some_agent.py"])

## How to create a new agent:

1. Write the agent file in servicetsunami_supervisor/:
   execute_shell("cat > servicetsunami_supervisor/my_agent.py << 'PYEOF'\nfrom google.adk.agents import Agent\nfrom config.settings import settings\n\nmy_agent = Agent(name='my_agent', model=settings.adk_model, instruction='...', tools=[...])\nPYEOF")

2. Add import + export in __init__.py
3. Add import + sub_agents entry in agent.py
4. Add routing rules to the supervisor instruction in agent.py
5. Test: execute_shell("python -c \\"from servicetsunami_supervisor import root_agent; print(root_agent.sub_agents)\\"")
6. Deploy all changed files

## How to install a Python package:

1. Install: execute_shell("pip install some-package")
2. Freeze: execute_shell("pip freeze | grep some-package >> requirements.txt")
3. Test import: execute_shell("python -c \\"import some_package; print('OK')\\"")
4. Deploy: deploy_changes("feat: add some-package dependency", ["requirements.txt"])

## Important rules:

- ALWAYS test code locally before deploying (python -c "import ...")
- ALWAYS record what you changed and why using record_observation
- Use descriptive commit messages starting with feat:, fix:, or refactor:
- After deploy_changes, tell the user the deploy will take ~3 minutes
- The deploy only affects files under apps/adk-server/ — changes outside this path won't trigger the ADK workflow
- You run as non-root (UID 1000), you cannot modify system files outside /app
- Be careful with pip install — it persists only until the next deploy rebuilds the image. Add packages to requirements.txt to make them permanent.
""",
    tools=[
        execute_shell,
        deploy_changes,
        search_knowledge,
        record_observation,
    ],
)
```

**Step 2: Verify the agent imports correctly**

Run: `cd /Users/nomade/Documents/GitHub/servicetsunami-agents/apps/adk-server && python -c "from servicetsunami_supervisor.dev_ops import dev_ops; print(dev_ops.name)"`
Expected: `dev_ops`

**Step 3: Commit**

```bash
git add apps/adk-server/servicetsunami_supervisor/dev_ops.py
git commit -m "feat: add dev_ops sub-agent with shell and deploy tools"
```

---

### Task 4: Wire `dev_ops` into supervisor

**Files:**
- Modify: `apps/adk-server/servicetsunami_supervisor/__init__.py:1-16`
- Modify: `apps/adk-server/servicetsunami_supervisor/agent.py:1-77`

**Step 1: Add dev_ops to `__init__.py`**

Add import on line 7 (before the agent.py import) and add to `__all__`:

```python
"""Agent definitions for ServiceTsunami ADK server."""
from .data_analyst import data_analyst
from .report_generator import report_generator
from .knowledge_manager import knowledge_manager
from .customer_support import customer_support
from .sales_agent import sales_agent
from .dev_ops import dev_ops
from .agent import root_agent

__all__ = [
    "root_agent",
    "data_analyst",
    "report_generator",
    "knowledge_manager",
    "customer_support",
    "sales_agent",
    "dev_ops",
]
```

**Step 2: Add dev_ops to supervisor in `agent.py`**

Add the import line after line 13 (after `from .sales_agent import sales_agent`):

```python
from .dev_ops import dev_ops
```

Add to the supervisor instruction (inside the specialist agents list, after sales_agent description):

```
- dev_ops: For code modifications, creating new tools/agents/connectors, shell commands, pip installs, system debugging, log inspection, and deploying code changes
```

Add routing rules (after the sales_agent routing rules):

```
- Code modifications, new tools, pip installs -> transfer to dev_ops
- "Create a tool/connector/agent for X" -> transfer to dev_ops
- Shell commands, system debugging, log inspection -> transfer to dev_ops
- Infrastructure questions, deployment status -> transfer to dev_ops
```

Add `dev_ops` to the `sub_agents` list on line 76:

```python
sub_agents=[data_analyst, report_generator, knowledge_manager, web_researcher, customer_support, sales_agent, dev_ops],
```

**Step 3: Verify the full agent tree loads**

Run: `cd /Users/nomade/Documents/GitHub/servicetsunami-agents/apps/adk-server && python -c "from servicetsunami_supervisor import root_agent; print([a.name for a in root_agent.sub_agents])"`
Expected: `['data_analyst', 'report_generator', 'knowledge_manager', 'web_researcher', 'customer_support', 'sales_agent', 'dev_ops']`

**Step 4: Commit**

```bash
git add apps/adk-server/servicetsunami_supervisor/__init__.py apps/adk-server/servicetsunami_supervisor/agent.py
git commit -m "feat: wire dev_ops agent into supervisor routing"
```

---

### Task 5: Update Dockerfile to install git

**Files:**
- Modify: `apps/adk-server/Dockerfile:6-9`

**Step 1: Add git to system dependencies**

Change the apt-get install line to include `git`:

```dockerfile
# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    git \
    && rm -rf /var/lib/apt/lists/*
```

**Step 2: Add git config and entrypoint script**

After the `COPY . .` line (line 16) and before the `useradd` line, add:

```dockerfile
# Git entrypoint script for configuring git identity at runtime
COPY entrypoint.sh /app/entrypoint.sh
```

Change the `CMD` on line 26 to use the entrypoint:

```dockerfile
CMD ["/bin/bash", "/app/entrypoint.sh"]
```

Full Dockerfile should be:

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    git \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Git entrypoint script for configuring git identity at runtime
COPY entrypoint.sh /app/entrypoint.sh

# Create non-root user
RUN useradd -m -u 1000 adk && chown -R adk:adk /app
USER adk

# Expose ADK API server port
EXPOSE 8080

# Run entrypoint which configures git then starts server
CMD ["/bin/bash", "/app/entrypoint.sh"]
```

**Step 3: Create `entrypoint.sh`**

Create `apps/adk-server/entrypoint.sh`:

```bash
#!/bin/bash
set -e

# Configure git identity if env vars are present
if [ -n "$GIT_AUTHOR_NAME" ]; then
    git config --global user.name "$GIT_AUTHOR_NAME"
fi
if [ -n "$GIT_AUTHOR_EMAIL" ]; then
    git config --global user.email "$GIT_AUTHOR_EMAIL"
fi

# Configure git remote with token for push access
if [ -n "$GITHUB_TOKEN" ]; then
    git config --global url."https://x-access-token:${GITHUB_TOKEN}@github.com/".insteadOf "https://github.com/"
    # Mark /app as safe directory (owned by different user during build)
    git config --global --add safe.directory /app
fi

# Start ADK server
exec python server.py
```

**Step 4: Commit**

```bash
git add apps/adk-server/Dockerfile apps/adk-server/entrypoint.sh
git commit -m "feat: add git to Dockerfile and entrypoint for dev_ops agent"
```

---

### Task 6: Add git credentials to Helm values

**Files:**
- Modify: `helm/values/servicetsunami-adk.yaml:83-118`

**Step 1: Add git env vars to configMap**

Add to the `configMap.data` section (after line 96):

```yaml
    GIT_AUTHOR_NAME: "ServiceTsunami Agent"
    GIT_AUTHOR_EMAIL: "agent@servicetsunami.com"
```

**Step 2: Add GITHUB_TOKEN to externalSecret**

Add a new entry to `externalSecret.data` (after the MCP_API_KEY entry, line 118):

```yaml
    - secretKey: GITHUB_TOKEN
      remoteRef:
        key: servicetsunami-github-token
```

**Step 3: Commit**

```bash
git add helm/values/servicetsunami-adk.yaml
git commit -m "feat: add git credentials to ADK Helm values"
```

---

### Task 7: Create GCP Secret Manager entry for GitHub token

**Files:** None (infrastructure command)

**Step 1: Create the secret in GCP Secret Manager**

This must be done manually or via `gcloud`:

```bash
# Create the secret (replace <YOUR_GITHUB_PAT> with a GitHub PAT that has repo push access)
gcloud secrets create servicetsunami-github-token --project=ai-agency-479516
echo -n "<YOUR_GITHUB_PAT>" | gcloud secrets versions add servicetsunami-github-token --data-file=- --project=ai-agency-479516
```

**Step 2: Verify the secret exists**

```bash
gcloud secrets versions access latest --secret=servicetsunami-github-token --project=ai-agency-479516
```

**Step 3: No commit needed** (this is a manual infrastructure step)

---

### Task 8: Deploy and verify

**Step 1: Push all changes to trigger the ADK deploy**

All code changes should already be committed from Tasks 1-6. Push to main:

```bash
git push origin main
```

**Step 2: Monitor the deploy**

```bash
# Watch GitHub Actions
gh run list --workflow=adk-deploy.yaml --limit=1

# Watch pod rollout
kubectl rollout status deployment/servicetsunami-adk -n prod
```

**Step 3: Verify dev_ops agent is available**

Once the new pod is running, test via the ADK API:

```bash
# Check agents list
kubectl exec -n prod deploy/servicetsunami-adk -c servicetsunami-adk -- python -c "from servicetsunami_supervisor import root_agent; print([a.name for a in root_agent.sub_agents])"
```

Expected: list includes `dev_ops`

**Step 4: Verify git is configured**

```bash
kubectl exec -n prod deploy/servicetsunami-adk -c servicetsunami-adk -- git config --list
```

Expected: shows `user.name=ServiceTsunami Agent` and `user.email=agent@servicetsunami.com`

**Step 5: Commit** (no commit needed — this is verification only)

---

## Summary

| Task | What | Files |
|------|------|-------|
| 1 | `execute_shell` tool | `tools/shell_tools.py` (create) |
| 2 | `deploy_changes` tool | `tools/shell_tools.py` (modify) |
| 3 | `dev_ops` agent | `servicetsunami_supervisor/dev_ops.py` (create) |
| 4 | Wire into supervisor | `__init__.py` + `agent.py` (modify) |
| 5 | Dockerfile + entrypoint | `Dockerfile` + `entrypoint.sh` (modify/create) |
| 6 | Helm git credentials | `servicetsunami-adk.yaml` (modify) |
| 7 | GCP secret for GitHub PAT | Manual `gcloud` command |
| 8 | Deploy and verify | Push + kubectl verification |
