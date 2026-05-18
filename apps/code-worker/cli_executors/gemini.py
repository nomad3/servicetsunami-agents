"""Gemini CLI chat executor — hoisted from workflows.py in Phase 1.6.

Body byte-identical to the previous ``_execute_gemini_chat`` (only the
two ``cli_runtime.*`` call sites differ). Workflows-side helpers
(_fetch_integration_credentials, _prepare_gemini_home,
_prepare_gemini_home_apikey, _INTEGRATION_NOT_CONNECTED_MESSAGES,
ChatCliResult, WORKSPACE) are imported lazily inside
the function body.

The module owns its own ``logger`` so log records are namespaced under
``cli_executors.gemini``. Behavior unchanged.
"""
from __future__ import annotations

import json
import logging
import os
import re

import cli_runtime
from cli_executors import gemini_stream_parser
from session_event_emitter import SessionEventEmitter

logger = logging.getLogger(__name__)


def execute_gemini_chat(task_input, session_dir: str, image_path: str):
    from workflows import (
        _fetch_integration_credentials,
        _prepare_gemini_home,
        _prepare_gemini_home_apikey,
        _INTEGRATION_NOT_CONNECTED_MESSAGES,
        ChatCliResult,
        WORKSPACE,
    )
    # Check for API key first (simplest auth — no OAuth needed)
    api_key = os.environ.get("GEMINI_API_KEY", "")

    if not api_key:
        try:
            creds = _fetch_integration_credentials("gemini_cli", task_input.tenant_id)
            # Also check if the tenant stored an api_key credential
            api_key = creds.get("api_key", "")
        except Exception as exc:
            return ChatCliResult(response_text="", success=False, error=f"Failed to load Gemini credentials: {exc}")

    # ── tenant HOME on workspaces volume (task #267 Phase 1) ────────────
    # Gemini CLI reads its credentials and per-tenant ``.gemini/`` state
    # from ``$HOME/.gemini/``, so it's the one executor where the HOME
    # redirect must thread through the prep helper too — otherwise the
    # CLI looks for ``oauth_creds.json`` in the persistent-volume HOME
    # while we wrote it into the legacy session_dir. ``tenant_home`` falls
    # back to ``session_dir`` for non-UUID tenant_ids (same defensive
    # shape as ``resolve_cli_cwd``) so this is a pure no-op in tests that
    # don't mount WORKSPACES_ROOT.
    try:
        tenant_home = str(cli_runtime.tenant_home_dir(task_input.tenant_id))
    except (ValueError, OSError):
        tenant_home = session_dir
    if api_key:
        # API key auth — no OAuth, no ADC, just set GEMINI_API_KEY env var
        gemini_home = _prepare_gemini_home_apikey(tenant_home, task_input.mcp_config)
    else:
        # OAuth auth — write the persisted oauth_creds.json blob to disk so the
        # Gemini CLI uses its own client_id binding (refresh tokens are bound to
        # the issuing client_id and cannot be cross-client refreshed).
        oauth_creds_blob = creds.get("oauth_creds")
        oauth_token = creds.get("oauth_token") or creds.get("session_token")
        refresh_token = creds.get("refresh_token")

        if not oauth_creds_blob and not oauth_token:
            logger.error("No oauth_creds, oauth_token or session_token found in creds: %s", list(creds.keys()))
            return ChatCliResult(
                response_text="",
                success=False,
                error=_INTEGRATION_NOT_CONNECTED_MESSAGES["gemini_cli"],
            )

        logger.info("Gemini creds: oauth_creds blob=%s, oauth_token=%s, refresh_token=%s",
                    bool(oauth_creds_blob), bool(oauth_token), bool(refresh_token))

        auth_payload = {
            "oauth_creds": oauth_creds_blob,
            "access_token": oauth_token,
            "refresh_token": refresh_token,
            "email": creds.get("email"),
        }
        gemini_home = _prepare_gemini_home(tenant_home, auth_payload, task_input.mcp_config)
    
    if not gemini_home:
        logger.error("gemini_home is None!")
        return ChatCliResult(response_text="", success=False, error="System error: failed to prepare Gemini environment")

    prompt = task_input.message
    if task_input.instruction_md_content.strip():
        # Inject instruction context and previous messages into the prompt body
        prompt = f"{task_input.instruction_md_content.strip()}\n\n# User Request\n\n{task_input.message}"

    cmd = [
        "gemini",
        "-p",
        prompt,
        "-y",
        "--skip-trust",
        "--output-format",
        "json",
    ]

    env = os.environ.copy()
    env["HOME"] = tenant_home  # Tell Gemini CLI where to find .gemini/
    env["GEMINI_TELEMETRY"] = "0"
    # Bypass gemini-cli's "trusted folders" gate. The CLI added it as a
    # safety check for interactive use; in our headless code-worker the
    # session dir is sandboxed and short-lived per task, so the trust
    # check has no defensive value but does break dispatch with
    # exit code 55. Both --skip-trust and the env var are honored.
    env["GEMINI_CLI_TRUST_WORKSPACE"] = "true"

    # Strip every Google/GCP env that would push gemini-cli into Cloud Code
    # Assist enterprise mode (which probes 553113309640 / Cloud Code Private API).
    # We rely entirely on the oauth_creds.json blob written into GEMINI_HOME.
    google_vars = [
        "GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_PROJECT_ID", "GEMINI_PROJECT_ID",
        "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_APPLICATION_CREDENTIALS",
        "GOOGLE_GENAI_USE_VERTEXAI", "GOOGLE_GENAI_USE_GCA", "CLOUDSDK_CORE_PROJECT",
        "CLOUD_SDK_CONFIG", "GCLOUD_PROJECT", "GOOGLE_API_KEY", "GEMINI_API_KEY",
        "GEMINI_AUTH_TOKEN",
    ]
    for var in google_vars:
        env.pop(var, None)

    if api_key:
        env["GEMINI_API_KEY"] = api_key

    # ── tenant workspace cwd (task #259) ─────────────────────────────────
    # Gemini's ``.gemini/settings.json`` is HOME-relative (we set HOME to
    # ``gemini_home`` above), so the only thing the cwd swap affects is
    # where the CLI resolves relative paths for tool writes — exactly
    # what we want for files-in-FileTreePanel.
    _cwd_fallback = WORKSPACE if os.path.isdir(WORKSPACE) else session_dir
    cli_cwd = cli_runtime.resolve_cli_cwd(task_input, _cwd_fallback)
    env["WORKSPACE"] = cli_cwd

    logger.info("GEMINI_HOME: %s", gemini_home)
    for f in ["oauth_creds.json", "credentials.json", "settings.json", "projects.json", "google_accounts.json"]:
        p = os.path.join(gemini_home, f)
        logger.info("Gemini home file %s present=%s", f, os.path.exists(p))

    # ---- streaming emitter (plan 2026-05-16 §4.5 / §2.3) ----
    # Gemini's stdout is a single end-of-run JSON blob, but stderr
    # carries live tool errors. The parser classifies stderr lightly
    # and forwards the rest verbatim.
    emitter = SessionEventEmitter(
        chat_session_id=getattr(task_input, "chat_session_id", "") or "",
        tenant_id=task_input.tenant_id,
        platform="gemini_cli",
        attempt=getattr(task_input, "attempt", 1) or 1,
    )
    on_chunk = gemini_stream_parser.build_parser(emitter) if emitter.enabled else None
    try:
        result = cli_runtime.run_cli_with_heartbeat(
            cmd,
            label="Gemini CLI",
            timeout=1500,
            env=env,
            cwd=cli_cwd,
            on_chunk=on_chunk,
        )
    finally:
        emitter.close()
    logger.info("Gemini CLI exit code: %s", result.returncode)
    if result.stdout:
        logger.info("Gemini CLI stdout: %s", result.stdout[:500])
    if result.stderr:
        logger.warning("Gemini CLI stderr: %s", result.stderr[:500])

    # Parse stderr for tool-call signals. Gemini CLI in --output-format=json
    # does not surface successful tool invocations in stdout; failures appear
    # in stderr as "Error executing tool <name>: <reason>". This is partial
    # observability (failures only) but enough to catch the common
    # hallucination signature: assistant lists specific names but stderr
    # shows zero tool activity.
    #
    # Note the non-greedy `\S+?` followed by `:\s+` — tool names can themselves
    # contain colons (e.g. the made-up `default_api:list_connected_email_accounts`
    # namespace Gemini sometimes invents), so the separator we anchor on is
    # `colon then whitespace`, not just `colon`. Greedy matching here would
    # truncate `default_api:foo` to `default_api`.
    #
    # Shape note: each entry is a dict with `name`, `status`, `error`. Any
    # future consumer that flattens this list (e.g. auto_quality_scorer's
    # `tools_called: list[str]` parameter) must extract `name` first.
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
            metadata={"platform": "gemini_cli", "tools_called": tool_errors},
        )

    raw = result.stdout.strip()
    if not raw:
        return ChatCliResult(
            response_text="",
            success=False,
            error="Gemini produced no output",
            metadata={"platform": "gemini_cli", "tools_called": tool_errors},
        )

    try:
        data = json.loads(raw)
        text = data.get("result") or data.get("response") or data.get("content") or data.get("text") or raw
        meta = {
            "platform": "gemini_cli",
            "input_tokens": (data.get("usage") or {}).get("input_tokens", 0),
            "output_tokens": (data.get("usage") or {}).get("output_tokens", 0),
            "model": data.get("model", "gemini-2.5-pro"),
            "tools_called": tool_errors,
        }
        return ChatCliResult(response_text=text, success=True, metadata=meta)
    except json.JSONDecodeError:
        return ChatCliResult(
            response_text=raw,
            success=True,
            metadata={"platform": "gemini_cli", "tools_called": tool_errors},
        )
