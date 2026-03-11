"""Temporal workflow and activities for Claude Code tasks."""

import json
import logging
import os
import subprocess
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

import httpx
from temporalio import activity, workflow
from temporalio.common import RetryPolicy

logger = logging.getLogger(__name__)

WORKSPACE = "/workspace"
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
API_INTERNAL_KEY = os.environ.get("API_INTERNAL_KEY", "").strip()
API_BASE_URL = os.environ.get("API_BASE_URL", "http://servicetsunami-api").strip()


@dataclass
class CodeTaskInput:
    task_description: str
    tenant_id: str
    context: Optional[str] = None


@dataclass
class CodeTaskResult:
    pr_url: str
    summary: str
    branch: str
    files_changed: list[str]
    claude_output: str
    success: bool
    error: Optional[str] = None


def _run(cmd: str, cwd: str = WORKSPACE, timeout: int = 600, extra_env: dict | None = None) -> str:
    """Run a shell command and return stdout. Raises on failure."""
    logger.info("Running: %s", cmd)
    env = None
    if extra_env:
        env = {**os.environ, **extra_env}
    result = subprocess.run(
        cmd, shell=True, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env
    )
    if result.returncode != 0:
        error_detail = result.stderr or result.stdout
        logger.error("Command failed: %s\nstderr: %s\nstdout: %s", cmd, result.stderr, result.stdout[:2000])
        raise RuntimeError(f"Command failed: {cmd}\n{error_detail}")
    return result.stdout.strip()


def _fetch_claude_token(tenant_id: str) -> str:
    """Fetch the Claude Code session token from the API's internal endpoint."""
    url = f"{API_BASE_URL}/api/v1/oauth/internal/token/claude_code"  # integration_name=claude_code
    headers = {"X-Internal-Key": API_INTERNAL_KEY}
    params = {"tenant_id": tenant_id}

    with httpx.Client(timeout=10.0) as client:
        resp = client.get(url, headers=headers, params=params)
        resp.raise_for_status()
        data = resp.json()

    token = data.get("session_token")
    if not token:
        raise RuntimeError(f"No session_token in response: {data}")
    return token


@activity.defn
async def execute_code_task(task_input: CodeTaskInput) -> CodeTaskResult:
    """Execute a code task using Claude Code CLI."""
    branch_id = uuid.uuid4().hex[:8]
    branch_name = f"code/task-{branch_id}"

    try:
        # 1. Fetch tenant's Claude Code session token
        activity.heartbeat("Fetching Claude token...")
        token = _fetch_claude_token(task_input.tenant_id)
        claude_env = {"CLAUDE_CODE_OAUTH_TOKEN": token}

        # 2. Pull latest code
        activity.heartbeat("Pulling latest code...")
        _run("git fetch origin && git checkout main && git pull origin main")

        # 3. Create feature branch
        activity.heartbeat("Creating feature branch...")
        _run(f"git checkout -b {branch_name}")

        # 4. Build the prompt
        prompt = task_input.task_description
        if task_input.context:
            prompt = f"{task_input.context}\n\n{prompt}"

        # 5. Run Claude Code (auth via ANTHROPIC_API_KEY env var)
        activity.heartbeat("Running Claude Code...")
        prompt_escaped = prompt.replace("'", "'\\''")
        claude_cmd = (
            f"claude -p '{prompt_escaped}' "
            f"--output-format json "
            f'--allowedTools "Edit,Write,Bash,Read,Glob,Grep"'
        )
        claude_output = _run(claude_cmd, timeout=600, extra_env=claude_env)

        # Parse Claude output
        try:
            claude_data = json.loads(claude_output)
        except json.JSONDecodeError:
            claude_data = {"raw": claude_output}

        # 7. Check if there are any changes to commit
        status = _run("git status --porcelain")
        if not status:
            return CodeTaskResult(
                pr_url="",
                summary="No changes were made by Claude Code.",
                branch=branch_name,
                files_changed=[],
                claude_output=claude_output[:5000],
                success=True,
            )

        # 8. Stage, commit and push
        activity.heartbeat("Pushing changes...")
        _run("git add -A")
        commit_msg = task_input.task_description[:100].replace('"', '\\"')
        _run(f'git commit -m "feat: {commit_msg}"')
        _run(f'git push origin {branch_name}')

        # 9. Get changed files
        files_changed = _run("git diff --name-only main").split("\n")
        files_changed = [f for f in files_changed if f]

        # 10. Create PR
        activity.heartbeat("Creating PR...")
        pr_title = task_input.task_description[:70]
        pr_body = (
            f"## Summary\n\n"
            f"Autonomously implemented by Claude Code.\n\n"
            f"## Task\n\n"
            f"{task_input.task_description}\n\n"
            f"---\n\n"
            f"Files changed: {len(files_changed)}"
        )
        logger.info("Creating PR: title=%s", pr_title)
        pr_result = subprocess.run(
            ["gh", "pr", "create", "--title", pr_title, "--body", pr_body,
             "--head", branch_name, "--base", "main"],
            cwd=WORKSPACE, capture_output=True, text=True, timeout=60,
        )
        if pr_result.returncode != 0:
            raise RuntimeError(f"gh pr create failed: {pr_result.stderr or pr_result.stdout}")
        pr_output = pr_result.stdout.strip()

        # Extract PR URL from gh output
        pr_url = pr_output.split("\n")[-1]

        summary = claude_data.get("result", claude_output[:2000]) if isinstance(claude_data, dict) else claude_output[:2000]

        return CodeTaskResult(
            pr_url=pr_url,
            summary=str(summary)[:2000],
            branch=branch_name,
            files_changed=files_changed,
            claude_output=claude_output[:5000],
            success=True,
        )

    except Exception as e:
        logger.exception("Code task failed: %s", e)
        # Clean up: switch back to main
        try:
            _run("git checkout main", timeout=10)
        except Exception:
            pass

        return CodeTaskResult(
            pr_url="",
            summary="",
            branch=branch_name,
            files_changed=[],
            claude_output="",
            success=False,
            error=str(e),
        )


@workflow.defn
class CodeTaskWorkflow:
    """Temporal workflow for executing a code task via Claude Code CLI."""

    @workflow.run
    async def run(self, task_input: CodeTaskInput) -> CodeTaskResult:
        retry_policy = RetryPolicy(
            maximum_attempts=2,
            initial_interval=timedelta(seconds=30),
            backoff_coefficient=2.0,
            maximum_interval=timedelta(seconds=120),
        )

        return await workflow.execute_activity(
            execute_code_task,
            task_input,
            start_to_close_timeout=timedelta(minutes=15),
            schedule_to_close_timeout=timedelta(minutes=45),
            heartbeat_timeout=timedelta(seconds=120),
            retry_policy=retry_policy,
        )
