"""Qwen Code CLI chat executor — Wave 1b of the CLI integration catalog.

Mirrors ``cli_executors/gemini.py`` in shape since both ship as npm
packages with a near-identical command surface (``-p`` prompt flag,
``-y`` non-interactive, ``--output-format json`` terminal blob).

Auth strategy
-------------

BYOK first: ``QWEN_API_KEY`` is read from the credential vault (or the
process env on local dev). Lane-B platform-key fallback is intentionally
deferred; if the tenant has no key stored the executor returns the
standard ``_INTEGRATION_NOT_CONNECTED_MESSAGES`` friendly error so the
resolver classifies it as ``missing_credential`` and chains to the next
CLI without burning a cooldown slot.

Streaming
---------

stdout is a single end-of-run JSON blob, but stderr carries live tool
progress and errors. The ``qwen_stream_parser`` classifies stderr lines
and forwards everything else as plain stderr — same shape as gemini.
"""
from __future__ import annotations

import json
import logging
import os
import re

import cli_runtime
from cli_executors import qwen_stream_parser
from session_event_emitter import SessionEventEmitter

logger = logging.getLogger(__name__)


def execute_qwen_chat(task_input, session_dir: str):
    from workflows import (
        _fetch_integration_credentials,
        _INTEGRATION_NOT_CONNECTED_MESSAGES,
        ChatCliResult,
        WORKSPACE,
    )

    # ── credential resolution ────────────────────────────────────────────
    # BYOK: read ``QWEN_API_KEY`` from the tenant vault. Process env is
    # honoured first for local-dev convenience (mirrors gemini.py), but
    # in production every tenant has its own key.
    api_key = os.environ.get("QWEN_API_KEY", "")
    if not api_key:
        try:
            creds = _fetch_integration_credentials("qwen_code", task_input.tenant_id)
            api_key = creds.get("api_key", "") or creds.get("QWEN_API_KEY", "")
        except Exception as exc:
            # Friendly not-connected error — resolver classifies as
            # ``missing_credential`` (chain-skip, no cooldown). A bare
            # exception here would surface as the raw httpx 404 text,
            # which the missing-credential regex doesn't match and the
            # turn would bubble up as a hard failure.
            friendly = _INTEGRATION_NOT_CONNECTED_MESSAGES.get(
                "qwen_code",
                "Qwen Code is not connected. Please connect your Qwen API key in Settings → Integrations.",
            )
            logger.info("Qwen creds fetch failed: %s", exc)
            return ChatCliResult(response_text="", success=False, error=friendly)

    if not api_key:
        return ChatCliResult(
            response_text="",
            success=False,
            error=_INTEGRATION_NOT_CONNECTED_MESSAGES.get(
                "qwen_code",
                "Qwen Code is not connected. Please connect your Qwen API key in Settings → Integrations.",
            ),
        )

    # ── tenant HOME on workspaces volume (task #267 Phase 1) ────────────
    # Qwen CLI reads ``.qwen/`` config from $HOME. Pin it onto the
    # persistent workspaces volume so per-tenant config survives container
    # recycles and doesn't grow the writable layer.
    try:
        tenant_home = str(cli_runtime.tenant_home_dir(task_input.tenant_id))
    except (ValueError, OSError) as exc:
        logger.warning(
            "tenant_home_dir(%s) failed (%s); HOME falls back to session_dir=%s",
            task_input.tenant_id, exc, session_dir,
        )
        tenant_home = session_dir

    prompt = task_input.message
    if task_input.instruction_md_content.strip():
        prompt = f"{task_input.instruction_md_content.strip()}\n\n# User Request\n\n{task_input.message}"

    cmd = [
        "qwen",
        "-p",
        prompt,
        "-y",
        "--output-format",
        "json",
    ]

    env = os.environ.copy()
    env["HOME"] = tenant_home
    env["QWEN_API_KEY"] = api_key
    # Qwen Code reads OpenAI-compatible env vars when targeting DashScope
    # via the OpenAI shim. Set both so either auth path resolves the key
    # without us hard-coding which endpoint the CLI chose.
    env["DASHSCOPE_API_KEY"] = api_key
    env["QWEN_TELEMETRY"] = "0"

    # Strip Google/GCP env that gemini-cli's shared base honors — Qwen
    # forked from gemini-cli and inherited the same enterprise-mode
    # probing. We rely entirely on the API key set above.
    google_vars = [
        "GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_PROJECT_ID", "GEMINI_PROJECT_ID",
        "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_APPLICATION_CREDENTIALS",
        "GOOGLE_GENAI_USE_VERTEXAI", "GOOGLE_GENAI_USE_GCA", "CLOUDSDK_CORE_PROJECT",
        "CLOUD_SDK_CONFIG", "GCLOUD_PROJECT", "GOOGLE_API_KEY", "GEMINI_API_KEY",
        "GEMINI_AUTH_TOKEN",
    ]
    for var in google_vars:
        env.pop(var, None)

    # ── tenant workspace cwd (task #259) ─────────────────────────────────
    _cwd_fallback = WORKSPACE if os.path.isdir(WORKSPACE) else session_dir
    cli_cwd = cli_runtime.resolve_cli_cwd(task_input, _cwd_fallback)
    env["WORKSPACE"] = cli_cwd

    # ── streaming emitter ────────────────────────────────────────────────
    emitter = SessionEventEmitter(
        chat_session_id=getattr(task_input, "chat_session_id", "") or "",
        tenant_id=task_input.tenant_id,
        platform="qwen_code",
        attempt=getattr(task_input, "attempt", 1) or 1,
    )
    on_chunk = qwen_stream_parser.build_parser(emitter) if emitter.enabled else None
    try:
        result = cli_runtime.run_cli_with_heartbeat(
            cmd,
            label="Qwen Code CLI",
            timeout=1500,
            env=env,
            cwd=cli_cwd,
            on_chunk=on_chunk,
        )
    finally:
        emitter.close()

    logger.info("Qwen Code CLI exit code: %s", result.returncode)
    if result.stdout:
        logger.info("Qwen Code CLI stdout: %s", result.stdout[:500])
    if result.stderr:
        logger.warning("Qwen Code CLI stderr: %s", result.stderr[:500])

    # Parse stderr for tool-call failure signals. Same shape as gemini —
    # Qwen Code inherits the parent CLI's stderr conventions, so the
    # "Error executing tool X: Y" line format is identical.
    tool_errors: list[dict] = []
    if result.stderr:
        for m in re.finditer(r"Error executing tool (\S+?):\s+(.+)", result.stderr):
            tool_errors.append({
                "name": m.group(1).strip(),
                "status": "error",
                "error": m.group(2).strip()[:300],
            })

    if result.returncode != 0:
        err = cli_runtime.safe_cli_error_snippet(result.stderr, result.stdout, 2000)
        return ChatCliResult(
            response_text="",
            success=False,
            error=f"CLI exit {result.returncode}: {err}",
            metadata={"platform": "qwen_code", "tools_called": tool_errors},
        )

    raw = result.stdout.strip()
    if not raw:
        return ChatCliResult(
            response_text="",
            success=False,
            error="Qwen Code produced no output",
            metadata={"platform": "qwen_code", "tools_called": tool_errors},
        )

    try:
        data = json.loads(raw)
        text = data.get("result") or data.get("response") or data.get("content") or data.get("text") or raw
        meta = {
            "platform": "qwen_code",
            "input_tokens": (data.get("usage") or {}).get("input_tokens", 0),
            "output_tokens": (data.get("usage") or {}).get("output_tokens", 0),
            "model": data.get("model", "qwen-coder"),
            "tools_called": tool_errors,
        }
        return ChatCliResult(response_text=text, success=True, metadata=meta)
    except json.JSONDecodeError:
        return ChatCliResult(
            response_text=raw,
            success=True,
            metadata={"platform": "qwen_code", "tools_called": tool_errors},
        )
