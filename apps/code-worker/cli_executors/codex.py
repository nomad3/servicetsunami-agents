"""Codex chat executor — hoisted from workflows.py in Phase 1.6.

Body byte-identical to the previous ``_execute_codex_chat`` (only the
two ``cli_runtime.*`` call sites differ from the workflows.py original).
Workflows-side helpers are imported lazily inside the function body.
"""
from __future__ import annotations

import json
import os

import cli_runtime
from cli_executors import codex_stream_parser
from session_event_emitter import SessionEventEmitter


def execute_codex_chat(task_input, session_dir: str, image_path: str):
    from workflows import (
        _fetch_integration_credentials,
        _INTEGRATION_NOT_CONNECTED_MESSAGES,
        _prepare_codex_home,
        _extract_codex_last_message,
        _extract_codex_metadata,
        ChatCliResult,
        WORKSPACE,
    )
    try:
        creds = _fetch_integration_credentials("codex", task_input.tenant_id)
    except Exception as exc:
        return ChatCliResult(response_text="", success=False, error=f"Failed to load Codex credentials: {exc}")

    raw_auth = creds.get("auth_json") or creds.get("session_token")
    if not raw_auth:
        return ChatCliResult(
            response_text="",
            success=False,
            error=_INTEGRATION_NOT_CONNECTED_MESSAGES["codex"],
        )

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

    # ── tenant workspace cwd (task #259) ─────────────────────────────────
    # Codex's ``-C <dir>`` is the project root + cwd. Point both at the
    # tenant's persistent workspace projects dir so plan files land in
    # the shared ``workspaces`` volume and appear in FileTreePanel.
    # ``CODEX_HOME`` is HOME-relative (~/.codex) and unaffected by cwd —
    # we keep the explicit env var below for the auth blob.
    _cwd_fallback = WORKSPACE if os.path.isdir(WORKSPACE) else session_dir
    cli_cwd = cli_runtime.resolve_cli_cwd(task_input, _cwd_fallback)

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
        cli_cwd,
    ]

    # ``--skip-git-repo-check`` must ALWAYS be passed now that ``-C`` may
    # point at the tenant workspace projects dir (freshly ``mkdir``'d on
    # first turn, no ``.git`` inside). Without it codex refuses to run
    # with "not a git repository, are you sure?". Even on the legacy
    # ``WORKSPACE``-fallback branch this is harmless — codex no-ops the
    # flag when the cwd IS a git repo. See review B1 on PR #532.
    cmd.extend(["--skip-git-repo-check"])
    if os.path.isdir(WORKSPACE):
        cmd.extend(["--add-dir", session_dir])

    if image_path:
        cmd.extend(["--image", image_path])

    env = os.environ.copy()
    env["CODEX_HOME"] = codex_home
    env["WORKSPACE"] = cli_cwd
    # ── tenant HOME on workspaces volume (task #267 Phase 1) ────────────
    # ``CODEX_HOME`` already pins codex's own state dir, but the codex
    # subprocess will still ``pip install --user`` / write ``.cache`` /
    # spawn sandboxed skills that honour ``$HOME``. Redirect HOME onto
    # the persistent workspaces volume so that growth doesn't land on
    # the code-worker writable layer.
    try:
        env["HOME"] = str(cli_runtime.tenant_home_dir(task_input.tenant_id))
    except (ValueError, OSError):
        pass

    # ---- streaming emitter (plan 2026-05-16 §4.5) ----
    # codex --json already streams NDJSON; the parser maps each line
    # to a chunk_kind for live terminal rendering. Emitter is a no-op
    # when chat_session_id is missing.
    emitter = SessionEventEmitter(
        chat_session_id=getattr(task_input, "chat_session_id", "") or "",
        tenant_id=task_input.tenant_id,
        platform="codex",
        attempt=getattr(task_input, "attempt", 1) or 1,
    )
    on_chunk = codex_stream_parser.build_parser(emitter) if emitter.enabled else None

    try:
        result = cli_runtime.run_cli_with_heartbeat(
            cmd,
            label="Codex",
            timeout=1500,
            env=env,
            cwd=cli_cwd,
            on_chunk=on_chunk,
        )
    finally:
        emitter.close()
    if result.returncode != 0:
        err = cli_runtime.safe_cli_error_snippet(result.stderr, result.stdout, 2000)
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
