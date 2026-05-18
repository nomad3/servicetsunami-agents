"""OpenCode chat executor — hoisted from workflows.py in Phase 1.6.

OpenCode is the local-first chat path: a persistent OpenCode server
running on OPENCODE_PORT in the worker container, talking to a native
Ollama hosting Gemma 4. This module owns:

  * the 3 module-level OPENCODE_* env-driven constants
  * the per-tenant session cache (_opencode_sessions)
  * the public ``execute_opencode_chat`` (server path, with CLI fallback)
  * the internal ``_execute_opencode_chat_cli`` (subprocess fallback)

Bodies are byte-identical to their previous home in workflows.py.
ChatCliResult is imported lazily inside the function
body to break the workflows<->cli_executors cycle.
"""
from __future__ import annotations

import httpx
import json
import logging
import os
import subprocess
import time

import cli_runtime
import tenant_home_quota

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# OpenCode CLI — local Gemma 4 via Ollama with MCP tool access
# ---------------------------------------------------------------------------

OPENCODE_OLLAMA_URL = os.environ.get("OPENCODE_OLLAMA_URL", "http://host.docker.internal:11434/v1")
OPENCODE_MODEL = os.environ.get("OPENCODE_MODEL", "gemma4")
OPENCODE_PORT = int(os.environ.get("OPENCODE_PORT", "8200"))

# Per-tenant OpenCode session cache (tenant_id → session_id)
_opencode_sessions: dict[str, str] = {}


def execute_opencode_chat(task_input, session_dir: str):
    """Execute a chat turn via the persistent OpenCode server (local Gemma 4).

    Uses the in-process OpenCode server started by entrypoint.sh on OPENCODE_PORT.
    Creates one session per tenant for context continuity. Falls back to `opencode run`
    if the server is unreachable.
    """
    from workflows import ChatCliResult
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

        # Wrap message with tenant context if MCP is enabled
        prompt = task_input.message
        if task_input.mcp_config:
            # Inject tenant_id so MCP tools know which data to access
            context_prefix = (
                f"[Context: tenant_id={tenant}. "
                f"Always pass tenant_id in ALL MCP tool calls.]\n\n"
            )
            prompt = context_prefix + prompt

        # Send message to OpenCode server.
        #
        # OpenCode adopted a multipart message schema. The body must be
        # `{"parts": [{"type": "text", "text": "..."}]}`, not the old
        # `{"message": "..."}`. The mismatch caused every fallback to
        # 400 with `expected array, received undefined` on `parts`,
        # which surfaced to users as `OpenCode server failed (400 Bad
        # Request)` and the CLI fallback (also broken) emitted raw
        # JSON like `CLI exit 1: {"type":"session.skills_loaded",...}`
        # into chat. Live diagnostic 2026-05-05.
        resp = httpx.post(
            f"{base_url}/session/{session_id}/message",
            json={"parts": [{"type": "text", "text": prompt}]},
            timeout=120,  # Local LLM can be slow
        )
        resp.raise_for_status()
        data = resp.json()

        # Response shape also moved to multipart. Try the new shape first
        # (parts[].text concatenated), fall back to the legacy `response`
        # field if the server downgraded for compatibility.
        text = ""
        for part in (data.get("parts") or []):
            if isinstance(part, dict) and part.get("type") == "text":
                text += part.get("text", "")
        if not text:
            text = data.get("response", "")
        meta = {
            "platform": "opencode",
            "session_id": session_id,
            "model": OPENCODE_MODEL,
            "usage": data.get("usage", {}),
        }
        return ChatCliResult(response_text=text, success=True, metadata=meta)

    except Exception as e:
        logger.warning("OpenCode server failed (%s), falling back to CLI", e)
        # Fallback to CLI
        return _execute_opencode_chat_cli(task_input, session_dir)


def _execute_opencode_chat_cli(task_input, session_dir: str):
    """Fallback: Execute opencode turn via CLI subprocess."""
    from workflows import ChatCliResult, WORKSPACE
    import os
    import subprocess

    prompt = task_input.message
    if task_input.mcp_config:
        context_prefix = f"[Context: tenant_id={task_input.tenant_id}]\n\n"
        prompt = context_prefix + prompt

    # ── tenant workspace cwd (task #259) ─────────────────────────────────
    # Even on the CLI fallback path, scope cwd to the tenant's persistent
    # workspace projects dir so any files OpenCode writes via tools land
    # in the shared ``workspaces`` volume and show in FileTreePanel.
    _cwd_fallback = WORKSPACE if os.path.isdir(WORKSPACE) else session_dir
    cli_cwd = cli_runtime.resolve_cli_cwd(task_input, _cwd_fallback)
    env = {**os.environ, "WORKSPACE": cli_cwd}
    # ── tenant HOME on workspaces volume (task #267 Phase 1) ────────────
    # Redirect HOME onto the persistent workspaces volume so OpenCode's
    # ``.local`` / ``.cache`` / ``--user`` installs survive container
    # recycles AND don't grow the code-worker writable layer.
    tenant_home_path: str | None = None
    try:
        tenant_home_path = str(cli_runtime.tenant_home_dir(task_input.tenant_id))
        env["HOME"] = tenant_home_path
    except (ValueError, OSError) as exc:
        logger.warning(
            "tenant_home_dir(%s) failed (%s); HOME falls back to container default",
            task_input.tenant_id, exc,
        )

    cmd = ["opencode", "run", "-p", prompt, "-y", "--output-format", "json"]
    result: subprocess.CompletedProcess | None = None
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300,
            cwd=cli_cwd, env=env,
        )
        if result.returncode != 0:
            err = cli_runtime.safe_cli_error_snippet(result.stderr, result.stdout, 1000)
            return ChatCliResult(
                response_text="",
                success=False,
                error=f"OpenCode CLI failed: {err}",
            )

        data = json.loads(result.stdout)
        return ChatCliResult(
            response_text=data.get("response", ""),
            success=True,
            metadata={"platform": "opencode_cli", "model": OPENCODE_MODEL},
        )
    except Exception as e:
        return ChatCliResult(response_text="", success=False, error=str(e))
    finally:
        # Phase 2 quota walker (task #264) — OpenCode doesn't go through
        # the SessionEventEmitter so there's no real chunk counter to
        # pass. Approximate from stdout size (~256B per "chunk-ish unit")
        # so the watermark gate's delta logic still kicks in for big
        # outputs without spuriously firing on tiny ones.
        if tenant_home_path:
            _stdout_len = 0
            try:
                if result is not None and result.stdout:
                    _stdout_len = len(result.stdout)
            except Exception:  # noqa: BLE001
                _stdout_len = 0
            tenant_home_quota.maybe_enforce_quota(
                task_input.tenant_id,
                tenant_home_path,
                cumulative_chunks=_stdout_len // 256,
            )
