"""Claude Code chat executor — hoisted from workflows.py in Phase 1.6.

Body is byte-identical to the previous ``_execute_claude_chat`` (just
renamed to ``execute_claude_chat`` and with the two helper calls
rewired to the new ``cli_runtime`` module). Workflows-side helpers
(``_fetch_claude_token``, ``_INTEGRATION_NOT_CONNECTED_MESSAGES``,
``_build_allowed_tools_from_mcp``, the ``ChatCliResult``
dataclasses, and the module-level constants) are imported lazily inside
the function body so:

  1. The import cycle ``workflows -> cli_executors -> workflows`` does
     not fire at module-load time (workflows imports executors via the
     dispatch table inside ``execute_chat_cli``).
  2. Existing test monkeypatches on ``wf._fetch_claude_token`` etc. still
     take effect — lazy imports re-resolve the attribute on every call.

2026-05-16: gains a streaming pump (`SessionEventEmitter` +
`claude_stream_parser`) wired through `cli_runtime.on_chunk` so the
dashboard terminal card sees reasoning + tool_use + tool_result live
instead of waiting for `proc.communicate()` to return. The
`--output-format stream-json --verbose` switch is rollout-flagged via
`tenant_features.cli_stream_output` (default OFF prod, ON for the
saguilera test tenant). When the flag is OFF we keep the legacy
single-line `--output-format json` shape.
"""
from __future__ import annotations

import json
import logging
import os

import cli_runtime
import tenant_home_quota
from cli_executors import claude_stream_parser
from session_event_emitter import SessionEventEmitter
from tenant_feature_flags import is_enabled as _feature_enabled

logger = logging.getLogger(__name__)


def execute_claude_chat(task_input, session_dir: str):
    from workflows import (
        _fetch_claude_token,
        _fetch_claude_credential,
        _INTEGRATION_NOT_CONNECTED_MESSAGES,
        _build_allowed_tools_from_mcp,
        ChatCliResult,
        WORKSPACE,
        CLAUDE_CODE_MODEL,
    )
    # Prefer the typed credential (OAuth vs api_key); fall back to the
    # legacy OAuth-only helper so existing test monkeypatches on
    # `_fetch_claude_token` keep working.
    credential = _fetch_claude_credential(task_input.tenant_id)
    if credential is None:
        legacy_token = _fetch_claude_token(task_input.tenant_id)
        credential = (legacy_token, "oauth") if legacy_token else None
    if not credential:
        # Canonical not-connected message — must match
        # `cli_platform_resolver._MISSING_CRED_PATTERNS` so the
        # resolver chain classifies this as `missing_credential`
        # (skip without cooldown). The short form "Claude Code not
        # connected" did NOT match the regex (only the long
        # "subscription is not connected" did) — that broke chain
        # fallback for tenants who hit a credential-missing CLI.
        return ChatCliResult(
            response_text="",
            success=False,
            error=_INTEGRATION_NOT_CONNECTED_MESSAGES["claude_code"],
        )

    if task_input.instruction_md_content:
        with open(os.path.join(session_dir, "CLAUDE.md"), "w") as f:
            f.write(task_input.instruction_md_content)

    if task_input.mcp_config:
        with open(os.path.join(session_dir, "mcp.json"), "w") as f:
            f.write(task_input.mcp_config)

    _model = task_input.model or CLAUDE_CODE_MODEL
    _allowed = task_input.allowed_tools or _build_allowed_tools_from_mcp(
        task_input.mcp_config, extra="Bash,Read,Edit,Write,WebFetch,WebSearch"
    )
    
    prompt = task_input.message
    if task_input.instruction_md_content.strip():
        # Bypass the 20KB limit of --append-system-prompt by injecting
        # instructions and conversation history directly into the prompt.
        prompt = f"{task_input.instruction_md_content.strip()}\n\n# User Request\n\n{task_input.message}"

    # ---- output-format rollout gate (plan 2026-05-16 §9) ----
    # `stream-json` emits NDJSON per event (system.init / assistant.text /
    # tool_use / tool_result / result.*) which the terminal card renders
    # live. Falls back to single-line `json` when the flag is OFF so the
    # legacy parse below (line 125) still works byte-for-byte.
    _stream_enabled = _feature_enabled(
        task_input.tenant_id, "cli_stream_output", default=False
    )
    cmd = ["claude", "-p", prompt]
    if _stream_enabled:
        cmd.extend(["--output-format", "stream-json", "--verbose"])
    else:
        cmd.extend(["--output-format", "json"])
    cmd.extend([
        "--model", _model,
        "--allowedTools", _allowed,
        "--add-dir", session_dir,
    ])
    if os.path.isdir(WORKSPACE):
        cmd.extend(["--add-dir", WORKSPACE])

    # NOTE: --resume intentionally NOT used. Previously we stored an
    # ever-growing session_id per chat and resumed it on every message.
    # For long conversations (Luna on WhatsApp), the JSONL session file
    # grew to 16+ MB, causing:
    #   - slow startup (loading + parsing the full file)
    #   - lossy context compaction (old details silently dropped)
    #   - context loss on specific entities (names, prior lead gen lists)
    # Instead, each `claude -p` invocation is a fresh one-shot session,
    # and the caller (chat.py) is responsible for passing the last N
    # messages via --append-system-prompt. This gives deterministic,
    # bounded context under our control.
    # Use --no-session-persistence to avoid leaking JSONL files on every
    # call (842+ files were accumulated in the previous model).
    cmd.append("--no-session-persistence")

    mcp_path = os.path.join(session_dir, "mcp.json")
    if os.path.exists(mcp_path):
        cmd.extend(["--mcp-config", mcp_path])

    token, kind = credential
    env = os.environ.copy()
    if kind == "oauth":
        # Subscription-OAuth flow: token is a Bearer for claude.com.
        # CRITICAL: also drop any inherited ANTHROPIC_API_KEY from the
        # container env (loaded via env_file: ./apps/api/.env). Claude
        # Code's auth priority puts ANTHROPIC_API_KEY ahead of
        # CLAUDE_CODE_OAUTH_TOKEN — so a leftover API key in the
        # container would silently route the subscription user to the
        # Console billing path, surfacing as "Credit balance is too
        # low" even though the OAuth account has quota. Observed
        # 2026-05-16 after PR #530 recreated code-worker.
        env["CLAUDE_CODE_OAUTH_TOKEN"] = token
        env.pop("ANTHROPIC_API_KEY", None)
    else:
        # API-key flow (kind == "api_key"): claude CLI honours
        # ANTHROPIC_API_KEY and routes to the Console billing path.
        env["ANTHROPIC_API_KEY"] = token
        # Defensive: clear any stale OAuth token from the container env
        # so the per-tenant API key takes effect.
        env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)

    # ── tenant workspace cwd (task #259) ─────────────────────────────────
    # Scope the subprocess cwd to the tenant's persistent workspace
    # projects dir so files Claude writes via the Write/Edit tools land
    # in the named ``workspaces`` volume that's shared with the api
    # container — and therefore appear in the dashboard's FileTreePanel.
    # Falls back to the legacy ``WORKSPACE`` (=/workspace) or session_dir
    # when the volume isn't mounted (tests, bare-metal dev).
    _cwd_fallback = WORKSPACE if os.path.isdir(WORKSPACE) else session_dir
    cli_cwd = cli_runtime.resolve_cli_cwd(task_input, _cwd_fallback)
    env["WORKSPACE"] = cli_cwd
    # Also let Claude see the tenant workspace as an --add-dir so it
    # treats files there as in-scope for Read/Edit.
    if cli_cwd != _cwd_fallback:
        cmd.extend(["--add-dir", cli_cwd])

    # ── tenant HOME on workspaces volume (task #267 Phase 1) ────────────
    # Redirect HOME onto the persistent workspaces volume so per-tenant
    # ``.local/`` / ``.cache/`` / package installs don't grow the
    # code-worker writable layer (root cause of the 2026-05-04 &
    # 2026-05-17 disk-full incidents). Non-UUID tenant_id falls back to
    # the container's default HOME — same defensive shape as
    # ``resolve_cli_cwd``.
    tenant_home_path: str | None = None
    try:
        tenant_home_path = str(cli_runtime.tenant_home_dir(task_input.tenant_id))
        env["HOME"] = tenant_home_path
    except (ValueError, OSError) as exc:
        logger.warning(
            "tenant_home_dir(%s) failed (%s); HOME falls back to container default",
            task_input.tenant_id, exc,
        )

    # ---- streaming emitter (no-op if flag off / chat_session_id missing) ----
    emitter = SessionEventEmitter(
        chat_session_id=getattr(task_input, "chat_session_id", "") or "",
        tenant_id=task_input.tenant_id,
        platform="claude_code",
        attempt=getattr(task_input, "attempt", 1) or 1,
    )
    on_chunk = claude_stream_parser.build_parser(emitter) if (_stream_enabled and emitter.enabled) else None

    try:
        result = cli_runtime.run_cli_with_heartbeat(
            cmd,
            label="Claude Code",
            timeout=1500,
            env=env,
            cwd=cli_cwd,
            on_chunk=on_chunk,
        )
    finally:
        _stats = emitter.close()
        # ── Phase 2 quota walker (task #264) ────────────────────────────
        # Walk the tenant HOME dir on the workspaces volume and prune
        # non-essential subtrees if we're over the 2 GiB soft cap.
        # Watermark-gated inside maybe_enforce_quota; non-raising.
        if tenant_home_path:
            tenant_home_quota.maybe_enforce_quota(
                task_input.tenant_id,
                tenant_home_path,
                cumulative_chunks=int(_stats.get("emitted", 0)) if isinstance(_stats, dict) else 0,
            )

    if result.returncode != 0:
        err = cli_runtime.safe_cli_error_snippet(result.stderr, result.stdout, 1000)
        return ChatCliResult(response_text="", success=False, error=f"CLI exit {result.returncode}: {err}")

    raw = result.stdout.strip()
    if not raw:
        return ChatCliResult(response_text="", success=False, error="CLI produced no output")

    # stream-json: response is NDJSON, the final `result` event holds the
    # cost + usage data and ``result.result`` is the assistant's full
    # answer. Find the LAST non-empty line and parse that (the only line
    # with subtype="success"). Fall back to legacy single-line json
    # parse if the line isn't a result envelope.
    if _stream_enabled:
        last_line = ""
        for ln in reversed(raw.splitlines()):
            if ln.strip():
                last_line = ln.strip()
                break
        try:
            data = json.loads(last_line)
            if data.get("type") != "result":
                # Final non-empty line wasn't the result envelope —
                # could be a trailing assistant message. Walk back to
                # find the result event.
                for ln in reversed(raw.splitlines()):
                    if not ln.strip():
                        continue
                    try:
                        obj = json.loads(ln)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(obj, dict) and obj.get("type") == "result":
                        data = obj
                        break
            text = data.get("result") or data.get("response") or data.get("content") or data.get("text") or ""
            usage = data.get("usage") or {}
            meta = {
                "platform": "claude_code",
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
                "model": data.get("model"),
                "claude_session_id": data.get("session_id", ""),
                "cost_usd": data.get("total_cost_usd", 0),
            }
            if not text:
                # Edge case: no result envelope captured. Return raw so
                # the user sees something rather than a silent failure.
                text = raw
            return ChatCliResult(response_text=text, success=True, metadata=meta)
        except (json.JSONDecodeError, AttributeError):
            return ChatCliResult(
                response_text=raw,
                success=True,
                metadata={"platform": "claude_code"},
            )

    # Legacy single-line `--output-format json` path (flag OFF).
    try:
        data = json.loads(raw)
        text = data.get("result") or data.get("response") or data.get("content") or data.get("text") or raw
        meta = {
            "platform": "claude_code",
            "input_tokens": (data.get("usage") or {}).get("input_tokens", 0),
            "output_tokens": (data.get("usage") or {}).get("output_tokens", 0),
            "model": data.get("model"),
            "claude_session_id": data.get("session_id", ""),
            "cost_usd": data.get("total_cost_usd", 0),
        }
        return ChatCliResult(response_text=text, success=True, metadata=meta)
    except json.JSONDecodeError:
        return ChatCliResult(
            response_text=raw,
            success=True,
            metadata={"platform": "claude_code"},
        )
