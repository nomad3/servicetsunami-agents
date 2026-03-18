"""Shell execution MCP tools.

Provides controlled shell command execution and git-based deploy workflows.
Used by coding agents to run build commands, tests, linting, and push
code changes that trigger CI/CD pipelines.
"""
import asyncio
import logging
import subprocess
from typing import Optional

from mcp.server.fastmcp import Context

from src.mcp_app import mcp

logger = logging.getLogger(__name__)

# Output size limits to prevent memory issues
_MAX_STDOUT_BYTES = 10 * 1024  # 10 KB
_MAX_STDERR_BYTES = 5 * 1024   # 5 KB
_MAX_TIMEOUT = 300              # 5 minutes

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


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def execute_shell(
    command: str,
    working_dir: str = "/app",
    timeout: int = 60,
    ctx: Context = None,
) -> dict:
    """Execute a shell command and return its output.

    Runs any shell command in a subprocess, capturing stdout and stderr.
    Use this to run build commands, tests, linting, file inspection, or
    any CLI tool available in the container.

    Args:
        command: The shell command to execute (e.g. "python -m pytest tests/"). Required.
        working_dir: Working directory for the command. Defaults to "/app".
        timeout: Maximum seconds before the command is killed. Defaults to 60, max 300.
        ctx: MCP request context (injected automatically).

    Returns:
        Dict with stdout, stderr, return_code, and the original command.
        Output is truncated to 10KB stdout / 5KB stderr.
    """
    if not command:
        return {"error": "command is required."}

    timeout = min(max(timeout, 1), _MAX_TIMEOUT)
    logger.info("execute_shell: %s (cwd=%s, timeout=%ds)", command, working_dir, timeout)

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
