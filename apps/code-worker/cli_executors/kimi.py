"""Kimi K2 (Moonshot AI) CLI chat executor — Wave 1c.

Kimi K2 is Moonshot AI's coding-tuned model. The model weights ship under
Apache 2.0 (Lane B per ``docs/plans/2026-05-18-cli-integration-catalog.md``
— commercial resale permitted). The API surface is OpenAI-compatible:

  * Base URL: ``https://api.moonshot.ai/v1`` (international tier; the
    Chinese tier is ``https://api.moonshot.cn/v1``).
  * Auth: ``MOONSHOT_API_KEY`` bearer token.
  * Default model: ``kimi-k2-instruct``.

The official CLI is ``kimi-cli`` (npm: ``@moonshotai/kimi-cli``). The
code-worker Dockerfile globally installs the package alongside the other
``npm install -g`` CLIs (Claude Code / Codex / Gemini / Copilot), so the
binary is on PATH at runtime. The executor falls back to
``npx @moonshotai/kimi-cli`` if the binary isn't resolvable — useful for
local dev shells that haven't run the global install.

The shape mirrors ``cli_executors.gemini`` and ``cli_executors.codex``:

  * Credentials fetched via ``_fetch_integration_credentials("kimi_k2", ...)``
    from the tenant vault (the integration card on the Integrations page
    stores ``api_key``).
  * ``MOONSHOT_API_KEY`` falls back to the process env if no per-tenant
    credential is wired, which lets a single shared MOONSHOT_API_KEY
    operator-provisioned at container start work for every tenant.
  * Tenant HOME redirected onto the workspaces volume (task #267 Phase 1).
  * cwd resolved against the tenant workspace projects dir (task #259).
  * Streaming pump uses the passthrough parser — kimi-cli's JSONL stream
    shape is not yet stable enough to write a typed mapper for. Once it
    settles we can promote it to a dedicated stream parser like
    ``gemini_stream_parser`` without changing the executor body.
"""
from __future__ import annotations

import json
import logging
import os
import shutil

import cli_runtime
from cli_executors import passthrough_stream_parser
from session_event_emitter import SessionEventEmitter

logger = logging.getLogger(__name__)


# Default model + base URL. Both are overridable per-tenant by storing a
# ``model`` or ``base_url`` credential in the integration vault, or per
# container by setting ``KIMI_MODEL`` / ``MOONSHOT_BASE_URL`` env vars.
# International endpoint is the default — switch to ``api.moonshot.cn``
# only for tenants who need the Chinese-region tier.
_DEFAULT_MODEL = os.environ.get("KIMI_MODEL", "kimi-k2-instruct")
_DEFAULT_BASE_URL = os.environ.get("MOONSHOT_BASE_URL", "https://api.moonshot.ai/v1")


def _resolve_cli_binary() -> list[str]:
    """Return the argv prefix for invoking kimi-cli.

    Prefers the globally installed ``kimi`` binary (installed by
    ``apps/code-worker/Dockerfile`` via ``npm install -g
    @moonshotai/kimi-cli``). Falls back to ``npx @moonshotai/kimi-cli``
    when the global install isn't on PATH — useful in local dev shells
    and in tests that don't bake the npm package into the image.
    """
    if shutil.which("kimi"):
        return ["kimi"]
    return ["npx", "--yes", "@moonshotai/kimi-cli"]


def execute_kimi_chat(task_input, session_dir: str):
    from workflows import (
        _fetch_integration_credentials,
        _INTEGRATION_NOT_CONNECTED_MESSAGES,
        ChatCliResult,
        WORKSPACE,
    )

    # ── credential resolution ─────────────────────────────────────────
    # Order: env var (operator shared key) → tenant vault (per-tenant key).
    # Vault wins when both are present — a tenant that bothered to wire
    # their own MOONSHOT_API_KEY into the Integrations page expects it
    # to be used.
    api_key = ""
    base_url = _DEFAULT_BASE_URL
    model = _DEFAULT_MODEL
    try:
        creds = _fetch_integration_credentials("kimi_k2", task_input.tenant_id)
        api_key = creds.get("api_key", "") or ""
        base_url = creds.get("base_url", "") or base_url
        model = creds.get("model", "") or model
    except Exception as exc:
        # Vault miss is the common-case "tenant didn't connect Kimi yet"
        # path; fall back to the shared env var. Only return a friendly
        # not-connected error if BOTH paths are empty.
        logger.info("Kimi vault lookup failed (%s); falling back to env", exc)

    if not api_key:
        api_key = os.environ.get("MOONSHOT_API_KEY", "")

    if not api_key:
        return ChatCliResult(
            response_text="",
            success=False,
            error=_INTEGRATION_NOT_CONNECTED_MESSAGES.get(
                "kimi_k2",
                "Kimi K2 is not connected. Please paste a MOONSHOT_API_KEY "
                "in Settings → Integrations.",
            ),
        )

    # ── tenant HOME on workspaces volume (task #267 Phase 1) ─────────
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
        prompt = (
            f"{task_input.instruction_md_content.strip()}"
            f"\n\n# User Request\n\n{task_input.message}"
        )

    # ── build argv ────────────────────────────────────────────────────
    # kimi-cli mirrors the qwen-code / gemini-cli shape: prompt is passed
    # via ``-p`` (or stdin), and ``--output-format json`` produces a
    # single end-of-run JSON blob we can parse.
    cmd = _resolve_cli_binary() + [
        "-p", prompt,
        "--model", model,
        "--output-format", "json",
        "-y",
    ]

    # ── env propagation ───────────────────────────────────────────────
    # kimi-cli reads MOONSHOT_API_KEY and (optionally) MOONSHOT_BASE_URL
    # from env. Some forks read OPENAI_API_KEY / OPENAI_BASE_URL because
    # the wire shape is OpenAI-compatible; we export both pairs so the
    # CLI picks whichever convention it's compiled against. Stripping
    # any existing OPENAI_* avoids a stray ChatGPT key leaking into a
    # Kimi request.
    env = os.environ.copy()
    env["HOME"] = tenant_home
    env["MOONSHOT_API_KEY"] = api_key
    env["MOONSHOT_BASE_URL"] = base_url
    env["OPENAI_API_KEY"] = api_key
    env["OPENAI_BASE_URL"] = base_url

    # ── tenant workspace cwd (task #259) ─────────────────────────────
    _cwd_fallback = WORKSPACE if os.path.isdir(WORKSPACE) else session_dir
    cli_cwd = cli_runtime.resolve_cli_cwd(task_input, _cwd_fallback)
    env["WORKSPACE"] = cli_cwd

    # ── streaming emitter ────────────────────────────────────────────
    emitter = SessionEventEmitter(
        chat_session_id=getattr(task_input, "chat_session_id", "") or "",
        tenant_id=task_input.tenant_id,
        platform="kimi_k2",
        attempt=getattr(task_input, "attempt", 1) or 1,
    )
    on_chunk = passthrough_stream_parser.build_parser(emitter) if emitter.enabled else None
    try:
        result = cli_runtime.run_cli_with_heartbeat(
            cmd,
            label="Kimi K2 CLI",
            timeout=1500,
            env=env,
            cwd=cli_cwd,
            on_chunk=on_chunk,
        )
    finally:
        emitter.close()

    logger.info("Kimi CLI exit code: %s", result.returncode)
    if result.stdout:
        logger.info("Kimi CLI stdout: %s", result.stdout[:500])
    if result.stderr:
        logger.warning("Kimi CLI stderr: %s", result.stderr[:500])

    if result.returncode != 0:
        err = cli_runtime.safe_cli_error_snippet(result.stderr, result.stdout, 2000)
        return ChatCliResult(
            response_text="",
            success=False,
            error=f"CLI exit {result.returncode}: {err}",
            metadata={"platform": "kimi_k2"},
        )

    raw = (result.stdout or "").strip()
    if not raw:
        return ChatCliResult(
            response_text="",
            success=False,
            error="Kimi K2 produced no output",
            metadata={"platform": "kimi_k2"},
        )

    # kimi-cli emits the same OpenAI-shape JSON wrappers as the other
    # OpenAI-compatible CLIs (gemini --output-format=json, qwen-code).
    # Try the common keys before falling back to the raw text.
    try:
        data = json.loads(raw)
        text = (
            data.get("result")
            or data.get("response")
            or data.get("content")
            or data.get("text")
            or raw
        )
        usage = data.get("usage") or {}
        meta = {
            "platform": "kimi_k2",
            "input_tokens": usage.get("input_tokens") or usage.get("prompt_tokens") or 0,
            "output_tokens": usage.get("output_tokens") or usage.get("completion_tokens") or 0,
            "model": data.get("model", model),
        }
        return ChatCliResult(response_text=text, success=True, metadata=meta)
    except json.JSONDecodeError:
        return ChatCliResult(
            response_text=raw,
            success=True,
            metadata={"platform": "kimi_k2", "model": model},
        )
