"""Tests for the OpenCode CLI executor — v1.15.x argv + event-stream parser.

These tests pin two regressions that fired in production on 2026-05-24
when Luna fell back to OpenCode (no quota remaining on cloud CLIs) and
got the CLI's `--help` text back as the assistant response:

  1. **argv bug** — the executor invoked `opencode run -p <prompt> -y
     --output-format json`. In OpenCode 1.15.x (pinned in
     ``apps/code-worker/Dockerfile`` since 2026-05-18), `-p` is the
     `--password` flag (NOT the prompt), `-y` doesn't exist, and
     `--output-format` was renamed to `--format`. Result: the message
     was being passed as the basic-auth password, the actual `message`
     positional was empty, and the CLI printed `--help` text + exited.

  2. **parser bug** — the executor called `json.loads(result.stdout)`
     expecting a single response object. OpenCode 1.15.x emits an
     EVENT STREAM (one JSON object per line). The parser silently
     returned an empty `response_text` on every 1.15+ call.

The fix in ``cli_executors/opencode.py`` switches argv to positional
+ `--format json` and replaces the single-load parser with an
event-stream walker that concatenates `type=="text"` events.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import pytest

from cli_executors import opencode as oc


def _task_input(
    message: str = "hello world",
    tenant_id: str = "tenant-abc",
    instruction_md_content: str = "",
):
    return SimpleNamespace(
        message=message,
        tenant_id=tenant_id,
        mcp_config=None,
        # Default empty matches the pre-2026-05-25 behavior for the
        # existing tests; new persona-prepend tests pass a non-empty value.
        instruction_md_content=instruction_md_content,
    )


# ── argv regression guard ─────────────────────────────────────────────


def test_cli_fallback_passes_prompt_as_positional_not_dash_p(tmp_path, monkeypatch):
    """Lock the argv: prompt MUST be a positional, NOT after `-p`.

    `-p` is `--password` in OpenCode 1.15.x. Passing the user's
    message as `-p <message>` was setting it as the basic-auth
    password and leaving the real message positional empty, which
    made the CLI print `--help` text instead of executing.
    """
    captured: dict = {}

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return SimpleNamespace(
            returncode=0,
            # One text event so the parser has something to return
            stdout=json.dumps({
                "type": "text",
                "part": {"type": "text", "text": "ok"},
            }) + "\n",
            stderr="",
        )

    monkeypatch.setattr(oc.subprocess, "run", _fake_run)

    # Patch the workflow's WORKSPACE since the function imports it locally
    fake_wf = MagicMock()
    fake_wf.WORKSPACE = str(tmp_path)
    fake_wf.ChatCliResult = SimpleNamespace
    monkeypatch.setitem(
        __import__("sys").modules,
        "workflows",
        fake_wf,
    )

    # Skip the tenant_home_dir call that needs real workspace
    monkeypatch.setattr(
        oc.cli_runtime, "tenant_home_dir",
        lambda tid: tmp_path,
    )
    monkeypatch.setattr(
        oc.cli_runtime, "resolve_cli_cwd",
        lambda task, fallback: str(tmp_path),
    )
    monkeypatch.setattr(
        oc.tenant_home_quota, "maybe_enforce_quota",
        lambda *a, **kw: None,
    )

    oc._execute_opencode_chat_cli(_task_input("create devops subagent"), str(tmp_path))

    cmd = captured["cmd"]
    # The hard invariants this test protects:
    assert cmd[0] == "opencode"
    assert cmd[1] == "run"
    # Prompt is positional + comes AFTER the `--` separator (the
    # separator protects against user prompts starting with `-` being
    # parsed as flags — same silent-corruption class as the original
    # `-p` bug).
    assert "--" in cmd, f"`--` separator required before prompt; got {cmd!r}"
    sep_idx = cmd.index("--")
    assert cmd[sep_idx + 1] == "create devops subagent", (
        f"prompt must follow `--` separator; got {cmd!r}"
    )
    # `-p` MUST NOT appear (it's --password in 1.15.x).
    assert "-p" not in cmd, f"`-p` is --password in OpenCode 1.15.x; got {cmd!r}"
    # `-y` MUST NOT appear (doesn't exist; was silently ignored pre-fix).
    assert "-y" not in cmd, f"`-y` is not an OpenCode flag; got {cmd!r}"
    # The format flag was renamed: `--output-format` → `--format`.
    assert "--output-format" not in cmd, (
        f"`--output-format` was renamed to `--format` in 1.15.x; got {cmd!r}"
    )
    assert "--format" in cmd and cmd[cmd.index("--format") + 1] == "json"


# ── I1: flag-injection guard ──────────────────────────────────────────


def test_dash_prefixed_prompt_does_not_get_parsed_as_flag(tmp_path, monkeypatch):
    """If a user prompt starts with `-` or `--`, the `--` separator
    must keep it as a positional, not a flag. Same silent-corruption
    class as the original `-p` bug."""
    captured: dict = {}

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({
                "type": "text",
                "part": {"type": "text", "text": "ok"},
            }) + "\n",
            stderr="",
        )

    monkeypatch.setattr(oc.subprocess, "run", _fake_run)
    fake_wf = MagicMock()
    fake_wf.WORKSPACE = str(tmp_path)
    fake_wf.ChatCliResult = SimpleNamespace
    monkeypatch.setitem(__import__("sys").modules, "workflows", fake_wf)
    monkeypatch.setattr(oc.cli_runtime, "tenant_home_dir", lambda tid: tmp_path)
    monkeypatch.setattr(oc.cli_runtime, "resolve_cli_cwd", lambda task, fb: str(tmp_path))
    monkeypatch.setattr(oc.tenant_home_quota, "maybe_enforce_quota", lambda *a, **kw: None)

    adversarial_prompt = "--print-logs and explain how X works"
    oc._execute_opencode_chat_cli(_task_input(adversarial_prompt), str(tmp_path))

    cmd = captured["cmd"]
    # The prompt MUST land after `--`.
    assert "--" in cmd
    sep_idx = cmd.index("--")
    assert cmd[sep_idx + 1] == adversarial_prompt, (
        f"adversarial `--`-prefixed prompt must stay as positional; got {cmd!r}"
    )
    # And `--print-logs` MUST NOT appear as a separate token before `--`.
    flags_section = cmd[:sep_idx]
    assert "--print-logs" not in flags_section, (
        f"--print-logs leaked into flag section; got {cmd!r}"
    )


# ── I2: empty-result-is-failure ───────────────────────────────────────


def test_empty_result_returns_success_false(tmp_path, monkeypatch):
    """Tool-only / step-only turns on the CLI fallback are surfaced as
    success=False, matching the GLM precedent. A blank response from
    the last-resort floor is almost always a bug, not a legitimate
    turn (legitimate tool-only turns belong on the server path which
    has session continuity)."""
    stream = json.dumps({"type": "step_start", "part": {"type": "step-start"}})

    def _fake_run(cmd, **kwargs):
        return SimpleNamespace(returncode=0, stdout=stream, stderr="")

    monkeypatch.setattr(oc.subprocess, "run", _fake_run)
    fake_wf = MagicMock()
    fake_wf.WORKSPACE = str(tmp_path)
    captured_result = {}
    fake_wf.ChatCliResult = lambda **kw: captured_result.update(kw) or SimpleNamespace(**kw)
    monkeypatch.setitem(__import__("sys").modules, "workflows", fake_wf)
    monkeypatch.setattr(oc.cli_runtime, "tenant_home_dir", lambda tid: tmp_path)
    monkeypatch.setattr(oc.cli_runtime, "resolve_cli_cwd", lambda task, fb: str(tmp_path))
    monkeypatch.setattr(oc.tenant_home_quota, "maybe_enforce_quota", lambda *a, **kw: None)

    oc._execute_opencode_chat_cli(_task_input("anything"), str(tmp_path))
    assert captured_result["success"] is False
    assert "no text output" in captured_result["error"].lower()
    assert captured_result["metadata"]["platform"] == "opencode_cli"


# ── I3: type==error events ────────────────────────────────────────────


def test_error_event_surfaces_as_failure_with_message(tmp_path, monkeypatch):
    """OpenCode 1.15.x can return exit 0 even on hard errors — the
    error appears ONLY as a `type=="error"` event in the stream
    (`error.data.message`). The parser must NOT silently swallow it
    and return success=True; that's the WhatsApp silent-empty failure
    mode that motivated this whole PR."""
    # Real shape verified against `opencode run --model bogus/x --format json`
    stream = json.dumps({
        "type": "error",
        "timestamp": 1779673049281,
        "sessionID": "ses_X",
        "error": {
            "name": "UnknownError",
            "data": {"message": "Model not found: bogus/nonexistent."},
        },
    })

    def _fake_run(cmd, **kwargs):
        # Note: returncode=0 — that's the realistic case
        return SimpleNamespace(returncode=0, stdout=stream, stderr="")

    monkeypatch.setattr(oc.subprocess, "run", _fake_run)
    fake_wf = MagicMock()
    fake_wf.WORKSPACE = str(tmp_path)
    captured_result = {}
    fake_wf.ChatCliResult = lambda **kw: captured_result.update(kw) or SimpleNamespace(**kw)
    monkeypatch.setitem(__import__("sys").modules, "workflows", fake_wf)
    monkeypatch.setattr(oc.cli_runtime, "tenant_home_dir", lambda tid: tmp_path)
    monkeypatch.setattr(oc.cli_runtime, "resolve_cli_cwd", lambda task, fb: str(tmp_path))
    monkeypatch.setattr(oc.tenant_home_quota, "maybe_enforce_quota", lambda *a, **kw: None)

    oc._execute_opencode_chat_cli(_task_input("anything"), str(tmp_path))
    assert captured_result["success"] is False
    assert "Model not found" in captured_result["error"]
    assert "OpenCode event-stream error" in captured_result["error"]


def test_partial_text_then_error_returns_failure_with_partial_text(tmp_path, monkeypatch):
    """If we got some text BEFORE the error event, preserve it in
    response_text (so the operator sees how far the model got) but
    still mark success=False."""
    stream = "\n".join([
        json.dumps({"type": "text", "part": {"type": "text", "text": "Starting analysis... "}}),
        json.dumps({
            "type": "error",
            "error": {"data": {"message": "Tool execution timed out"}},
        }),
    ])

    def _fake_run(cmd, **kwargs):
        return SimpleNamespace(returncode=0, stdout=stream, stderr="")

    monkeypatch.setattr(oc.subprocess, "run", _fake_run)
    fake_wf = MagicMock()
    fake_wf.WORKSPACE = str(tmp_path)
    captured_result = {}
    fake_wf.ChatCliResult = lambda **kw: captured_result.update(kw) or SimpleNamespace(**kw)
    monkeypatch.setitem(__import__("sys").modules, "workflows", fake_wf)
    monkeypatch.setattr(oc.cli_runtime, "tenant_home_dir", lambda tid: tmp_path)
    monkeypatch.setattr(oc.cli_runtime, "resolve_cli_cwd", lambda task, fb: str(tmp_path))
    monkeypatch.setattr(oc.tenant_home_quota, "maybe_enforce_quota", lambda *a, **kw: None)

    oc._execute_opencode_chat_cli(_task_input("analyze x"), str(tmp_path))
    assert captured_result["success"] is False
    assert captured_result["response_text"] == "Starting analysis... "
    assert "Tool execution timed out" in captured_result["error"]


# ── parser regression guard ────────────────────────────────────────────


def test_event_stream_parser_concatenates_text_chunks(tmp_path, monkeypatch):
    """Lock the parser: assembles `response_text` from `type=="text"`
    events' `part.text`, skipping non-text events + malformed lines."""
    stream = "\n".join([
        json.dumps({"type": "step_start", "part": {"type": "step-start"}}),
        json.dumps({"type": "text", "part": {"type": "text", "text": "Hello, "}}),
        "not-valid-json-line",  # defensive: parser must skip, not crash
        json.dumps({"type": "tool_call", "part": {"type": "tool", "name": "noop"}}),
        json.dumps({"type": "text", "part": {"type": "text", "text": "world!"}}),
        "",  # empty line — must skip
    ])

    def _fake_run(cmd, **kwargs):
        return SimpleNamespace(returncode=0, stdout=stream, stderr="")

    monkeypatch.setattr(oc.subprocess, "run", _fake_run)
    fake_wf = MagicMock()
    fake_wf.WORKSPACE = str(tmp_path)
    captured_result = {}

    def _capture_result(**kw):
        captured_result.update(kw)
        return SimpleNamespace(**kw)

    fake_wf.ChatCliResult = _capture_result
    monkeypatch.setitem(
        __import__("sys").modules,
        "workflows",
        fake_wf,
    )
    monkeypatch.setattr(oc.cli_runtime, "tenant_home_dir", lambda tid: tmp_path)
    monkeypatch.setattr(oc.cli_runtime, "resolve_cli_cwd", lambda task, fb: str(tmp_path))
    monkeypatch.setattr(oc.tenant_home_quota, "maybe_enforce_quota", lambda *a, **kw: None)

    oc._execute_opencode_chat_cli(_task_input("anything"), str(tmp_path))

    assert captured_result["response_text"] == "Hello, world!"
    assert captured_result["success"] is True
    assert captured_result["metadata"]["platform"] == "opencode_cli"


# ── Persona-leak regression guard (2026-05-25 fix) ─────────────────────
#
# Before this fix, both opencode paths did `prompt = task_input.message`
# without prepending `task_input.instruction_md_content`. That made
# Gemma 4 reply as "I'm OpenCode" instead of as the dispatched agent —
# observed live on Luna's WhatsApp 2026-05-24. The fix mirrors the
# codex + claude_code pattern: prepend the persona prompt with
# "<persona>\n\n# User Request\n\n<message>".
#
# Both paths fixed together (server-path execute_opencode_chat + CLI
# fallback _execute_opencode_chat_cli) — tests pin both.


def test_cli_fallback_prepends_persona_to_prompt(tmp_path, monkeypatch):
    """The CLI fallback path MUST prepend task_input.instruction_md_content
    to the user message. Without it, Gemma 4 has no agent identity."""
    captured: dict = {}

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({
                "type": "text",
                "part": {"type": "text", "text": "ok"},
            }) + "\n",
            stderr="",
        )

    monkeypatch.setattr(oc.subprocess, "run", _fake_run)
    fake_wf = MagicMock()
    fake_wf.WORKSPACE = str(tmp_path)
    fake_wf.ChatCliResult = SimpleNamespace
    monkeypatch.setitem(__import__("sys").modules, "workflows", fake_wf)
    monkeypatch.setattr(oc.cli_runtime, "tenant_home_dir", lambda tid: tmp_path)
    monkeypatch.setattr(oc.cli_runtime, "resolve_cli_cwd", lambda task, fb: str(tmp_path))
    monkeypatch.setattr(oc.tenant_home_quota, "maybe_enforce_quota", lambda *a, **kw: None)

    persona = "You are Luna, an intelligent AI co-pilot."
    user_msg = "Hi"
    oc._execute_opencode_chat_cli(
        _task_input(message=user_msg, instruction_md_content=persona),
        str(tmp_path),
    )

    # Find the prompt positional in the argv (it's after `--`).
    cmd = captured["cmd"]
    assert "--" in cmd
    prompt = cmd[cmd.index("--") + 1]
    # Persona MUST appear BEFORE the user message.
    assert persona in prompt, (
        f"persona MUST be prepended to opencode prompt; got: {prompt!r}"
    )
    assert "# User Request" in prompt, (
        f"User Request delimiter MUST appear between persona + msg; got: {prompt!r}"
    )
    # Order check: persona index < user msg index.
    assert prompt.index(persona) < prompt.index(user_msg), (
        f"persona MUST come before user message; got: {prompt!r}"
    )


def test_cli_fallback_skips_persona_prepend_when_empty(tmp_path, monkeypatch):
    """When instruction_md_content is empty (e.g. raw chat without an
    agent), opencode should NOT inject the persona block — the prompt
    is just the user message. Mirrors codex/claude_code behavior."""
    captured: dict = {}

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({
                "type": "text",
                "part": {"type": "text", "text": "ok"},
            }) + "\n",
            stderr="",
        )

    monkeypatch.setattr(oc.subprocess, "run", _fake_run)
    fake_wf = MagicMock()
    fake_wf.WORKSPACE = str(tmp_path)
    fake_wf.ChatCliResult = SimpleNamespace
    monkeypatch.setitem(__import__("sys").modules, "workflows", fake_wf)
    monkeypatch.setattr(oc.cli_runtime, "tenant_home_dir", lambda tid: tmp_path)
    monkeypatch.setattr(oc.cli_runtime, "resolve_cli_cwd", lambda task, fb: str(tmp_path))
    monkeypatch.setattr(oc.tenant_home_quota, "maybe_enforce_quota", lambda *a, **kw: None)

    user_msg = "hi"
    # Default _task_input has instruction_md_content="" — exercises the skip path.
    oc._execute_opencode_chat_cli(_task_input(message=user_msg), str(tmp_path))

    cmd = captured["cmd"]
    assert "--" in cmd
    prompt = cmd[cmd.index("--") + 1]
    assert prompt == user_msg, (
        f"empty persona MUST NOT prepend anything; got: {prompt!r}"
    )
    # User Request delimiter MUST NOT appear when no persona.
    assert "# User Request" not in prompt


# ── Server-path coverage (the path that actually shipped the WhatsApp leak) ──
#
# IMPORTANT #1 from the PR #717 superpowers review: the production leak
# was on `execute_opencode_chat` (HTTP server on port 8200), NOT on the
# CLI fallback. The CLI tests above cover the same prepend logic, but a
# future refactor of the server path could silently re-introduce the bug.
# Lock the server-path persona injection here.


def test_server_path_prepends_persona_to_parts_text(tmp_path, monkeypatch):
    """execute_opencode_chat (server path) MUST prepend persona to the
    prompt before POSTing to the OpenCode server. Body shape is
    {"parts": [{"type": "text", "text": "<prompt>"}]}."""
    captured: dict = {}

    def _fake_post(url, json=None, timeout=None):
        # Two endpoints: /session (create) + /session/<id>/message (send)
        if url.endswith("/session"):
            return SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: {"id": "ses_test_42"},
            )
        # The message endpoint — this is where persona MUST be present
        captured["url"] = url
        captured["body"] = json
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {
                "parts": [{"type": "text", "text": "Sure, I'll help."}],
                "usage": {},
            },
        )

    monkeypatch.setattr(oc.httpx, "post", _fake_post)
    fake_wf = MagicMock()
    fake_wf.ChatCliResult = SimpleNamespace
    monkeypatch.setitem(__import__("sys").modules, "workflows", fake_wf)
    # Clear the per-tenant session cache so we exercise the create-session path
    monkeypatch.setattr(oc, "_opencode_sessions", {})

    persona = "You are Luna, an intelligent AI co-pilot."
    user_msg = "Who are you?"
    oc.execute_opencode_chat(
        _task_input(message=user_msg, instruction_md_content=persona),
        str(tmp_path),
    )

    assert captured["url"].endswith("/message")
    parts = captured["body"]["parts"]
    assert len(parts) == 1 and parts[0]["type"] == "text"
    prompt_in_body = parts[0]["text"]
    assert persona in prompt_in_body, (
        f"persona MUST be prepended on server path; got: {prompt_in_body!r}"
    )
    assert "# User Request" in prompt_in_body
    assert prompt_in_body.index(persona) < prompt_in_body.index(user_msg)


# ── Combined-prefix ordering (persona at top, context inside User Request) ──
#
# IMPORTANT #2 from the PR #717 review: with both mcp_config + persona,
# the resulting prompt must put persona at the TOP (system-level
# identity) and [Context: tenant_id=...] INSIDE the User Request block
# (closer to the message it scopes). Locks the ordering choice so a
# future refactor doesn't silently invert it.


def test_persona_precedes_mcp_context_when_both_present(tmp_path, monkeypatch):
    """Combined prompt shape (CLI fallback path):
        <persona>

        # User Request

        [Context: tenant_id=...]

        <message>
    Server path uses the same composition logic — covered by the
    server-path test above."""
    captured: dict = {}

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({
                "type": "text",
                "part": {"type": "text", "text": "ok"},
            }) + "\n",
            stderr="",
        )

    monkeypatch.setattr(oc.subprocess, "run", _fake_run)
    fake_wf = MagicMock()
    fake_wf.WORKSPACE = str(tmp_path)
    fake_wf.ChatCliResult = SimpleNamespace
    monkeypatch.setitem(__import__("sys").modules, "workflows", fake_wf)
    monkeypatch.setattr(oc.cli_runtime, "tenant_home_dir", lambda tid: tmp_path)
    monkeypatch.setattr(oc.cli_runtime, "resolve_cli_cwd", lambda task, fb: str(tmp_path))
    monkeypatch.setattr(oc.tenant_home_quota, "maybe_enforce_quota", lambda *a, **kw: None)

    persona = "You are Luna, an intelligent AI co-pilot."
    user_msg = "What's our PLM status?"
    task = _task_input(message=user_msg, instruction_md_content=persona)
    task.mcp_config = '{"mcpServers": {"agentprovision": {}}}'

    oc._execute_opencode_chat_cli(task, str(tmp_path))

    cmd = captured["cmd"]
    assert "--" in cmd
    prompt = cmd[cmd.index("--") + 1]
    # All four components must be present
    assert persona in prompt
    assert "# User Request" in prompt
    assert "[Context: tenant_id=" in prompt
    assert user_msg in prompt
    # Ordering invariant: persona < User Request delimiter < Context < message
    p_idx = prompt.index(persona)
    ur_idx = prompt.index("# User Request")
    ctx_idx = prompt.index("[Context: tenant_id=")
    msg_idx = prompt.index(user_msg)
    assert p_idx < ur_idx < ctx_idx < msg_idx, (
        f"ordering invariant violated; positions persona={p_idx}, "
        f"User Request={ur_idx}, Context={ctx_idx}, message={msg_idx} "
        f"in prompt {prompt!r}"
    )

