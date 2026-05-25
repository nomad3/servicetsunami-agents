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


def _task_input(message: str = "hello world", tenant_id: str = "tenant-abc"):
    return SimpleNamespace(
        message=message,
        tenant_id=tenant_id,
        mcp_config=None,
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
    monkeypatch.setattr(oc, "WORKSPACE", str(tmp_path)) if hasattr(oc, "WORKSPACE") else None

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
    # Prompt is positional — appears as cmd[2], NOT after a `-p` flag.
    assert cmd[2] == "create devops subagent", (
        f"prompt must be positional; got {cmd!r}"
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


def test_parser_returns_empty_string_when_no_text_events(tmp_path, monkeypatch):
    """Tool-only or step-only responses → empty response_text but success=True.
    (Caller decides what to do with an empty turn; this is not a CLI failure.)"""
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
    assert captured_result["response_text"] == ""
    assert captured_result["success"] is True
