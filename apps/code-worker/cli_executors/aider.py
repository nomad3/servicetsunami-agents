"""Aider chat executor — Wave 2c of the CLI integration catalog (#272).

Aider (https://aider.chat — Apache 2.0) is a Python CLI that pair-programs
against any provider you give it an API key for: Anthropic, OpenAI,
DeepSeek, Mistral, Ollama, Bedrock, Vertex, etc. It ships on PyPI as the
``aider-chat`` package and exposes a single binary, ``aider``, on
``$PATH`` once installed.

Auth model (BYOK to any provider)
---------------------------------

Aider does NOT have its own API surface — it's a thin shell over LiteLLM,
so the auth env var depends on which model the tenant pinned:

  * ``anthropic/claude-*``  → ``ANTHROPIC_API_KEY``
  * ``openai/gpt-*``        → ``OPENAI_API_KEY``
  * ``deepseek/deepseek-*`` → ``DEEPSEEK_API_KEY``
  * ``gemini/gemini-*``     → ``GEMINI_API_KEY``
  * (everything LiteLLM supports follows the same pattern.)

To avoid making the tenant juggle N separate integration cards, the
``aider`` card asks for ONE blob: ``model`` + ``api_key``. The executor
derives the right env var name from the model prefix and sets it on the
subprocess.

Invocation surface
------------------

``aider --model <model> --no-show-model-warnings --yes-always
        --no-stream --message <prompt>``

  * ``--no-stream`` returns the full completion at once. Real streaming
    requires the chat-API integration (not via CLI) which is a follow-up;
    non-streaming is fine for v1 because the outer activity has its own
    heartbeat.
  * ``--yes-always`` auto-confirms all file-write prompts. Critical: an
    interactive aider blocks forever otherwise.
  * ``--no-show-model-warnings`` silences the "this model isn't tested"
    banner that prefixes stderr for non-canonical model slugs.
  * ``--message`` runs a single one-shot turn (vs the default REPL) and
    exits when the assistant finishes.

Workspace cwd
-------------

Aider scopes file edits to its cwd. We point it at the per-tenant
workspace (same convention as codex / claude_code) so plan files land
on the persistent volume and appear in the FileTreePanel.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Tuple

import cli_runtime
from cli_executors import passthrough_stream_parser
from session_event_emitter import SessionEventEmitter

logger = logging.getLogger(__name__)


# Default model is Anthropic's mid-tier Claude — most tenants who connect
# Aider already have an Anthropic key on hand. Overridable per-tenant via
# the integration card or per-turn via ``ChatCliInput.model``.
_DEFAULT_MODEL = os.environ.get(
    "AIDER_DEFAULT_MODEL", "anthropic/claude-3-5-sonnet-20241022",
)

# Map LiteLLM provider prefix → env var Aider expects. LiteLLM's full
# table covers ~30 providers; we ship the handful we actively support
# and fall through to ``OPENAI_API_KEY`` for unknown prefixes (which is
# what Aider itself does when the model slug has no recognised vendor
# prefix — it assumes OpenAI).
_PROVIDER_KEY_ENV: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "claude": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gpt": "OPENAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "google": "GEMINI_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "groq": "GROQ_API_KEY",
    "cohere": "COHERE_API_KEY",
    "bedrock": "AWS_ACCESS_KEY_ID",
    "ollama": "OLLAMA_API_BASE",
    "azure": "AZURE_API_KEY",
}


def _env_var_for_model(model: str) -> str:
    """Pick the right LiteLLM-flavoured env var from the model slug.

    LiteLLM convention is ``<provider>/<model>`` — strip the prefix
    before the first ``/`` and look it up. Unknown prefix falls back to
    ``OPENAI_API_KEY`` (matches Aider's own heuristic).
    """
    if not model:
        return "OPENAI_API_KEY"
    prefix = model.split("/", 1)[0].lower()
    return _PROVIDER_KEY_ENV.get(prefix, "OPENAI_API_KEY")


def _extract_response(stdout: str) -> Tuple[str, dict]:
    """Pull the assistant turn out of Aider's terminal-formatted stdout.

    Aider prints a banner, the diff (if any), then the assistant message
    block, then a footer ("Tokens: X sent, Y received."). With
    ``--no-stream`` the assistant block is the trailing chunk before the
    footer. We strip ANSI, drop the banner / footer noise, and return
    the middle.

    Metadata: token counts come from the footer if present. Best-effort —
    Aider's output format isn't guaranteed stable, but this matches
    0.65.0..0.78.x.
    """
    if not stdout:
        return "", {}

    # Strip ANSI colour codes — Aider colourises with rich when stdout
    # is a TTY. We allocate a PTY in run_cli_with_heartbeat so the CLI
    # thinks it's a terminal; that's necessary for ``--yes-always`` to
    # not block, but it also means we have to scrub the codes.
    ansi_re = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
    text = ansi_re.sub("", stdout)

    # Pull tokens-used line for metadata before we slice the body.
    meta: dict = {}
    tok_match = re.search(
        r"Tokens:\s*([\d,\.]+)k?\s*sent,\s*([\d,\.]+)k?\s*received",
        text,
        re.IGNORECASE,
    )
    if tok_match:
        def _to_int(s: str) -> int:
            s = s.replace(",", "")
            try:
                return int(float(s) * (1000 if s.replace(".", "").isdigit() and "." in s else 1))
            except ValueError:
                return 0
        meta["input_tokens"] = _to_int(tok_match.group(1))
        meta["output_tokens"] = _to_int(tok_match.group(2))

    # Aider banner ends with a line like ``Aider v0.65.0`` and the
    # session header ``Model: ...``. Drop everything up to the first
    # ``> `` prompt OR the ``Repo-map: ...`` line, whichever appears.
    lines = text.splitlines()
    start = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("Repo-map:") or stripped.startswith("> ") or stripped.startswith("─"):
            start = i + 1
            break

    # Trailing footer: drop ``Tokens: ...`` and ``Cost: ...`` lines.
    end = len(lines)
    for i in range(len(lines) - 1, -1, -1):
        stripped = lines[i].strip()
        if stripped.startswith("Tokens:") or stripped.startswith("Cost:"):
            end = i
            continue
        if stripped:
            break

    body = "\n".join(lines[start:end]).strip()
    if not body:
        # Fall back to the full stripped text — better than empty.
        body = text.strip()
    return body, meta


def execute_aider_chat(task_input, session_dir: str):
    from workflows import (
        _fetch_integration_credentials,
        _INTEGRATION_NOT_CONNECTED_MESSAGES,
        ChatCliResult,
        WORKSPACE,
    )

    # ── credential resolution ────────────────────────────────────────
    # The vault stores two fields on the ``aider`` card: ``model`` (the
    # LiteLLM-style slug) and ``api_key`` (the provider key matching
    # that model). Env-var fallback lets an operator wire a shared key
    # in without per-tenant config.
    api_key = ""
    model = _DEFAULT_MODEL
    try:
        creds = _fetch_integration_credentials("aider", task_input.tenant_id)
        api_key = creds.get("api_key", "") or ""
        model = creds.get("model", "") or model
    except Exception as exc:
        logger.info("Aider vault lookup failed (%s); falling back to env", exc)

    if not api_key:
        # Try the env var matching the chosen model; this is how a
        # shared operator key wires in. We honour any of the known
        # provider keys — first match wins.
        guessed = _env_var_for_model(model)
        api_key = os.environ.get(guessed, "")
        if not api_key:
            api_key = os.environ.get("AIDER_MODEL_API_KEY", "")

    if not api_key:
        return ChatCliResult(
            response_text="",
            success=False,
            error=_INTEGRATION_NOT_CONNECTED_MESSAGES.get(
                "aider",
                "Aider is not connected. "
                "Please connect your Aider account in Settings → Integrations.",
            ),
        )

    # Per-turn model override (e.g. an agent wants a one-shot Haiku call).
    requested_model = getattr(task_input, "model", "") or ""
    if requested_model:
        model = requested_model

    # ── tenant HOME on workspaces volume (task #267 Phase 1) ────────
    # Aider writes ``.aider.chat.history.md`` and ``.aider.input.history``
    # into the cwd, plus a ``.aider.tags.cache`` to ``~/.aider`` — pin
    # HOME onto the persistent volume so the cache survives recycles.
    try:
        tenant_home = str(cli_runtime.tenant_home_dir(task_input.tenant_id))
    except (ValueError, OSError) as exc:
        logger.warning(
            "tenant_home_dir(%s) failed (%s); HOME falls back to session_dir=%s",
            task_input.tenant_id, exc, session_dir,
        )
        tenant_home = session_dir
    os.makedirs(tenant_home, exist_ok=True)

    # ── workspace cwd ────────────────────────────────────────────────
    _cwd_fallback = WORKSPACE if os.path.isdir(WORKSPACE) else session_dir
    cli_cwd = cli_runtime.resolve_cli_cwd(task_input, _cwd_fallback)

    # Compose prompt — instruction header + user message, same shape as
    # the other executors so agents that ship a persona via
    # ``instruction_md_content`` get consistent behaviour.
    prompt = task_input.message
    if task_input.instruction_md_content.strip():
        prompt = f"{task_input.instruction_md_content.strip()}\n\n# User Request\n\n{task_input.message}"

    cmd = [
        "aider",
        "--model", model,
        "--no-show-model-warnings",
        "--yes-always",
        "--no-stream",
        "--no-auto-commits",  # we own the commit policy at the workflow layer
        "--no-pretty",  # disable rich-style formatting so plain stdout is clean
        "--no-check-update",
        "--message", prompt,
    ]

    env = os.environ.copy()
    env["HOME"] = tenant_home
    env["WORKSPACE"] = cli_cwd
    # Inject the provider key under the right env var. We set BOTH the
    # model-derived var (so Aider/LiteLLM finds it) AND a generic alias
    # for diagnostics — harmless if duplicated.
    key_env = _env_var_for_model(model)
    env[key_env] = api_key
    env["AIDER_MODEL_API_KEY"] = api_key
    # Aider phones home for telemetry unless told otherwise.
    env["AIDER_ANALYTICS"] = "false"
    # Stop Aider from prompting for a one-time onboarding banner — only
    # honoured on >=0.65 but harmless on older versions.
    env["AIDER_GITIGNORE"] = "false"

    # ── streaming emitter ────────────────────────────────────────────
    emitter = SessionEventEmitter(
        chat_session_id=getattr(task_input, "chat_session_id", "") or "",
        tenant_id=task_input.tenant_id,
        platform="aider",
        attempt=getattr(task_input, "attempt", 1) or 1,
    )
    # Passthrough parser is fine for v1 — Aider's stdout/stderr shape is
    # human-readable, not structured. A dedicated parser can come later.
    on_chunk = passthrough_stream_parser.build_parser(emitter) if emitter.enabled else None

    try:
        result = cli_runtime.run_cli_with_heartbeat(
            cmd,
            label="Aider",
            timeout=1500,
            env=env,
            cwd=cli_cwd,
            on_chunk=on_chunk,
        )
    finally:
        emitter.close()

    if result.returncode != 0:
        err = cli_runtime.safe_cli_error_snippet(result.stderr, result.stdout, 2000)
        return ChatCliResult(
            response_text="",
            success=False,
            error=f"CLI exit {result.returncode}: {err}",
            metadata={"platform": "aider", "model": model},
        )

    response_text, parsed_meta = _extract_response(result.stdout or "")
    if not response_text:
        return ChatCliResult(
            response_text="",
            success=False,
            error="Aider produced no output",
            metadata={"platform": "aider", "model": model},
        )

    meta = {"platform": "aider", "model": model}
    meta.update(parsed_meta)
    return ChatCliResult(response_text=response_text, success=True, metadata=meta)
