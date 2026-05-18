"""Goose (Block) CLI chat executor — Wave 2d of the CLI integration catalog.

Goose is Block's Apache-2.0 Rust agent CLI. The binary ships from
``github.com/block/goose`` releases (downloaded into the code-worker
image at build time — see ``apps/code-worker/Dockerfile``). Two
properties make Goose a natural fit alongside the other CLIs in this
suite:

  * **MCP-native** — Goose auto-discovers MCP servers from
    ``~/.config/goose/mcp.json``. The executor materialises the tenant's
    existing ``task_input.mcp_config`` blob into that file before each
    turn, so any MCP source the tenant has wired (Higgsfield, GitHub,
    Slack, …) is reachable from Goose without an additional config
    surface.

  * **BYOK to any provider** — Goose talks to whichever LLM provider
    the tenant has credentials for (OpenAI, Anthropic, Databricks,
    Ollama, …). Following the Aider pattern, we don't store yet another
    secret blob: the tenant's *existing* provider credentials (already
    stored under their respective integrations) are propagated through
    env vars, plus a single ``provider`` + optional ``model`` setting
    on the goose integration card to tell Goose which provider to use.

Invocation:

    goose session --resume <session_id> \\
        --provider <provider> --model <model> \\
        -t "<prompt>"

We resume per session_id when one is provided so multi-turn chat keeps
context inside Goose's own session store (rooted under
``~/.config/goose/sessions/`` — which thanks to PR #540 lands on the
persistent workspaces volume via the tenant HOME redirect).

stderr carries live tool / provider chatter; we don't have a dedicated
stream parser yet (Wave 2d scope ends at executor + dispatch), so the
default passthrough parser surfaces stderr lines verbatim. A future
``goose_stream_parser`` slot is reserved by the import line below.
"""
from __future__ import annotations

import json
import logging
import os
import re

import cli_runtime
from session_event_emitter import SessionEventEmitter

logger = logging.getLogger(__name__)


# Default provider + model. Both are overridable per-tenant via the
# Goose integration card (``provider`` / ``model`` credentials) and per
# container by the ``GOOSE_PROVIDER`` / ``GOOSE_MODEL`` env vars. The
# defaults pick the most commonly-available pair: any tenant who has
# Anthropic creds stored under another integration gets a working chat
# turn with zero further configuration.
_DEFAULT_PROVIDER = os.environ.get("GOOSE_PROVIDER", "anthropic")
_DEFAULT_MODEL = os.environ.get("GOOSE_MODEL", "claude-3-5-sonnet-latest")

# Provider → env var name(s) Goose reads for that provider's API key.
# When the tenant has a matching integration credential stored under
# any of these names we propagate it into Goose's process env so the
# Rust binary picks it up without us touching its config files.
_PROVIDER_KEY_ENV: dict[str, tuple[str, ...]] = {
    "anthropic": ("ANTHROPIC_API_KEY",),
    "openai": ("OPENAI_API_KEY",),
    "groq": ("GROQ_API_KEY",),
    "databricks": ("DATABRICKS_TOKEN",),
    "google": ("GOOGLE_API_KEY", "GEMINI_API_KEY"),
    "openrouter": ("OPENROUTER_API_KEY",),
    "ollama": (),  # local, no key
}


def _write_mcp_config(goose_config_dir: str, mcp_config: str) -> None:
    """Materialise the tenant's MCP source list into Goose's auto-discovery file.

    Goose reads ``~/.config/goose/mcp.json`` at startup and registers
    every server it finds there. ``task_input.mcp_config`` is already
    formatted as Claude-Code-style MCP JSON ({"mcpServers": {...}});
    Goose accepts the same shape, so the materialisation is a direct
    write. We always overwrite — the file is fully derived from the
    tenant's current integrations, so stale entries between turns
    aren't a concern.
    """
    os.makedirs(goose_config_dir, exist_ok=True)
    target = os.path.join(goose_config_dir, "mcp.json")
    if not mcp_config or not mcp_config.strip():
        # No MCP sources wired — write an empty stub so Goose doesn't
        # spawn the demo defaults shipped with the binary.
        with open(target, "w") as f:
            f.write('{"mcpServers": {}}\n')
        return
    # If the incoming blob doesn't parse, fall back to empty rather
    # than letting Goose choke on malformed JSON at startup.
    try:
        json.loads(mcp_config)
        payload = mcp_config
    except json.JSONDecodeError:
        logger.warning("Goose: incoming mcp_config did not parse; writing empty stub")
        payload = '{"mcpServers": {}}'
    with open(target, "w") as f:
        f.write(payload)


def execute_goose_chat(task_input, session_dir: str):
    from workflows import (
        _fetch_integration_credentials,
        _INTEGRATION_NOT_CONNECTED_MESSAGES,
        ChatCliResult,
        WORKSPACE,
    )

    # ── credential resolution ────────────────────────────────────────────
    # The Goose integration stores a ``provider`` + optional ``model`` +
    # an optional provider api_key. If a key isn't on the goose card we
    # fall through to the process env, which is how an operator can wire
    # a shared key for every tenant (parity with the Aider pattern).
    provider = _DEFAULT_PROVIDER
    model = _DEFAULT_MODEL
    provider_key = ""
    try:
        creds = _fetch_integration_credentials("goose", task_input.tenant_id)
        provider = (creds.get("provider") or "").strip() or provider
        model = (creds.get("model") or "").strip() or model
        provider_key = (creds.get("api_key") or "").strip()
    except Exception as exc:
        # No goose integration row → friendly "not connected" message.
        # Resolver classifies as ``missing_credential`` and chain-skips
        # past goose without a 10-minute cooldown.
        friendly = _INTEGRATION_NOT_CONNECTED_MESSAGES.get(
            "goose",
            "Goose is not connected. Please connect your Goose account "
            "in Settings → Integrations.",
        )
        logger.info("Goose creds fetch failed: %s", exc)
        return ChatCliResult(response_text="", success=False, error=friendly)

    # Allow per-turn override of model via ChatCliInput.model — same
    # convention the other executors use when an agent pins a specific
    # variant for a given chat surface.
    requested_model = (getattr(task_input, "model", "") or "").strip()
    if requested_model:
        model = requested_model

    # ── tenant HOME on workspaces volume (task #267 Phase 1) ────────────
    # Goose stores sessions under ``$HOME/.config/goose/sessions/`` and
    # its config under ``$HOME/.config/goose/``. Pin both onto the
    # persistent workspaces volume so per-tenant config + multi-turn
    # session state survive container recycles and don't grow the
    # writable layer (PR #540 — workspaces-volume HOME redirect).
    try:
        tenant_home = str(cli_runtime.tenant_home_dir(task_input.tenant_id))
    except (ValueError, OSError) as exc:
        logger.warning(
            "tenant_home_dir(%s) failed (%s); HOME falls back to session_dir=%s",
            task_input.tenant_id, exc, session_dir,
        )
        tenant_home = session_dir
    os.makedirs(tenant_home, exist_ok=True)
    goose_config_dir = os.path.join(tenant_home, ".config", "goose")

    # ── MCP server auto-discovery ────────────────────────────────────────
    # Goose reads its MCP server list from ``~/.config/goose/mcp.json``.
    # We write the tenant's current source list (Higgsfield et al.) so
    # Goose picks them up at startup without us pre-baking anything.
    _write_mcp_config(goose_config_dir, task_input.mcp_config or "")

    # ── compose the prompt ───────────────────────────────────────────────
    # Goose's ``-t`` flag is the headless one-shot prompt. Prepend the
    # agent's instruction_md if supplied — same shape as every other
    # executor in the suite.
    prompt = task_input.message or ""
    if task_input.instruction_md_content and task_input.instruction_md_content.strip():
        prompt = (
            f"{task_input.instruction_md_content.strip()}\n\n"
            f"# User Request\n\n{task_input.message or ''}"
        )

    # ── command shape ────────────────────────────────────────────────────
    # ``goose session --resume <id>`` re-binds to an existing session
    # when one is supplied so multi-turn chat keeps context inside
    # Goose's own store. Falling back to a fresh session is fine — the
    # next turn supplies the session_id and resumes.
    session_id = (getattr(task_input, "session_id", "") or "").strip()
    cmd = ["goose", "session"]
    if session_id:
        cmd += ["--resume", session_id]
    cmd += ["--provider", provider, "--model", model, "-t", prompt]

    env = os.environ.copy()
    env["HOME"] = tenant_home
    # XDG_CONFIG_HOME explicit — Goose follows the XDG spec, so this
    # lets us pin config independently of $HOME if a future refactor
    # decouples them.
    env["XDG_CONFIG_HOME"] = os.path.join(tenant_home, ".config")
    # Goose is noisy without an explicit telemetry opt-out; the binary
    # honours both ``GOOSE_TELEMETRY=0`` and the standard
    # ``DO_NOT_TRACK=1`` knob.
    env["GOOSE_TELEMETRY"] = "0"
    env["DO_NOT_TRACK"] = "1"

    # Propagate the provider API key under whichever env var Goose
    # expects for the chosen provider. If the tenant didn't paste a
    # key into the goose card we leave the env untouched — Goose may
    # still pick up a shared operator-wired key from the container env,
    # or surface a clear provider auth error which the classifier
    # routes back through the chain.
    if provider_key:
        for var in _PROVIDER_KEY_ENV.get(provider, ()):
            env[var] = provider_key

    # ── tenant workspace cwd (task #259) ─────────────────────────────────
    _cwd_fallback = WORKSPACE if os.path.isdir(WORKSPACE) else session_dir
    cli_cwd = cli_runtime.resolve_cli_cwd(task_input, _cwd_fallback)
    env["WORKSPACE"] = cli_cwd

    # ── streaming emitter ────────────────────────────────────────────────
    # No dedicated stream parser yet (Wave 2d scope) — emitter receives
    # stdout/stderr chunks verbatim and the Den surfaces them as raw
    # terminal output. A future ``goose_stream_parser`` can plug in
    # without touching this call site.
    emitter = SessionEventEmitter(
        chat_session_id=getattr(task_input, "chat_session_id", "") or "",
        tenant_id=task_input.tenant_id,
        platform="goose",
        attempt=getattr(task_input, "attempt", 1) or 1,
    )
    try:
        result = cli_runtime.run_cli_with_heartbeat(
            cmd,
            label="Goose",
            timeout=1500,
            env=env,
            cwd=cli_cwd,
            on_chunk=None,
        )
    finally:
        emitter.close()

    logger.info("Goose CLI exit code: %s", result.returncode)
    if result.stdout:
        logger.info("Goose CLI stdout: %s", result.stdout[:500])
    if result.stderr:
        logger.warning("Goose CLI stderr: %s", result.stderr[:500])

    # Pull tool-call failures out of stderr. Goose's tool-error format
    # mirrors the gemini/qwen family: ``Error executing tool X: Y``.
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
            metadata={
                "platform": "goose",
                "provider": provider,
                "model": model,
                "tools_called": tool_errors,
            },
        )

    raw = (result.stdout or "").strip()
    if not raw:
        return ChatCliResult(
            response_text="",
            success=False,
            error="Goose produced no output",
            metadata={
                "platform": "goose",
                "provider": provider,
                "model": model,
                "tools_called": tool_errors,
            },
        )

    return ChatCliResult(
        response_text=raw,
        success=True,
        metadata={
            "platform": "goose",
            "provider": provider,
            "model": model,
            "tools_called": tool_errors,
        },
    )
