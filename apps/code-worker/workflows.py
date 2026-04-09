"""Temporal workflow and activities for Claude Code tasks."""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

import httpx
from temporalio import activity, workflow
from temporalio.common import RetryPolicy

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
        tools.append("mcp__servicetsunami__*")
    if not any("mcp__" in t for t in tools):
        tools.append("mcp__servicetsunami__*")
    return ",".join(tools)


WORKSPACE = "/workspace"
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
API_INTERNAL_KEY = os.environ.get("API_INTERNAL_KEY", "").strip()
API_BASE_URL = os.environ.get("API_BASE_URL", "http://servicetsunami-api").strip()
CODE_TASK_COMMAND_TIMEOUT_SECONDS = 45 * 60
CODE_TASK_ACTIVITY_TIMEOUT_MINUTES = 120
CODE_TASK_SCHEDULE_TIMEOUT_MINUTES = 150
CODE_TASK_HEARTBEAT_SECONDS = 240
CLAUDE_CODE_MODEL = os.environ.get("CLAUDE_CODE_MODEL", "sonnet").strip() or "sonnet"
CLAUDE_CREDIT_ERROR_PATTERNS = (
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

CODEX_CREDIT_ERROR_PATTERNS = (
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


@dataclass
class AgentReview:
    agent_role: str
    approved: bool
    verdict: str          # "APPROVED" | "REJECTED" | "CONDITIONAL"
    issues: list
    suggestions: list
    summary: str


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


def _fetch_claude_token(tenant_id: str) -> str:
    """Fetch the Claude Code session token from the API's internal endpoint."""
    data = _fetch_integration_credentials("claude_code", tenant_id)

    token = data.get("session_token")
    if not token:
        raise RuntimeError(f"No session_token in response: {data}")
    return token


def _is_claude_credit_exhausted(error_text: str) -> bool:
    text = (error_text or "").lower()
    return any(pattern in text for pattern in CLAUDE_CREDIT_ERROR_PATTERNS)


def _is_codex_credit_exhausted(error_text: str) -> bool:
    text = (error_text or "").lower()
    return any(pattern in text for pattern in CODEX_CREDIT_ERROR_PATTERNS)


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
        status = _run("git status --porcelain")
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


@dataclass
class ChatCliResult:
    response_text: str
    success: bool
    error: Optional[str] = None
    metadata: Optional[dict] = None


@activity.defn
async def execute_chat_cli(task_input: ChatCliInput) -> ChatCliResult:
    """Run a conversational CLI turn through the selected provider."""
    try:
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
        # Must NOT be under /tmp — Codex refuses to create helper binaries in temp dirs
        session_dir = os.path.join("/home/codeworker/st_sessions", task_input.tenant_id)
        os.makedirs(session_dir, exist_ok=True)

        # Save image if provided
        image_path = ""
        if task_input.image_b64 and task_input.image_mime:
            import base64 as b64
            ext = task_input.image_mime.split("/")[-1].replace("jpeg", "jpg")
            image_path = os.path.join(session_dir, f"user_image.{ext}")
            with open(image_path, "wb") as f:
                f.write(b64.b64decode(task_input.image_b64))

        if task_input.platform == "claude_code":
            claude_result = _execute_claude_chat(task_input, session_dir)
            if claude_result.success:
                return claude_result

            # Claude Code failed — try Codex first as fallback
            logger.warning("Claude Code failed (%s), falling back to Codex", claude_result.error[:200] if claude_result.error else "unknown")
            codex_result = _execute_codex_chat(task_input, session_dir, image_path)
            if codex_result.success:
                meta = dict(codex_result.metadata or {})
                meta["fallback_from"] = "claude_code"
                meta["requested_platform"] = "claude_code"
                meta["claude_error"] = (claude_result.error or "")[:200]
                codex_result.metadata = meta
                return codex_result

            # Codex also failed — try Gemini CLI as final fallback
            logger.warning("Codex fallback failed (%s), falling back to Gemini CLI", codex_result.error[:200] if codex_result.error else "unknown")
            gemini_result = _execute_gemini_chat(task_input, session_dir, image_path)
            if gemini_result.success:
                meta = dict(gemini_result.metadata or {})
                meta["fallback_from"] = "codex"
                meta["requested_platform"] = "claude_code"
                meta["claude_error"] = (claude_result.error or "")[:200]
                meta["codex_error"] = (codex_result.error or "")[:200]
                gemini_result.metadata = meta
                return gemini_result

            return ChatCliResult(
                response_text="",
                success=False,
                error=f"Claude Code failed: {claude_result.error}. Codex fallback failed: {codex_result.error}. Gemini CLI fallback failed: {gemini_result.error}",
            )

        if task_input.platform == "codex":
            codex_result = _execute_codex_chat(task_input, session_dir, image_path)
            if codex_result.success:
                return codex_result

            # Codex failed — fallback to Gemini CLI
            logger.warning("Codex failed (%s), falling back to Gemini CLI", codex_result.error[:200] if codex_result.error else "unknown")
            gemini_result = _execute_gemini_chat(task_input, session_dir, image_path)
            if gemini_result.success:
                meta = dict(gemini_result.metadata or {})
                meta["fallback_from"] = "codex"
                meta["requested_platform"] = "codex"
                meta["codex_error"] = (codex_result.error or "")[:200]
                gemini_result.metadata = meta
                return gemini_result

            # Gemini failed — try Claude Code
            logger.warning("Gemini fallback failed (%s), falling back to Claude Code", gemini_result.error[:200] if gemini_result.error else "unknown")
            task_input.model = ""
            claude_result = _execute_claude_chat(task_input, session_dir)
            if claude_result.success:
                meta = dict(claude_result.metadata or {})
                meta["fallback_from"] = "gemini_cli"
                meta["requested_platform"] = "codex"
                meta["codex_error"] = (codex_result.error or "")[:200]
                meta["gemini_error"] = (gemini_result.error or "")[:200]
                claude_result.metadata = meta
                return claude_result

            return ChatCliResult(
                response_text="",
                success=False,
                error=f"Codex failed: {codex_result.error}. Gemini fallback failed: {gemini_result.error}. Claude fallback failed: {claude_result.error}",
            )

        if task_input.platform == "gemini_cli":
            gemini_result = _execute_gemini_chat(task_input, session_dir, image_path)
            if gemini_result.success:
                return gemini_result

            # Gemini failed — fallback to Claude Code
            logger.warning("Gemini CLI failed (%s), falling back to Claude Code", gemini_result.error[:200] if gemini_result.error else "unknown")
            claude_result = _execute_claude_chat(task_input, session_dir)
            if claude_result.success:
                meta = dict(claude_result.metadata or {})
                meta["fallback_from"] = "gemini_cli"
                meta["requested_platform"] = "gemini_cli"
                meta["gemini_error"] = (gemini_result.error or "")[:200]
                claude_result.metadata = meta
                return claude_result

            # Claude failed — try Codex
            logger.warning("Claude fallback failed (%s), falling back to Codex", claude_result.error[:200] if claude_result.error else "unknown")
            codex_result = _execute_codex_chat(task_input, session_dir, image_path)
            if codex_result.success:
                meta = dict(codex_result.metadata or {})
                meta["fallback_from"] = "claude_code"
                meta["requested_platform"] = "gemini_cli"
                meta["gemini_error"] = (gemini_result.error or "")[:200]
                meta["claude_error"] = (claude_result.error or "")[:200]
                codex_result.metadata = meta
                return codex_result

            return ChatCliResult(
                response_text="",
                success=False,
                error=f"Gemini CLI failed: {gemini_result.error}. Claude fallback failed: {claude_result.error}. Codex fallback failed: {codex_result.error}",
            )
        if task_input.platform == "opencode":
            return _execute_opencode_chat(task_input, session_dir)
        return ChatCliResult(
            response_text="",
            success=False,
            error=f"Unsupported CLI platform '{task_input.platform}'",
        )

    except subprocess.TimeoutExpired:
        return ChatCliResult(response_text="", success=False, error="CLI timed out")
    except Exception as e:
        return ChatCliResult(response_text="", success=False, error=str(e))


def _execute_claude_chat(task_input: ChatCliInput, session_dir: str) -> ChatCliResult:
    token = _fetch_claude_token(task_input.tenant_id)
    if not token:
        return ChatCliResult(response_text="", success=False, error="Claude Code not connected")

    if task_input.instruction_md_content:
        with open(os.path.join(session_dir, "CLAUDE.md"), "w") as f:
            f.write(task_input.instruction_md_content)

    if task_input.mcp_config:
        with open(os.path.join(session_dir, "mcp.json"), "w") as f:
            f.write(task_input.mcp_config)

    _model = task_input.model or CLAUDE_CODE_MODEL
    _allowed = task_input.allowed_tools or _build_allowed_tools_from_mcp(
        task_input.mcp_config, extra="Bash,Read,Edit,Write,WebFetch,WebSearch"
    )
    
    prompt = task_input.message
    if task_input.instruction_md_content.strip():
        # Bypass the 20KB limit of --append-system-prompt by injecting
        # instructions and conversation history directly into the prompt.
        prompt = f"{task_input.instruction_md_content.strip()}\n\n# User Request\n\n{task_input.message}"

    cmd = [
        "claude", "-p", prompt,
        "--output-format", "json",
        "--model", _model,
        "--allowedTools", _allowed,
        "--add-dir", session_dir,
    ]
    if os.path.isdir(WORKSPACE):
        cmd.extend(["--add-dir", WORKSPACE])

    # NOTE: --resume intentionally NOT used. Previously we stored an
    # ever-growing session_id per chat and resumed it on every message.
    # For long conversations (Luna on WhatsApp), the JSONL session file
    # grew to 16+ MB, causing:
    #   - slow startup (loading + parsing the full file)
    #   - lossy context compaction (old details silently dropped)
    #   - context loss on specific entities (names, prior lead gen lists)
    # Instead, each `claude -p` invocation is a fresh one-shot session,
    # and the caller (chat.py) is responsible for passing the last N
    # messages via --append-system-prompt. This gives deterministic,
    # bounded context under our control.
    # Use --no-session-persistence to avoid leaking JSONL files on every
    # call (842+ files were accumulated in the previous model).
    cmd.append("--no-session-persistence")

    mcp_path = os.path.join(session_dir, "mcp.json")
    if os.path.exists(mcp_path):
        cmd.extend(["--mcp-config", mcp_path])

    env = os.environ.copy()
    env["CLAUDE_CODE_OAUTH_TOKEN"] = token

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=1500,
        env=env,
        cwd=WORKSPACE if os.path.isdir(WORKSPACE) else session_dir,
    )
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "")[:1000]
        return ChatCliResult(response_text="", success=False, error=f"CLI exit {result.returncode}: {err}")

    raw = result.stdout.strip()
    if not raw:
        return ChatCliResult(response_text="", success=False, error="CLI produced no output")

    try:
        data = json.loads(raw)
        text = data.get("result") or data.get("response") or data.get("content") or data.get("text") or raw
        meta = {
            "platform": "claude_code",
            "input_tokens": (data.get("usage") or {}).get("input_tokens", 0),
            "output_tokens": (data.get("usage") or {}).get("output_tokens", 0),
            "model": data.get("model"),
            "claude_session_id": data.get("session_id", ""),
            "cost_usd": data.get("total_cost_usd", 0),
        }
        return ChatCliResult(response_text=text, success=True, metadata=meta)
    except json.JSONDecodeError:
        return ChatCliResult(
            response_text=raw,
            success=True,
            metadata={"platform": "claude_code"},
        )


def _execute_codex_chat(task_input: ChatCliInput, session_dir: str, image_path: str) -> ChatCliResult:
    try:
        creds = _fetch_integration_credentials("codex", task_input.tenant_id)
    except Exception as exc:
        return ChatCliResult(response_text="", success=False, error=f"Failed to load Codex credentials: {exc}")

    raw_auth = creds.get("auth_json") or creds.get("session_token")
    if not raw_auth:
        return ChatCliResult(response_text="", success=False, error="Codex not connected")

    try:
        auth_payload = raw_auth if isinstance(raw_auth, dict) else json.loads(raw_auth)
    except json.JSONDecodeError:
        return ChatCliResult(
            response_text="",
            success=False,
            error="Codex credential must be valid ~/.codex/auth.json contents from 'codex login' or 'codex login --device-auth'",
        )

    codex_home = _prepare_codex_home(session_dir, auth_payload, task_input.mcp_config)
    prompt = task_input.message
    if task_input.instruction_md_content.strip():
        prompt = f"{task_input.instruction_md_content.strip()}\n\n# User Request\n\n{task_input.message}"

    output_path = os.path.join(session_dir, "codex-last-message.txt")
    cmd = [
        "codex",
        "exec",
        prompt,
        "--json",
        "--output-last-message",
        output_path,
        "--dangerously-bypass-approvals-and-sandbox",
        "-C",
        WORKSPACE if os.path.isdir(WORKSPACE) else session_dir,
    ]

    if os.path.isdir(WORKSPACE):
        cmd.extend(["--add-dir", session_dir])
    else:
        cmd.extend(["--skip-git-repo-check"])

    if image_path:
        cmd.extend(["--image", image_path])

    env = os.environ.copy()
    env["CODEX_HOME"] = codex_home

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=1500,
        env=env,
        cwd=WORKSPACE if os.path.isdir(WORKSPACE) else session_dir,
    )
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "")[:2000]
        return ChatCliResult(response_text="", success=False, error=f"CLI exit {result.returncode}: {err}")

    response_text = ""
    if os.path.exists(output_path):
        with open(output_path) as f:
            response_text = f.read().strip()
    if not response_text:
        response_text = _extract_codex_last_message(result.stdout)
    if not response_text:
        return ChatCliResult(response_text="", success=False, error="Codex produced no final response")

    metadata = _extract_codex_metadata(result.stdout)
    metadata["platform"] = "codex"
    # Codex exec is one-shot — no native session resume. Continuity via
    # conversation summary in the prompt. Track a synthetic session ID so
    # the platform can persist it uniformly.
    if not metadata.get("codex_session_id"):
        import hashlib
        metadata["codex_session_id"] = hashlib.sha1(
            f"{task_input.tenant_id}-codex".encode()
        ).hexdigest()[:16]
    return ChatCliResult(response_text=response_text, success=True, metadata=metadata)


def _execute_codex_code_task(task_input: CodeTaskInput, prompt: str, session_dir: str) -> tuple[str, dict]:
    try:
        creds = _fetch_integration_credentials("codex", task_input.tenant_id)
    except Exception as exc:
        raise RuntimeError(f"Failed to load Codex credentials: {exc}") from exc

    raw_auth = creds.get("auth_json") or creds.get("session_token")
    if not raw_auth:
        raise RuntimeError("Codex not connected")

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


def _prepare_codex_home(session_dir: str, auth_payload: dict, mcp_config_json: str) -> str:
    """Materialize tenant-scoped CODEX_HOME with auth.json and MCP config.toml."""
    codex_home = os.path.join(session_dir, ".codex")
    os.makedirs(codex_home, exist_ok=True)

    with open(os.path.join(codex_home, "auth.json"), "w") as f:
        json.dump(auth_payload, f)

    config_lines = [
        f'[projects."{WORKSPACE if os.path.isdir(WORKSPACE) else session_dir}"]',
        'trust_level = "trusted"',
        "",
        f'[projects."{session_dir}"]',
        'trust_level = "trusted"',
    ]

    if mcp_config_json:
        config_lines.extend(_codex_mcp_config_lines(mcp_config_json))

    with open(os.path.join(codex_home, "config.toml"), "w") as f:
        f.write("\n".join(config_lines).strip() + "\n")

    return codex_home


def _codex_mcp_config_lines(mcp_config_json: str) -> list[str]:
    """Convert the shared MCP JSON config into Codex config.toml entries."""
    data = json.loads(mcp_config_json)
    servers = data.get("mcpServers") or {}
    lines: list[str] = []
    for server_name, config in servers.items():
        if not isinstance(config, dict):
            continue
        lines.append("")
        lines.append(f"[mcp_servers.{server_name}]")
        lines.append('transport = "streamable_http"')
        if config.get("url"):
            lines.append(f'url = "{_toml_escape(str(config["url"]))}"')
        headers = config.get("headers") or {}
        if headers:
            lines.append(f"http_headers = {_toml_inline_table(headers)}")
    return lines


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


def _execute_gemini_chat(task_input: ChatCliInput, session_dir: str, image_path: str) -> ChatCliResult:
    try:
        creds = _fetch_integration_credentials("gemini_cli", task_input.tenant_id)
    except Exception as exc:
        return ChatCliResult(response_text="", success=False, error=f"Failed to load Gemini credentials: {exc}")

    # Gemini uses OAuth token, so credentials should contain oauth_token
    oauth_token = creds.get("oauth_token") or creds.get("session_token")
    if not oauth_token:
        return ChatCliResult(response_text="", success=False, error="Gemini CLI not connected")

    auth_payload = {"access_token": oauth_token}
    if "refresh_token" in creds:
        auth_payload["refresh_token"] = creds["refresh_token"]
        
    gemini_home = _prepare_gemini_home(session_dir, auth_payload, task_input.mcp_config)
    
    prompt = task_input.message
    if task_input.instruction_md_content.strip():
        # Inject instruction context and previous messages into the prompt body
        prompt = f"{task_input.instruction_md_content.strip()}\n\n# User Request\n\n{task_input.message}"

    cmd = [
        "gemini",
        "-p",
        prompt,
        "-y",
    ]

    env = os.environ.copy()
    env["HOME"] = session_dir  # Tell Gemini CLI where to find .gemini/
    env["GEMINI_AUTH_TOKEN"] = oauth_token
    env["GOOGLE_APPLICATION_CREDENTIALS"] = os.path.join(gemini_home, "application_default_credentials.json")
    env["GOOGLE_GENAI_USE_VERTEXAI"] = "0"
    env["GOOGLE_GENAI_USE_GCA"] = "0"
    env["GEMINI_PROJECT_ID"] = "personal-project" # Placeholder to avoid registry lookup

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=1500,
        env=env,
        cwd=WORKSPACE if os.path.isdir(WORKSPACE) else session_dir,
    )
    logger.info("Gemini CLI exit code: %s", result.returncode)
    if result.stdout:
        logger.info("Gemini CLI stdout: %s", result.stdout[:500])
    if result.stderr:
        logger.warning("Gemini CLI stderr: %s", result.stderr[:500])

    if result.returncode != 0:
        err = (result.stderr or result.stdout or "")[:2000]
        return ChatCliResult(response_text="", success=False, error=f"CLI exit {result.returncode}: {err}")

    raw = result.stdout.strip()
    if not raw:
        return ChatCliResult(response_text="", success=False, error="Gemini produced no output")

    try:
        data = json.loads(raw)
        text = data.get("result") or data.get("response") or data.get("content") or data.get("text") or raw
        meta = {
            "platform": "gemini_cli",
            "input_tokens": (data.get("usage") or {}).get("input_tokens", 0),
            "output_tokens": (data.get("usage") or {}).get("output_tokens", 0),
            "model": data.get("model", "gemini-2.5-pro"),
        }
        return ChatCliResult(response_text=text, success=True, metadata=meta)
    except json.JSONDecodeError:
        return ChatCliResult(
            response_text=raw,
            success=True,
            metadata={"platform": "gemini_cli"},
        )


def _prepare_gemini_home(session_dir: str, auth_payload: dict, mcp_config_json: str) -> str:
    """Materialize tenant-scoped GEMINI_HOME with credentials.json and MCP settings.json."""
    # Create .gemini directory for settings and credentials
    gemini_home = os.path.join(session_dir, ".gemini")
    os.makedirs(gemini_home, exist_ok=True)

    # Pre-create projects.json to avoid rename errors and initialize the workspace project
    projects_path = os.path.join(gemini_home, "projects.json")
    if not os.path.exists(projects_path):
        with open(projects_path, "w") as f:
            # Proper registry format to avoid ProjectRegistry.getShortId TypeError
            registry = {
                "projects": {
                    "/workspace": {
                        "id": "workspace-project",
                        "name": "workspace",
                        "is_active": True
                    }
                },
                "active_project_path": "/workspace"
            }
            json.dump(registry, f, indent=2)

    # Gemini CLI credentials.json format for oauth-personal
    import time
    expiry_ms = int((time.time() + 3600) * 1000)  # Assume 1h expiry if unknown
    
    # Use any existing refresh token or access token from auth_payload
    creds_payload = {
        "access_token": auth_payload.get("access_token"),
        "refresh_token": auth_payload.get("refresh_token"),
        "scope": "https://www.googleapis.com/auth/gmail.readonly https://www.googleapis.com/auth/gmail.send https://www.googleapis.com/auth/calendar.readonly https://www.googleapis.com/auth/calendar.events https://www.googleapis.com/auth/drive.readonly https://www.googleapis.com/auth/drive.file https://www.googleapis.com/auth/cloud-platform https://www.googleapis.com/auth/accounts.reauth https://www.googleapis.com/auth/userinfo.email https://www.googleapis.com/auth/userinfo.profile openid",
        "token_type": "Bearer",
        "expiry_date": expiry_ms
    }
    
    with open(os.path.join(gemini_home, "credentials.json"), "w") as f:
        json.dump(creds_payload, f, indent=2)

    # Also maintain ADC for tools that might use it (Vertex AI nodes)
    adc_path = os.path.join(gemini_home, "application_default_credentials.json")
    adc_payload = {
        "access_token": auth_payload.get("access_token"),
        "refresh_token": auth_payload.get("refresh_token"),
        "client_id": os.environ.get("GOOGLE_CLIENT_ID", ""),
        "client_secret": os.environ.get("GOOGLE_CLIENT_SECRET", ""),
        "type": "authorized_user",
    }
    with open(adc_path, "w") as f:
        json.dump(adc_payload, f, indent=2)
    
    # settings.json for auth enforcement and feature disabling
    settings = {
        "security": {
            "auth": {
                "enforcedType": "oauth-personal",
                "selectedType": "oauth-personal"
            }
        },
        "cloudCodeAssist": {
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


# ---------------------------------------------------------------------------
# OpenCode CLI — local Gemma 4 via Ollama with MCP tool access
# ---------------------------------------------------------------------------

OPENCODE_OLLAMA_URL = os.environ.get("OPENCODE_OLLAMA_URL", "http://host.docker.internal:11434/v1")
OPENCODE_MODEL = os.environ.get("OPENCODE_MODEL", "gemma4")
OPENCODE_PORT = int(os.environ.get("OPENCODE_PORT", "8200"))

# Per-tenant OpenCode session cache (tenant_id → session_id)
_opencode_sessions: dict[str, str] = {}


def _execute_opencode_chat(task_input: ChatCliInput, session_dir: str) -> ChatCliResult:
    """Execute a chat turn via the persistent OpenCode server (local Gemma 4).

    Uses the in-process OpenCode server started by entrypoint.sh on OPENCODE_PORT.
    Creates one session per tenant for context continuity. Falls back to `opencode run`
    if the server is unreachable.
    """
    import httpx

    base_url = f"http://127.0.0.1:{OPENCODE_PORT}"

    # Get or create a session for this tenant
    tenant = task_input.tenant_id
    session_id = _opencode_sessions.get(tenant)

    try:
        if not session_id:
            resp = httpx.post(f"{base_url}/session", timeout=10)
            resp.raise_for_status()
            session_id = resp.json()["id"]
            _opencode_sessions[tenant] = session_id

        # Prepend tenant context to the message so Gemma knows the tenant_id
        prompt = task_input.message
        if task_input.instruction_md_content:
            # First message in session: include persona + tenant context
            context_prefix = (
                f"[Context: tenant_id={tenant}. "
                f"Always pass tenant_id in ALL MCP tool calls.]\n\n"
            )
            prompt = context_prefix + prompt

        # Send message to OpenCode server
        resp = httpx.post(
            f"{base_url}/session/{session_id}/message",
            json={"parts": [{"type": "text", "text": prompt}]},
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()

        # Extract response text from parts
        info = data.get("info", {})
        parts = data.get("parts", [])
        texts = []
        for p in parts:
            ptype = p.get("type", "")
            if ptype == "text":
                texts.append(p.get("text", ""))
        response_text = "\n".join(texts).strip()

        tokens = info.get("tokens", {})
        return ChatCliResult(
            response_text=response_text or "(no response from Gemma 4)",
            success=bool(response_text),
            metadata={
                "platform": "opencode",
                "model": OPENCODE_MODEL,
                "cost_usd": 0,
                "input_tokens": tokens.get("input", 0),
                "output_tokens": tokens.get("output", 0),
            },
        )

    except Exception as e:
        # Server not ready or failed — fall back to opencode run (slow but works)
        logger.warning("OpenCode server call failed (%s), falling back to opencode run", e)
        _opencode_sessions.pop(tenant, None)  # Clear stale session

        cmd = ["opencode", "run", task_input.message]
        env = os.environ.copy()
        env["HOME"] = session_dir

        # Write config for CLI fallback
        config_path = os.path.join(session_dir, "opencode.json")
        if not os.path.exists(config_path):
            opencode_config = {
                "$schema": "https://opencode.ai/config.json",
                "provider": {
                    "ollama": {
                        "npm": "@ai-sdk/openai-compatible",
                        "name": "Ollama",
                        "options": {"baseURL": OPENCODE_OLLAMA_URL},
                        "models": {OPENCODE_MODEL: {"name": OPENCODE_MODEL}},
                    },
                },
                "model": f"ollama/{OPENCODE_MODEL}",
            }
            with open(config_path, "w") as f:
                json.dump(opencode_config, f, indent=2)

        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300,
            env=env, cwd=session_dir,
        )
        raw = result.stdout.strip()
        return ChatCliResult(
            response_text=raw or "(no response)",
            success=bool(raw),
            error=None if raw else f"OpenCode run exit {result.returncode}",
            metadata={"platform": "opencode", "model": OPENCODE_MODEL, "cost_usd": 0},
        )


@workflow.defn
class ChatCliWorkflow:
    """Temporal workflow for chat CLI sessions.

    Flexible timeout: Claude CLI may do complex multi-tool work
    (email scanning, calendar creation, code analysis, multi-file implementations).
    Allow up to 150 minutes with heartbeat to keep Temporal informed.
    """

    @workflow.run
    async def run(self, task_input: ChatCliInput) -> ChatCliResult:
        return await workflow.execute_activity(
            execute_chat_cli,
            task_input,
            start_to_close_timeout=timedelta(minutes=150),
            schedule_to_close_timeout=timedelta(minutes=165),
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
            start_to_close_timeout=timedelta(minutes=CODE_TASK_ACTIVITY_TIMEOUT_MINUTES),
            schedule_to_close_timeout=timedelta(minutes=CODE_TASK_SCHEDULE_TIMEOUT_MINUTES),
            heartbeat_timeout=timedelta(seconds=CODE_TASK_HEARTBEAT_SECONDS),
            retry_policy=retry_policy,
        )


# ---------------------------------------------------------------------------
# Multi-Provider Review Council
# ---------------------------------------------------------------------------

PROVIDER_REVIEW_PROMPT = """You are reviewing an AI agent response for quality. Evaluate it and return a JSON verdict.

USER MESSAGE:
{user_message}

AGENT RESPONSE ({agent_slug} via {platform_used}):
{agent_response}

CONTEXT:
- Channel: {channel}
- Tools called: {tools_called}
- Entities recalled: {entities_recalled}

Score the response 0-100 across these dimensions:
- Accuracy (0-25): factually correct, no hallucinations
- Helpfulness (0-20): addresses user need, actionable
- Tool usage (0-20): appropriate tool selection
- Efficiency (0-10): concise, no padding
- Context (0-10): uses conversation history

Return ONLY this JSON:
{{"approved": true/false, "verdict": "APPROVED|REJECTED|CONDITIONAL", "score": <0-100>, "issues": ["issue 1"], "suggestions": ["fix 1"], "summary": "1-2 sentence review"}}"""


@dataclass
class ProviderReviewInput:
    user_message: str
    agent_response: str
    agent_slug: str
    platform_used: str
    tools_called: str
    entities_recalled: str
    channel: str
    tenant_id: str
    original_experience_id: str = ""


@dataclass
class ProviderReview:
    provider: str
    approved: bool
    verdict: str
    score: int
    issues: list
    suggestions: list
    summary: str
    tokens_used: int = 0
    cost_usd: float = 0.0
    duration_ms: int = 0
    error: Optional[str] = None


@dataclass
class ProviderCouncilResult:
    consensus: bool
    provider_agreement: float
    reviews: list
    disagreements: list
    recommended_platform: str
    total_cost: float
    total_tokens: int


def _parse_provider_review(provider: str, raw_output: str, duration_ms: int) -> ProviderReview:
    """Parse CLI output into a ProviderReview. Defensively handles bad JSON.

    Handles multiple output formats:
    - Claude: {"result": "...", "usage": {...}, "total_cost_usd": ...}
    - Codex: multi-line JSON stream or plain text with --output-last-message
    - Gemma 4/Ollama: raw text (may include unexpected tags)
    """
    if not raw_output or not raw_output.strip():
        return ProviderReview(
            provider=provider, approved=True, verdict="PARSE_ERROR",
            score=50, issues=[], suggestions=[],
            summary="Empty response from provider",
            duration_ms=duration_ms, error="empty_response",
        )

    text = raw_output.strip()
    outer = {}
    tokens = 0
    cost = 0.0

    try:
        # Try parsing as a JSON wrapper first (Claude format)
        try:
            outer = json.loads(text)
            if isinstance(outer, dict) and "result" in outer:
                text = str(outer.get("result", ""))
                tokens = (outer.get("usage") or {}).get("input_tokens", 0) + (outer.get("usage") or {}).get("output_tokens", 0)
                cost = outer.get("total_cost_usd", 0) or 0
        except json.JSONDecodeError:
            pass  # Not a JSON wrapper — treat as raw text

        # Strip ALL <think>...</think> blocks (greedy, handles nested braces inside)
        text = re.sub(r"<think>[\s\S]*?</think>", "", text).strip()
        # Strip unclosed <think> tags (model cut off mid-thought)
        text = re.sub(r"<think>[\s\S]*$", "", text).strip()
        # Strip markdown fences
        text = re.sub(r"```(?:json)?\s*|\s*```", "", text).strip()

        # Find the JSON object with "approved" key (most reliable signal)
        # Use a targeted regex that finds JSON objects containing "approved"
        json_candidates = re.findall(r"\{[^{}]*\"approved\"[^{}]*\}", text)
        if json_candidates:
            data = json.loads(json_candidates[0])
        else:
            # Fallback: find any JSON object
            match = re.search(r"\{[^{}]+\}", text)
            if match:
                data = json.loads(match.group(0))
            else:
                data = json.loads(text)

        return ProviderReview(
            provider=provider,
            approved=bool(data.get("approved", False)),
            verdict=str(data.get("verdict", "UNKNOWN")),
            score=max(0, min(100, int(data.get("score", 50)))),
            issues=list(data.get("issues", []))[:5],
            suggestions=list(data.get("suggestions", []))[:5],
            summary=str(data.get("summary", ""))[:300],
            tokens_used=tokens,
            cost_usd=cost,
            duration_ms=duration_ms,
        )
    except Exception as e:
        # Log enough of the raw output for debugging
        logger.debug("Provider %s parse failed on: %s", provider, text[:300])
        return ProviderReview(
            provider=provider, approved=True, verdict="PARSE_ERROR",
            score=50, issues=[], suggestions=[],
            summary=f"Could not parse review: {e}",
            duration_ms=duration_ms, error=str(e),
        )


@activity.defn
async def review_with_claude(input: ProviderReviewInput) -> ProviderReview:
    """Review a response using Claude Code CLI (tenant's subscription, sonnet model)."""
    try:
        token = _fetch_claude_token(input.tenant_id)
        if not token:
            return ProviderReview(provider="claude_code", approved=True, verdict="SKIPPED",
                                  score=0, issues=[], suggestions=[], summary="No Claude subscription")
    except Exception:
        return ProviderReview(provider="claude_code", approved=True, verdict="SKIPPED",
                              score=0, issues=[], suggestions=[], summary="Claude credential fetch failed")

    prompt = PROVIDER_REVIEW_PROMPT.format(
        user_message=input.user_message[:500],
        agent_response=input.agent_response[:1000],
        agent_slug=input.agent_slug,
        platform_used=input.platform_used,
        tools_called=input.tools_called,
        entities_recalled=input.entities_recalled,
        channel=input.channel,
    )

    start = time.time()
    result = subprocess.run(
        ["claude", "-p", prompt, "--output-format", "json", "--model", "sonnet"],
        capture_output=True, text=True, timeout=300,
        env={**os.environ, "CLAUDE_CODE_OAUTH_TOKEN": token},
    )
    duration_ms = int((time.time() - start) * 1000)

    if result.returncode != 0:
        return ProviderReview(provider="claude_code", approved=True, verdict="ERROR",
                              score=0, issues=[], suggestions=[],
                              summary=f"CLI exit {result.returncode}: {result.stderr[:200]}",
                              duration_ms=duration_ms)

    return _parse_provider_review("claude_code", result.stdout, duration_ms)


@activity.defn
async def review_with_codex(input: ProviderReviewInput) -> ProviderReview:
    """Review a response using Codex CLI (tenant's subscription)."""
    try:
        creds = _fetch_integration_credentials("codex", input.tenant_id)
        raw_auth = creds.get("auth_json") or creds.get("session_token")
        if not raw_auth:
            return ProviderReview(provider="codex", approved=True, verdict="SKIPPED",
                                  score=0, issues=[], suggestions=[], summary="No Codex subscription")
        auth_payload = raw_auth if isinstance(raw_auth, dict) else json.loads(raw_auth)
    except Exception:
        return ProviderReview(provider="codex", approved=True, verdict="SKIPPED",
                              score=0, issues=[], suggestions=[], summary="Codex credential fetch failed")

    session_dir = os.path.join("/home/codeworker/st_provider_review", input.tenant_id)
    os.makedirs(session_dir, exist_ok=True)
    codex_home = _prepare_codex_home(session_dir, auth_payload, "")

    prompt = PROVIDER_REVIEW_PROMPT.format(
        user_message=input.user_message[:500],
        agent_response=input.agent_response[:1000],
        agent_slug=input.agent_slug,
        platform_used=input.platform_used,
        tools_called=input.tools_called,
        entities_recalled=input.entities_recalled,
        channel=input.channel,
    )

    output_path = os.path.join(session_dir, "review-output.txt")
    start = time.time()
    result = subprocess.run(
        ["codex", "exec", prompt, "--json", "--output-last-message", output_path,
         "--dangerously-bypass-approvals-and-sandbox", "--skip-git-repo-check"],
        capture_output=True, text=True, timeout=300,
        env={**os.environ, "CODEX_HOME": codex_home},
    )
    duration_ms = int((time.time() - start) * 1000)

    if result.returncode != 0:
        return ProviderReview(provider="codex", approved=True, verdict="ERROR",
                              score=0, issues=[], suggestions=[],
                              summary=f"CLI exit {result.returncode}: {result.stderr[:200]}",
                              duration_ms=duration_ms)

    # Read output — try --output-last-message file first, then parse stdout
    response_text = ""
    if os.path.exists(output_path):
        with open(output_path) as f:
            response_text = f.read().strip()
    if not response_text:
        # Codex --json outputs multiple JSON lines; extract the last agent_message
        response_text = _extract_codex_last_message(result.stdout)
    if not response_text:
        response_text = result.stdout.strip()

    return _parse_provider_review("codex", response_text, duration_ms)


@activity.defn
async def review_with_local_gemma(input: ProviderReviewInput) -> ProviderReview:
    """Review a response using local Gemma 4 via Ollama (free, always available)."""
    import httpx as _httpx

    OLLAMA_URL = os.environ.get("OLLAMA_HOST", "http://host.docker.internal:11434")
    MODEL = os.environ.get("LOCAL_TOOL_MODEL", "gemma4")

    prompt = PROVIDER_REVIEW_PROMPT.format(
        user_message=input.user_message[:500],
        agent_response=input.agent_response[:1000],
        agent_slug=input.agent_slug,
        platform_used=input.platform_used,
        tools_called=input.tools_called,
        entities_recalled=input.entities_recalled,
        channel=input.channel,
    )

    start = time.time()
    try:
        with _httpx.Client(timeout=120) as client:
            resp = client.post(f"{OLLAMA_URL}/api/chat", json={
                "model": MODEL,
                "messages": [
                    {"role": "system", "content": "You are a response quality reviewer. Return only valid JSON."},
                    {"role": "user", "content": prompt},
                ],
                "stream": False,
                "options": {"temperature": 0.1, "num_predict": 400},
            })
        duration_ms = int((time.time() - start) * 1000)
        if resp.status_code != 200:
            return ProviderReview(provider="local_gemma", approved=True, verdict="ERROR",
                                  score=0, issues=[], suggestions=[],
                                  summary=f"Ollama HTTP {resp.status_code}", duration_ms=duration_ms)
        resp_json = resp.json()
        raw = resp_json.get("message", {}).get("content", "")
        logger.info("Gemma 4 review: status=%s raw_len=%d", resp.status_code, len(raw))
        return _parse_provider_review("local_gemma", raw, duration_ms)
    except Exception as e:
        duration_ms = int((time.time() - start) * 1000)
        logger.warning("Gemma 4 review exception: %s", e)
        return ProviderReview(provider="local_gemma", approved=True, verdict="ERROR",
                              score=0, issues=[], suggestions=[],
                              summary=str(e)[:200], duration_ms=duration_ms)


@activity.defn
async def finalize_provider_council(
    tenant_id: str,
    experience_id: str,
    result_json: str,
) -> dict:
    """Update the RL experience with provider council results."""
    try:
        resp = httpx.post(
            f"{API_BASE_URL}/api/v1/rl/internal/provider-council",
            headers={"X-Internal-Key": API_INTERNAL_KEY or "dev_mcp_key"},
            json={
                "tenant_id": tenant_id,
                "experience_id": experience_id,
                "provider_council": json.loads(result_json),
            },
            timeout=10,
        )
        logger.info("Provider council RL update: %s", resp.status_code)
        return {"updated": resp.status_code == 200}
    except Exception as e:
        logger.warning("Provider council RL update failed: %s", e)
        return {"updated": False, "error": str(e)}


@workflow.defn
class ProviderReviewWorkflow:
    """Multi-provider review council — Claude, Codex, and Gemma 4 each review a response."""

    @workflow.run
    async def run(self, input: ProviderReviewInput) -> ProviderCouncilResult:
        # Run all available providers in parallel — each wrapped so one failure
        # doesn't abort the others
        retry = RetryPolicy(maximum_attempts=1)
        timeout = timedelta(minutes=5)

        async def _safe_review(activity_fn, provider_name):
            try:
                return await workflow.execute_activity(
                    activity_fn, input,
                    start_to_close_timeout=timeout, retry_policy=retry,
                )
            except Exception as e:
                return ProviderReview(
                    provider=provider_name, approved=True, verdict="ERROR",
                    score=0, issues=[], suggestions=[],
                    summary=f"Activity failed: {str(e)[:200]}",
                    error=str(e)[:200],
                )

        # Start all three reviews as concurrent coroutines
        import asyncio as _asyncio
        results = list(await _asyncio.gather(
            _safe_review(review_with_claude, "claude_code"),
            _safe_review(review_with_codex, "codex"),
            _safe_review(review_with_local_gemma, "local_gemma"),
        ))

        # All reviews including errors
        reviews = [r for r in results if isinstance(r, ProviderReview)]
        # Active = produced a real verdict (not skipped/errored/parse-failed)
        active = [r for r in reviews if r.verdict not in ("SKIPPED", "ERROR", "PARSE_ERROR")]
        failed = [r for r in reviews if r.verdict in ("ERROR", "PARSE_ERROR")]

        # Meta-adjudication — agreement is computed over ALL reviews, not just active
        total_reviewers = len(reviews)
        active_approved = sum(1 for r in active if r.approved)

        if active:
            consensus = active_approved > len(active) / 2
            scores = [r.score for r in active if r.score > 0]

            # Detect disagreements
            disagreements = []
            for r in failed:
                disagreements.append(f"[{r.provider}] {r.verdict}: {r.summary[:100]}")
            for r in active:
                if not r.approved:
                    for issue in r.issues[:2]:
                        disagreements.append(f"[{r.provider}] {issue}")

            # Recommend platform with highest score
            best = max(active, key=lambda r: r.score)
            recommended = best.provider

            # Agreement = active approvals / total reviewers (failed count as non-approvals)
            agreement = active_approved / total_reviewers if total_reviewers > 0 else 0.0
        else:
            consensus = False  # No valid reviews = no consensus
            agreement = 0.0
            disagreements = [f"[{r.provider}] {r.verdict}: {r.summary[:100]}" for r in failed]
            recommended = "unknown"

        total_cost = sum(r.cost_usd for r in reviews)
        total_tokens = sum(r.tokens_used for r in reviews)

        council_result = ProviderCouncilResult(
            consensus=consensus,
            provider_agreement=agreement,
            reviews=[{
                "provider": r.provider, "approved": r.approved,
                "verdict": r.verdict, "score": r.score,
                "issues": r.issues, "summary": r.summary,
                "cost_usd": r.cost_usd, "duration_ms": r.duration_ms,
            } for r in reviews],
            disagreements=disagreements,
            recommended_platform=recommended,
            total_cost=total_cost,
            total_tokens=total_tokens,
        )

        # Update RL experience if we have one
        if input.original_experience_id:
            await workflow.execute_activity(
                finalize_provider_council,
                args=[input.tenant_id, input.original_experience_id,
                      json.dumps({
                          "consensus": council_result.consensus,
                          "agreement": council_result.provider_agreement,
                          "reviews": council_result.reviews,
                          "disagreements": council_result.disagreements,
                          "recommended_platform": council_result.recommended_platform,
                          "total_cost": council_result.total_cost,
                      })],
                start_to_close_timeout=timedelta(seconds=30),
            )

        return council_result
