"""Tests for execute_chat_cli — the platform dispatcher.

execute_chat_cli is a sync activity. Its responsibilities are:
  1. Fetch the tenant's GitHub token from the internal API and configure git.
  2. Build a per-tenant session directory.
  3. Optionally write user-supplied image bytes to disk.
  4. Dispatch to the right ``_execute_<platform>_chat`` helper.

We mock every external boundary (httpx, subprocess.run, the per-platform
helpers themselves) so the test stays a unit test and never hits the network.
"""
from __future__ import annotations

import os
import sys

import pytest

import cli_runtime
import cli_executors.claude as claude_executor
from cli_executors import claude_interactive
import workflows as wf


def _make_input(**overrides) -> wf.ChatCliInput:
    base = dict(
        platform="claude_code",
        message="hello",
        tenant_id="tenant-aaa",
        instruction_md_content="",
        mcp_config="",
        image_b64="",
        image_mime="",
        session_id="",
        model="",
        allowed_tools="",
    )
    base.update(overrides)
    return wf.ChatCliInput(**base)


@pytest.fixture(autouse=True)
def _isolate_session_dir(monkeypatch, tmp_path):
    """Redirect /home/codeworker/st_sessions into pytest tmp_path."""
    sessions_root = tmp_path / "st_sessions"
    sessions_root.mkdir()

    original = os.path.join

    def patched(*parts):
        if parts and isinstance(parts[0], str) and parts[0].startswith(
            "/home/codeworker/st_sessions"
        ):
            return original(str(sessions_root), *parts[1:])
        return original(*parts)

    monkeypatch.setattr(wf.os.path, "join", patched)
    yield


@pytest.fixture(autouse=True)
def _stub_github_token(monkeypatch):
    """Default: no GitHub token, no remote configuration."""
    monkeypatch.setattr(wf, "_fetch_github_token", lambda tid: None)
    # Also short-circuit any subprocess.run calls (git remote / gh auth).
    monkeypatch.setattr(wf.subprocess, "run", lambda *a, **kw: None)
    yield


class TestPlatformDispatch:
    """Each known platform dispatches to its own helper exactly once."""

    @pytest.mark.parametrize(
        "platform, helper_name",
        [
            ("claude_code", "_execute_claude_chat"),
            ("codex", "_execute_codex_chat"),
            ("copilot_cli", "_execute_copilot_chat"),
            ("gemini_cli", "_execute_gemini_chat"),
            ("opencode", "_execute_opencode_chat"),
        ],
    )
    def test_dispatches_to_helper(self, monkeypatch, platform, helper_name):
        sentinel = wf.ChatCliResult(response_text="OK", success=True)
        called: list[tuple] = []

        def fake_helper(*args, **kwargs):
            called.append((args, kwargs))
            return sentinel

        monkeypatch.setattr(wf, helper_name, fake_helper)

        out = wf.execute_chat_cli(_make_input(platform=platform))

        assert out is sentinel
        assert len(called) == 1, f"{helper_name} should be called exactly once"

    def test_unsupported_platform_returns_failure(self, monkeypatch):
        out = wf.execute_chat_cli(_make_input(platform="bogus_cli"))
        assert out.success is False
        assert "Unsupported" in out.error

    def test_helper_exception_is_caught_and_returned(self, monkeypatch):
        def boom(*a, **kw):
            raise RuntimeError("disk full")

        monkeypatch.setattr(wf, "_execute_claude_chat", boom)
        out = wf.execute_chat_cli(_make_input(platform="claude_code"))
        assert out.success is False
        assert "disk full" in out.error


class TestImageHandling:
    """When image_b64 + image_mime are provided, the file lands on disk."""

    def test_image_bytes_written_to_session_dir(self, monkeypatch, tmp_path):
        captured: dict = {}

        def fake_helper(task_input, session_dir, image_path):
            captured["session_dir"] = session_dir
            captured["image_path"] = image_path
            return wf.ChatCliResult(response_text="ok", success=True)

        monkeypatch.setattr(wf, "_execute_codex_chat", fake_helper)

        # 'AAAA' base64-decodes to 3 NUL bytes; enough to verify a write.
        wf.execute_chat_cli(_make_input(
            platform="codex", image_b64="QUJD", image_mime="image/jpeg",
        ))

        assert captured["image_path"].endswith("user_image.jpg")
        assert os.path.exists(captured["image_path"])
        # Bytes from "QUJD" base64 = b"ABC".
        assert open(captured["image_path"], "rb").read() == b"ABC"

    def test_no_image_means_empty_image_path(self, monkeypatch):
        captured: dict = {}

        def fake_helper(task_input, session_dir, image_path):
            captured["image_path"] = image_path
            return wf.ChatCliResult(response_text="ok", success=True)

        monkeypatch.setattr(wf, "_execute_gemini_chat", fake_helper)
        wf.execute_chat_cli(_make_input(platform="gemini_cli"))
        assert captured["image_path"] == ""


class TestGithubTokenIntegration:
    """When a GitHub token is found, it is exported and git is configured."""

    def test_token_wires_git_remote_and_gh_auth(self, monkeypatch):
        seen: list[list] = []

        def fake_run(cmd, **kwargs):
            seen.append(cmd)
            class R:
                returncode = 0
                stdout = b""
                stderr = b""
            return R()

        monkeypatch.setattr(wf.subprocess, "run", fake_run)
        monkeypatch.setattr(wf, "_fetch_github_token", lambda tid: "ghp_abc")
        monkeypatch.setattr(
            wf, "_execute_claude_chat",
            lambda *a, **kw: wf.ChatCliResult(response_text="x", success=True),
        )

        wf.execute_chat_cli(_make_input(platform="claude_code"))

        # Two subprocess.run calls expected: git remote set-url + gh auth login.
        joined = [" ".join(c) if isinstance(c, list) else c for c in seen]
        assert any("remote" in s and "set-url" in s for s in joined)
        assert any("gh" in s and "auth" in s and "login" in s for s in joined)
        # Token must be exported into the env.
        assert os.environ.get("GITHUB_TOKEN") == "ghp_abc"


# ── _execute_claude_chat smoke (covers JSON parse path) ─────────────────

class TestExecuteClaudeChat:
    @pytest.fixture(autouse=True)
    def _default_claude_mode(self, monkeypatch):
        monkeypatch.delenv("CLAUDE_CODE_EXECUTION_MODE", raising=False)

    def test_missing_token_returns_friendly_error(self, monkeypatch):
        # Patch the module-private second-definition that the helper actually
        # binds — both names point at the same function object.
        monkeypatch.setattr(wf, "_fetch_claude_token", lambda tid: None)
        result = wf._execute_claude_chat(
            _make_input(platform="claude_code"), session_dir="/tmp/x",
        )
        assert result.success is False
        assert "not connected" in result.error.lower()

    def test_parses_json_response(self, monkeypatch, tmp_path):
        monkeypatch.setattr(wf, "_fetch_claude_token", lambda tid: "tok")

        import subprocess as sp

        fake_completed = sp.CompletedProcess(
            args=["claude"],
            returncode=0,
            stdout='{"result": "hi", "usage": {"input_tokens": 1, "output_tokens": 2}, "model": "claude-x", "session_id": "s", "total_cost_usd": 0.0}',
            stderr="",
        )
        monkeypatch.setattr(
            cli_runtime, "run_cli_with_heartbeat",
            lambda cmd, **kw: fake_completed,
        )

        out = wf._execute_claude_chat(
            _make_input(platform="claude_code"), session_dir=str(tmp_path),
        )
        assert out.success is True
        assert out.response_text == "hi"
        assert out.metadata["platform"] == "claude_code"
        assert out.metadata["input_tokens"] == 1

    def test_print_mode_uses_claude_print_flag(self, monkeypatch, tmp_path):
        monkeypatch.setattr(wf, "_fetch_claude_token", lambda tid: "tok")
        captured = {}

        import subprocess as sp

        def fake_run(cmd, **kw):
            captured["cmd"] = cmd
            return sp.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout='{"result": "ok"}',
                stderr="",
            )

        monkeypatch.setattr(cli_runtime, "run_cli_with_heartbeat", fake_run)

        out = wf._execute_claude_chat(
            _make_input(
                platform="claude_code",
                message="hello",
                tenant_id="752626d9-8b2c-4aa2-87ef-c458d48bd38a",
            ),
            session_dir=str(tmp_path),
        )

        assert out.success is True
        assert captured["cmd"][:3] == ["claude", "-p", "hello"]
        # acceptEdits is interactive-only; print mode is already headless.
        assert "--permission-mode" not in captured["cmd"]

    def test_interactive_mode_avoids_print_flag(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CLAUDE_CODE_EXECUTION_MODE", "interactive")
        monkeypatch.setattr(claude_executor, "_feature_enabled", lambda *a, **k: True)
        monkeypatch.setattr(wf, "_fetch_claude_token", lambda tid: "tok")
        captured = {}

        import subprocess as sp

        def fake_interactive(cmd, **kw):
            captured["cmd"] = cmd
            captured["env"] = kw["env"]
            captured["prompt"] = kw["prompt"]
            captured["answer_file"] = kw.get("answer_file")
            return sp.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout="interactive ok",
                stderr="",
            )

        monkeypatch.setattr(
            claude_interactive,
            "run_claude_interactive_with_heartbeat",
            fake_interactive,
        )
        monkeypatch.setattr(
            claude_executor.claude_stream_parser,
            "build_parser",
            lambda emitter: pytest.fail("interactive mode must not use stream-json parser"),
        )

        out = wf._execute_claude_chat(
            _make_input(
                platform="claude_code",
                message="hello",
                tenant_id="752626d9-8b2c-4aa2-87ef-c458d48bd38a",
            ),
            session_dir=str(tmp_path),
        )

        assert out.success is True
        assert out.response_text == "interactive ok"
        assert "-p" not in captured["cmd"]
        assert "--output-format" not in captured["cmd"]
        assert captured["cmd"][0] == "claude"
        # Approach C (plan 2026-05-30): the turn message is NOT appended
        # positionally — the REPL ignores it. The runner receives a single-line
        # trigger to TYPE, and the blob is written to turn_prompt.md instead.
        assert captured["cmd"][-1] != "hello"
        assert "hello" not in captured["cmd"]
        assert captured["prompt"] != "hello"
        # Permission fix (2026-05-30): interactive mode auto-accepts edits so
        # Claude's Write(answer.md) isn't blocked by a tool-permission menu the
        # PTY runner can't answer (would SIGTERM the turn → exit 143).
        assert "--permission-mode" in captured["cmd"]
        assert captured["cmd"][captured["cmd"].index("--permission-mode") + 1] == "acceptEdits"
        assert "\n" not in captured["prompt"]
        assert str(tmp_path / "turn_prompt.md") in captured["prompt"]
        assert (tmp_path / "turn_prompt.md").read_text() == "hello"
        # Defect 2 (plan 2026-05-30): the trigger also instructs Claude to write
        # its answer out-of-band, and the runner is handed that ``answer_file``
        # to read back (the TUI transcript can't be reliably cleaned).
        # FINDING 1 (stale answer replay): the answer file is a UNIQUE per-turn
        # name (answer_<hex>.md), not the fixed answer.md, so a non-empty file
        # is always THIS turn's reply — never a leftover from a prior turn in
        # the reused per-tenant session_dir.
        import os as _os
        answer_file = captured.get("answer_file")
        assert _os.path.basename(answer_file).startswith("answer_")
        assert _os.path.basename(answer_file).endswith(".md")
        assert _os.path.basename(answer_file) != "answer.md"
        assert _os.path.dirname(answer_file) == str(tmp_path)
        assert answer_file in captured["prompt"]
        # FINDING 3 (Luna): the trigger asks for the COMPLETE final response.
        assert "COMPLETE" in captured["prompt"]
        assert "ANTHROPIC_API_KEY" not in captured["env"]
        assert "CLAUDE_CODE_OAUTH_TOKEN" not in captured["env"]

    def test_interactive_mode_can_use_worker_home_for_native_auth(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CLAUDE_CODE_EXECUTION_MODE", "interactive")
        monkeypatch.setenv("CLAUDE_CODE_INTERACTIVE_HOME", "worker")
        monkeypatch.setenv("CLAUDE_CODE_WORKER_HOME", "/home/codeworker")
        monkeypatch.setattr(wf, "_fetch_claude_token", lambda tid: "tok")
        captured = {}

        import subprocess as sp

        def fake_interactive(cmd, **kw):
            captured["env"] = kw["env"]
            return sp.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout="interactive ok",
                stderr="",
            )

        monkeypatch.setattr(
            claude_interactive,
            "run_claude_interactive_with_heartbeat",
            fake_interactive,
        )

        out = wf._execute_claude_chat(
            _make_input(
                platform="claude_code",
                message="hello",
                tenant_id="752626d9-8b2c-4aa2-87ef-c458d48bd38a",
            ),
            session_dir=str(tmp_path),
        )

        assert out.success is True
        assert captured["env"]["HOME"] == "/home/codeworker"
        assert "CLAUDE_CODE_OAUTH_TOKEN" not in captured["env"]

    def test_interactive_mode_keeps_api_key_tenants_on_print_path(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CLAUDE_CODE_EXECUTION_MODE", "interactive")
        monkeypatch.setattr(wf, "_fetch_claude_credential", lambda tid: ("sk-ant-fake", "api_key"))
        captured = {}

        import subprocess as sp

        def fake_run(cmd, **kw):
            captured["cmd"] = cmd
            captured["env"] = kw["env"]
            return sp.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout='{"result": "api key ok"}',
                stderr="",
            )

        monkeypatch.setattr(cli_runtime, "run_cli_with_heartbeat", fake_run)

        out = wf._execute_claude_chat(
            _make_input(platform="claude_code", message="hello"),
            session_dir=str(tmp_path),
        )

        assert out.success is True
        assert captured["cmd"][:3] == ["claude", "-p", "hello"]
        assert captured["env"]["ANTHROPIC_API_KEY"] == "sk-ant-fake"

    def test_interactive_transcript_cleaner_strips_terminal_chrome(self):
        raw = "\x1b[32mClaude Code\x1b[0m\r\n> hello\r\n╭ box\r\nUseful answer\r\n/exit\r\n"

        cleaned = claude_interactive.clean_interactive_transcript(raw, "hello")

        assert cleaned == "Useful answer"

    def test_interactive_runner_sends_exit_after_idle(self, tmp_path):
        # Approach C (plan 2026-05-30): the runner now TYPES the trigger into
        # the REPL after the settle window, waits for a post-submit response,
        # then `/exit`s on idle. The fake REPL echoes the typed line as its
        # "answer" so the response gate fires.
        script = """
import sys
print("Ready")
sys.stdout.flush()
for line in sys.stdin:
    if line.strip() == "/exit":
        print("Goodbye")
        sys.stdout.flush()
        break
    print("Answer: " + line.strip())
    sys.stdout.flush()
"""
        result = claude_interactive.run_claude_interactive_with_heartbeat(
            [sys.executable, "-c", script],
            prompt="please answer",
            label="Claude Code",
            timeout=5,
            env=os.environ.copy(),
            cwd=str(tmp_path),
            submit_settle_seconds=0.1,
            idle_exit_seconds=0.1,
            exit_grace_seconds=1,
        )

        assert result.returncode == 0
        assert "Ready" in result.stdout
        # The trigger was typed and the REPL answered it.
        assert "Answer: please answer" in result.stdout
        assert "Goodbye" in result.stdout

    def test_interactive_runner_waits_for_slow_first_output(self, tmp_path):
        # First output (banner) arrives AFTER idle_exit_seconds (simulating MCP
        # load / model warm-up). The runner must NOT `/exit` mid-startup — it
        # waits, then submits the trigger and captures the answer. (Under the
        # old logic the idle timer fired from spawn and killed the launch.)
        script = """
import sys, time
time.sleep(0.6)
print("Ready")
sys.stdout.flush()
for line in sys.stdin:
    if line.strip() == "/exit":
        break
    print("Useful answer")
    sys.stdout.flush()
"""
        result = claude_interactive.run_claude_interactive_with_heartbeat(
            [sys.executable, "-c", script],
            prompt="please answer",
            label="Claude Code",
            timeout=10,
            env=os.environ.copy(),
            cwd=str(tmp_path),
            submit_settle_seconds=0.1,
            idle_exit_seconds=0.1,
            exit_grace_seconds=1,
            first_output_seconds=5,
        )

        assert result.returncode == 0
        assert "Useful answer" in result.stdout

    def test_interactive_runner_fails_fast_on_no_output(self, tmp_path):
        # A true hang: Claude never emits anything. The runner must kill it at
        # first_output_seconds, NOT wait for idle_exit (high here) or the full
        # timeout. Distinguishes the new first-output deadline from the old path.
        import time
        script = "import time\ntime.sleep(30)\n"
        t0 = time.monotonic()
        result = claude_interactive.run_claude_interactive_with_heartbeat(
            [sys.executable, "-c", script],
            prompt="hello",
            label="Claude Code",
            timeout=20,
            env=os.environ.copy(),
            cwd=str(tmp_path),
            idle_exit_seconds=10,
            exit_grace_seconds=1,
            first_output_seconds=0.5,
        )
        elapsed = time.monotonic() - t0

        assert result.returncode != 0
        assert result.stdout.strip() == ""
        assert elapsed < 5

    def test_non_zero_exit_returns_error_with_truncated_stderr(self, monkeypatch, tmp_path):
        monkeypatch.setattr(wf, "_fetch_claude_token", lambda tid: "tok")

        import subprocess as sp

        fake_completed = sp.CompletedProcess(
            args=["claude"], returncode=2,
            stdout="", stderr="oh no",
        )
        monkeypatch.setattr(cli_runtime, "run_cli_with_heartbeat", lambda cmd, **kw: fake_completed)

        out = wf._execute_claude_chat(
            _make_input(platform="claude_code"), session_dir=str(tmp_path),
        )
        assert out.success is False
        assert "exit 2" in out.error
        assert "oh no" in out.error

    def test_empty_stdout_returns_error(self, monkeypatch, tmp_path):
        monkeypatch.setattr(wf, "_fetch_claude_token", lambda tid: "tok")
        import subprocess as sp
        fake = sp.CompletedProcess(args=["claude"], returncode=0, stdout="", stderr="")
        monkeypatch.setattr(cli_runtime, "run_cli_with_heartbeat", lambda cmd, **kw: fake)
        out = wf._execute_claude_chat(_make_input(platform="claude_code"), session_dir=str(tmp_path))
        assert out.success is False
        assert "no output" in out.error.lower()

    def test_non_json_stdout_returned_as_text(self, monkeypatch, tmp_path):
        monkeypatch.setattr(wf, "_fetch_claude_token", lambda tid: "tok")
        import subprocess as sp
        fake = sp.CompletedProcess(
            args=["claude"], returncode=0, stdout="plain text response", stderr="",
        )
        monkeypatch.setattr(cli_runtime, "run_cli_with_heartbeat", lambda cmd, **kw: fake)
        out = wf._execute_claude_chat(_make_input(platform="claude_code"), session_dir=str(tmp_path))
        assert out.success is True
        assert out.response_text == "plain text response"
        assert out.metadata["platform"] == "claude_code"
