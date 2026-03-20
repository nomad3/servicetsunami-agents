"""Temporal workflow and activities for Claude Code tasks."""

import json
import logging
import os
import re
import subprocess
import tempfile
import time
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


def _extract_goal(task_description: str) -> str:
    """Extract a clean one-line goal from a structured task brief."""
    # Look for ## Goal section and grab the line after it
    match = re.search(r'##\s*Goal\s*\n+(.+)', task_description)
    if match:
        return match.group(1).strip()
    # Fallback: first non-header, non-empty line
    for line in task_description.splitlines():
        line = line.strip()
        if line and not line.startswith('#'):
            return line
    return task_description[:70]


_TAG_KEYWORDS = {
    'fix': ['fix', 'bug', 'broken', 'error', 'crash', 'issue', 'patch', 'repair', 'resolve'],
    'feat': ['add', 'create', 'implement', 'build', 'new', 'feature', 'introduce', 'support'],
    'infra': ['helm', 'kubernetes', 'k8s', 'deploy', 'terraform', 'ci', 'cd', 'pipeline', 'docker', 'infra'],
    'db': ['migration', 'schema', 'table', 'column', 'database', 'sql', 'index', 'alter'],
    'refactor': ['refactor', 'rename', 'reorganize', 'clean', 'simplify', 'restructure'],
    'docs': ['document', 'readme', 'comment', 'docstring', 'jsdoc'],
}


def _detect_tag(task_description: str) -> str:
    """Detect a conventional tag (fix/feat/infra/db/refactor/docs) from task text."""
    text = task_description.lower()
    for tag, keywords in _TAG_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return tag
    return 'feat'


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


def _log_code_task_rl(
    tenant_id: str,
    branch: str,
    tag: str,
    files_changed: list,
    pr_number: int,
) -> None:
    """Log an RL experience for the code_task decision point.

    Reward is initially 0 — it will be assigned later when the PR outcome
    is reported via the /api/v1/knowledge/pr-outcome endpoint or nightly polling.
    """
    try:
        resp = httpx.post(
            f"{API_BASE_URL}/api/v1/rl/internal/experience",
            headers={"X-Internal-Key": API_INTERNAL_KEY or "dev_mcp_key"},
            json={
                "tenant_id": tenant_id,
                "decision_point": "code_task",
                "state": {
                    "task_type": tag,
                    "affected_files": files_changed[:10],
                    "branch": branch,
                    "pr_number": pr_number,
                },
                "action": {
                    "platform": "claude_code",
                    "branch": branch,
                    "files_changed": len(files_changed),
                },
                "state_text": (
                    f"Task: {tag}, affected_files: {files_changed[:5]}, "
                    f"branch: {branch}, PR #{pr_number}"
                ),
            },
            timeout=10,
        )
        logger.info("RL experience logged for code_task PR #%s: %s", pr_number, resp.status_code)
    except Exception as e:
        logger.debug("RL experience log failed: %s", e)


@activity.defn
async def execute_code_task(task_input: CodeTaskInput) -> CodeTaskResult:
    """Execute a code task using Claude Code CLI."""
    # Generate readable branch name: code/feat/add-comment-to-main-03-11-1456
    goal = _extract_goal(task_input.task_description)
    tag = _detect_tag(task_input.task_description)
    slug = re.sub(r'[^a-z0-9]+', '-', goal[:60].lower()).strip('-')[:40]
    ts = time.strftime('%m-%d-%H%M')
    branch_name = f"code/{tag}/{slug}-{ts}"

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

        # 4. Build the prompt with full project context
        prompt_parts = []
        if task_input.context:
            prompt_parts.append(task_input.context)
        prompt_parts.append(task_input.task_description)
        prompt = "\n\n".join(prompt_parts)

        # Write prompt to temp file (avoids shell escaping issues)
        prompt_file = os.path.join(WORKSPACE, ".claude-task-prompt.md")
        with open(prompt_file, "w") as f:
            f.write(prompt)

        # 5. Run Claude Code with project context
        activity.heartbeat("Running Claude Code...")
        system_prompt = (
            "You are an autonomous code agent working on the ServiceTsunami monorepo. "
            "IMPORTANT: Read and follow the CLAUDE.md file in the project root — it contains "
            "the full architecture, patterns, conventions, and development commands for this codebase. "
            "Follow established patterns strictly: multi-tenant models with tenant_id, "
            "services layer for business logic, FastAPI routes at /api/v1/, React pages with "
            "Bootstrap 5, and Helm values for any Kubernetes changes. "
            "Do NOT create documentation files, READMEs, or test scripts in the root folder. "
            "Make minimal, focused changes — only what the task requires."
        )
        claude_result = subprocess.run(
            [
                "claude", "-p", prompt,
                "--output-format", "json",
                "--allowedTools", "Edit,Write,Bash,Read,Glob,Grep",
                "--append-system-prompt", system_prompt,
                "--dangerously-skip-permissions",
            ],
            cwd=WORKSPACE, capture_output=True, text=True, timeout=600,
            env={**os.environ, **claude_env},
        )
        # Clean up prompt file
        try:
            os.remove(prompt_file)
        except OSError:
            pass
        if claude_result.returncode != 0:
            error_detail = claude_result.stderr or claude_result.stdout
            logger.error("Claude Code failed: %s\nstdout: %s", claude_result.stderr, claude_result.stdout[:2000])
            raise RuntimeError(f"Claude Code failed:\n{error_detail}")
        claude_output = claude_result.stdout.strip()

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
        commit_msg = _extract_goal(task_input.task_description)[:100].replace('"', '\\"')
        _run(f'git commit -m "{tag}: {commit_msg}"')
        _run(f'git push origin {branch_name}')

        # 9. Get changed files
        files_changed = _run("git diff --name-only main").split("\n")
        files_changed = [f for f in files_changed if f]

        # 10. Create PR
        activity.heartbeat("Creating PR...")
        pr_title = f"{tag}: {_extract_goal(task_input.task_description)[:67]}"

        # Gather commit log and claude summary for traceability
        commit_log = _run(f"git log main..{branch_name} --pretty=format:'- %h %s' --reverse")
        claude_summary = ""
        if isinstance(claude_data, dict):
            claude_summary = str(claude_data.get("result", ""))[:1500]
        if not claude_summary:
            claude_summary = claude_output[:1500]
        files_list = "\n".join(f"- `{f}`" for f in files_changed)

        pr_body = (
            f"## Summary\n\n"
            f"Autonomously implemented by Claude Code.\n\n"
            f"## Task\n\n"
            f"{task_input.task_description}\n\n"
            f"## Claude Code Output\n\n"
            f"{claude_summary}\n\n"
            f"## Commits\n\n"
            f"{commit_log}\n\n"
            f"## Files Changed ({len(files_changed)})\n\n"
            f"{files_list}\n\n"
            f"---\n"
            f"*Generated by [ServiceTsunami Code Agent](https://servicetsunami.com)*"
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

        # Log RL experience for code_task decision point (reward assigned on PR outcome)
        try:
            pr_number_match = re.search(r'/pull/(\d+)', pr_url)
            pr_num = int(pr_number_match.group(1)) if pr_number_match else 0
            _log_code_task_rl(
                tenant_id=task_input.tenant_id,
                branch=branch_name,
                tag=tag,
                files_changed=files_changed,
                pr_number=pr_num,
            )
        except Exception as e:
            logger.debug("RL experience logging skipped: %s", e)

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


# ---------------------------------------------------------------------------
# Chat CLI — lightweight activity for conversational agent sessions
# ---------------------------------------------------------------------------

@dataclass
class ChatCliInput:
    message: str
    tenant_id: str
    claude_md_content: str = ""
    mcp_config: str = ""  # JSON string
    image_b64: str = ""   # Base64-encoded image (optional)
    image_mime: str = ""   # e.g. "image/jpeg"
    session_id: str = ""  # Claude Code session ID for continuity


@dataclass
class ChatCliResult:
    response_text: str
    success: bool
    error: Optional[str] = None
    metadata: Optional[dict] = None


@activity.defn
async def execute_chat_cli(task_input: ChatCliInput) -> ChatCliResult:
    """Run claude -p with session persistence via --session-id / --resume.

    First message in a session uses --session-id to create it.
    Subsequent messages use --resume to continue with full context.
    Sessions persist in ~/.claude/projects/ inside the container.
    """
    try:
        # Fetch tenant's OAuth token
        token = _fetch_claude_token(task_input.tenant_id)
        if not token:
            return ChatCliResult(response_text="", success=False, error="Claude Code not connected")

        # Fetch GitHub token from vault for git operations
        github_token = _fetch_github_token(task_input.tenant_id)
        if github_token:
            os.environ["GITHUB_TOKEN"] = github_token
            # Update git remote with token
            subprocess.run(
                ["git", "remote", "set-url", "origin",
                 f"https://{github_token}@github.com/nomad3/servicetsunami-agents.git"],
                cwd=WORKSPACE, capture_output=True,
            )
            # Auth gh CLI
            subprocess.run(
                ["gh", "auth", "login", "--with-token"],
                input=github_token, text=True, cwd=WORKSPACE, capture_output=True,
            )

        # Persistent session directory per tenant (not temp — survives across calls)
        session_dir = os.path.join("/tmp", "st_sessions", task_input.tenant_id)
        os.makedirs(session_dir, exist_ok=True)

        # Always write CLAUDE.md — needed for --resume retry fallback
        if task_input.claude_md_content:
            with open(os.path.join(session_dir, "CLAUDE.md"), "w") as f:
                f.write(task_input.claude_md_content)

        # Write MCP config
        if task_input.mcp_config:
            with open(os.path.join(session_dir, "mcp.json"), "w") as f:
                f.write(task_input.mcp_config)

        # Save image if provided
        if task_input.image_b64 and task_input.image_mime:
            import base64 as b64
            ext = task_input.image_mime.split("/")[-1].replace("jpeg", "jpg")
            img_path = os.path.join(session_dir, f"user_image.{ext}")
            with open(img_path, "wb") as f:
                f.write(b64.b64decode(task_input.image_b64))

        # Build command — full dev capabilities (git, edit, bash, MCP tools)
        cmd = [
            "claude", "-p", task_input.message,
            "--output-format", "json",
            "--model", "opus",
            "--allowedTools", "mcp__servicetsunami__*,Bash,Read,Edit,Write",
            "--add-dir", session_dir,
        ]

        # Give access to the repo workspace for code changes
        if os.path.isdir(WORKSPACE):
            cmd.extend(["--add-dir", WORKSPACE])

        # Always inject system prompt — claude -p is stateless, --resume
        # doesn't work in print mode. Context comes from conversation history
        # injected in the CLAUDE.md by the API.
        claude_md_path = os.path.join(session_dir, "CLAUDE.md")
        if os.path.exists(claude_md_path):
            with open(claude_md_path) as f:
                system_prompt = f.read()
            if system_prompt.strip():
                cmd.extend(["--append-system-prompt", system_prompt[:20000]])

        # MCP config
        mcp_path = os.path.join(session_dir, "mcp.json")
        if os.path.exists(mcp_path):
            cmd.extend(["--mcp-config", mcp_path])

        env = os.environ.copy()
        env["CLAUDE_CODE_OAUTH_TOKEN"] = token

        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=1500, env=env, cwd=WORKSPACE if os.path.isdir(WORKSPACE) else session_dir,
        )

        if result.returncode != 0:
            err = (result.stderr or result.stdout or "")[:1000]
            return ChatCliResult(response_text="", success=False, error=f"CLI exit {result.returncode}: {err}")

        raw = result.stdout.strip()
        if not raw:
            return ChatCliResult(response_text="", success=False, error="CLI produced no output")

        # Parse JSON output
        try:
            data = json.loads(raw)
            text = data.get("result") or data.get("response") or data.get("content") or data.get("text") or raw
            meta = {
                "input_tokens": (data.get("usage") or {}).get("input_tokens", 0),
                "output_tokens": (data.get("usage") or {}).get("output_tokens", 0),
                "model": data.get("model"),
                "claude_session_id": data.get("session_id", ""),
                "cost_usd": data.get("total_cost_usd", 0),
            }
            return ChatCliResult(response_text=text, success=True, metadata=meta)
        except json.JSONDecodeError:
            return ChatCliResult(response_text=raw, success=True)

    except subprocess.TimeoutExpired:
        return ChatCliResult(response_text="", success=False, error="CLI timed out (5 min)")
    except Exception as e:
        return ChatCliResult(response_text="", success=False, error=str(e))


def _fetch_github_token(tenant_id: str) -> Optional[str]:
    """Fetch GitHub OAuth token from API credential vault."""
    try:
        resp = httpx.get(
            f"{API_BASE_URL}/api/v1/oauth/internal/token/github",
            params={"tenant_id": tenant_id},
            headers={"X-Internal-Key": API_INTERNAL_KEY or "dev_mcp_key"},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            return data.get("oauth_token") or data.get("session_token")
    except Exception as e:
        logger.warning("Failed to fetch github token: %s", e)
    return None


def _fetch_claude_token(tenant_id: str) -> Optional[str]:
    """Fetch Claude Code OAuth token from API credential vault."""
    try:
        resp = httpx.get(
            f"{API_BASE_URL}/api/v1/oauth/internal/token/claude_code",
            params={"tenant_id": tenant_id},
            headers={"X-Internal-Key": API_INTERNAL_KEY or "dev_mcp_key"},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            return data.get("session_token") or data.get("oauth_token")
    except Exception as e:
        logger.error("Failed to fetch claude token: %s", e)
    return None


@workflow.defn
class ChatCliWorkflow:
    """Temporal workflow for chat CLI sessions.

    Flexible timeout: Claude CLI may do complex multi-tool work
    (email scanning, calendar creation, code analysis). Allow up to
    30 minutes with heartbeat to keep Temporal informed.
    """

    @workflow.run
    async def run(self, task_input: ChatCliInput) -> ChatCliResult:
        return await workflow.execute_activity(
            execute_chat_cli,
            task_input,
            start_to_close_timeout=timedelta(minutes=30),
            schedule_to_close_timeout=timedelta(minutes=45),
            heartbeat_timeout=timedelta(seconds=300),
            retry_policy=RetryPolicy(maximum_attempts=2),
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
