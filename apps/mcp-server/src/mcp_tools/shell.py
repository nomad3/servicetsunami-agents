"""Shell execution MCP tools.

Provides controlled shell command execution and git-based deploy workflows.
Used by coding agents to run build commands, tests, linting, and push
code changes that trigger CI/CD pipelines.
"""
import asyncio
import logging
import subprocess
import time
import uuid
from typing import Optional

from mcp.server.fastmcp import Context

from src.mcp_app import mcp

logger = logging.getLogger(__name__)

# Output size limits to prevent memory issues
_MAX_STDOUT_BYTES = 10 * 1024  # 10 KB
_MAX_STDERR_BYTES = 5 * 1024   # 5 KB
_MAX_TIMEOUT = 300              # 5 minutes

# Background job registry — in-process state, survives HTTP request boundaries.
# Jobs older than _JOB_TTL_SECONDS are purged on each new job creation.
_jobs: dict[str, dict] = {}
_JOB_TTL_SECONDS = 3600  # 1 hour


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _truncate(text: str, max_bytes: int) -> str:
    """Truncate text to max_bytes, appending a notice if truncated."""
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return text
    truncated = encoded[:max_bytes].decode("utf-8", errors="replace")
    return truncated + f"\n... [truncated, {len(encoded)} bytes total]"


def _run_shell(command: str, working_dir: str, timeout: int) -> dict:
    """Synchronous subprocess wrapper (called via asyncio.to_thread)."""
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            cwd=working_dir,
            timeout=timeout,
        )
        return {
            "stdout": _truncate(result.stdout, _MAX_STDOUT_BYTES),
            "stderr": _truncate(result.stderr, _MAX_STDERR_BYTES),
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


def _purge_old_jobs() -> None:
    """Remove jobs older than _JOB_TTL_SECONDS to prevent unbounded growth."""
    cutoff = time.time() - _JOB_TTL_SECONDS
    stale = [jid for jid, j in _jobs.items() if j.get("created_at", 0) < cutoff]
    for jid in stale:
        del _jobs[jid]


async def _run_job(job_id: str, command: str, working_dir: str, timeout: int) -> None:
    """Run command as a background asyncio task, updating _jobs when done."""
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=working_dir,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
            _jobs[job_id].update(
                {
                    "status": "done",
                    "return_code": proc.returncode,
                    "stdout": _truncate(
                        stdout_bytes.decode("utf-8", errors="replace"), _MAX_STDOUT_BYTES
                    ),
                    "stderr": _truncate(
                        stderr_bytes.decode("utf-8", errors="replace"), _MAX_STDERR_BYTES
                    ),
                    "finished_at": time.time(),
                }
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
                await proc.communicate()
            except Exception:
                pass
            _jobs[job_id].update(
                {
                    "status": "timeout",
                    "return_code": -1,
                    "stdout": "",
                    "stderr": f"Command timed out after {timeout}s",
                    "finished_at": time.time(),
                }
            )
    except Exception as exc:
        _jobs[job_id].update(
            {
                "status": "error",
                "return_code": -1,
                "stdout": "",
                "stderr": str(exc),
                "finished_at": time.time(),
            }
        )
    finally:
        logger.info(
            "shell job %s finished: status=%s rc=%s",
            job_id,
            _jobs.get(job_id, {}).get("status"),
            _jobs.get(job_id, {}).get("return_code"),
        )


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def execute_shell(
    command: str,
    working_dir: str = "/app",
    timeout: int = 60,
    background: bool = False,
    ctx: Context = None,
) -> dict:
    """Execute a shell command and return its output.

    Runs any shell command in a subprocess, capturing stdout and stderr.
    Use this to run build commands, tests, linting, file inspection, or
    any CLI tool available in the container.

    For long-running commands (>30s) set background=True. The tool returns
    immediately with a job_id; use get_shell_job(job_id) to poll for results.
    This avoids HTTP transport timeouts from Cloudflare or proxies.

    Args:
        command: The shell command to execute (e.g. "python -m pytest tests/"). Required.
        working_dir: Working directory for the command. Defaults to "/app".
        timeout: Maximum seconds before the command is killed. Defaults to 60, max 300.
        background: If True, run the command in the background and return a job_id
            immediately. Poll with get_shell_job(job_id). Default False.
        ctx: MCP request context (injected automatically).

    Returns:
        Foreground: dict with stdout, stderr, return_code, command.
        Background: dict with job_id, status="running", message.
        Output is truncated to 10KB stdout / 5KB stderr.
    """
    if not command:
        return {"error": "command is required."}

    timeout = min(max(timeout, 1), _MAX_TIMEOUT)
    logger.info(
        "execute_shell: %s (cwd=%s, timeout=%ds, background=%s)",
        command, working_dir, timeout, background,
    )

    if background:
        _purge_old_jobs()
        job_id = uuid.uuid4().hex[:8]
        _jobs[job_id] = {
            "status": "running",
            "command": command,
            "working_dir": working_dir,
            "timeout": timeout,
            "created_at": time.time(),
        }
        asyncio.create_task(_run_job(job_id, command, working_dir, timeout))
        return {
            "job_id": job_id,
            "status": "running",
            "message": f"Command started in background (timeout={timeout}s). "
                       f"Call get_shell_job('{job_id}') to poll for results.",
        }

    result = await asyncio.to_thread(_run_shell, command, working_dir, timeout)

    if result["return_code"] != 0:
        logger.warning(
            "execute_shell non-zero exit (%d): %s — stderr: %s",
            result["return_code"],
            command,
            result["stderr"][:200],
        )
    return result


@mcp.tool()
async def get_shell_job(
    job_id: str,
    ctx: Context = None,
) -> dict:
    """Poll the result of a background shell job started with execute_shell(background=True).

    Args:
        job_id: The job ID returned by execute_shell when background=True. Required.
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with job_id, status ("running" | "done" | "timeout" | "error"),
        return_code, stdout, stderr, and timing fields.
        Returns {"error": "..."} if the job_id is not found.
    """
    if not job_id:
        return {"error": "job_id is required."}

    job = _jobs.get(job_id)
    if job is None:
        return {"error": f"Job '{job_id}' not found. It may have expired or never existed."}

    elapsed = time.time() - job.get("created_at", time.time())
    return {
        "job_id": job_id,
        **{k: v for k, v in job.items() if k != "created_at"},
        "elapsed_seconds": round(elapsed, 1),
    }


@mcp.tool()
async def deploy_changes(
    commit_message: str,
    files: str = "",
    ctx: Context = None,
) -> dict:
    """Stage, commit, and push code changes to trigger CI/CD deployment.

    Stages the specified files (or all changes), commits with the given
    message, and pushes to the main branch.

    Args:
        commit_message: Git commit message describing the changes. Required.
        files: Comma-separated file paths to stage. If empty, stages all changes
            with `git add -A`.
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with status, commit_sha, and files_changed.
        On error, returns dict with status "error" and details.
    """
    if not commit_message:
        return {"error": "commit_message is required."}

    logger.info("deploy_changes: message=%r, files=%s", commit_message, files)
    working_dir = "/app"

    file_list = [f.strip() for f in files.split(",") if f.strip()] if files else []

    try:
        # Stage files
        if file_list:
            for f in file_list:
                stage_result = await asyncio.to_thread(
                    _run_shell, f"git add {f}", working_dir, 30
                )
                if stage_result["return_code"] != 0:
                    return {
                        "status": "error",
                        "step": "stage",
                        "detail": f"Failed to stage {f}: {stage_result['stderr']}",
                    }
        else:
            stage_result = await asyncio.to_thread(
                _run_shell, "git add -A", working_dir, 30
            )
            if stage_result["return_code"] != 0:
                return {
                    "status": "error",
                    "step": "stage",
                    "detail": stage_result["stderr"],
                }

        # Determine changed files for deploy detection
        diff_result = await asyncio.to_thread(
            _run_shell, "git diff --cached --name-only", working_dir, 30
        )
        files_changed = [
            line for line in diff_result["stdout"].strip().split("\n") if line
        ]

        if not files_changed:
            return {
                "status": "nothing_to_commit",
                "commit_sha": "",
                "files_changed": [],
                "deploy_triggered": False,
            }

        # Commit
        safe_message = commit_message.replace("'", "'\\''")
        commit_result = await asyncio.to_thread(
            _run_shell, f"git commit -m '{safe_message}'", working_dir, 30
        )
        if commit_result["return_code"] != 0:
            return {
                "status": "error",
                "step": "commit",
                "detail": commit_result["stderr"],
            }

        # Extract commit SHA
        sha_result = await asyncio.to_thread(
            _run_shell, "git rev-parse --short HEAD", working_dir, 10
        )
        commit_sha = sha_result["stdout"].strip()

        # Push to main
        push_result = await asyncio.to_thread(
            _run_shell, "git push origin main", working_dir, 120
        )
        if push_result["return_code"] != 0:
            return {
                "status": "error",
                "step": "push",
                "commit_sha": commit_sha,
                "detail": push_result["stderr"],
            }

        logger.info(
            "deploy_changes: pushed %s (%d files)",
            commit_sha, len(files_changed),
        )

        return {
            "status": "pushed",
            "commit_sha": commit_sha,
            "files_changed": files_changed,
        }

    except Exception as e:
        logger.error("deploy_changes failed: %s", e)
        return {
            "status": "error",
            "step": "unknown",
            "detail": str(e),
        }
