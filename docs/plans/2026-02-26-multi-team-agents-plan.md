# Multi-Team Agent Hierarchy Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Restructure ADK agents from a flat 6-agent model into a team-based hierarchy with 4 specialized teams, a personal assistant co-pilot, and self-modifying dev capabilities via shell access and git deploy.

**Architecture:** Root supervisor routes to 5 top-level entities (personal_assistant, dev_team, data_team, sales_team, marketing_team). Each team has a sub-supervisor wrapping existing agents. The dev_team adds 5 new agents with a strict development cycle. Shell tools enable code modification and git-based deployment. Container changes add git support.

**Tech Stack:** Python 3.11, Google ADK (Agent framework), subprocess, git CLI, Helm/Kubernetes, GitHub Actions

**Design Doc:** `docs/plans/2026-02-26-self-modifying-agents-design.md`

---

### Task 1: Create `tools/shell_tools.py` with `execute_shell` and `deploy_changes`

**Files:**
- Create: `apps/adk-server/tools/shell_tools.py`

**Step 1: Create the shell tools module**

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


async def deploy_changes(
    commit_message: str,
    files: Optional[list] = None,
) -> dict:
    """Stage, commit, and push code changes to trigger a CI/CD deploy.

    Pushes to the main branch. The GitHub Actions workflow
    adk-deploy.yaml auto-triggers for changes under apps/adk-server/.
    Deploy takes ~3 minutes. The new code takes effect on the next pod restart.

    Args:
        commit_message: Git commit message describing the change.
        files: Specific files to stage. If None, stages all changes (git add -A).

    Returns:
        Dict with status, commit_sha, files_changed, and deploy_triggered flag.
    """
    cwd = "/app"
    logger.info("deploy_changes: %s (files=%s)", commit_message, files)

    try:
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

        diff = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            capture_output=True, text=True, cwd=cwd,
        )
        changed_files = [f for f in diff.stdout.strip().split("\n") if f]
        if not changed_files:
            return {"status": "no_changes", "error": "No staged changes to commit"}

        commit = subprocess.run(
            ["git", "commit", "-m", commit_message],
            capture_output=True, text=True, cwd=cwd,
        )
        if commit.returncode != 0:
            return {"status": "error", "error": f"git commit failed: {commit.stderr}"}

        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, cwd=cwd,
        )
        commit_sha = sha.stdout.strip()

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

**Step 2: Verify syntax**

Run: `cd /Users/nomade/Documents/GitHub/servicetsunami-agents/apps/adk-server && python -c "from tools.shell_tools import execute_shell, deploy_changes; print('OK')"`
Expected: `OK`

**Step 3: Commit**

```bash
git add apps/adk-server/tools/shell_tools.py
git commit -m "feat: add shell_tools with execute_shell and deploy_changes"
```

---

### Task 2: Create `architect` agent

**Files:**
- Create: `apps/adk-server/servicetsunami_supervisor/architect.py`

**Step 1: Create the architect agent**

```python
"""Architect agent for the dev team.

Explores the codebase, designs solutions, and writes specs for the coder
to implement. Does NOT write implementation code — only specs.
"""
from google.adk.agents import Agent

from tools.shell_tools import execute_shell
from tools.knowledge_tools import search_knowledge, record_observation
from config.settings import settings

architect = Agent(
    name="architect",
    model=settings.adk_model,
    instruction="""You are the architect in a development team. Your job is to explore the existing codebase, understand patterns, and design solutions.

IMPORTANT: For the tenant_id parameter in knowledge tools, use the value from the session state.
If you cannot access the session state, use "auto" as tenant_id and the system will resolve it.

## Your role in the dev cycle:
You are step 1 of 5: architect -> coder -> tester -> dev_ops -> user_agent

## What you do:
1. Explore the codebase using execute_shell with read-only commands: ls, cat, grep, find, head, wc
2. Understand existing patterns (how tools are structured, how agents are defined, how imports work)
3. Design the solution: which files to create/modify, what code to write, what the interface looks like
4. Write a clear spec that the coder can follow
5. Record your spec as an observation so it persists in the knowledge graph

## What you do NOT do:
- Do NOT write files or create code (that's the coder's job)
- Do NOT run tests (that's the tester's job)
- Do NOT deploy anything (that's dev_ops's job)

## Spec format:
Your spec should include:
- Goal: What we're building and why
- Files to create/modify: Exact paths
- Code: Complete implementation (the coder will write it to disk)
- Imports: What needs to be imported where
- Wiring: How to connect the new code to existing code (agent tools lists, supervisor routing, etc.)
- Verification: How to test it works (import check, pytest command)

## Codebase layout (/app):
- tools/ — Tool modules. Each exports async Python functions used by agents.
- servicetsunami_supervisor/ — Agent definitions. Each file exports an Agent instance.
- servicetsunami_supervisor/agent.py — Root supervisor with sub_agents list.
- servicetsunami_supervisor/__init__.py — Exports all agents.
- config/settings.py — Environment configuration (pydantic-settings).
- server.py — FastAPI wrapper for ADK.

## Useful exploration commands:
- execute_shell("ls tools/") — list tool modules
- execute_shell("cat tools/connector_tools.py") — read a tool file
- execute_shell("grep -r 'async def' tools/") — find all tool functions
- execute_shell("cat servicetsunami_supervisor/agent.py") — read supervisor
- execute_shell("find . -name '*.py' | head -30") — find Python files

After completing your spec, say "Spec complete. Handing off to coder." so the dev_team supervisor knows to transfer to the next agent.
""",
    tools=[
        execute_shell,
        search_knowledge,
        record_observation,
    ],
)
```

**Step 2: Verify import**

Run: `cd /Users/nomade/Documents/GitHub/servicetsunami-agents/apps/adk-server && python -c "from servicetsunami_supervisor.architect import architect; print(architect.name)"`
Expected: `architect`

**Step 3: Commit**

```bash
git add apps/adk-server/servicetsunami_supervisor/architect.py
git commit -m "feat: add architect agent for dev team"
```

---

### Task 3: Create `coder` agent

**Files:**
- Create: `apps/adk-server/servicetsunami_supervisor/coder.py`

**Step 1: Create the coder agent**

```python
"""Coder agent for the dev team.

Implements code based on the architect's spec. Writes files, installs
dependencies, and verifies imports. Does NOT deploy.
"""
from google.adk.agents import Agent

from tools.shell_tools import execute_shell
from tools.knowledge_tools import search_knowledge, record_observation
from config.settings import settings

coder = Agent(
    name="coder",
    model=settings.adk_model,
    instruction="""You are the coder in a development team. Your job is to implement code based on the architect's spec.

IMPORTANT: For the tenant_id parameter in knowledge tools, use the value from the session state.
If you cannot access the session state, use "auto" as tenant_id and the system will resolve it.

## Your role in the dev cycle:
You are step 2 of 5: architect -> coder -> tester -> dev_ops -> user_agent

## What you do:
1. Read the architect's spec from the conversation context
2. Write implementation files using execute_shell with heredoc: execute_shell("cat > path/file.py << 'PYEOF'\\n...code...\\nPYEOF")
3. Install any needed Python packages: execute_shell("pip install package-name")
4. Add new packages to requirements.txt: execute_shell("echo 'package-name>=1.0' >> requirements.txt")
5. Verify imports work: execute_shell("python -c \\"from module import func; print('OK')\\"")
6. Record what you implemented as an observation

## What you do NOT do:
- Do NOT design the solution (architect already did that)
- Do NOT write test files (that's the tester's job)
- Do NOT deploy or git push (that's dev_ops's job)

## Writing files with heredoc:
```
execute_shell("cat > tools/my_tool.py << 'PYEOF'\\nimport logging\\n\\nasync def my_func():\\n    return {}\\nPYEOF")
```
IMPORTANT: Use 'PYEOF' (single-quoted) to prevent shell variable expansion.

## Modifying existing files:
For appending: execute_shell("cat >> file.py << 'PYEOF'\\nnew code\\nPYEOF")
For inserting at specific line: execute_shell("sed -i 'Ni\\\\new line' file.py") where N is line number
For replacing: execute_shell("sed -i 's/old/new/g' file.py")
For complex edits, read the file first, then write the whole file.

## Verification:
Always verify your code compiles: execute_shell("python -c \\"import py_compile; py_compile.compile('path/file.py', doraise=True)\\"")
Always verify imports: execute_shell("python -c \\"from module import func; print('OK')\\"")

After completing implementation, say "Implementation complete. Handing off to tester." so the dev_team supervisor transfers to the next agent.
""",
    tools=[
        execute_shell,
        search_knowledge,
        record_observation,
    ],
)
```

**Step 2: Verify import**

Run: `cd /Users/nomade/Documents/GitHub/servicetsunami-agents/apps/adk-server && python -c "from servicetsunami_supervisor.coder import coder; print(coder.name)"`
Expected: `coder`

**Step 3: Commit**

```bash
git add apps/adk-server/servicetsunami_supervisor/coder.py
git commit -m "feat: add coder agent for dev team"
```

---

### Task 4: Create `tester` agent

**Files:**
- Create: `apps/adk-server/servicetsunami_supervisor/tester.py`

**Step 1: Create the tester agent**

```python
"""Tester agent for the dev team.

Writes and runs tests against new code. Reports pass/fail results.
Can fix test files but not implementation files.
"""
from google.adk.agents import Agent

from tools.shell_tools import execute_shell
from tools.knowledge_tools import search_knowledge, record_observation
from config.settings import settings

tester = Agent(
    name="tester",
    model=settings.adk_model,
    instruction="""You are the tester in a development team. Your job is to write tests, run them, and report results.

IMPORTANT: For the tenant_id parameter in knowledge tools, use the value from the session state.
If you cannot access the session state, use "auto" as tenant_id and the system will resolve it.

## Your role in the dev cycle:
You are step 3 of 5: architect -> coder -> tester -> dev_ops -> user_agent

## What you do:
1. Read what the coder implemented from conversation context
2. Write test files using execute_shell with heredoc
3. Run tests: execute_shell("python -m pytest tests/test_file.py -v")
4. Report results clearly: which tests passed, which failed, and why
5. If tests fail due to test bugs (not implementation bugs), fix the test and re-run
6. Record test results as an observation

## What you do NOT do:
- Do NOT modify implementation files (only test files)
- Do NOT deploy anything (that's dev_ops's job)
- If implementation has bugs, report them clearly and let the dev_team supervisor decide next steps

## Test file conventions:
- Test files go in the project root or a tests/ directory
- Name: test_<module>.py
- Use pytest with plain assert statements
- For async functions, use pytest-asyncio: @pytest.mark.asyncio

## Example test:
```python
import pytest

@pytest.mark.asyncio
async def test_my_function():
    from tools.my_tool import my_function
    result = await my_function("input")
    assert result["status"] == "ok"
    assert "data" in result
```

## Writing tests:
execute_shell("cat > tests/test_my_tool.py << 'PYEOF'\\nimport pytest\\n...\\nPYEOF")

## Running tests:
execute_shell("python -m pytest tests/test_my_tool.py -v")
execute_shell("python -m pytest tests/test_my_tool.py::test_specific -v")

## Quick smoke test (when full pytest is overkill):
execute_shell("python -c \\"from tools.my_tool import func; import asyncio; print(asyncio.run(func('test')))\\"")

After all tests pass, say "All tests passing. Handing off to dev_ops." so the dev_team supervisor transfers to the next agent.

If tests fail due to implementation bugs, say "Tests failing due to implementation issue: [description]. Needs coder fix." so the dev_team supervisor can route back to coder.
""",
    tools=[
        execute_shell,
        search_knowledge,
        record_observation,
    ],
)
```

**Step 2: Verify import**

Run: `cd /Users/nomade/Documents/GitHub/servicetsunami-agents/apps/adk-server && python -c "from servicetsunami_supervisor.tester import tester; print(tester.name)"`
Expected: `tester`

**Step 3: Commit**

```bash
git add apps/adk-server/servicetsunami_supervisor/tester.py
git commit -m "feat: add tester agent for dev team"
```

---

### Task 5: Create `dev_ops` agent

**Files:**
- Create: `apps/adk-server/servicetsunami_supervisor/dev_ops.py`

**Step 1: Create the dev_ops agent**

```python
"""DevOps agent for the dev team.

Deploys code changes via git commit + push. Monitors CI/CD status.
The only agent with the deploy_changes tool.
"""
from google.adk.agents import Agent

from tools.shell_tools import execute_shell, deploy_changes
from tools.knowledge_tools import search_knowledge, record_observation
from config.settings import settings

dev_ops = Agent(
    name="dev_ops",
    model=settings.adk_model,
    instruction="""You are the DevOps engineer in a development team. Your job is to deploy code changes and monitor the CI/CD pipeline.

IMPORTANT: For the tenant_id parameter in knowledge tools, use the value from the session state.
If you cannot access the session state, use "auto" as tenant_id and the system will resolve it.

## Your role in the dev cycle:
You are step 4 of 5: architect -> coder -> tester -> dev_ops -> user_agent

## What you do:
1. Review what was implemented and tested from conversation context
2. Check git status: execute_shell("git status")
3. Deploy using deploy_changes(commit_message, files) — this commits and pushes to main
4. Report the deploy result: commit SHA, files changed, whether CI/CD was triggered
5. Optionally check CI status: execute_shell("gh run list --workflow=adk-deploy.yaml --limit=1") (if gh CLI is available)
6. Record the deploy event as an observation

## What you do NOT do:
- Do NOT write implementation code (coder did that)
- Do NOT write tests (tester did that)
- Do NOT validate the deployment (user_agent does that)

## deploy_changes usage:
- Specific files: deploy_changes("feat: add weather tool", ["tools/weather_tools.py", "servicetsunami_supervisor/web_researcher.py"])
- All changes: deploy_changes("feat: restructure agent hierarchy")
- Commit messages should start with feat:, fix:, or refactor:

## Important:
- The deploy triggers GitHub Actions workflow adk-deploy.yaml
- Deploy takes ~3 minutes (Docker build + Helm upgrade)
- Only files under apps/adk-server/ or helm/values/servicetsunami-adk.yaml trigger the workflow
- Tell the user the deploy will take ~3 minutes
- You run as non-root (UID 1000) inside the container

After deploying, say "Deploy complete. Commit [SHA]. CI/CD triggered. ~3 min to propagate. Handing off to user_agent." so the dev_team supervisor transfers to the next agent.
""",
    tools=[
        execute_shell,
        deploy_changes,
        search_knowledge,
        record_observation,
    ],
)
```

**Step 2: Verify import**

Run: `cd /Users/nomade/Documents/GitHub/servicetsunami-agents/apps/adk-server && python -c "from servicetsunami_supervisor.dev_ops import dev_ops; print(dev_ops.name)"`
Expected: `dev_ops`

**Step 3: Commit**

```bash
git add apps/adk-server/servicetsunami_supervisor/dev_ops.py
git commit -m "feat: add dev_ops agent for dev team"
```

---

### Task 6: Create `user_agent`

**Files:**
- Create: `apps/adk-server/servicetsunami_supervisor/user_agent.py`

**Step 1: Create the user_agent**

```python
"""User agent for the dev team.

Smoke-tests deployed changes from a user perspective. Calls APIs,
verifies behavior, reports validation results.
"""
from google.adk.agents import Agent

from tools.shell_tools import execute_shell
from tools.knowledge_tools import search_knowledge, record_observation
from config.settings import settings

user_agent = Agent(
    name="user_agent",
    model=settings.adk_model,
    instruction="""You are the user validation agent in a development team. Your job is to smoke-test deployed changes from a user's perspective.

IMPORTANT: For the tenant_id parameter in knowledge tools, use the value from the session state.
If you cannot access the session state, use "auto" as tenant_id and the system will resolve it.

## Your role in the dev cycle:
You are step 5 of 5 (final): architect -> coder -> tester -> dev_ops -> user_agent

## What you do:
1. Read what was deployed from conversation context
2. Wait briefly if the deploy just happened: execute_shell("sleep 10")
3. Test the deployed changes using real API calls:
   - execute_shell("curl -s http://localhost:8080/list-apps | python -m json.tool")
   - execute_shell("curl -s -X POST http://localhost:8080/run -H 'Content-Type: application/json' -d '{...}'")
4. Verify the new feature/fix works end-to-end
5. Report validation results clearly: what worked, what didn't
6. Record validation results as an observation

## What you do NOT do:
- Do NOT write code or modify files
- Do NOT deploy anything
- You are a user — you only interact with the system through its public interfaces

## Testing approaches:
- API health: execute_shell("curl -s http://localhost:8080/list-apps")
- Agent availability: execute_shell("python -c \\"from servicetsunami_supervisor import root_agent; print([a.name for a in root_agent.sub_agents])\\"")
- Import verification: execute_shell("python -c \\"from tools.new_tool import new_func; print('OK')\\"")
- ADK run: execute_shell("curl -s -X POST http://localhost:8080/run -H 'Content-Type: application/json' -d '{\"app_name\": \"servicetsunami_supervisor\", \"user_id\": \"test\", \"session_id\": \"test\", \"new_message\": {\"role\": \"user\", \"parts\": [{\"text\": \"test message\"}]}}'")

## Note on timing:
If dev_ops just pushed, the changes won't be live until the CI/CD pipeline completes (~3 min).
For immediate verification, test locally: execute_shell("python -c \\"import ...\\"")
For post-deploy verification, you may need to wait or check pod status.

After validation, say "Validation complete. [summary of results]." to conclude the dev cycle.
""",
    tools=[
        execute_shell,
        search_knowledge,
        record_observation,
    ],
)
```

**Step 2: Verify import**

Run: `cd /Users/nomade/Documents/GitHub/servicetsunami-agents/apps/adk-server && python -c "from servicetsunami_supervisor.user_agent import user_agent; print(user_agent.name)"`
Expected: `user_agent`

**Step 3: Commit**

```bash
git add apps/adk-server/servicetsunami_supervisor/user_agent.py
git commit -m "feat: add user_agent for dev team validation"
```

---

### Task 7: Create `dev_team` sub-supervisor

**Files:**
- Create: `apps/adk-server/servicetsunami_supervisor/dev_team.py`

**Step 1: Create the dev_team supervisor**

```python
"""Dev Team sub-supervisor.

Orchestrates the full development cycle with a strict 5-step pipeline:
architect -> coder -> tester -> dev_ops -> user_agent
"""
from google.adk.agents import Agent

from .architect import architect
from .coder import coder
from .tester import tester
from .dev_ops import dev_ops
from .user_agent import user_agent
from config.settings import settings

dev_team = Agent(
    name="dev_team",
    model=settings.adk_model,
    instruction="""You are the Dev Team supervisor. You orchestrate a strict 5-step development cycle for all code changes to the ServiceTsunami platform.

IMPORTANT: You are a ROUTING agent only. You do NOT have tools. Your ONLY capability is to transfer tasks to your sub-agents using transfer_to_agent.

## Your team (ALWAYS execute in this exact order, no skipping):

1. **architect** — Explores codebase, designs the solution, writes a spec
2. **coder** — Implements the code based on architect's spec
3. **tester** — Writes and runs tests
4. **dev_ops** — Commits and pushes to git (triggers CI/CD deploy)
5. **user_agent** — Smoke-tests the deployed changes

## Rules:
- ALWAYS start with architect, even for "simple" changes
- ALWAYS go through ALL 5 steps in order
- NEVER skip a step
- If tester reports implementation bugs, transfer back to coder, then re-run tester
- If user_agent reports issues after deploy, start a new cycle from architect
- Each agent will say when they're done and ready to hand off

## Routing:
- Start of any dev request -> transfer to architect
- Architect says "Spec complete" -> transfer to coder
- Coder says "Implementation complete" -> transfer to tester
- Tester says "All tests passing" -> transfer to dev_ops
- Tester says "Tests failing due to implementation" -> transfer back to coder
- Dev_ops says "Deploy complete" -> transfer to user_agent
- User_agent says "Validation complete" -> report final summary to user

## Context passing:
Each agent reads the conversation history to understand what previous agents did. You don't need to summarize — just transfer and the ADK framework preserves context.

Always tell the user which step you're starting: "Step 1/5: Architect is analyzing the codebase..."
""",
    sub_agents=[architect, coder, tester, dev_ops, user_agent],
)
```

**Step 2: Verify import**

Run: `cd /Users/nomade/Documents/GitHub/servicetsunami-agents/apps/adk-server && python -c "from servicetsunami_supervisor.dev_team import dev_team; print(dev_team.name, [a.name for a in dev_team.sub_agents])"`
Expected: `dev_team ['architect', 'coder', 'tester', 'dev_ops', 'user_agent']`

**Step 3: Commit**

```bash
git add apps/adk-server/servicetsunami_supervisor/dev_team.py
git commit -m "feat: add dev_team sub-supervisor with strict 5-step cycle"
```

---

### Task 8: Create `data_team` sub-supervisor

**Files:**
- Create: `apps/adk-server/servicetsunami_supervisor/data_team.py`

**Step 1: Create the data_team supervisor**

```python
"""Data Team sub-supervisor.

Routes data analytics and reporting requests to the appropriate specialist.
"""
from google.adk.agents import Agent

from .data_analyst import data_analyst
from .report_generator import report_generator
from config.settings import settings

data_team = Agent(
    name="data_team",
    model=settings.adk_model,
    instruction="""You are the Data Team supervisor. You route data-related requests to the appropriate specialist.

IMPORTANT: You are a ROUTING agent only. You do NOT have tools. Your ONLY capability is to transfer tasks to your sub-agents using transfer_to_agent.

## Your team:
- **data_analyst** — SQL queries, statistical analysis, dataset discovery, natural language to SQL, insights generation
- **report_generator** — Formatted reports, chart/visualization specifications, data exports

## Routing:
- Data queries, SQL, analytics, statistics, dataset exploration, insights -> transfer to data_analyst
- Reports, charts, visualizations, formatted outputs, data exports -> transfer to report_generator
- Complex requests (analyze + visualize) -> transfer to data_analyst first, then report_generator
- "Show me the data on X" -> data_analyst
- "Create a report about X" -> report_generator
- "Analyze X and make a chart" -> data_analyst first, then report_generator

Always explain which specialist you're routing to and why.
""",
    sub_agents=[data_analyst, report_generator],
)
```

**Step 2: Verify import**

Run: `cd /Users/nomade/Documents/GitHub/servicetsunami-agents/apps/adk-server && python -c "from servicetsunami_supervisor.data_team import data_team; print(data_team.name, [a.name for a in data_team.sub_agents])"`
Expected: `data_team ['data_analyst', 'report_generator']`

**Step 3: Commit**

```bash
git add apps/adk-server/servicetsunami_supervisor/data_team.py
git commit -m "feat: add data_team sub-supervisor"
```

---

### Task 9: Create `sales_team` sub-supervisor

**Files:**
- Create: `apps/adk-server/servicetsunami_supervisor/sales_team.py`

**Step 1: Create the sales_team supervisor**

```python
"""Sales Team sub-supervisor.

Routes sales and customer support requests to the appropriate specialist.
"""
from google.adk.agents import Agent

from .sales_agent import sales_agent
from .customer_support import customer_support
from config.settings import settings

sales_team = Agent(
    name="sales_team",
    model=settings.adk_model,
    instruction="""You are the Sales Team supervisor. You route sales and customer-facing requests to the appropriate specialist.

IMPORTANT: You are a ROUTING agent only. You do NOT have tools. Your ONLY capability is to transfer tasks to your sub-agents using transfer_to_agent.

## Your team:
- **sales_agent** — Lead qualification (BANT), outreach drafting, pipeline management, proposals, follow-up scheduling, B2B sales
- **customer_support** — FAQ, product inquiries, order status, complaints, general conversation, greetings

## Routing:
- Lead qualification, BANT analysis, outreach drafting -> transfer to sales_agent
- Pipeline management, stage updates, pipeline summary -> transfer to sales_agent
- Proposal generation, sales automation -> transfer to sales_agent
- Customer inquiries, FAQ, product info -> transfer to customer_support
- Order status, account lookups -> transfer to customer_support
- Complaints, feedback -> transfer to customer_support
- Greetings, casual conversation, general chat -> transfer to customer_support
- If unclear whether support or sales -> default to customer_support

## PharmApp / Remedia routing:
- Medication search ("buscar", "necesito", drug names) -> customer_support
- Price comparison ("precio", "mas barato", "comparar") -> customer_support
- Order status ("orden", "pedido", "mi compra") -> customer_support
- Pharmacy info ("farmacia", "cerca", "horario") -> customer_support
- Adherence/refill ("recarga", "adherencia", "recordatorio") -> customer_support
- Pharmacy partnerships, B2B sales, outreach campaigns -> sales_agent
- Retention campaigns, price alert setup, re-engagement -> sales_agent
- Spanish greetings ("hola", "buenos dias") -> customer_support

Always explain which specialist you're routing to and why.
""",
    sub_agents=[sales_agent, customer_support],
)
```

**Step 2: Verify import**

Run: `cd /Users/nomade/Documents/GitHub/servicetsunami-agents/apps/adk-server && python -c "from servicetsunami_supervisor.sales_team import sales_team; print(sales_team.name, [a.name for a in sales_team.sub_agents])"`
Expected: `sales_team ['sales_agent', 'customer_support']`

**Step 3: Commit**

```bash
git add apps/adk-server/servicetsunami_supervisor/sales_team.py
git commit -m "feat: add sales_team sub-supervisor"
```

---

### Task 10: Create `marketing_team` sub-supervisor

**Files:**
- Create: `apps/adk-server/servicetsunami_supervisor/marketing_team.py`

**Step 1: Create the marketing_team supervisor**

```python
"""Marketing Team sub-supervisor.

Routes research and knowledge management requests to the appropriate specialist.
"""
from google.adk.agents import Agent

from .web_researcher import web_researcher
from .knowledge_manager import knowledge_manager
from config.settings import settings

marketing_team = Agent(
    name="marketing_team",
    model=settings.adk_model,
    instruction="""You are the Marketing Team supervisor. You route research and knowledge management requests to the appropriate specialist.

IMPORTANT: You are a ROUTING agent only. You do NOT have tools. Your ONLY capability is to transfer tasks to your sub-agents using transfer_to_agent.

## Your team:
- **web_researcher** — Web scraping, internet search, lead generation, market intelligence, structured data extraction
- **knowledge_manager** — Entity CRUD, knowledge graph, relationships, lead scoring, semantic search, memory management

## Routing:
- Web research, scraping, internet search, market intelligence -> transfer to web_researcher
- Lead generation, finding companies/contacts online -> transfer to web_researcher
- Storing entities, updating records, entity CRUD -> transfer to knowledge_manager
- Lead scoring (ai_lead, hca_deal, marketing_signal rubrics) -> transfer to knowledge_manager
- Knowledge graph queries, semantic search, entity relationships -> transfer to knowledge_manager
- Research + store results -> transfer to web_researcher first, then knowledge_manager
- "Find companies that do X" -> web_researcher
- "Score this lead" -> knowledge_manager
- "Research X and save what you find" -> web_researcher first, then knowledge_manager

## Entity categories in knowledge graph:
- lead: Companies that might buy products/services
- contact: Decision makers at companies
- investor: VCs, angels, funding sources
- accelerator: Programs, incubators
- organization: Generic companies
- person: Generic people

Always explain which specialist you're routing to and why.
""",
    sub_agents=[web_researcher, knowledge_manager],
)
```

**Step 2: Verify import**

Run: `cd /Users/nomade/Documents/GitHub/servicetsunami-agents/apps/adk-server && python -c "from servicetsunami_supervisor.marketing_team import marketing_team; print(marketing_team.name, [a.name for a in marketing_team.sub_agents])"`
Expected: `marketing_team ['web_researcher', 'knowledge_manager']`

**Step 3: Commit**

```bash
git add apps/adk-server/servicetsunami_supervisor/marketing_team.py
git commit -m "feat: add marketing_team sub-supervisor"
```

---

### Task 11: Create `personal_assistant` agent (Luna)

**Files:**
- Create: `apps/adk-server/servicetsunami_supervisor/personal_assistant.py`

**Step 1: Create Luna**

```python
"""Personal Assistant agent — Luna.

WhatsApp-native business co-pilot. Manages reminders, daily briefings,
task management, and orchestrates the agent teams on behalf of the user.
"""
from google.adk.agents import Agent

from tools.shell_tools import execute_shell
from tools.knowledge_tools import (
    search_knowledge,
    find_entities,
    create_entity,
    update_entity,
    record_observation,
)
from tools.connector_tools import query_data_source
from tools.sales_tools import schedule_followup
from config.settings import settings

personal_assistant = Agent(
    name="personal_assistant",
    model=settings.adk_model,
    instruction="""You are Luna, a proactive and empowered business co-pilot. You're the user's senior chief of staff — warm, confident, and always one step ahead.

IMPORTANT: For the tenant_id parameter in all tools, use the value from the session state.
If you cannot access the session state, use "auto" as tenant_id and the system will resolve it.

## Your personality:
- You are an empowered business woman who genuinely wants to make the user's life easier
- Warm but efficient. Confident. Not robotic.
- Use first person: "I've scheduled that for you", "I'll have the data team pull those numbers"
- Anticipate needs — if someone mentions a meeting, offer to set a reminder
- You're the friendly front door to the entire ServiceTsunami platform

## Your capabilities:

### 1. Reminders & Scheduling
- "Remind me to follow up with Acme in 3 days" -> use schedule_followup with action="send_whatsapp" and delay_hours=72
- "Set a daily standup reminder at 9am" -> use schedule_followup with appropriate delay
- For entities: create a task entity first, then schedule the follow-up linked to it

### 2. Daily Briefing
When asked for a briefing or "what's on my plate":
- search_knowledge for recent observations and pending tasks
- find_entities with category="task" for open todos
- find_entities with category="lead" for pipeline updates
- query_data_source for any connected calendar/CRM data
- Summarize everything concisely

### 3. Task Management
- "Add to my todos: review the Q1 report" -> create_entity(name="Review Q1 report", category="task", properties={"status": "pending", "created": "today"})
- "What are my open tasks?" -> find_entities(category="task") then filter for status != "done"
- "Mark X as done" -> update_entity with properties={"status": "done"}

### 4. Connector Hub
- "Check my email for invoices" -> query_data_source to search connected email/CRM
- "What's the latest from Slack?" -> query_data_source for connected Slack data
- "Pull customer data for Acme" -> query_data_source with SQL query

### 5. Team Orchestration
When the user asks something that belongs to another team, guide them:
- "I need a report on sales" -> "I'll route that to the data team for you."
- "Research competitor X" -> "Let me send that to the marketing team."
- "Add a new tool" -> "The dev team can handle that."
You don't transfer directly (that's the root supervisor's job), but you help the user understand what's possible and frame their requests.

## Response style:
- Keep WhatsApp messages short and scannable
- Use bullet points for lists
- Lead with the action, not the explanation
- Be proactive: suggest next steps, offer reminders, flag things that need attention
- Respond in the user's language (Spanish if they write in Spanish)

## Spanish greeting examples:
- "Buenos dias! Aqui tienes tu resumen del dia..."
- "Listo, te agendo un recordatorio para el viernes."
- "Tienes 3 tareas pendientes y 2 leads nuevos en el pipeline."
""",
    tools=[
        execute_shell,
        search_knowledge,
        find_entities,
        create_entity,
        update_entity,
        record_observation,
        query_data_source,
        schedule_followup,
    ],
)
```

**Step 2: Verify import**

Run: `cd /Users/nomade/Documents/GitHub/servicetsunami-agents/apps/adk-server && python -c "from servicetsunami_supervisor.personal_assistant import personal_assistant; print(personal_assistant.name)"`
Expected: `personal_assistant`

**Step 3: Commit**

```bash
git add apps/adk-server/servicetsunami_supervisor/personal_assistant.py
git commit -m "feat: add Luna personal assistant agent"
```

---

### Task 12: Rewire root supervisor and `__init__.py`

**Files:**
- Modify: `apps/adk-server/servicetsunami_supervisor/agent.py`
- Modify: `apps/adk-server/servicetsunami_supervisor/__init__.py`

**Step 1: Rewrite `agent.py` with team-based hierarchy**

Replace the entire contents of `apps/adk-server/servicetsunami_supervisor/agent.py`:

```python
"""Root agent definition for ServiceTsunami ADK server.

This is the main entry point for the ADK API server.
The root_agent coordinates team sub-supervisors for different domains.
"""
from google.adk.agents import Agent

from .personal_assistant import personal_assistant
from .dev_team import dev_team
from .data_team import data_team
from .sales_team import sales_team
from .marketing_team import marketing_team
from config.settings import settings


# Root supervisor agent - coordinates team supervisors
root_agent = Agent(
    name="servicetsunami_supervisor",
    model=settings.adk_model,
    instruction="""You are the ServiceTsunami AI supervisor — an intelligent orchestrator that routes requests to specialized teams and your personal assistant.

IMPORTANT: You are a ROUTING agent only. You do NOT have tools.
Your ONLY capability is to transfer tasks to your teams or personal assistant using transfer_to_agent. NEVER try to call tools directly.

## Your teams:

- **personal_assistant**: Luna, your business co-pilot. Handles reminders, daily briefings, task management, general orchestration, and warm conversation. This is the DEFAULT for personal or ambiguous requests.

- **dev_team**: Full development cycle (architect -> coder -> tester -> dev_ops -> user_agent). For code modifications, new tools/agents/connectors, shell commands, deployments, and infrastructure.

- **data_team**: Data analytics and reporting (data_analyst + report_generator). For SQL queries, statistical analysis, dataset exploration, reports, charts, and visualizations.

- **sales_team**: Sales and customer support (sales_agent + customer_support). For lead qualification, outreach, pipeline management, proposals, customer inquiries, FAQ, order status, and complaints.

- **marketing_team**: Research and knowledge management (web_researcher + knowledge_manager). For web scraping, internet research, lead generation, entity management, knowledge graph, and lead scoring.

## Routing guidelines:

### personal_assistant (Luna):
- Reminders, scheduling, "remind me to..."
- Daily briefing, agenda, "what's on my plate"
- Personal task management, todos
- General orchestration requests, "help me with..."
- Greetings, casual conversation, general chat
- WhatsApp messages from the owner/admin
- Ambiguous personal requests
- "Check my email/Slack/calendar"

### dev_team:
- Code modifications, new tools, pip installs
- "Create a tool/connector/agent for X"
- Shell commands, system debugging, log inspection
- Infrastructure questions, deployment status
- "Add a feature", "fix a bug", "refactor X"

### data_team:
- Data queries, SQL, analytics, statistics
- Dataset exploration, insights
- Reports, charts, visualizations
- "Show me the data on X"
- "Create a report about X"

### sales_team:
- Lead qualification, BANT analysis, outreach drafting
- Pipeline management, stage updates, pipeline summary
- Proposal generation, sales automation
- Customer inquiries, FAQ, product info, order status
- Complaints, feedback
- PharmApp / Remedia: medication search, price comparison, order status, pharmacy info

### marketing_team:
- Web research, scraping, lead generation
- Market intelligence, competitor analysis
- Entity management, knowledge graph
- Lead scoring (ai_lead, hca_deal, marketing_signal rubrics)
- "Research X", "Find companies that do Y"
- "Score this lead", "Store this entity"

## Default routing:
- If unclear -> personal_assistant (Luna handles it gracefully)
- Spanish greetings ("hola", "buenos dias") -> personal_assistant
- Always explain what you're doing before delegating
""",
    sub_agents=[personal_assistant, dev_team, data_team, sales_team, marketing_team],
)
```

**Step 2: Rewrite `__init__.py` with full exports**

Replace the entire contents of `apps/adk-server/servicetsunami_supervisor/__init__.py`:

```python
"""Agent definitions for ServiceTsunami ADK server."""
# Leaf agents
from .data_analyst import data_analyst
from .report_generator import report_generator
from .knowledge_manager import knowledge_manager
from .web_researcher import web_researcher
from .customer_support import customer_support
from .sales_agent import sales_agent
from .architect import architect
from .coder import coder
from .tester import tester
from .dev_ops import dev_ops
from .user_agent import user_agent
from .personal_assistant import personal_assistant

# Team supervisors
from .dev_team import dev_team
from .data_team import data_team
from .sales_team import sales_team
from .marketing_team import marketing_team

# Root supervisor
from .agent import root_agent

__all__ = [
    "root_agent",
    # Teams
    "dev_team",
    "data_team",
    "sales_team",
    "marketing_team",
    # Personal assistant
    "personal_assistant",
    # Leaf agents
    "data_analyst",
    "report_generator",
    "knowledge_manager",
    "web_researcher",
    "customer_support",
    "sales_agent",
    "architect",
    "coder",
    "tester",
    "dev_ops",
    "user_agent",
]
```

**Step 3: Verify the full agent tree loads**

Run: `cd /Users/nomade/Documents/GitHub/servicetsunami-agents/apps/adk-server && python -c "from servicetsunami_supervisor import root_agent; print([a.name for a in root_agent.sub_agents])"`
Expected: `['personal_assistant', 'dev_team', 'data_team', 'sales_team', 'marketing_team']`

Run: `cd /Users/nomade/Documents/GitHub/servicetsunami-agents/apps/adk-server && python -c "from servicetsunami_supervisor import dev_team; print([a.name for a in dev_team.sub_agents])"`
Expected: `['architect', 'coder', 'tester', 'dev_ops', 'user_agent']`

**Step 4: Commit**

```bash
git add apps/adk-server/servicetsunami_supervisor/agent.py apps/adk-server/servicetsunami_supervisor/__init__.py
git commit -m "feat: rewire root supervisor with team-based hierarchy"
```

---

### Task 13: Update Dockerfile and create entrypoint

**Files:**
- Modify: `apps/adk-server/Dockerfile`
- Create: `apps/adk-server/entrypoint.sh`

**Step 1: Update Dockerfile**

Replace the entire contents of `apps/adk-server/Dockerfile`:

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

# Git entrypoint script
COPY entrypoint.sh /app/entrypoint.sh

# Create non-root user
RUN useradd -m -u 1000 adk && chown -R adk:adk /app
USER adk

# Expose ADK API server port
EXPOSE 8080

# Run entrypoint which configures git then starts server
CMD ["/bin/bash", "/app/entrypoint.sh"]
```

**Step 2: Create entrypoint.sh**

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
    git config --global --add safe.directory /app
fi

# Start ADK server
exec python server.py
```

**Step 3: Make entrypoint executable**

Run: `chmod +x /Users/nomade/Documents/GitHub/servicetsunami-agents/apps/adk-server/entrypoint.sh`

**Step 4: Commit**

```bash
git add apps/adk-server/Dockerfile apps/adk-server/entrypoint.sh
git commit -m "feat: add git to Dockerfile and entrypoint for dev_ops agent"
```

---

### Task 14: Update Helm values with git credentials

**Files:**
- Modify: `helm/values/servicetsunami-adk.yaml:83-118`

**Step 1: Add git env vars to configMap**

In `helm/values/servicetsunami-adk.yaml`, add these two lines after `EMBEDDING_MODEL: "text-embedding-005"` (line 96):

```yaml
    GIT_AUTHOR_NAME: "ServiceTsunami Agent"
    GIT_AUTHOR_EMAIL: "agent@servicetsunami.com"
```

**Step 2: Add GITHUB_TOKEN to externalSecret**

In the `externalSecret.data` array, add a new entry after the MCP_API_KEY entry (after line 118):

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

### Task 15: Create GCP secret and deploy

**Files:** None (infrastructure + deployment)

**Step 1: Create the GCP Secret Manager entry**

```bash
# Create the secret (replace <YOUR_GITHUB_PAT> with a GitHub PAT with repo push access to nomad3/servicetsunami-agents)
gcloud secrets create servicetsunami-github-token --project=ai-agency-479516
echo -n "<YOUR_GITHUB_PAT>" | gcloud secrets versions add servicetsunami-github-token --data-file=- --project=ai-agency-479516
```

**Step 2: Verify the secret**

```bash
gcloud secrets versions access latest --secret=servicetsunami-github-token --project=ai-agency-479516
```

**Step 3: Push all changes**

```bash
git push origin main
```

**Step 4: Monitor deploy**

```bash
gh run list --workflow=adk-deploy.yaml --limit=1
kubectl rollout status deployment/servicetsunami-adk -n prod
```

**Step 5: Verify agent hierarchy post-deploy**

```bash
kubectl exec -n prod deploy/servicetsunami-adk -c servicetsunami-adk -- python -c "from servicetsunami_supervisor import root_agent; print([a.name for a in root_agent.sub_agents])"
```

Expected: `['personal_assistant', 'dev_team', 'data_team', 'sales_team', 'marketing_team']`

---

## Summary

| Task | What | Files |
|------|------|-------|
| 1 | Shell tools (execute_shell + deploy_changes) | `tools/shell_tools.py` (create) |
| 2 | Architect agent | `servicetsunami_supervisor/architect.py` (create) |
| 3 | Coder agent | `servicetsunami_supervisor/coder.py` (create) |
| 4 | Tester agent | `servicetsunami_supervisor/tester.py` (create) |
| 5 | DevOps agent | `servicetsunami_supervisor/dev_ops.py` (create) |
| 6 | User agent | `servicetsunami_supervisor/user_agent.py` (create) |
| 7 | Dev team sub-supervisor | `servicetsunami_supervisor/dev_team.py` (create) |
| 8 | Data team sub-supervisor | `servicetsunami_supervisor/data_team.py` (create) |
| 9 | Sales team sub-supervisor | `servicetsunami_supervisor/sales_team.py` (create) |
| 10 | Marketing team sub-supervisor | `servicetsunami_supervisor/marketing_team.py` (create) |
| 11 | Luna personal assistant | `servicetsunami_supervisor/personal_assistant.py` (create) |
| 12 | Rewire root supervisor + exports | `agent.py` + `__init__.py` (modify) |
| 13 | Dockerfile + entrypoint | `Dockerfile` + `entrypoint.sh` (modify/create) |
| 14 | Helm git credentials | `servicetsunami-adk.yaml` (modify) |
| 15 | GCP secret + deploy | Manual gcloud + push |
