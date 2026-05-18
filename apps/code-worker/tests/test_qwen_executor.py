"""Tests for the Qwen Code CLI executor — Wave 1b.

Mirrors ``test_chat_cli_helpers.py::TestExecuteGeminiChat`` in shape
because qwen.py mirrors gemini.py: both ship as npm CLIs with a
``-p PROMPT -y --output-format json`` surface, and both emit a single
terminal JSON blob on stdout plus tool-error signals on stderr.

What's covered:
  * happy path — process env api_key → stdout JSON parsed
  * happy path — vault api_key (env unset)
  * vault failure → friendly "not connected" error (so the resolver
    classifies as ``missing_credential`` and chains, not as a hard fail)
  * vault returns empty creds → same friendly message
  * non-zero exit code with stderr tool-error pattern → tool_errors
    metadata captured even on failure
  * stream parser — tool_use / tool_result / stderr classification
  * env propagation — QWEN_API_KEY + DASHSCOPE_API_KEY set, Google
    enterprise-mode env vars stripped
"""
from __future__ import annotations

import subprocess
import sys

import pytest

import cli_runtime
from cli_executors import qwen as qwen_mod
from cli_executors import qwen_stream_parser


@pytest.fixture
def wf():
    """Re-import workflows on each test so a sibling test (e.g. the
    provider-adapter contract suite) popping ``workflows`` from
    ``sys.modules`` doesn't leave us patching a stale module. The
    executor body does a lazy ``from workflows import ...`` and resolves
    against whatever is currently in ``sys.modules``."""
    import workflows  # noqa: F401 — ensures the module is in sys.modules
    return sys.modules["workflows"]


# ── helpers ──────────────────────────────────────────────────────────────

def _make_input(**overrides):
    # Resolve ChatCliInput from sys.modules at call time so a sibling
    # test that re-imports workflows doesn't strand us on a stale class.
    import workflows  # noqa: F401
    base = dict(
        platform="qwen_code",
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
    return sys.modules["workflows"].ChatCliInput(**base)


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(
        args=["qwen"], returncode=returncode, stdout=stdout, stderr=stderr,
    )


class _RecordingEmitter:
    """Stand-in SessionEventEmitter that just records calls."""
    def __init__(self):
        self.calls: list[tuple[str, str, str]] = []

    def emit_chunk(self, kind: str, payload: str, *, fd: str = "stdout") -> None:
        self.calls.append((kind, payload, fd))


# ── executor tests ───────────────────────────────────────────────────────

class TestExecuteQwenChat:
    @pytest.fixture(autouse=True)
    def _no_api_key(self, monkeypatch):
        monkeypatch.delenv("QWEN_API_KEY", raising=False)
        monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)

    def test_no_credentials_returns_not_connected(self, monkeypatch, tmp_path, wf):
        """Vault returns empty dict → friendly "not connected" error so
        the resolver chains to the next CLI without a hard failure."""
        monkeypatch.setattr(wf, "_fetch_integration_credentials", lambda i, t: {})
        out = qwen_mod.execute_qwen_chat(
            _make_input(),
            session_dir=str(tmp_path),
        )
        assert out.success is False
        assert "not connected" in out.error.lower()
        assert "qwen" in out.error.lower()

    def test_creds_fetch_exception_returns_friendly_error(self, monkeypatch, tmp_path, wf):
        """A raw exception from the vault fetch must be translated to the
        friendly message — the resolver's ``missing_credential`` regex
        anchors on the long form, not on raw httpx 404 text."""
        def boom(i, t):
            raise RuntimeError("network failed")
        monkeypatch.setattr(wf, "_fetch_integration_credentials", boom)
        out = qwen_mod.execute_qwen_chat(
            _make_input(),
            session_dir=str(tmp_path),
        )
        assert out.success is False
        assert "not connected" in out.error.lower()

    def test_happy_path_env_api_key(self, monkeypatch, tmp_path):
        """``QWEN_API_KEY`` in process env wins over vault — local-dev
        convenience path."""
        monkeypatch.setenv("QWEN_API_KEY", "sk-qwen-FAKE-env")

        captured = {}

        def fake_run(cmd, **kw):
            captured["cmd"] = cmd
            captured["env"] = kw.get("env", {})
            return _completed(
                returncode=0,
                stdout='{"result": "Qwen speaking", "model": "qwen-coder", "usage": {"input_tokens": 12, "output_tokens": 7}}',
                stderr="",
            )

        monkeypatch.setattr(cli_runtime, "run_cli_with_heartbeat", fake_run)

        out = qwen_mod.execute_qwen_chat(
            _make_input(),
            session_dir=str(tmp_path),
        )
        assert out.success is True
        assert out.response_text == "Qwen speaking"
        assert out.metadata["platform"] == "qwen_code"
        assert out.metadata["model"] == "qwen-coder"
        assert out.metadata["input_tokens"] == 12
        # Command shape mirrors gemini-cli's surface.
        assert captured["cmd"][0] == "qwen"
        assert "-p" in captured["cmd"]
        assert "--output-format" in captured["cmd"]
        # API key is propagated under both vendor-specific env vars so
        # either the DashScope-native path or the OpenAI-compatible shim
        # picks it up without the executor branching on endpoint flavour.
        assert captured["env"].get("QWEN_API_KEY") == "sk-qwen-FAKE-env"
        assert captured["env"].get("DASHSCOPE_API_KEY") == "sk-qwen-FAKE-env"
        # Google enterprise-mode env vars MUST be stripped — qwen-code
        # forked from gemini-cli and inherits the same probing logic.
        assert "GEMINI_API_KEY" not in captured["env"]
        assert "GOOGLE_CLOUD_PROJECT" not in captured["env"]

    def test_happy_path_vault_api_key(self, monkeypatch, tmp_path, wf):
        """No process env → fall through to the vault and use the
        ``api_key`` field. This is the production BYOK path."""
        monkeypatch.setattr(
            wf, "_fetch_integration_credentials",
            lambda i, t: {"api_key": "sk-qwen-FAKE-vault"},
        )
        captured = {}

        def fake_run(cmd, **kw):
            captured["env"] = kw.get("env", {})
            return _completed(
                returncode=0,
                stdout='{"result": "from vault"}',
                stderr="",
            )

        monkeypatch.setattr(cli_runtime, "run_cli_with_heartbeat", fake_run)

        out = qwen_mod.execute_qwen_chat(
            _make_input(),
            session_dir=str(tmp_path),
        )
        assert out.success is True
        assert out.response_text == "from vault"
        assert captured["env"].get("QWEN_API_KEY") == "sk-qwen-FAKE-vault"

    def test_non_zero_exit_includes_tool_error_metadata(self, monkeypatch, tmp_path):
        """Stderr ``Error executing tool X: Y`` must be lifted into
        ``metadata.tools_called`` even on failure — same contract as the
        gemini executor."""
        monkeypatch.setenv("QWEN_API_KEY", "sk-qwen-FAKE")
        stderr_text = "Error executing tool default_api:list_files: not authorized\n"
        monkeypatch.setattr(
            cli_runtime, "run_cli_with_heartbeat",
            lambda cmd, **kw: _completed(returncode=1, stdout="", stderr=stderr_text),
        )

        out = qwen_mod.execute_qwen_chat(
            _make_input(),
            session_dir=str(tmp_path),
        )
        assert out.success is False
        assert "exit 1" in out.error
        tools_called = out.metadata["tools_called"]
        assert any("default_api:list_files" in t["name"] for t in tools_called)
        assert out.metadata["platform"] == "qwen_code"

    def test_empty_stdout_returns_no_output_error(self, monkeypatch, tmp_path):
        """A zero exit but empty stdout is treated as a soft failure so
        the resolver can still chain to the next CLI."""
        monkeypatch.setenv("QWEN_API_KEY", "sk-qwen-FAKE")
        monkeypatch.setattr(
            cli_runtime, "run_cli_with_heartbeat",
            lambda cmd, **kw: _completed(returncode=0, stdout="", stderr=""),
        )
        out = qwen_mod.execute_qwen_chat(
            _make_input(),
            session_dir=str(tmp_path),
        )
        assert out.success is False
        assert "no output" in out.error.lower()

    def test_non_json_stdout_passed_through(self, monkeypatch, tmp_path):
        """If the CLI emits plain text instead of JSON (older qwen-code
        versions occasionally do this on errors), the executor returns
        the text verbatim with success=True — mirrors gemini behaviour."""
        monkeypatch.setenv("QWEN_API_KEY", "sk-qwen-FAKE")
        monkeypatch.setattr(
            cli_runtime, "run_cli_with_heartbeat",
            lambda cmd, **kw: _completed(returncode=0, stdout="bare text reply", stderr=""),
        )
        out = qwen_mod.execute_qwen_chat(
            _make_input(),
            session_dir=str(tmp_path),
        )
        assert out.success is True
        assert out.response_text == "bare text reply"
        assert out.metadata["platform"] == "qwen_code"


# ── stream parser tests ──────────────────────────────────────────────────

class TestQwenStreamParser:
    def test_stderr_tool_error_emits_tool_result(self):
        emitter = _RecordingEmitter()
        on_chunk = qwen_stream_parser.build_parser(emitter)
        on_chunk("Error executing tool fs:read_file: permission denied\n", "stderr")
        kinds = [c[0] for c in emitter.calls]
        assert "tool_result" in kinds
        # Payload contains the tool name + truncated reason.
        payload = next(c[1] for c in emitter.calls if c[0] == "tool_result")
        assert "fs:read_file" in payload

    def test_stderr_tool_use_emits_tool_use(self):
        """Both ``[qwen]`` and ``[qwen-code]`` prefixes are accepted —
        upstream uses both depending on which sub-binary handled the call."""
        emitter = _RecordingEmitter()
        on_chunk = qwen_stream_parser.build_parser(emitter)
        on_chunk("[qwen] tool: search_web\n", "stderr")
        on_chunk("[qwen-code] tool: edit_file\n", "stderr")
        kinds = [c[0] for c in emitter.calls]
        assert kinds.count("tool_use") == 2

    def test_unclassified_stderr_falls_through(self):
        emitter = _RecordingEmitter()
        on_chunk = qwen_stream_parser.build_parser(emitter)
        on_chunk("some random log line\n", "stderr")
        assert emitter.calls == [("stderr", "some random log line\n", "stderr")]

    def test_stdout_forwarded_as_stdout(self):
        """Stdout is a terminal JSON blob — parser forwards it as plain
        stdout (live UI sees the dump shape, executor body parses it
        once at end of run)."""
        emitter = _RecordingEmitter()
        on_chunk = qwen_stream_parser.build_parser(emitter)
        on_chunk('{"result": "x"}', "stdout")
        assert emitter.calls == [("stdout", '{"result": "x"}', "stdout")]

    def test_blank_lines_dropped(self):
        emitter = _RecordingEmitter()
        on_chunk = qwen_stream_parser.build_parser(emitter)
        on_chunk("   \n", "stderr")
        on_chunk("", "stdout")
        assert emitter.calls == []
