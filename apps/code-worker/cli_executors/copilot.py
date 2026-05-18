"""GitHub Copilot CLI chat executor — hoisted from workflows.py in Phase 1.6.

Body byte-identical to the previous ``_execute_copilot_chat`` (only the
two ``cli_runtime.*`` call sites differ). Workflows-side helpers
(_fetch_github_token, _prepare_copilot_home,
_INTEGRATION_NOT_CONNECTED_MESSAGES, ChatCliResult,
WORKSPACE, API_BASE_URL, API_INTERNAL_KEY) imported lazily inside the
function body.
"""
from __future__ import annotations

import json
import logging
import os

import cli_runtime
import tenant_home_quota
from cli_executors import passthrough_stream_parser
from session_event_emitter import SessionEventEmitter

logger = logging.getLogger(__name__)


def execute_copilot_chat(task_input, session_dir: str):
    """Execute a chat turn via GitHub Copilot CLI.

    Uses --output-format json (JSONL stream) so we can extract the actual
    assistant response and per-call usage data (premium requests, token
    counts, session duration, files modified) — surfaced back as
    metadata on the ChatCliResult so the chat path can RL-log it.

    Auth precedence (per `copilot help environment`):
      COPILOT_GITHUB_TOKEN > GH_TOKEN > GITHUB_TOKEN

    We set the highest-precedence one with the tenant's OAuth token so
    that any GITHUB_TOKEN baked into the container image (used for git
    operations) doesn't override per-tenant Copilot subscription routing.

    Token reuse: ``execute_chat_cli`` already fetches the per-tenant
    github token + sets ``os.environ["GITHUB_TOKEN"]`` at the top of
    every chat dispatch (for git remote setup). Reuse that value here
    instead of re-fetching from the API — saves 2-3 HTTP round-trips
    per chat turn on the Copilot path. Fall back to a fresh fetch
    only when env is empty (defensive, e.g. if execute_chat_cli was
    bypassed by a different caller). I6 from the holistic review.
    """
    from workflows import (
        _fetch_github_token,
        _prepare_copilot_home,
        _INTEGRATION_NOT_CONNECTED_MESSAGES,
        ChatCliResult,
        WORKSPACE,
        API_BASE_URL,
        API_INTERNAL_KEY,
    )
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        token = _fetch_github_token(task_input.tenant_id)
    if not token:
        return ChatCliResult(
            response_text="",
            success=False,
            error=_INTEGRATION_NOT_CONNECTED_MESSAGES["copilot_cli"],
        )

    mcp_config_json = task_input.mcp_config
    if not mcp_config_json:
        mcp_config_json = json.dumps({
            "servers": {
                "agentprovision": {
                    "type": "http",
                    "url": f"{API_BASE_URL}/mcp",
                    "headers": {"X-Internal-Key": API_INTERNAL_KEY or "dev_mcp_key", "X-Tenant-Id": task_input.tenant_id}
                }
            }
        })

    copilot_home = _prepare_copilot_home(session_dir, mcp_config_json)

    prompt = task_input.message
    if task_input.instruction_md_content.strip():
        prompt = f"{task_input.instruction_md_content.strip()}\n\n# User Request\n\n{task_input.message}"

    cmd = [
        "copilot",
        "-p", prompt,
        # JSONL output: one JSON object per line. Lets us parse usage data
        # and pluck the final assistant.message reliably (vs trying to
        # parse free-form text). `-s` (silent) is unnecessary in JSON mode.
        "--output-format", "json",
        "--no-ask-user",       # autonomous — disable the ask_user tool
        "--allow-all",         # = --allow-all-tools --allow-all-paths --allow-all-urls
        "--no-auto-update",    # never download CLI updates mid-run
        "--add-dir", session_dir,
    ]
    if os.path.isdir(WORKSPACE):
        cmd.extend(["--add-dir", WORKSPACE])

    env = os.environ.copy()
    # Highest-precedence auth env var (per `copilot help environment`).
    # Setting only COPILOT_GITHUB_TOKEN means a tenant-OAuth value here
    # always wins over the container's GITHUB_TOKEN (which is the platform
    # PAT used for git remote ops).
    env["COPILOT_GITHUB_TOKEN"] = token
    # `COPILOT_HOME` is the documented way to redirect Copilot's
    # state/config directory without nuking $HOME for child processes.
    env["COPILOT_HOME"] = copilot_home
    # Belt-and-suspenders for CI mode (auto-update is also auto-detected
    # via $CI/$BUILD_NUMBER/$RUN_ID, but explicit is cheaper than relying
    # on detection).
    env.setdefault("CI", "1")

    # ── tenant workspace cwd (task #259) ─────────────────────────────────
    # Run Copilot with cwd inside the tenant's persistent workspace so
    # files it writes via tool calls land in the named ``workspaces``
    # volume and surface in the dashboard's FileTreePanel. Also push the
    # tenant workspace into --add-dir so Copilot treats it as in-scope.
    _cwd_fallback = WORKSPACE if os.path.isdir(WORKSPACE) else session_dir
    cli_cwd = cli_runtime.resolve_cli_cwd(task_input, _cwd_fallback)
    env["WORKSPACE"] = cli_cwd
    if cli_cwd != _cwd_fallback:
        cmd.extend(["--add-dir", cli_cwd])

    # ── tenant HOME on workspaces volume (task #267 Phase 1) ────────────
    # ``COPILOT_HOME`` already pins copilot's own state dir, but tools
    # the copilot subprocess invokes still honour ``$HOME`` for caches
    # and ``--user`` installs. Redirect HOME onto the persistent
    # workspaces volume so that growth doesn't land on the code-worker
    # writable layer.
    tenant_home_path: str | None = None
    try:
        tenant_home_path = str(cli_runtime.tenant_home_dir(task_input.tenant_id))
        env["HOME"] = tenant_home_path
    except (ValueError, OSError) as exc:
        logger.warning(
            "tenant_home_dir(%s) failed (%s); HOME falls back to container default",
            task_input.tenant_id, exc,
        )

    # ---- streaming emitter (plan 2026-05-16 §2.4) ----
    # Copilot CLI uses passthrough — terminal sees the raw JSONL stream.
    emitter = SessionEventEmitter(
        chat_session_id=getattr(task_input, "chat_session_id", "") or "",
        tenant_id=task_input.tenant_id,
        platform="copilot_cli",
        attempt=getattr(task_input, "attempt", 1) or 1,
    )
    on_chunk = passthrough_stream_parser.build_parser(emitter) if emitter.enabled else None
    try:
        result = cli_runtime.run_cli_with_heartbeat(
            cmd,
            label="Copilot CLI",
            timeout=1500,
            env=env,
            cwd=cli_cwd,
            on_chunk=on_chunk,
        )
    finally:
        _stats = emitter.close()
        # Phase 2 quota walker (task #264) — see claude.py for rationale.
        if tenant_home_path:
            tenant_home_quota.maybe_enforce_quota(
                task_input.tenant_id,
                tenant_home_path,
                cumulative_chunks=int(_stats.get("emitted", 0)) if isinstance(_stats, dict) else 0,
            )

    if result.returncode != 0:
        err = cli_runtime.safe_cli_error_snippet(result.stderr, result.stdout, 1000)
        return ChatCliResult(response_text="", success=False, error=f"CLI exit {result.returncode}: {err}")

    raw = (result.stdout or "").strip()
    if not raw:
        return ChatCliResult(response_text="", success=False, error="Copilot produced no output")

    # Parse JSONL: collect assistant messages, sum output tokens, and the
    # trailing `result` event for session-level usage stats.
    response_pieces: list[str] = []
    final_answer_text = ""        # last assistant.message with NO tool calls (= the answer)
    last_message_text = ""        # last non-empty assistant.message (any kind, fallback)
    output_tokens_total = 0
    usage: dict = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        et = ev.get("type")
        if et == "assistant.message":
            data = ev.get("data") or {}
            content = data.get("content")
            tool_requests = data.get("toolRequests") or []
            try:
                output_tokens_total += int(data.get("outputTokens") or 0)
            except (TypeError, ValueError):
                pass
            if isinstance(content, str) and content:
                last_message_text = content
                # Prefer messages that aren't issuing tool calls — those
                # are the final-answer turns. Intermediate tool-call turns
                # also have content (sometimes empty, sometimes prose
                # explaining what the model is about to do).
                if not tool_requests:
                    final_answer_text = content
        elif et == "assistant.message_delta":
            # Streaming chunks. In -p mode these usually duplicate the
            # eventual `assistant.message` but accumulate as a fallback
            # for sessions that interrupt before the final message.
            delta = (ev.get("data") or {}).get("deltaContent") or ""
            if delta:
                response_pieces.append(delta)
        elif et == "result":
            usage = ev.get("usage") or {}
            usage["sessionId"] = ev.get("sessionId")
            usage["exitCode"] = ev.get("exitCode", 0)

    # Pick the response: prefer a final no-tool-call message, fall back to
    # the last assistant.message (could be a tool-call turn that included
    # explanatory prose), then to streamed deltas.
    response_text = final_answer_text or last_message_text or "".join(response_pieces)

    # Don't leak the raw JSONL stream as the response if NOTHING parsed —
    # treat that as a hard failure so callers can degrade gracefully
    # rather than ship JSONL to a user-facing channel.
    if not response_text:
        snippet = raw[:300].replace("\n", " ")
        return ChatCliResult(
            response_text="",
            success=False,
            error=f"Copilot returned no parseable assistant message (raw start: {snippet!r})",
        )

    # Build metadata using the field names the chat path's downstream
    # aggregator already reads (`output_tokens`, optionally `cost_usd`)
    # — see cli_session_manager.run_agent_session, which sums
    # input_tokens + output_tokens into `tokens_used`. Without these
    # field names, RL/cost telemetry would silently be zero per turn.
    metadata: dict = {
        "platform": "copilot_cli",
        # input_tokens is not reported per-message by Copilot CLI in -p
        # mode (only output_tokens are emitted on assistant.message). Set
        # to 0 explicitly so `tokens_used = input + output` is correct.
        "input_tokens": 0,
        "output_tokens": output_tokens_total,
    }
    if usage:
        # Copilot-specific telemetry — useful for per-tenant cost
        # tracking and RL reward shaping. NOT consumed by the existing
        # tokens_used aggregator (it reads input/output_tokens above).
        # cost_usd is intentionally not synthesized: GitHub publishes
        # premium-request quotas per plan but no fixed $/request rate,
        # so any multiplier would be wrong. Track premium_requests as
        # the billing unit and let downstream apply the tenant's plan
        # rate if needed.
        metadata["premium_requests"] = usage.get("premiumRequests")
        metadata["api_duration_ms"] = usage.get("totalApiDurationMs")
        metadata["session_duration_ms"] = usage.get("sessionDurationMs")
        metadata["session_id"] = usage.get("sessionId")
        # code_changes is a nested dict ({linesAdded, linesRemoved,
        # filesModified}); keep it nested under a clearly-namespaced key
        # to avoid clashes with any other metric called "linesAdded".
        metadata["copilot_code_changes"] = usage.get("codeChanges")

    return ChatCliResult(
        response_text=response_text,
        success=True,
        metadata=metadata,
    )
