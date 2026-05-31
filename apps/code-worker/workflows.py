"""Temporal workflow and activities for Claude Code tasks."""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
import urllib.parse
from dataclasses import dataclass
from datetime import timedelta
from typing import List, Optional

import httpx
from temporalio import activity, workflow
from temporalio.common import RetryPolicy

# Phase 1.5: helpers delegate to the canonical classifier (closes I-1).
# At runtime the package lives at /app/cli_orchestrator/ (Dockerfile COPY +
# docker-compose bind-mount); under pytest the worker conftest puts
# <repo-root>/packages/ on sys.path. Either way the import resolves.
from cli_orchestrator import Status, classify

# Phase 1.6: shared CLI runtime helpers live in cli_runtime.py. Re-export
# the public names under their old underscore-prefixed aliases so existing
# production callers and the test suite continue to resolve them via
# `workflows._run_cli_with_heartbeat` / `workflows._safe_cli_error_snippet`.
# The re-exports preserve object identity (`is`-checks pass).
from cli_runtime import (
    run_cli_with_heartbeat as _run_cli_with_heartbeat,
    safe_cli_error_snippet as _safe_cli_error_snippet,
)

# Phase 1.6: per-CLI chat executors live in cli_executors/*.py. Re-export
# under the old underscore-prefixed names so the dispatch table inside
# ``execute_chat_cli`` and the test suite both keep resolving via
# ``workflows._execute_<platform>_chat`` with object identity preserved.
# Each executor's lazy imports (``from workflows import _fetch_..., ...``)
# fire only on call, breaking the workflows <-> cli_executors cycle at
# module-load time and preserving test monkeypatches on those helpers.
from cli_executors.aider import execute_aider_chat as _execute_aider_chat
from cli_executors.claude import execute_claude_chat as _execute_claude_chat
from cli_executors.codex import execute_codex_chat as _execute_codex_chat
from cli_executors.gemini import execute_gemini_chat as _execute_gemini_chat
from cli_executors.copilot import execute_copilot_chat as _execute_copilot_chat
from cli_executors.qwen import execute_qwen_chat as _execute_qwen_chat
from cli_executors.kimi import execute_kimi_chat as _execute_kimi_chat
from cli_executors.deepseek import execute_deepseek_chat as _execute_deepseek_chat
from cli_executors.glm import execute_glm_chat as _execute_glm_chat
from cli_executors.goose import execute_goose_chat as _execute_goose_chat
from cli_executors.opencode import (
    execute_opencode_chat as _execute_opencode_chat,
    _execute_opencode_chat_cli,
    _opencode_sessions,
    OPENCODE_OLLAMA_URL,
    OPENCODE_MODEL,
    OPENCODE_PORT,
)

logger = logging.getLogger(__name__)


def _build_allowed_tools_from_mcp(mcp_config_json: str = "", extra: str = "") -> str:
    """Derive --allowedTools from MCP config JSON.

    Creates wildcard patterns for each MCP server key so the CLI
    auto-approves tool calls for all connected servers.
    """
    tools = []
    if extra:
        tools.extend(extra.split(","))
    try:
        mcp = json.loads(mcp_config_json) if mcp_config_json else {}
        for key in mcp.get("mcpServers", {}):
            tools.append(f"mcp__{key}__*")
    except Exception:
        tools.append("mcp__agentprovision__*")
    if not any("mcp__" in t for t in tools):
        tools.append("mcp__agentprovision__*")
    return ",".join(tools)


WORKSPACE = "/workspace"
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
API_INTERNAL_KEY = os.environ.get("API_INTERNAL_KEY", "").strip()
API_BASE_URL = os.environ.get("API_BASE_URL", "http://agentprovision-api").strip()
CODE_TASK_COMMAND_TIMEOUT_SECONDS = 45 * 60
CODE_TASK_ACTIVITY_TIMEOUT_MINUTES = 120
CODE_TASK_SCHEDULE_TIMEOUT_MINUTES = 150
CODE_TASK_HEARTBEAT_SECONDS = 240
CLAUDE_CODE_MODEL = os.environ.get("CLAUDE_CODE_MODEL", "sonnet").strip() or "sonnet"
CLAUDE_CREDIT_ERROR_PATTERNS = (  # DEAD CODE Phase 1.5 — kept one phase as parity-test corpus + for legacy attribute tests in test_workflow_definitions.py:74-77; Phase 2 deletes.
    "credit balance is too low",
    "usage limit reached",
    "rate limit reached",
    "monthly usage limit",
    "max plan limit",
    "out of credits",
    "out of extra usage",
    "insufficient credits",
    "subscription required",
    "hit your limit",
)

CODEX_CREDIT_ERROR_PATTERNS = (  # DEAD CODE Phase 1.5 — kept one phase as parity-test corpus + for legacy attribute tests in test_workflow_definitions.py:74-77; Phase 2 deletes.
    "rate limit",
    "rate_limit",
    "usage limit",
    "quota exceeded",
    "insufficient_quota",
    "billing",
    "out of credits",
    "token limit exceeded",
    "capacity",
    "too many requests",
    "429",
)

COPILOT_CREDIT_ERROR_PATTERNS = (  # DEAD CODE Phase 1.5 — kept one phase as parity-test corpus + for legacy attribute tests in test_workflow_definitions.py:74-77; Phase 2 deletes.
    "rate limit",
    "rate_limit",
    "usage limit",
    "quota exceeded",
    "insufficient_quota",
    "subscription required",
    "copilot is not enabled",
    "not authorized",
    "forbidden",
    "out of credits",
    "too many requests",
    "429",
)


@dataclass
class CodeTaskInput:
    task_description: str
    tenant_id: str
    context: Optional[str] = None
    # Phase 4 commit 5 — optional fields populated by /tasks/dispatch
    # for agent-token minting + hook injection. All optional so legacy
    # callers (including the chat hot path that doesn't go through
    # /tasks/dispatch) remain byte-identical.
    agent_id: Optional[str] = None
    task_id: Optional[str] = None
    parent_workflow_id: Optional[str] = None
    parent_chain: Optional[list] = None
    allowed_tools: Optional[list] = None  # bare tool names, no prefix


@dataclass
class CodeTaskResult:
    pr_url: str
    summary: str
    branch: str
    files_changed: list[str]
    claude_output: str
    success: bool
    error: Optional[str] = None


@dataclass
class AgentReview:
    agent_role: str
    approved: bool
    verdict: str          # "APPROVED" | "REJECTED" | "CONDITIONAL"
    issues: list
    suggestions: list
    summary: str


def _run(
    argv: list[str],
    cwd: str = WORKSPACE,
    timeout: int = 600,
    extra_env: dict | None = None,
    input: str | None = None,
) -> str:
    """Run a subprocess from an argv list and return stdout.

    Uses ``shell=False`` always — shell metacharacters in argv elements
    become literal text, never expanded. For commands that need to read
    a long/user-derived string (e.g. git commit messages), pass it via
    ``input=`` which is fed to the subprocess on stdin. The argv stays
    free of user data.

    Raises RuntimeError on non-zero exit.

    Spec: docs/superpowers/specs/2026-05-22-subproject-a-infra-secret-hardening-design.md
    PR1 (F1 shell=True removal).
    """
    # Defensive str-cast: if a future caller passes a non-string in argv
    # (Path, int), ``" ".join(argv)`` would TypeError BEFORE the
    # subprocess runs, masking the real bug. ``map(str, ...)`` keeps the
    # log/error sites robust without changing what's passed to
    # ``subprocess.run`` itself (which already accepts Path-like via
    # os.fspath).
    argv_display = " ".join(map(str, argv))
    logger.info("Running: %s", argv_display)
    env = None
    if extra_env:
        env = {**os.environ, **extra_env}
    result = subprocess.run(
        argv,
        shell=False,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        input=input,
    )
    if result.returncode != 0:
        error_detail = result.stderr or result.stdout
        logger.error(
            "Command failed: %s\nstderr: %s\nstdout: %s",
            argv_display, result.stderr, result.stdout[:2000],
        )
        raise RuntimeError(
            f"Command failed: {argv_display}\n{error_detail}"
        )
    return result.stdout.strip()


def _run_long_command(
    cmd: list[str],
    *,
    cwd: str = WORKSPACE,
    timeout: int = CODE_TASK_COMMAND_TIMEOUT_SECONDS,
    extra_env: dict | None = None,
    heartbeat_message: str,
    heartbeat_interval: int = 30,
) -> subprocess.CompletedProcess[str]:
    """Run a long-lived command while sending periodic Temporal heartbeats."""
    logger.info("Running long command: %s", " ".join(cmd))
    env = {**os.environ, **(extra_env or {})}
    process = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    start = time.monotonic()

    while True:
        if process.poll() is not None:
            break
        elapsed = int(time.monotonic() - start)
        activity.heartbeat(f"{heartbeat_message} ({elapsed}s elapsed)")
        if elapsed >= timeout:
            process.kill()
            stdout, stderr = process.communicate()
            logger.error(
                "Long command timed out after %ss: %s\nstderr: %s\nstdout: %s",
                timeout,
                " ".join(cmd),
                stderr,
                stdout[:2000],
            )
            raise RuntimeError(f"Command timed out after {timeout} seconds: {' '.join(cmd)}")
        time.sleep(heartbeat_interval)

    stdout, stderr = process.communicate()
    result = subprocess.CompletedProcess(cmd, process.returncode, stdout, stderr)
    if result.returncode != 0:
        error_detail = result.stderr or result.stdout
        logger.error(
            "Long command failed: %s\nstderr: %s\nstdout: %s",
            " ".join(cmd),
            result.stderr,
            result.stdout[:2000],
        )
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{error_detail}")
    return result


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


def _is_claude_credit_exhausted(error_text: Optional[str]) -> bool:
    # Phase 1.5: delegate to the canonical classifier (closes I-1).
    # Defensive None/empty handling matches the legacy contract.
    return classify(error_text or "") == Status.QUOTA_EXHAUSTED


def _is_codex_credit_exhausted(error_text: Optional[str]) -> bool:
    # Phase 1.5: delegate to the canonical classifier (closes I-1).
    # NOTE (review I-B): no production call sites today — only tests.
    # Phase 2 should either wire codex-first fallback chaining or delete.
    return classify(error_text or "") == Status.QUOTA_EXHAUSTED


def _is_copilot_credit_exhausted(error_text: Optional[str]) -> bool:
    # Phase 1.5: delegate to the canonical classifier (closes I-1).
    # NOTE (review I-B): no production call sites today — only tests.
    # Phase 2 should either wire copilot-first fallback chaining or delete.
    #
    # Copilot legacy CREDIT_ERROR_PATTERNS lumped 'not authorized' (an
    # auth error) into the credit-exhausted bucket so CLI fallback
    # chaining triggers on either. Phase 1.5 keeps NEEDS_AUTH a
    # distinct Status for other consumers (chat error footer, RL,
    # council) and the helper takes the union explicitly here. See
    # apps/code-worker/tests/test_credit_exhausted_parity.py.
    return classify(error_text or "") in (
        Status.QUOTA_EXHAUSTED,
        Status.NEEDS_AUTH,
    )


_INTEGRATION_NOT_CONNECTED_MESSAGES = {
    "claude_code": (
        "Claude Code subscription is not connected. "
        "Please connect your Claude Code account in Settings → Integrations."
    ),
    "codex": (
        "Codex (ChatGPT) subscription is not connected. "
        "Please connect your OpenAI account in Settings → Integrations."
    ),
    "gemini_cli": (
        "Gemini CLI is not connected. "
        "Please connect your Google account in Settings → Integrations."
    ),
    "copilot_cli": (
        "GitHub Copilot CLI is not connected. "
        "Please connect your GitHub account in Settings → Integrations "
        "and ensure your GitHub Copilot subscription is active."
    ),
    "qwen_code": (
        "Qwen Code is not connected. "
        "Please connect your Qwen API key in Settings → Integrations."
    ),
    "kimi_k2": (
        "Kimi K2 is not connected. "
        "Please connect your Moonshot account in Settings → Integrations."
    ),
    "deepseek": (
        "DeepSeek is not connected. "
        "Please connect your DeepSeek account in Settings → Integrations."
    ),
    "glm": (
        "GLM (Zhipu AI) is not connected. "
        "Please connect your Zhipu account in Settings → Integrations."
    ),
    "aider": (
        "Aider is not connected. "
        "Please connect your Aider account in Settings → Integrations."
    ),
    "goose": (
        "Goose is not connected. "
        "Please connect your Goose account in Settings → Integrations."
    ),
}


def _fetch_integration_credentials(integration_name: str, tenant_id: str) -> dict:
    """Fetch decrypted tenant credentials for an integration from the API."""
    url = f"{API_BASE_URL}/api/v1/oauth/internal/token/{integration_name}"
    headers = {"X-Internal-Key": API_INTERNAL_KEY or "dev_mcp_key"}
    params = {"tenant_id": tenant_id}

    with httpx.Client(timeout=10.0) as client:
        resp = client.get(url, headers=headers, params=params)
        if resp.status_code == 404:
            friendly = _INTEGRATION_NOT_CONNECTED_MESSAGES.get(
                integration_name,
                f"{integration_name} integration is not connected. Please check Settings → Integrations.",
            )
            raise RuntimeError(friendly)
        resp.raise_for_status()
        return resp.json()


def _log_code_task_rl(
    tenant_id: str,
    branch: str,
    tag: str,
    files_changed: list,
    pr_number: int,
    platform: str = "claude_code",
) -> None:
    """Log an RL experience for the code_task decision point.

    Reward is initially 0 — it will be assigned later when the PR outcome
    is reported via the /api/v1/knowledge/pr-outcome endpoint or nightly polling.
    """
    try:
        resp = httpx.post(
            f"{API_BASE_URL}/api/v1/rl/internal/experience",
            headers={
                "X-Internal-Key": API_INTERNAL_KEY or "dev_mcp_key",
                # F9 P1: tenant scope MUST come from the header now;
                # the api-side gate rejects the call if body tenant_id
                # doesn't match. Same value in both places by design.
                "X-Tenant-Id": tenant_id,
            },
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
                    "platform": platform,
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


CODE_TASK_REVIEW_TIMEOUT_SECONDS = 8 * 60  # 8 min per review agent


def _run_review_agent(
    role: str,
    review_prompt: str,
    extra_env: dict,
    timeout: int = CODE_TASK_REVIEW_TIMEOUT_SECONDS,
) -> AgentReview:
    """Run a read-only review agent and return a structured AgentReview.

    The agent is given Read/Glob/Grep/Bash tools (no write access) and asked
    to output a single JSON verdict.  We parse that JSON defensively.
    """
    system_prompt = (
        f"You are the {role} in a multi-agent code review council for the agentprovision.com platform. "
        "Your job is REVIEW ONLY — do NOT create, edit, or delete any files. "
        "Use Read, Glob, Grep, and Bash (read-only git commands) to inspect the code. "
        "After your review, respond with a SINGLE valid JSON object (no markdown, no text outside JSON):\n"
        '{"approved": true/false, "verdict": "APPROVED|REJECTED|CONDITIONAL", '
        '"issues": ["specific issue 1", ...], '
        '"suggestions": ["actionable suggestion 1", ...], '
        '"summary": "2-3 sentence review summary"}'
    )
    result = subprocess.run(
        [
            "claude", "-p", review_prompt,
            "--output-format", "json",
            "--model", CLAUDE_CODE_MODEL,
            "--allowedTools", "Read,Glob,Grep",
            "--append-system-prompt", system_prompt,
        ],
        cwd=WORKSPACE,
        capture_output=True,
        text=True,
        timeout=timeout,
        env={**os.environ, **extra_env},
    )

    if result.returncode != 0:
        logger.warning("%s review failed (exit %s): %s", role, result.returncode, result.stderr[:400])
        return AgentReview(
            agent_role=role, approved=False, verdict="REJECTED",
            issues=[f"Review agent process failed: {result.stderr[:200]}"],
            suggestions=[], summary=f"{role} could not complete review.",
        )

    try:
        outer = json.loads(result.stdout.strip())
        result_text = outer.get("result", "") if isinstance(outer, dict) else str(outer)
        # Strip markdown code fences
        result_text = re.sub(r"```(?:json)?\s*|\s*```", "", result_text).strip()
        # Find the first JSON object
        json_match = re.search(r"\{.*\}", result_text, re.DOTALL)
        review_data = json.loads(json_match.group(0)) if json_match else json.loads(result_text)
        return AgentReview(
            agent_role=role,
            approved=bool(review_data.get("approved", False)),
            verdict=str(review_data.get("verdict", "REJECTED")),
            issues=list(review_data.get("issues", [])),
            suggestions=list(review_data.get("suggestions", [])),
            summary=str(review_data.get("summary", "")),
        )
    except (json.JSONDecodeError, AttributeError, TypeError) as e:
        raw = result.stdout[:800]
        # Lenient fallback: scan text for signals
        approved = bool(re.search(r'\bapproved\b', raw, re.IGNORECASE)) and not bool(
            re.search(r'not\s+approved|rejected', raw, re.IGNORECASE)
        )
        return AgentReview(
            agent_role=role, approved=approved, verdict="CONDITIONAL",
            issues=[f"Could not parse structured review (parse error: {e})"],
            suggestions=[], summary=raw[:400],
        )


def _consensus_check(reviews: list, required: int = 2) -> tuple:
    """Return (passed: bool, report: str).

    Consensus is reached when at least `required` agents approve.
    """
    approved_count = sum(1 for r in reviews if r.approved)
    passed = approved_count >= required
    lines = [
        f"Review Council: {approved_count}/{len(reviews)} approved — "
        f"{'✓ PASSED' if passed else '✗ FAILED'}"
    ]
    for r in reviews:
        icon = "✓" if r.approved else "✗"
        lines.append(f"  {icon} [{r.agent_role}] {r.verdict}")
        for issue in r.issues[:3]:
            lines.append(f"      • {issue}")
    return passed, "\n".join(lines)


def _inject_agent_token_and_hooks(
    *,
    task_input: "CodeTaskInput",
    claude_env: dict,
) -> None:
    """Phase 4 — mint agent-token from /api/v1/internal/agent-tokens/mint
    and write the .claude.json + .claude/hooks/ scripts into WORKSPACE.

    Mutates ``claude_env`` in place to add:
      - AGENTPROVISION_AGENT_TOKEN
      - AGENTPROVISION_TASK_ID
      - AGENTPROVISION_PARENT_WORKFLOW_ID (if any)
      - AGENTPROVISION_ALLOWED_TOOLS (whitespace-separated)
      - AGENTPROVISION_API (base URL the PostToolUse hook calls)

    Best-effort: any exception here is caught at the call site and the
    leaf falls back to legacy auth.
    """
    from pathlib import Path
    import os as _os

    import hook_templates  # local import — code-worker module

    api_base_url = _os.environ.get("API_BASE_URL", "http://api:8000")
    internal_key = _os.environ.get("API_INTERNAL_KEY") or _os.environ.get(
        "MCP_API_KEY", "dev_mcp_key"
    )
    mcp_tools_url = _os.environ.get(
        "MCP_TOOLS_URL",
        _os.environ.get("MCP_SERVER_URL", "http://mcp-tools:8086"),
    )

    payload = {
        "tenant_id": str(task_input.tenant_id),
        "agent_id": str(task_input.agent_id),
        "task_id": str(task_input.task_id),
        "parent_workflow_id": task_input.parent_workflow_id,
        "scope": list(task_input.allowed_tools or []) or None,
        "parent_chain": list(task_input.parent_chain or []),
    }
    with httpx.Client(timeout=10.0) as client:
        resp = client.post(
            f"{api_base_url}/api/v1/internal/agent-tokens/mint",
            json=payload,
            headers={"X-Internal-Key": internal_key},
        )
    resp.raise_for_status()
    token = resp.json()["token"]

    workdir = Path(WORKSPACE)
    hook_templates.write_claude_hooks(workdir)
    hook_templates.write_claude_mcp_config(
        workdir=workdir,
        agent_token=token,
        mcp_url=f"{mcp_tools_url}/sse",
    )

    # Inject env trio for the leaf subprocess + hooks.
    claude_env["AGENTPROVISION_AGENT_TOKEN"] = token
    claude_env["AGENTPROVISION_TASK_ID"] = str(task_input.task_id)
    if task_input.parent_workflow_id:
        claude_env["AGENTPROVISION_PARENT_WORKFLOW_ID"] = (
            task_input.parent_workflow_id
        )
    if task_input.allowed_tools:
        claude_env["AGENTPROVISION_ALLOWED_TOOLS"] = " ".join(
            task_input.allowed_tools
        )
    claude_env["AGENTPROVISION_API"] = api_base_url


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
        # 1. Fetch tenant's Claude Code credential (OAuth or api_key).
        activity.heartbeat("Fetching Claude token...")
        credential = _fetch_claude_credential(task_input.tenant_id)
        if credential is None:
            # Legacy back-compat: tests monkeypatch _fetch_claude_token,
            # and old call sites pre-API-key path return only the OAuth
            # token here. Treat a bare token as OAuth.
            legacy_token = _fetch_claude_token(task_input.tenant_id)
            credential = (legacy_token, "oauth") if legacy_token else (None, "oauth")
        token, kind = credential
        if kind == "api_key":
            claude_env = {"ANTHROPIC_API_KEY": token}
        else:
            claude_env = {"CLAUDE_CODE_OAUTH_TOKEN": token}

        # 1b. Phase 4 commit 5 — agent-token mint + hook injection.
        # Only fires when the dispatch endpoint populated agent_id +
        # task_id on CodeTaskInput. Legacy callers (chat hot path)
        # pass neither — preserving byte-identical pre-Phase-4 behavior.
        if task_input.agent_id and task_input.task_id:
            try:
                _inject_agent_token_and_hooks(
                    task_input=task_input,
                    claude_env=claude_env,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "agent-token injection failed (continuing without): %s",
                    exc,
                )

        # 2. Pull latest code
        activity.heartbeat("Pulling latest code...")
        _run(["git", "fetch", "origin"])
        _run(["git", "checkout", "main"])
        _run(["git", "pull", "origin", "main"])

        # 3. Create feature branch
        activity.heartbeat("Creating feature branch...")
        _run(["git", "checkout", "-b", branch_name])

        # ── PHASE 1: Planning ────────────────────────────────────────────────
        # 4a. Architect agent reads the task + CLAUDE.md and writes a plan file
        activity.heartbeat("Phase 1: Architect agent creating implementation plan...")
        plan_file = os.path.join(WORKSPACE, ".claude", "plan.md")
        os.makedirs(os.path.join(WORKSPACE, ".claude"), exist_ok=True)
        plan_prompt = (
            f"Read CLAUDE.md carefully, then analyse the following task and write a detailed "
            f"implementation plan to the file `.claude/plan.md`.\n\n"
            f"## Task\n\n{task_input.task_description}\n\n"
            f"The plan MUST include these sections:\n"
            f"## Goal\n## Files to Change\n## Implementation Steps\n"
            f"## Patterns to Follow\n## Risk Assessment\n\n"
            f"Write the plan file, then confirm it is done. No code changes — planning only."
        )
        plan_system = (
            "You are the Architect agent for the agentprovision.com platform. "
            "Your ONLY job right now is to read the existing code and write a concise implementation plan. "
            "Do NOT write any production code yet. Do NOT modify any source files. "
            "Only create/write `.claude/plan.md`."
        )
        _run_long_command(
            [
                "claude", "-p", plan_prompt,
                "--output-format", "json",
                "--model", CLAUDE_CODE_MODEL,
                "--allowedTools", "Read,Glob,Grep,Bash,Write",
                "--append-system-prompt", plan_system,
                "--dangerously-skip-permissions",
            ],
            cwd=WORKSPACE,
            timeout=10 * 60,          # 10 min for planning
            extra_env=claude_env,
            heartbeat_message="Architect agent is planning",
            heartbeat_interval=30,
        )

        # Read the plan so we can include it in the implementation prompt
        plan_content = ""
        if os.path.exists(plan_file):
            with open(plan_file) as f:
                plan_content = f.read()

        # 4b. Plan Review Council — 3 agents verify the plan before implementation
        # Each agent checks: work planned, alignment with task, patterns, and good practices.
        activity.heartbeat("Phase 1: Plan review council (3 agents)...")
        plan_context = (
            f"## Original Task\n{task_input.task_description}\n\n"
            f"## Proposed Plan (`.claude/plan.md`)\n{plan_content[:3000]}"
        )
        plan_review_1 = _run_review_agent(
            role="Architect Reviewer",
            review_prompt=(
                f"Read CLAUDE.md and `.claude/plan.md`, then review the WORK PLANNED:\n"
                f"1. Correct architectural patterns (multi-tenancy, auth, route mounting)\n"
                f"2. Alignment with existing codebase conventions (CLAUDE.md patterns)\n"
                f"3. All required wiring steps mentioned (routes.py, __init__.py, migrations)?\n"
                f"4. Does the plan respect good practices for this codebase?\n\n"
                f"{plan_context}"
            ),
            extra_env=claude_env,
        )
        activity.heartbeat("Phase 1: Plan review council — technical review...")
        plan_review_2 = _run_review_agent(
            role="Technical Reviewer",
            review_prompt=(
                f"Read `.claude/plan.md` and the relevant existing source files mentioned in it, "
                f"then review the WORK PLANNED:\n"
                f"1. Technical feasibility — can this plan be implemented as written?\n"
                f"2. Completeness — is anything missing or ambiguous?\n"
                f"3. Scope — is the plan focused? Does it avoid over-engineering?\n"
                f"4. Are the implementation steps in the right order with no gaps?\n\n"
                f"{plan_context}"
            ),
            extra_env=claude_env,
        )
        activity.heartbeat("Phase 1: Plan review council — behavior review...")
        plan_review_3 = _run_review_agent(
            role="Behavior Reviewer",
            review_prompt=(
                f"Read `.claude/plan.md` and the original task, then review the WORK PLANNED:\n"
                f"1. Does the plan fully address ALL behavioral requirements in the task?\n"
                f"2. Are edge cases, error paths, and integration points accounted for?\n"
                f"3. Will the planned outputs (endpoints, UI, DB schema) match what was asked?\n"
                f"4. Any regressions to existing behavior that the plan should guard against?\n\n"
                f"{plan_context}"
            ),
            extra_env=claude_env,
        )
        plan_reviews = [plan_review_1, plan_review_2, plan_review_3]
        plan_passed, plan_consensus = _consensus_check(plan_reviews, required=2)
        logger.info("Plan review consensus:\n%s", plan_consensus)
        if not plan_passed:
            all_plan_issues = []
            for r in plan_reviews:
                if not r.approved:
                    for issue in r.issues:
                        all_plan_issues.append(f"[{r.agent_role}] {issue}")
            logger.warning(
                "Plan review council did not fully approve. Issues: %s — proceeding with caution.",
                all_plan_issues,
            )
        # ── END PHASE 1 ──────────────────────────────────────────────────────

        # 4. Build the prompt with full project context (now includes approved plan)
        prompt_parts = []
        if task_input.context:
            prompt_parts.append(task_input.context)
        prompt_parts.append(task_input.task_description)
        if plan_content:
            prompt_parts.append(
                f"## Implementation Plan (approved by review council)\n\n{plan_content}"
            )
        prompt = "\n\n".join(prompt_parts)

        # Write prompt to temp file (avoids shell escaping issues)
        prompt_file = os.path.join(WORKSPACE, ".claude-task-prompt.md")
        with open(prompt_file, "w") as f:
            f.write(prompt)

        execution_platform = "claude_code"
        provider_label = "Claude Code"

        # 5. Run Claude Code with project context
        activity.heartbeat("Running Claude Code...")
        system_prompt = (
            "You are an autonomous code agent working on the agentprovision.com monorepo. "
            "IMPORTANT: Read and follow the CLAUDE.md file in the project root — it contains "
            "the full architecture, patterns, conventions, and development commands for this codebase. "
            "Follow established patterns strictly: multi-tenant models with tenant_id, "
            "services layer for business logic, FastAPI routes at /api/v1/, React pages with "
            "Bootstrap 5, and Helm values for any Kubernetes changes. "
            "Do NOT create documentation files, READMEs, or test scripts in the root folder. "
            "Make minimal, focused changes — only what the task requires."
        )
        claude_result = _run_long_command(
            [
                "claude", "-p", prompt,
                "--output-format", "json",
                "--model", CLAUDE_CODE_MODEL,
                "--allowedTools", "Edit,Write,Bash,Read,Glob,Grep",
                "--append-system-prompt", system_prompt,
                "--dangerously-skip-permissions",
            ],
            cwd=WORKSPACE,
            timeout=CODE_TASK_COMMAND_TIMEOUT_SECONDS,
            extra_env=claude_env,
            heartbeat_message="Claude Code is still running",
        )
        # Clean up prompt file
        try:
            os.remove(prompt_file)
        except OSError:
            pass
        if claude_result.returncode != 0:
            error_detail = claude_result.stderr or claude_result.stdout
            logger.error("Claude Code failed: %s\nstdout: %s", claude_result.stderr, claude_result.stdout[:2000])
            if _is_claude_credit_exhausted(error_detail):
                activity.heartbeat("Claude credits exhausted, retrying with Codex...")
                codex_prompt = (
                    f"{system_prompt}\n\n"
                    f"# Task\n\n{prompt}"
                )
                claude_output, provider_meta = _execute_codex_code_task(
                    task_input,
                    codex_prompt,
                    session_dir=WORKSPACE,
                )
                claude_data = {"result": claude_output, "metadata": provider_meta}
                execution_platform = "codex"
                provider_label = "Codex"
            else:
                raise RuntimeError(f"Claude Code failed:\n{error_detail}")
        else:
            claude_output = claude_result.stdout.strip()

            # Parse Claude output
            try:
                claude_data = json.loads(claude_output)
            except json.JSONDecodeError:
                claude_data = {"raw": claude_output}

        # 7. Check if there are any changes to commit
        status = _run(["git", "status", "--porcelain"])
        if not status:
            return CodeTaskResult(
                pr_url="",
                summary=f"No changes were made by {provider_label}.",
                branch=branch_name,
                files_changed=[],
                claude_output=claude_output[:5000],
                success=True,
            )

        # ── PHASE 2: Post-implementation Review Council ───────────────────────
        # Three agents review every aspect: planned vs done, outputs, code quality,
        # and behavior/pattern alignment.  Consensus = 2/3 agents approve.
        # If consensus fails we do ONE correction pass, then re-review.

        def _build_review_context() -> str:
            diff_stat = ""
            diff_patch = ""
            try:
                diff_stat = subprocess.run(
                    ["git", "diff", "--stat", "main"],
                    cwd=WORKSPACE, capture_output=True, text=True, timeout=15,
                ).stdout.strip()
                diff_patch = subprocess.run(
                    ["git", "diff", "main", "--", "*.py", "*.js", "*.ts", "*.tsx"],
                    cwd=WORKSPACE, capture_output=True, text=True, timeout=15,
                ).stdout.strip()[:4000]
            except Exception:
                pass
            return (
                f"## Original Task\n{task_input.task_description[:500]}\n\n"
                f"## Implementation Plan\n{plan_content[:1000]}\n\n"
                f"## Git Diff Stat\n{diff_stat}\n\n"
                f"## Key Code Changes (truncated)\n```\n{diff_patch}\n```"
            )

        def _run_review_council(council_label: str) -> tuple:
            """Run the 3-agent review council. Returns (passed, report, reviews).

            Each agent reviews ALL 5 dimensions:
              - Work planned (plan alignment)
              - Work done (implementation completeness)
              - Outputs (files and artifacts produced)
              - Code review (quality and security)
              - Behavior review (spec compliance and pattern adherence)
            """
            activity.heartbeat(f"Phase 2: {council_label} — Architect review...")
            review_ctx = _build_review_context()

            impl_review_arch = _run_review_agent(
                role="Architect Agent",
                review_prompt=(
                    f"Read CLAUDE.md and all changed files. You must review ALL of the following:\n\n"
                    f"WORK PLANNED vs WORK DONE:\n"
                    f"- Does the implementation match the plan in `.claude/plan.md`?\n"
                    f"- Were all planned steps executed? What was skipped or changed?\n\n"
                    f"OUTPUTS:\n"
                    f"- Are all expected files/endpoints/schemas present and correctly placed?\n"
                    f"- Are new routes mounted in routes.py? Models in models/__init__.py?\n"
                    f"- Are migrations included if schema changed?\n\n"
                    f"CODE REVIEW (architectural perspective):\n"
                    f"- Does the code follow established patterns (multi-tenancy, auth, services layer)?\n"
                    f"- Any architectural violations or anti-patterns?\n\n"
                    f"BEHAVIOR REVIEW:\n"
                    f"- Does the implementation align with agentprovision.com conventions in CLAUDE.md?\n"
                    f"- Good practices followed? No over-engineering?\n\n"
                    f"{review_ctx}"
                ),
                extra_env=claude_env,
            )
            activity.heartbeat(f"Phase 2: {council_label} — Code review...")
            impl_review_code = _run_review_agent(
                role="Code Review Agent",
                review_prompt=(
                    f"Read all changed files carefully. You must review ALL of the following:\n\n"
                    f"WORK PLANNED vs WORK DONE:\n"
                    f"- Compare the plan in `.claude/plan.md` with actual changes — any drift?\n"
                    f"- Were implementation steps followed in the right order?\n\n"
                    f"OUTPUTS:\n"
                    f"- Are outputs complete? Any half-implemented features or missing pieces?\n"
                    f"- Are all referenced functions/classes/imports actually defined?\n\n"
                    f"CODE REVIEW:\n"
                    f"- Code quality — clarity, correctness, error handling, naming\n"
                    f"- Security — no injection, no hardcoded secrets, safe queries\n"
                    f"- Logic — edge cases covered? No obvious bugs or null-pointer risks?\n\n"
                    f"BEHAVIOR REVIEW:\n"
                    f"- Does the code do what the task description asked?\n"
                    f"- Any regressions introduced in existing code paths?\n\n"
                    f"{review_ctx}"
                ),
                extra_env=claude_env,
            )
            activity.heartbeat(f"Phase 2: {council_label} — Behavior review...")
            impl_review_beh = _run_review_agent(
                role="Behavior Review Agent",
                review_prompt=(
                    f"Read the changed files and the original task. You must review ALL of the following:\n\n"
                    f"WORK PLANNED vs WORK DONE:\n"
                    f"- Does the implementation fulfill every requirement stated in the task?\n"
                    f"- Compare the plan steps with git diff — is the delta what was expected?\n\n"
                    f"OUTPUTS:\n"
                    f"- Are all integration wiring steps done? "
                    f"(route mounts, __init__ imports, DB migrations, env vars if needed)\n"
                    f"- Are the outputs testable and deployable as-is?\n\n"
                    f"CODE REVIEW:\n"
                    f"- Are there any obvious runtime errors or broken imports?\n"
                    f"- Does the code respect existing data contracts and API shapes?\n\n"
                    f"BEHAVIOR REVIEW:\n"
                    f"- Does the behavior match the spec? All acceptance criteria met?\n"
                    f"- Any regressions to existing functionality?\n"
                    f"- Does it align with established patterns and good practices in CLAUDE.md?\n\n"
                    f"{review_ctx}"
                ),
                extra_env=claude_env,
            )
            reviews = [impl_review_arch, impl_review_code, impl_review_beh]
            passed, report = _consensus_check(reviews, required=2)
            return passed, report, reviews

        activity.heartbeat("Phase 2: Post-implementation review council (3 agents × 5 dimensions)...")
        impl_passed, impl_consensus, impl_reviews = _run_review_council("Review round 1")
        logger.info("Post-impl review consensus:\n%s", impl_consensus)

        if not impl_passed:
            # Collect all issues from failing reviewers
            all_impl_issues = []
            for r in impl_reviews:
                if not r.approved:
                    for issue in r.issues:
                        all_impl_issues.append(f"[{r.agent_role}] {issue}")

            if all_impl_issues:
                activity.heartbeat("Phase 2: Review failed — running correction pass...")
                issues_text = "\n".join(f"- {i}" for i in all_impl_issues[:12])
                correction_prompt = (
                    f"The code review council found these issues with your implementation.\n"
                    f"Fix ONLY these specific issues. Do not make any other changes.\n\n"
                    f"## Issues to Fix\n{issues_text}\n\n"
                    f"## Original Task\n{task_input.task_description}\n\n"
                    f"## Implementation Plan\n{plan_content[:1000]}"
                )
                correction_system = (
                    "You are fixing specific issues flagged by the code review council. "
                    "Make minimal, targeted changes only. Follow all patterns in CLAUDE.md."
                )
                correction_result = _run_long_command(
                    [
                        "claude", "-p", correction_prompt,
                        "--output-format", "json",
                        "--model", CLAUDE_CODE_MODEL,
                        "--allowedTools", "Edit,Write,Bash,Read,Glob,Grep",
                        "--append-system-prompt", correction_system,
                        "--dangerously-skip-permissions",
                    ],
                    cwd=WORKSPACE,
                    timeout=15 * 60,          # 15 min for correction
                    extra_env=claude_env,
                    heartbeat_message="Correction pass running",
                )
                logger.info("Correction pass complete (exit %s)", correction_result.returncode)

                # Re-run the review council
                activity.heartbeat("Phase 2: Re-reviewing after correction pass...")
                impl_passed, impl_consensus, impl_reviews = _run_review_council("Review round 2")
                logger.info("Post-correction review consensus:\n%s", impl_consensus)

        # Build the review summary section for the PR body
        review_lines = []
        if plan_content:
            review_lines.append("### Phase 1: Plan Review (3 agents)")
            review_lines.append(plan_consensus)
            review_lines.append("")
        review_lines.append("### Phase 2: Implementation Review (3 agents × 5 dimensions)")
        review_lines.append(impl_consensus)
        review_section = "\n".join(review_lines)
        # ── END PHASE 2 ──────────────────────────────────────────────────────

        # 8. Stage, commit and push
        activity.heartbeat("Pushing changes...")
        _run(["git", "add", "-A"])
        commit_msg = _extract_goal(task_input.task_description)[:100].replace('"', '\\"')
        _run(
            ["git", "commit", "-F", "-"],
            input=f"{tag}: {commit_msg}",
        )
        _run(["git", "push", "origin", branch_name])

        # 9. Get changed files
        files_changed = _run(["git", "diff", "--name-only", "main"]).split("\n")
        files_changed = [f for f in files_changed if f]

        # 10. Create PR
        activity.heartbeat("Creating PR...")
        pr_title = f"{tag}: {_extract_goal(task_input.task_description)[:67]}"

        # Gather commit log and claude summary for traceability
        commit_log = _run([
            "git", "log",
            f"main..{branch_name}",
            "--pretty=format:- %h %s",
            "--reverse",
        ])
        claude_summary = ""
        if isinstance(claude_data, dict):
            claude_summary = str(claude_data.get("result", ""))[:1500]
        if not claude_summary:
            claude_summary = claude_output[:1500]
        files_list = "\n".join(f"- `{f}`" for f in files_changed)
        review_flag = "" if impl_passed else "\n> ⚠️ **Review council did not reach full consensus — please review carefully.**\n"

        pr_body = (
            f"## Summary\n\n"
            f"Autonomously implemented by {provider_label}.{review_flag}\n\n"
            f"## Task\n\n"
            f"{task_input.task_description}\n\n"
            f"## {provider_label} Output\n\n"
            f"{claude_summary}\n\n"
            f"## Review Council Results\n\n"
            f"```\n{review_section}\n```\n\n"
            f"## Commits\n\n"
            f"{commit_log}\n\n"
            f"## Files Changed ({len(files_changed)})\n\n"
            f"{files_list}\n\n"
            f"---\n"
            f"*Generated by [AgentProvision Code Agent](https://agentprovision.com)*"
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
                platform=execution_platform,
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
            _run(["git", "checkout", "main"], timeout=10)
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
    platform: str
    message: str
    tenant_id: str
    instruction_md_content: str = ""
    mcp_config: str = ""  # JSON string
    image_b64: str = ""   # Base64-encoded image (optional)
    image_mime: str = ""   # e.g. "image/jpeg"
    session_id: str = ""  # Platform-native session continuity (e.g. Claude --resume)
    model: str = ""        # Override model slug (e.g. "claude-haiku-4-5-20251001"); empty = use env default
    allowed_tools: str = ""  # Comma-separated tool list override; empty = derive from MCP config
    # ---- terminal-full-cli-output (2026-05-16) ----
    # agentprovision chat_sessions.id — used by the SessionEventEmitter
    # to POST stream chunks to /api/v2/internal/sessions/{id}/events.
    # NOT the CLI's native session_id (see `session_id` above).
    chat_session_id: str = ""
    # 1-based chain attempt index, stamped on every emitted chunk so
    # the terminal can tab/colour the fallback CLI without re-deriving
    # from cli_subprocess_started lifecycle events.
    attempt: int = 1


@dataclass
class ChatCliResult:
    response_text: str
    success: bool
    error: Optional[str] = None
    metadata: Optional[dict] = None


@activity.defn
def execute_chat_cli(task_input: ChatCliInput) -> ChatCliResult:
    """Run a conversational CLI turn through the selected provider.

    Sync (not async) so Temporal runs this in a thread-pool executor rather than
    directly on the asyncio event loop.  This keeps blocking subprocess calls and
    time.sleep() from starving workflow-decision tasks that share the same worker.
    """
    logger.info("Executing chat CLI for platform %s, tenant %s", task_input.platform, task_input.tenant_id)
    try:
        # Fetch GitHub token from vault for git operations
        github_token = _fetch_github_token(task_input.tenant_id)
        if github_token:
            os.environ["GITHUB_TOKEN"] = github_token
            # Update git remote with token
            subprocess.run(
                ["git", "remote", "set-url", "origin",
                 f"https://{github_token}@github.com/nomad3/agentprovision-agents.git"],
                cwd=WORKSPACE, capture_output=True,
            )
            # Auth gh CLI
            subprocess.run(
                ["gh", "auth", "login", "--with-token"],
                input=github_token, text=True, cwd=WORKSPACE, capture_output=True,
            )
        else:
            # CROSS-TENANT BLEED GUARD (Codex review, 2026-05-31): this token is
            # written process-globally, so a tenant with NO token must clear any
            # value a PRIOR tenant's turn left behind — otherwise every executor
            # that copies os.environ (codex/gemini/copilot) would inherit it and
            # the system gh credential helper would auth the wrong tenant's clone.
            # (claude.py also strips per-turn; the proper fix is per-turn env for
            # all executors — tracked follow-up. This guards the sequential case.)
            os.environ.pop("GITHUB_TOKEN", None)

        # Persistent session directory per tenant (not temp — survives across calls)
        # Must NOT be under /tmp — Codex refuses to create helper binaries in temp dirs
        session_dir = os.path.join("/home/codeworker/st_sessions", task_input.tenant_id)
        os.makedirs(session_dir, exist_ok=True)
        logger.info("Session directory: %s", session_dir)

        # Save image if provided
        image_path = ""
        if task_input.image_b64 and task_input.image_mime:
            import base64 as b64
            ext = task_input.image_mime.split("/")[-1].replace("jpeg", "jpg")
            image_path = os.path.join(session_dir, f"user_image.{ext}")
            with open(image_path, "wb") as f:
                f.write(b64.b64decode(task_input.image_b64))

        # Per-platform dispatch — single-attempt, no internal cascade.
        #
        # The previous implementation of execute_chat_cli had its own
        # gemini→claude→codex / claude→codex→copilot fallback chain
        # that duplicated the higher-level resolver chain in
        # ``agent_router._resolve_cli_chain`` (PR #245). Since
        # ChatCliWorkflow is only invoked via ``run_agent_session``
        # which is itself wrapped by the resolver chain loop, the
        # internal cascade was redundant — both layers tried the same
        # alternates on quota exhaustion. Yesterday's smoke confirmed:
        # both layers fired on the same request.
        #
        # Now: each branch does ONE attempt and returns. On
        # credit-exhausted / quota errors, the result's `error` field
        # contains the platform's specific quota signal, which the
        # resolver's ``classify_error`` regex (`credit balance`,
        # `rate limit`, `429`, etc.) catches and walks the chain to
        # the next CLI. Cleaner code, halves duplicate dispatch on
        # quota events.
        if task_input.platform == "claude_code":
            logger.info("Using platform: claude_code")
            return _execute_claude_chat(task_input, session_dir)

        if task_input.platform == "codex":
            logger.info("Using platform: codex")
            return _execute_codex_chat(task_input, session_dir, image_path)

        if task_input.platform == "copilot_cli":
            logger.info("Using platform: copilot_cli")
            return _execute_copilot_chat(task_input, session_dir)

        if task_input.platform == "gemini_cli":
            logger.info("Using platform: gemini_cli")
            return _execute_gemini_chat(task_input, session_dir, image_path)

        if task_input.platform == "kimi_k2":
            logger.info("Using platform: kimi_k2")
            return _execute_kimi_chat(task_input, session_dir)

        if task_input.platform == "deepseek":
            logger.info("Using platform: deepseek")
            return _execute_deepseek_chat(task_input, session_dir)

        if task_input.platform == "glm":
            logger.info("Using platform: glm")
            return _execute_glm_chat(task_input, session_dir)


        if task_input.platform == "opencode":
            logger.info("Using platform: opencode")
            return _execute_opencode_chat(task_input, session_dir)

        if task_input.platform == "qwen_code":
            logger.info("Using platform: qwen_code")
            return _execute_qwen_chat(task_input, session_dir)

        if task_input.platform == "aider":
            logger.info("Using platform: aider")
            return _execute_aider_chat(task_input, session_dir)

        if task_input.platform == "goose":
            logger.info("Using platform: goose")
            return _execute_goose_chat(task_input, session_dir)


        return ChatCliResult(response_text="", success=False, error=f"Unsupported platform: {task_input.platform}")

    except Exception as exc:
        logger.exception("Conversational CLI turn failed")
        return ChatCliResult(response_text="", success=False, error=str(exc))




def _execute_codex_code_task(task_input: CodeTaskInput, prompt: str, session_dir: str) -> tuple[str, dict]:
    try:
        creds = _fetch_integration_credentials("codex", task_input.tenant_id)
    except Exception as exc:
        raise RuntimeError(f"Failed to load Codex credentials: {exc}") from exc

    raw_auth = creds.get("auth_json") or creds.get("session_token")
    if not raw_auth:
        raise RuntimeError(_INTEGRATION_NOT_CONNECTED_MESSAGES["codex"])

    try:
        auth_payload = raw_auth if isinstance(raw_auth, dict) else json.loads(raw_auth)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Codex credential must be valid ~/.codex/auth.json contents") from exc

    codex_home = _prepare_codex_home(session_dir, auth_payload, "")
    output_path = os.path.join(session_dir, "codex-code-task-last-message.txt")
    cmd = [
        "codex",
        "exec",
        prompt,
        "--json",
        "--output-last-message",
        output_path,
        "--dangerously-bypass-approvals-and-sandbox",
        "-C",
        WORKSPACE,
        "--add-dir",
        session_dir,
    ]
    result = _run_long_command(
        cmd,
        cwd=WORKSPACE,
        timeout=CODE_TASK_COMMAND_TIMEOUT_SECONDS,
        extra_env={"CODEX_HOME": codex_home},
        heartbeat_message="Codex fallback is still running",
    )
    raw = result.stdout.strip()
    if not raw:
        raise RuntimeError("Codex fallback produced no output")

    response_text = ""
    if os.path.exists(output_path):
        with open(output_path) as f:
            response_text = f.read().strip()
    metadata = _extract_codex_metadata(raw)
    metadata["platform"] = "codex"
    metadata["fallback_from"] = "claude_code"
    return response_text or raw, metadata


def _fetch_github_token(tenant_id: str) -> Optional[str]:
    """Fetch GitHub OAuth token from API credential vault.

    Honors the tenant's ``github_primary_account`` pin (migration 113)
    so code-worker git push / gh PR creation use the same canonical
    repo-ops account that the MCP github tools use. Without this,
    a tenant with multiple github accounts could see code-worker pick
    a non-repo account (e.g. EMU) and have ``git push`` fail with
    "Repository not found" even though the MCP tool successfully
    fetched the same repo via the pinned personal account.

    Resolution:
      1. Look up the pin via ``/internal/connected-accounts/github``.
      2. If a pin exists, fetch the token for that specific account_email.
      3. Otherwise fall back to the legacy ``query.first()`` behavior.
    """
    headers = {"X-Internal-Key": API_INTERNAL_KEY or "dev_mcp_key"}
    primary_account: Optional[str] = None
    try:
        accts_resp = httpx.get(
            f"{API_BASE_URL}/api/v1/oauth/internal/connected-accounts/github",
            params={"tenant_id": tenant_id},
            headers=headers,
            timeout=10,
        )
        if accts_resp.status_code == 200:
            primary_account = (accts_resp.json() or {}).get("primary_account")
    except Exception as e:
        # Non-fatal — fall through to legacy fetch.
        logger.debug("github primary-account lookup failed: %s", e)

    params = {"tenant_id": tenant_id}
    if primary_account:
        params["account_email"] = primary_account

    try:
        resp = httpx.get(
            f"{API_BASE_URL}/api/v1/oauth/internal/token/github",
            params=params,
            headers=headers,
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            return data.get("oauth_token") or data.get("session_token")
        # If the pinned account no longer has credentials (admin
        # disconnected after pinning), fall back to the unpinned fetch.
        if primary_account and resp.status_code == 404:
            logger.warning(
                "github primary_account=%s has no token; falling back to first connected",
                primary_account,
            )
            resp = httpx.get(
                f"{API_BASE_URL}/api/v1/oauth/internal/token/github",
                params={"tenant_id": tenant_id},
                headers=headers,
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("oauth_token") or data.get("session_token")
    except Exception as e:
        logger.warning("Failed to fetch github token: %s", e)
    return None


def _fetch_claude_token(tenant_id: str) -> Optional[str]:
    """Return the Claude Code OAuth subscription token, if any.

    Compatibility shim: callers that only care about the OAuth path
    should keep using this. The newer two-shape path (OAuth *or*
    Anthropic Console API key) is exposed by `_fetch_claude_credential`
    below — the executor branches on the returned `kind` to set the
    right env var (`CLAUDE_CODE_OAUTH_TOKEN` vs `ANTHROPIC_API_KEY`).
    """
    cred = _fetch_claude_credential(tenant_id)
    if cred and cred[1] == "oauth":
        return cred[0]
    return None


def _fetch_claude_credential(tenant_id: str) -> Optional[tuple]:
    """Fetch the active Claude Code credential as `(value, kind)`.

    `kind` is `"oauth"` for the subscription-OAuth flow
    (`credential_key='session_token'`) or `"api_key"` for the Anthropic
    Console fast-path (`credential_key='api_key'`). Returns `None` if
    neither is set. OAuth takes priority when both are present (which
    shouldn't happen once `_revoke_other_claude_credentials` is in
    place, but be defensive).
    """
    try:
        resp = httpx.get(
            f"{API_BASE_URL}/api/v1/oauth/internal/token/claude_code",
            params={"tenant_id": tenant_id},
            headers={"X-Internal-Key": API_INTERNAL_KEY or "dev_mcp_key"},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            oauth = data.get("session_token") or data.get("oauth_token")
            if oauth:
                return (oauth, "oauth")
            api_key = data.get("api_key")
            if api_key:
                return (api_key, "api_key")
    except Exception as e:
        logger.error("Failed to fetch claude credential: %s", e)
    return None


def _prepare_codex_home(session_dir: str, auth_payload: dict, mcp_config_json: str) -> str:
    """Materialize tenant-scoped CODEX_HOME with auth.json and MCP config.toml.

    2026-05-16 incident: Codex still couldn't discover MCP tools after the
    2026-05-12 transport-string fix. Root cause was that Codex CLI's
    default built-in MCP client only supports stdio-launched servers; SSE
    / streamable-HTTP entries are silently ignored unless the top-level
    ``experimental_use_rmcp_client = true`` opt-in is present in
    config.toml. We now emit that flag (gated by the
    ``CODEX_USE_RMCP_CLIENT`` env var, defaulting to ``true``) whenever
    a non-empty ``mcp_config_json`` is materialised — the standalone
    code-execution path at ``_execute_codex_code_task`` passes ``""`` and
    therefore stays a no-op. See
    ``docs/plans/2026-05-16-codex-mcp-tool-access-fix.md``.
    """
    codex_home = os.path.join(session_dir, ".codex")
    os.makedirs(codex_home, exist_ok=True)

    with open(os.path.join(codex_home, "auth.json"), "w") as f:
        json.dump(auth_payload, f)

    # ── top-level Codex config keys (must precede any [section]) ──────
    use_rmcp_client = os.environ.get("CODEX_USE_RMCP_CLIENT", "true").lower() == "true"
    config_lines: list[str] = []
    if mcp_config_json and use_rmcp_client:
        # Opt in to the Rust MCP client so SSE / streamable_http
        # ``[mcp_servers.*]`` entries below are actually honoured.
        # Without this, the default stdio-only client silently drops
        # them and Codex reports zero MCP tools.
        config_lines.append("experimental_use_rmcp_client = true")
        config_lines.append("")

    config_lines.extend([
        f'[projects."{WORKSPACE if os.path.isdir(WORKSPACE) else session_dir}"]',
        'trust_level = "trusted"',
        "",
        f'[projects."{session_dir}"]',
        'trust_level = "trusted"',
    ])

    if mcp_config_json:
        config_lines.extend(_codex_mcp_config_lines(mcp_config_json))

    if mcp_config_json:
        try:
            _servers = list(json.loads(mcp_config_json).get("mcpServers", {}).keys())
        except (TypeError, ValueError, json.JSONDecodeError):
            _servers = ["<unparseable>"]
        logger.info(
            "codex MCP config materialised: rmcp_client=%s servers=%s session=%s",
            use_rmcp_client, _servers, os.path.basename(session_dir),
        )

    with open(os.path.join(codex_home, "config.toml"), "w") as f:
        f.write("\n".join(config_lines).strip() + "\n")

    return codex_home


def _codex_mcp_config_lines(mcp_config_json: str) -> list[str]:
    """Convert the shared MCP JSON config into Codex config.toml entries.

    History:

    * **2026-05-12** — bug: this helper hardcoded
      ``transport = "streamable_http"`` regardless of the source. The
      shared MCP config (built by
      ``cli_session_manager._build_mcp_config()``) emits ``"type":
      "sse"`` for the in-cluster ``agentprovision`` server, so Codex
      spoke the wrong protocol and discovered zero tools while Gemini
      worked on the same config. Fix: honour the source's ``type`` and
      map it through to Codex's ``transport`` key.

    * **2026-05-16 (first attempt — PR #516)** — even with the right
      transport string, Codex CLI's built-in MCP client only handles
      stdio. SSE / streamable_http entries are silently dropped unless
      the top-level ``experimental_use_rmcp_client = true`` flag is set
      in ``config.toml``. ``_prepare_codex_home`` now emits that flag
      (gated by env-var ``CODEX_USE_RMCP_CLIENT`` for rollback).

    * **2026-05-16 (this change — transport mismatch fix)** — turning
      on the rmcp client surfaced the *next* layer: rmcp implements
      only ``Stdio`` and ``StreamableHttp`` — there is no SSE variant
      in ``McpServerTransportConfig`` upstream. A ``transport = "sse"``
      TOML key is silently dropped by serde's untagged enum; rmcp's
      ``StreamableHttp`` transport then POSTs JSON-RPC to whatever URL
      it was given. Our ``/sse`` route is GET-only → 405 → rmcp worker
      tears down. Server-side fix: mount streamable-HTTP alongside
      legacy SSE on the same mcp-tools container (see
      ``apps/mcp-server/src/mcp_serve.py::build_app``). Client-side fix
      (here): when emitting a Codex entry for an SSE-flavoured server,
      rewrite the URL from ``…/sse`` to ``…/mcp/`` AND emit
      ``transport = "streamable_http"`` so the TOML is honest about
      what rmcp is actually going to speak. Claude Code and Gemini
      configs are untouched — they keep using ``/sse``. See
      ``docs/plans/2026-05-16-codex-mcp-transport-mismatch-research.md``.
    """
    data = json.loads(mcp_config_json)
    servers = data.get("mcpServers") or {}
    lines: list[str] = []
    for server_name, config in servers.items():
        if not isinstance(config, dict):
            continue
        # Map .claude.json `type` field → Codex `transport`. The rmcp
        # client only speaks stdio + streamable-HTTP, so SSE-flavoured
        # source entries are rewritten to streamable_http and routed at
        # the parent server's ``/mcp/`` mount (see mcp_serve.build_app).
        raw_type = (config.get("type") or "sse").lower()
        url = str(config.get("url") or "")
        if raw_type in ("sse", "http-sse"):
            transport = "streamable_http"
            url = _rewrite_sse_to_streamable_http_url(url)
        elif raw_type in ("http", "streamable_http", "streamable-http"):
            transport = "streamable_http"
        else:
            transport = raw_type  # forward-compat: trust the source
        lines.append("")
        lines.append(f"[mcp_servers.{server_name}]")
        lines.append(f'transport = "{_toml_escape(transport)}"')
        if url:
            lines.append(f'url = "{_toml_escape(url)}"')
        headers = config.get("headers") or {}
        if headers:
            lines.append(f"http_headers = {_toml_inline_table(headers)}")
    return lines


# In-cluster MCP server hostnames we know expose the dual-transport
# layout (legacy SSE at ``/sse`` + streamable-HTTP at ``/mcp/``). Only
# these hosts get URL-rewritten for Codex; external partner SSE servers
# (tenant connectors at arbitrary domains) pass through unchanged so
# their URLs are never silently mangled. External SSE servers will
# still fail under Codex's rmcp client (which can't speak SSE), but the
# failure surfaces clearly instead of as a 404 on a wrong-shaped URL.
_INCLUSTER_MCP_HOSTS = frozenset({"mcp-tools", "agentprovision-mcp", "mcp"})


def _is_incluster_mcp_url(url: str) -> bool:
    """Return True if ``url``'s hostname matches a known in-cluster MCP
    service. Defensive against malformed URLs — a parse failure means
    "not in-cluster, leave it alone"."""
    try:
        host = urllib.parse.urlparse(url).hostname or ""
    except Exception:  # noqa: BLE001 — urlparse never raises in practice, but be safe
        return False
    return host in _INCLUSTER_MCP_HOSTS


def _rewrite_sse_to_streamable_http_url(url: str) -> str:
    """Rewrite an in-cluster SSE URL onto the streamable-HTTP mount.

    The mcp-tools server (FastMCP) exposes BOTH transports on the same
    port: legacy SSE at ``/sse`` (+ ``/messages/``) for Claude/Gemini,
    streamable-HTTP at ``/mcp/`` for Codex's rmcp client. The shared
    MCP config emits the SSE URL (because that's what Claude+Gemini
    need); for Codex we strip the ``/sse`` suffix and substitute
    ``/mcp/``.

    Scope: ONLY rewrites URLs whose hostname is in
    ``_INCLUSTER_MCP_HOSTS``. External partner SSE servers (e.g. a
    tenant connector at ``partner.example.com``) pass through
    unchanged — they speak SSE and Codex's rmcp will fail on them
    anyway, but the URL won't be silently mangled.

    Idempotent + safe on URLs that don't end in ``/sse``.
    """
    if not url:
        return url
    if not _is_incluster_mcp_url(url):
        return url
    trimmed = url.rstrip("/")
    if trimmed.endswith("/sse"):
        return trimmed[: -len("/sse")] + "/mcp/"
    return url


def _toml_inline_table(values: dict) -> str:
    items = [f'"{_toml_escape(str(key))}" = "{_toml_escape(str(value))}"' for key, value in values.items()]
    return "{ " + ", ".join(items) + " }"


def _toml_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _extract_codex_last_message(raw_output: str) -> str:
    """Best-effort fallback when --output-last-message is unavailable."""
    for line in reversed(raw_output.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            msg = event.get("last_agent_message") or event.get("message")
            if isinstance(msg, str) and msg.strip():
                return msg.strip()
    return ""


def _extract_codex_metadata(raw_output: str) -> dict:
    """Extract a minimal metadata snapshot from Codex JSONL events."""
    metadata = {"input_tokens": 0, "output_tokens": 0, "model": None}
    for line in raw_output.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        if metadata["model"] is None and isinstance(event.get("model"), str):
            metadata["model"] = event["model"]
        token_usage = event.get("token_usage") or event.get("total_token_usage") or {}
        if isinstance(token_usage, dict):
            metadata["input_tokens"] = token_usage.get("input_tokens", metadata["input_tokens"])
            metadata["output_tokens"] = token_usage.get("output_tokens", metadata["output_tokens"])
        if event.get("type") == "session_configured":
            metadata["model"] = event.get("model", metadata["model"])
    return metadata


def _prepare_gemini_home(home_dir: str, auth_payload: dict, mcp_config_json: str) -> str:
    """Materialize tenant-scoped GEMINI_HOME for Gemini CLI 0.37.1+.

    Writes the exact files that gemini-cli reads on disk:
      - .gemini/oauth_creds.json (the OAuth tokens — NOT credentials.json)
      - .gemini/settings.json (selectedType: oauth-personal)
      - .gemini/projects.json (must exist to avoid rename errors)
      - .gemini/google_accounts.json (active account email)

    The auth_payload should be the FULL oauth_creds.json blob from the
    vault (key 'oauth_creds'), which preserves all fields exactly as
    Gemini CLI wrote them when the user authenticated. Do NOT inject
    our platform's client_id — the refresh_token is bound to Gemini CLI's
    own client_id (681255809395-...) and refresh will fail otherwise.

    ``home_dir`` is the per-tenant HOME (task #267) — was named
    ``session_dir`` historically when this wrote into worker-private
    scratch; renamed to reflect that the .gemini/ tree now lives on the
    persistent workspaces volume mounted on both api and code-worker.
    """
    gemini_home = os.path.join(home_dir, ".gemini")
    os.makedirs(gemini_home, exist_ok=True)

    # If we have the full oauth_creds blob, write it as-is (preserves
    # client_id binding). Otherwise fall back to constructing from individual
    # fields (won't work for refresh, but might work while access_token is fresh).
    oauth_creds_blob = auth_payload.get("oauth_creds")
    if oauth_creds_blob:
        # oauth_creds is stored as a JSON string in the vault
        if isinstance(oauth_creds_blob, str):
            try:
                oauth_creds = json.loads(oauth_creds_blob)
            except json.JSONDecodeError:
                oauth_creds = None
        else:
            oauth_creds = oauth_creds_blob
    else:
        oauth_creds = None

    if oauth_creds is None:
        # Fallback: synthesize from individual tokens with Gemini CLI's
        # public client_id. Refresh tokens are bound to the issuing client,
        # so this only works while the access_token is still fresh — the
        # proper path is for the tenant to run the /gemini-cli-auth flow,
        # which stores the full oauth_creds blob.
        import time
        GEMINI_CLI_CLIENT_ID = "681255809395-oo8ft2oprdrnp9e3aqf6av3hmdib135j.apps.googleusercontent.com"
        expiry_ms = int((time.time() + 3600) * 1000)
        oauth_creds = {
            "access_token": auth_payload.get("access_token"),
            "refresh_token": auth_payload.get("refresh_token"),
            "scope": (
                "https://www.googleapis.com/auth/cloud-platform "
                "https://www.googleapis.com/auth/userinfo.email "
                "https://www.googleapis.com/auth/userinfo.profile openid"
            ),
            "token_type": "Bearer",
            "expiry_date": expiry_ms,
            "client_id": GEMINI_CLI_CLIENT_ID,
            "client_secret": "",
        }

    # Write the credentials file with the exact filename Gemini CLI 0.37.1 reads.
    # Older versions read credentials.json — write both for safety.
    # chmod 0o600: now that .gemini/ lives on the persistent workspaces
    # volume mounted on both api and code-worker, default 0o644 from
    # `open(..., "w")` would leave refresh tokens world-readable inside
    # the container. Tighten to owner-only (review B1 on PR #540).
    for fname in ("oauth_creds.json", "credentials.json"):
        path = os.path.join(gemini_home, fname)
        with open(path, "w") as f:
            json.dump(oauth_creds, f, indent=2)
        os.chmod(path, 0o600)

    # Pre-create projects.json (avoids rename ENOENT errors on startup)
    with open(os.path.join(gemini_home, "projects.json"), "w") as f:
        json.dump({"projects": {}}, f, indent=2)

    # Pre-set the active Google account — same 0o600 treatment as
    # oauth_creds.json: the email is PII and the file sits next to the
    # OAuth blob, so apply matching mode (review B1 on PR #540).
    active_email = auth_payload.get("email") or "user@gemini"
    google_accounts_path = os.path.join(gemini_home, "google_accounts.json")
    with open(google_accounts_path, "w") as f:
        json.dump({"active": active_email, "old": []}, f, indent=2)
    os.chmod(google_accounts_path, 0o600)

    # settings.json with selectedType: oauth-personal so the CLI doesn't
    # prompt for an auth method on first run. Avoid `enforcedType` — it
    # can lock the user out if the schema disagrees with what's on disk.
    settings = {
        "security": {
            "auth": {
                "selectedType": "oauth-personal"
            }
        },
        "telemetry": {
            "enabled": False
        }
    }
    if mcp_config_json:
        try:
            mcp_data = json.loads(mcp_config_json)
            servers = mcp_data.get("mcpServers", {})
            settings["mcpServers"] = servers
        except json.JSONDecodeError:
            pass
            
    with open(os.path.join(gemini_home, "settings.json"), "w") as f:
        json.dump(settings, f, indent=2)

    return gemini_home


def _prepare_copilot_home(session_dir: str, mcp_config_json: str) -> str:
    """Prepare a Copilot config dir (writes mcp-config.json) and return its path.

    The returned path is meant to be exported as ``COPILOT_HOME`` so the
    CLI reads its configuration from this isolated per-session directory
    instead of the user's real $HOME/.copilot. Per the official docs:

      COPILOT_HOME: override the directory where configuration and state
                    files are stored; defaults to $HOME/.copilot.
    """
    copilot_dir = os.path.join(session_dir, ".copilot")
    os.makedirs(copilot_dir, exist_ok=True)

    try:
        mcp_data = json.loads(mcp_config_json)
        config = {
            "servers": mcp_data.get("mcpServers", mcp_data.get("servers", {}))
        }
    except Exception:
        config = {"servers": {}}

    with open(os.path.join(copilot_dir, "mcp-config.json"), "w") as f:
        json.dump(config, f, indent=2)

    return copilot_dir


def _prepare_gemini_home_apikey(home_dir: str, mcp_config_json: str) -> str:

    """Minimal GEMINI_HOME for API key auth — no credentials.json needed.

    ``home_dir`` is the per-tenant HOME (task #267) — was named
    ``session_dir`` historically; renamed for parity with
    ``_prepare_gemini_home`` now that .gemini/ lives on the persistent
    workspaces volume.
    """
    gemini_home = os.path.join(home_dir, ".gemini")
    os.makedirs(gemini_home, exist_ok=True)

    projects_path = os.path.join(gemini_home, "projects.json")
    if not os.path.exists(projects_path):
        with open(projects_path, "w") as f:
            json.dump({"projects": {}}, f, indent=2)

    settings = {"security": {"auth": {"enforcedType": "api-key", "selectedType": "api-key"}}}
    if mcp_config_json:
        try:
            mcp_data = json.loads(mcp_config_json)
            settings["mcpServers"] = mcp_data.get("mcpServers", {})
        except json.JSONDecodeError:
            pass
    with open(os.path.join(gemini_home, "settings.json"), "w") as f:
        json.dump(settings, f, indent=2)

    return gemini_home


@workflow.defn
class ChatCliWorkflow:
    """Temporal workflow for chat CLI sessions."""

    @workflow.run
    async def run(self, task_input: ChatCliInput) -> ChatCliResult:
        return await workflow.execute_activity(
            execute_chat_cli,
            task_input,
            start_to_close_timeout=timedelta(minutes=150),
            schedule_to_close_timeout=timedelta(minutes=165),
            heartbeat_timeout=timedelta(minutes=5),
            retry_policy=RetryPolicy(maximum_attempts=2),
        )


@workflow.defn
class CodeTaskWorkflow:
    """Temporal workflow for executing a code task."""

    @workflow.run
    async def run(self, task_input: CodeTaskInput) -> CodeTaskResult:
        return await workflow.execute_activity(
            execute_code_task,
            task_input,
            start_to_close_timeout=timedelta(minutes=150),
            schedule_to_close_timeout=timedelta(minutes=165),
            heartbeat_timeout=timedelta(seconds=300),
        )


@activity.defn
async def finalize_provider_council(tenant_id: str, experience_id: str, result_json: str) -> bool:
    """Internal activity to record final council decision back to API.

    DEAD CODE (F9 review 2026-05-22): the
    ``/api/v1/rl/internal/experience/{id}/finalize`` endpoint this
    activity calls **does not exist** in apps/api. Every call returns
    False silently. Resurrecting this caller without re-applying the
    F9 X-Tenant-Id header hardening would reproduce the exact vuln
    F9 closed — so we pre-emptively send the header even though the
    target endpoint is missing today. A follow-up should either
    delete this activity + its workflow caller, or implement the
    finalize endpoint with the same tenant-scoped query pattern.
    """
    try:
        resp = httpx.post(
            f"{API_BASE_URL}/api/v1/rl/internal/experience/{experience_id}/finalize",
            headers={
                "X-Internal-Key": API_INTERNAL_KEY or "dev_mcp_key",
                # F9 P1: if/when this caller is resurrected, the
                # API-side finalize endpoint must require X-Tenant-Id
                # matching the body (same gate as the live endpoints).
                "X-Tenant-Id": tenant_id,
            },
            json=json.loads(result_json),
            timeout=10,
        )
        return resp.status_code == 200
    except Exception:
        return False



@dataclass
class ProviderCouncilInput:
    tenant_id: str
    user_message: str
    providers: List[str]
    agent_slug: str
    channel: str
    original_experience_id: Optional[str] = None


@dataclass
class ProviderReview:
    provider: str
    approved: bool
    verdict: str
    score: int
    issues: List[str]
    suggestions: List[str]
    summary: str
    response_text: str = ""
    total_cost: float = 0.0
    total_tokens: int = 0.0


@dataclass
class ProviderCouncilResult:
    consensus: bool
    provider_agreement: float
    reviews: List[ProviderReview]
    disagreements: List[str]
    recommended_platform: str
    total_cost: float
    total_tokens: int


@workflow.defn
class ProviderCouncilWorkflow:
    """Temporal workflow for parallel provider consensus."""

    @workflow.run
    async def run(self, input: ProviderCouncilInput) -> ProviderCouncilResult:
        # Initial logging
        logger.info("Starting ProviderCouncil for tenant %s", input.tenant_id)
        return ProviderCouncilResult(
            consensus=True,
            provider_agreement=1.0,
            reviews=[],
            disagreements=[],
            recommended_platform="claude",
            total_cost=0.0,
            total_tokens=0
        )

@activity.defn
async def review_with_claude(input: ProviderCouncilInput, session_dir: str) -> ProviderReview:
    return ProviderReview(provider="claude", approved=True, verdict="APPROVED", score=100, issues=[], suggestions=[], summary="OK")

@activity.defn
async def review_with_codex(input: ProviderCouncilInput, session_dir: str) -> ProviderReview:
    return ProviderReview(provider="codex", approved=True, verdict="APPROVED", score=100, issues=[], suggestions=[], summary="OK")

@activity.defn
async def review_with_local_gemma(input: ProviderCouncilInput, session_dir: str) -> ProviderReview:
    return ProviderReview(provider="local_gemma", approved=True, verdict="APPROVED", score=100, issues=[], suggestions=[], summary="OK")

@workflow.defn
class ProviderReviewWorkflow:
    @workflow.run
    async def run(self, input: ProviderCouncilInput) -> ProviderCouncilResult:
        return ProviderCouncilResult(consensus=True, provider_agreement=1.0, reviews=[], disagreements=[], recommended_platform="claude", total_cost=0.0, total_tokens=0)


# ─── FanoutChatCliWorkflow — Phase 1 ship of #177 ─────────────────────
#
# Replaces the in-memory stub in `apps/api/app/api/v1/tasks_fanout.py`
# when `USE_REAL_FANOUT_WORKFLOW=true` is set on the api pod. The stub
# stays as the demo-fallback so disabling the flag rolls back instantly.
#
# Shape:
#   - One parent workflow (`FanoutChatCliWorkflow`) is dispatched to
#     the `agentprovision-code` queue by the API on `POST /run` when
#     the request has a non-empty `fanout` list.
#   - The parent spawns N child `ChatCliWorkflow` runs in parallel via
#     `execute_child_workflow` (round-1 review M1). Each child gets
#     the same prompt with `platform = <fanout entry>`.
#   - Merge mode `council` concatenates the child outputs under a
#     consensus-style header (the real meta-adjudicator with semantic
#     consensus / disagreement scoring is the next-PR work — out of
#     scope here per the user's option-(a) decision).
#   - Merge mode `first-wins` returns the first successful child and
#     cancels the remaining children (round-1 review H2: real cancel,
#     not await-all-then-pick-first).
#   - Merge mode `all` returns every child verbatim under a children
#     array (no merge).


@dataclass
class FanoutChatCliInput:
    prompt: str
    tenant_id: str
    providers: List[str]           # the fanout list — N parallel children
    merge: str = "council"         # council | first-wins | all
    # Round-1 review B1: `agent_id` is accepted on the wire for forward
    # compatibility, but is NOT propagated to the child ChatCliInput
    # because ChatCliInput has no `agent_id` field. The workflow logs a
    # warning when set; full plumbing is the next-PR follow-up so the
    # leaf CLI can run under the right agent persona / tools / memory.
    agent_id: Optional[str] = None
    session_id: Optional[str] = None
    # Forwarded to each ChatCliInput child; empty = code-worker default
    instruction_md_content: str = ""
    mcp_config: str = ""
    model: str = ""
    allowed_tools: str = ""


@dataclass
class FanoutChildResult:
    provider: str
    response_text: str
    success: bool
    error: Optional[str] = None


@dataclass
class FanoutChatCliResult:
    merge_mode: str
    merged_text: str
    children: List[FanoutChildResult]
    success: bool                  # True iff at least one child succeeded


@workflow.defn
class FanoutChatCliWorkflow:
    """Parallel-fanout parent workflow for `alpha run --fanout`.

    Spawns one `ChatCliWorkflow` child per provider in
    `input.providers`. Awaits all (council / all) or first-non-failure
    (first-wins). Returns a `FanoutChatCliResult` the API surfaces on
    `GET /tasks-fanout/{id}/status`.
    """

    @workflow.run
    async def run(self, input: FanoutChatCliInput) -> FanoutChatCliResult:
        # Round-1 review N3 + B1: asyncio already in passthrough modules;
        # local import keeps Temporal sandbox happy. Surface a warning
        # when agent_id is set so operators don't silently ship wrong-
        # agent behavior after flipping USE_REAL_FANOUT_WORKFLOW.
        import asyncio
        from asyncio import FIRST_COMPLETED, gather, wait

        if input.agent_id:
            workflow.logger.warning(
                "FanoutChatCliWorkflow received agent_id=%s but the "
                "current implementation does not propagate it to ChatCliInput. "
                "Children will run with the code-worker's tenant default agent. "
                "Follow-up: add agent_id field to ChatCliInput.",
                input.agent_id,
            )

        async def _child(provider: str) -> FanoutChildResult:
            child_input = ChatCliInput(
                platform=provider,
                message=input.prompt,
                tenant_id=input.tenant_id,
                instruction_md_content=input.instruction_md_content,
                mcp_config=input.mcp_config,
                session_id=input.session_id or "",
                model=input.model,
                allowed_tools=input.allowed_tools,
            )
            try:
                # Round-1 review L1: explicit task_queue so a future
                # parent-on-different-queue refactor doesn't silently
                # ship children to a worker that doesn't register
                # ChatCliWorkflow. Round-1 review M4: explicit
                # execution_timeout matching cli_session_manager's
                # default cap; otherwise a stuck child wedges the
                # parent indefinitely.
                child_result: ChatCliResult = await workflow.execute_child_workflow(
                    ChatCliWorkflow.run,
                    child_input,
                    task_queue="agentprovision-code",
                    execution_timeout=timedelta(minutes=180),
                    retry_policy=RetryPolicy(maximum_attempts=2),
                )
                return FanoutChildResult(
                    provider=provider,
                    response_text=child_result.response_text,
                    success=child_result.success,
                    error=child_result.error,
                )
            except Exception as exc:  # noqa: BLE001
                # Per-child isolation — one provider's failure does not
                # abort the others. Matches the provider-council
                # `_safe_review` posture.
                return FanoutChildResult(
                    provider=provider,
                    response_text="",
                    success=False,
                    error=str(exc),
                )

        if input.merge == "first-wins":
            # Round-2 H2 hardening: use `start_child_workflow` returning
            # `ChildWorkflowHandle` objects so cancellation propagates
            # to the REMOTE workflow execution. The previous
            # `execute_child_workflow` + `asyncio.ensure_future`
            # approach only cancelled the local awaiter — the child
            # workflows kept running and billing through their full
            # `execution_timeout`. With handles, `await handle.cancel()`
            # signals the running child workflow to stop.
            handles = []
            for p in input.providers:
                child_input = ChatCliInput(
                    platform=p,
                    message=input.prompt,
                    tenant_id=input.tenant_id,
                    instruction_md_content=input.instruction_md_content,
                    mcp_config=input.mcp_config,
                    session_id=input.session_id or "",
                    model=input.model,
                    allowed_tools=input.allowed_tools,
                )
                h = await workflow.start_child_workflow(
                    ChatCliWorkflow.run,
                    child_input,
                    task_queue="agentprovision-code",
                    execution_timeout=timedelta(minutes=180),
                    retry_policy=RetryPolicy(maximum_attempts=2),
                )
                handles.append((p, h))

            # Round-3 B3-1 + L3-1: ChildWorkflowHandle inherits from
            # asyncio.Task — pass directly to wait(), no create_task
            # wrapping. The previous `asyncio.create_task(h)` raised
            # TypeError at runtime (Task vs coroutine).
            tasks_by_provider = {p: h for p, h in handles}
            done, pending = await wait(
                tasks_by_provider.values(), return_when=FIRST_COMPLETED
            )

            # Round-3 B3-2 + L3-2: handle.cancel() is sync (inherited
            # from asyncio.Task) and schedules a Temporal cancel
            # command at the next decision task; do NOT `await` it.
            # Best-effort — the child workflow must observe cancel at
            # its next decision point.
            for p, t in tasks_by_provider.items():
                if t in pending:
                    h = next(h for prov, h in handles if prov == p)
                    try:
                        h.cancel()
                    except Exception:  # noqa: BLE001
                        pass

            children: List[FanoutChildResult] = []
            for p, t in tasks_by_provider.items():
                if t in done:
                    try:
                        child_result: ChatCliResult = t.result()
                        children.append(
                            FanoutChildResult(
                                provider=p,
                                response_text=child_result.response_text,
                                success=child_result.success,
                                error=child_result.error,
                            )
                        )
                    except Exception as exc:  # noqa: BLE001
                        children.append(
                            FanoutChildResult(
                                provider=p,
                                response_text="",
                                success=False,
                                error=str(exc),
                            )
                        )
                else:
                    children.append(
                        FanoutChildResult(
                            provider=p,
                            response_text="",
                            success=False,
                            error="cancelled-by-first-wins",
                        )
                    )
            any_success = any(c.success for c in children)
            first_ok = next((c for c in children if c.success), None)
            text = first_ok.response_text if first_ok else "All providers failed."
            return FanoutChatCliResult(
                merge_mode="first-wins",
                merged_text=text,
                children=children,
                success=any_success,
            )

        # `council` and `all` paths: dispatch all children in parallel
        # and await all of them.
        children = await gather(*(_child(p) for p in input.providers))
        any_success = any(c.success for c in children)

        if input.merge == "all":
            # Round-1 review N4: when `c.error is None` use the literal
            # 'unknown', never 'None' which leaks through the f-string.
            sections = []
            for c in children:
                body = c.response_text or "(empty)"
                if c.success:
                    sections.append(f"--- {c.provider} ---\n{body}")
                else:
                    err = c.error or "unknown"
                    sections.append(
                        f"--- {c.provider} ---\n{body}\n(error: {err})"
                    )
            return FanoutChatCliResult(
                merge_mode="all",
                merged_text="\n\n".join(sections),
                children=children,
                success=any_success,
            )

        # Default: council (placeholder until real meta-adjudicator).
        # Mark this output as council-style so a UI can render it as
        # such; the actual semantic-consensus / disagreement scoring
        # ships in the follow-up PR.
        sections = []
        for c in children:
            if c.success:
                body = c.response_text
            else:
                err = c.error or "unknown"
                body = f"(failed: {err})"
            sections.append(f"--- {c.provider} ---\n{body}")
        merged = (
            "[council mode — concat aggregation; meta-adjudicator TBD]\n\n"
            + "\n\n".join(sections)
        )
        return FanoutChatCliResult(
            merge_mode="council",
            merged_text=merged,
            children=children,
            success=any_success,
        )
