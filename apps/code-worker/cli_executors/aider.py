"""Aider chat executor — Wave 2c of the CLI integration catalog (#272).

Aider (https://aider.chat — Apache 2.0) is a Python CLI that pair-programs
against any provider you give it an API key for. We ship support for the
single-API-key providers: Anthropic, OpenAI, DeepSeek, Google (Gemini),
Moonshot (Kimi), Zhipu (GLM), Mistral, Cohere, Groq. Multi-credential
providers (Bedrock = access-key + secret + region; Azure = api_base +
version + key; Ollama = base URL) are intentionally NOT supported via
this single-``api_key`` integration card — they need dedicated cards.
It ships on PyPI as the ``aider-chat`` package and exposes a single
binary, ``aider``, on ``$PATH`` once installed.

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
from cli_orchestrator.redaction import redact
from session_event_emitter import SessionEventEmitter

logger = logging.getLogger(__name__)


# Default model is Anthropic's mid-tier Claude — most tenants who connect
# Aider already have an Anthropic key on hand. Overridable per-tenant via
# the integration card or per-turn via ``ChatCliInput.model``.
_DEFAULT_MODEL = os.environ.get(
    "AIDER_DEFAULT_MODEL", "anthropic/claude-3-5-sonnet-20241022",
)

# Map LiteLLM provider prefix → env var Aider expects. We ship only
# the single-api-key providers — Bedrock (access-key + secret + region),
# Azure (api_base + version + key), and Ollama (base URL, no key) each
# need multi-field credentials that the single-``api_key`` integration
# card can't represent, so they're intentionally excluded. Tenants who
# need those should wire them via dedicated cards in a future wave.
_PROVIDER_KEY_ENV: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "google": "GEMINI_API_KEY",
    "moonshot": "MOONSHOT_API_KEY",
    "zhipu": "ZHIPU_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "codestral": "MISTRAL_API_KEY",
    "groq": "GROQ_API_KEY",
    "cohere": "COHERE_API_KEY",
}


# Bare-slug vendor token detection, in priority order. The first regex
# that matches the (lowercased) slug wins. Order matters: ``claude-*``
# must come before any catch-all OpenAI rule because tenants who paste
# unprefixed Anthropic slugs (``claude-3-5-sonnet-20241022``) need to
# land on ``ANTHROPIC_API_KEY``, not silently bind to OpenAI.
_BARE_SLUG_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^claude[-_.]"), "ANTHROPIC_API_KEY"),
    (re.compile(r"^(gpt[-_.]|o1[-_.]|o3[-_.])"), "OPENAI_API_KEY"),
    (re.compile(r"^deepseek[-_.]"), "DEEPSEEK_API_KEY"),
    (re.compile(r"^(gemini|bison|palm)[-_.]"), "GEMINI_API_KEY"),
    (re.compile(r"^kimi[-_.]"), "MOONSHOT_API_KEY"),
    (re.compile(r"^glm[-_.]"), "ZHIPU_API_KEY"),
    (re.compile(r"^(mistral|codestral)[-_.]"), "MISTRAL_API_KEY"),
    (re.compile(r"^command[-_.]?"), "COHERE_API_KEY"),
    # Groq commonly serves llama-*-instruct slugs.
    (re.compile(r"^llama[-_.].*[-_.]instruct"), "GROQ_API_KEY"),
]


def _env_var_for_model(model: str) -> str | None:
    """Pick the right LiteLLM-flavoured env var from the model slug.

    Resolution order:

      1. If the slug is ``<provider>/<model>``, look up the prefix in
         the provider table. Unknown prefix → ``None`` (caller surfaces
         a clear error rather than silently binding to OpenAI).
      2. If there's no ``/``, scan the slug for vendor tokens
         (``claude-*``, ``gpt-*`` / ``o1-*`` / ``o3-*``, ``deepseek-*``,
         ``gemini-*`` / ``bison-*`` / ``palm-*``, ``kimi-*``, ``glm-*``,
         ``mistral-*`` / ``codestral-*``, ``command*``,
         ``llama-*-instruct``).
      3. No match → ``None``. Callers must raise "Unknown model — use
         the ``provider/model`` format" so the operator gets a clear
         signal instead of an authentication failure further downstream.
    """
    if not model:
        return None
    lowered = model.lower()
    if "/" in lowered:
        prefix = lowered.split("/", 1)[0]
        return _PROVIDER_KEY_ENV.get(prefix)
    for pattern, env_var in _BARE_SLUG_RULES:
        if pattern.match(lowered):
            return env_var
    return None


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
    # is a TTY. ``--no-pretty`` on the command line disables most of it,
    # but the residual leak ("Aider v…" banner, model-warning lines)
    # still arrives with escape codes on some terminals, so we scrub
    # defensively. (Note: ``--yes-always`` is an unconditional confirm
    # flag — it does NOT depend on having a TTY allocated; the executor
    # runs Aider as a normal subprocess, no PTY.)
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

    # Aider banner: with ``--no-pretty`` there's no ``─`` rule, and if
    # cwd isn't a git repo there's no ``Repo-map:`` line either. The
    # reliable approach is to strip a fixed set of banner-line patterns
    # from the front, then take the first non-empty line as the body
    # start. Banner shapes covered:
    #
    #   * ``Aider v0.65.0``
    #   * ``Model: …``
    #   * ``Git repo: …`` / ``Repo-map: …``
    #   * ``Added … to the chat.``
    #   * the rule line ``─`` (when ``--pretty`` is on)
    #   * the legacy ``> `` prompt line
    #   * pure-whitespace lines
    lines = text.splitlines()
    banner_re = re.compile(
        r"^("
        r"Aider v[\d.]+.*"
        r"|Model:\s.*"
        r"|Git repo:\s.*"
        r"|Repo-map:\s.*"
        r"|Added .* to the chat\.?"
        r"|─+"
        r"|>\s.*"
        r")$"
    )
    start = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if banner_re.match(stripped):
            continue
        start = i
        break
    else:
        # All lines were banner / blank — body is empty.
        start = len(lines)

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
    # If the body slice is empty, return ``""`` — do NOT fall back to
    # the raw banner. The caller's ``if not response_text:`` branch
    # surfaces a clean "no output" error so the resolver can chain past
    # Aider instead of feeding banner noise back to the user.
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
        if guessed:
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
        # Telemetry: Aider has a stateful ``analytics`` system; the prior
        # code set fake ``AIDER_ANALYTICS`` env vars that Aider does NOT
        # honour. ``--analytics-disable`` is the documented flag (see
        # https://aider.chat/docs/config/options.html — "Permanently
        # disable analytics").
        "--analytics-disable",
        # Stop Aider from auto-appending ``.aider*`` entries to the
        # tenant repo's ``.gitignore`` — we manage gitignore policy at
        # the workspace layer.
        "--no-gitignore",
        "--message", prompt,
    ]

    # Resolve the env var Aider/LiteLLM expects for this model. ``None``
    # here means the slug didn't match any known provider — fail fast
    # with a clear message instead of silently binding to OpenAI and
    # surfacing an auth error several seconds later.
    key_env = _env_var_for_model(model)
    if key_env is None:
        return ChatCliResult(
            response_text="",
            success=False,
            error=(
                f"Unknown model {model!r} — use the ``provider/model`` "
                "format (e.g. anthropic/claude-3-5-sonnet-20241022, "
                "openai/gpt-4o, deepseek/deepseek-chat)."
            ),
            metadata={"platform": "aider", "model": model},
        )

    env = os.environ.copy()
    env["HOME"] = tenant_home
    env["WORKSPACE"] = cli_cwd
    # Inject the provider key under the right env var. We set BOTH the
    # model-derived var (so Aider/LiteLLM finds it) AND a generic alias
    # for diagnostics — harmless if duplicated.
    env[key_env] = api_key
    env["AIDER_MODEL_API_KEY"] = api_key

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
        # LiteLLM error messages routinely echo the bad API key (or its
        # prefix) back at us. Route the snippet through the standard
        # redactor before bubbling up, so the user-visible
        # ``ChatCliResult.error`` and any downstream logs never carry
        # ``sk-ant-…`` / ``sk-…`` substrings. Mirrors what
        # ``AiderAdapter.run`` already does for raised exceptions.
        err = redact(err)
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
