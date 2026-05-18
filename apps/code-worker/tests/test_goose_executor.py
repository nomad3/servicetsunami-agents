"""Tests for the Goose (Block) CLI executor — Wave 2d.

Mirrors ``test_qwen_executor.py`` in shape because goose.py follows the
same subprocess pattern: a single CLI invocation, JSON-ish stdout on
success, tool errors on stderr, friendly "not connected" path for the
missing-credential resolver branch.

What's covered:

  * No credentials → friendly "not connected" error (vault fetch
    exception path).
  * Empty vault payload → still produces the friendly message.
  * Happy path: provider + model + api_key from the vault flow into the
    command + env, prompt is forwarded via ``--text``, stdout is returned.
  * Instruction prepend: ``instruction_md_content`` is concatenated
    above the user message before being passed to ``--text``.
  * Per-turn model override via ``task_input.model``.
  * Session resume: ``--session-id <id> --resume`` (bool flag) when the
    chat session id is present, ``--no-session`` when blank — matches
    the upstream ``goose run`` CLI surface.
  * Tool-error stderr lines surface in ``metadata.tools_called``.
  * MCP config materialisation: tenant ``mcp_config`` lands in
    ``~/.config/goose/mcp.json`` under the tenant HOME redirect.
  * Empty stdout on zero exit → soft failure (resolver chains).
  * Provider key propagation: ANTHROPIC_API_KEY (default provider) is
    set in env when the credential is present.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

import cli_runtime
from cli_executors import goose as goose_mod


@pytest.fixture
def wf():
    """Re-import workflows on each test so a sibling test popping
    ``workflows`` from ``sys.modules`` doesn't leave us patching a stale
    module. The executor body does a lazy ``from workflows import ...``
    and resolves against whatever is currently in ``sys.modules``."""
    import workflows  # noqa: F401 — ensures the module is in sys.modules
    return sys.modules["workflows"]


def _make_input(**overrides):
    import workflows  # noqa: F401
    base = dict(
        platform="goose",
        message="hello goose",
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
        args=["goose"], returncode=returncode, stdout=stdout, stderr=stderr,
    )


class TestExecuteGooseChat:
    def test_no_credentials_returns_not_connected(self, monkeypatch, tmp_path, wf):
        """Vault fetch raises → friendly "not connected" error so the
        resolver chains past goose without a hard failure."""
        def boom(integration, tenant):
            raise RuntimeError("404 not found")
        monkeypatch.setattr(wf, "_fetch_integration_credentials", boom)
        monkeypatch.setattr(
            cli_runtime, "tenant_home_dir",
            lambda tid: tmp_path / "home",
        )

        out = goose_mod.execute_goose_chat(
            _make_input(),
            session_dir=str(tmp_path),
        )
        assert out.success is False
        assert "not connected" in out.error.lower()
        assert "goose" in out.error.lower()

    def test_empty_creds_still_runs_with_defaults(self, monkeypatch, tmp_path, wf):
        """Vault returns ``{}`` → executor falls back to default provider
        and model (anthropic / claude-3-5-sonnet-latest) and lets the
        subprocess do its thing. The provider may still fail at runtime,
        but the executor doesn't fail-fast on missing keys — operator may
        have wired a shared key into the container env."""
        monkeypatch.setattr(wf, "_fetch_integration_credentials", lambda i, t: {})
        monkeypatch.setattr(
            cli_runtime, "tenant_home_dir",
            lambda tid: tmp_path / "home",
        )

        captured = {}

        def fake_run(cmd, **kw):
            captured["cmd"] = cmd
            captured["env"] = kw.get("env", {})
            return _completed(returncode=0, stdout="Goose says hi", stderr="")

        monkeypatch.setattr(cli_runtime, "run_cli_with_heartbeat", fake_run)

        out = goose_mod.execute_goose_chat(
            _make_input(),
            session_dir=str(tmp_path),
        )
        assert out.success is True
        assert "--provider" in captured["cmd"]
        i = captured["cmd"].index("--provider")
        assert captured["cmd"][i + 1] == "anthropic"
        # Default model
        j = captured["cmd"].index("--model")
        assert captured["cmd"][j + 1] == "claude-3-5-sonnet-latest"

    def test_happy_path_vault_provider_and_key(self, monkeypatch, tmp_path, wf):
        """Vault supplies provider + model + api_key → subprocess gets
        the right CLI args and provider-specific env var."""
        monkeypatch.setattr(
            wf, "_fetch_integration_credentials",
            lambda i, t: {
                "provider": "anthropic",
                "model": "claude-3-5-haiku-20241022",
                "api_key": "sk-ant-FAKE",
            },
        )
        monkeypatch.setattr(
            cli_runtime, "tenant_home_dir",
            lambda tid: tmp_path / "home",
        )

        captured = {}

        def fake_run(cmd, **kw):
            captured["cmd"] = cmd
            captured["env"] = kw.get("env", {})
            return _completed(returncode=0, stdout="Goose response", stderr="")

        monkeypatch.setattr(cli_runtime, "run_cli_with_heartbeat", fake_run)

        out = goose_mod.execute_goose_chat(
            _make_input(),
            session_dir=str(tmp_path),
        )
        assert out.success is True
        assert out.response_text == "Goose response"
        assert out.metadata["platform"] == "goose"
        assert out.metadata["provider"] == "anthropic"
        assert out.metadata["model"] == "claude-3-5-haiku-20241022"
        # Command shape — ``goose run`` is the headless one-shot
        # subcommand (NOT ``goose session``, which is interactive and
        # ignores --text/--provider/--model).
        assert captured["cmd"][:2] == ["goose", "run"]
        assert "--text" in captured["cmd"]
        # No session_id → ``--no-session`` (don't allocate a persistent
        # session for a one-shot turn).
        assert "--no-session" in captured["cmd"]
        assert "--session-id" not in captured["cmd"]
        # Provider-specific env var is set
        assert captured["env"].get("ANTHROPIC_API_KEY") == "sk-ant-FAKE"
        # Telemetry opt-out
        assert captured["env"].get("GOOSE_TELEMETRY") == "0"
        assert captured["env"].get("DO_NOT_TRACK") == "1"

    def test_instruction_md_prepended_to_prompt(self, monkeypatch, tmp_path, wf):
        """``instruction_md_content`` is concatenated above the user
        message before being passed to ``-t`` — same shape as the other
        executors in the suite."""
        monkeypatch.setattr(
            wf, "_fetch_integration_credentials",
            lambda i, t: {"provider": "anthropic", "api_key": "sk-ant"},
        )
        monkeypatch.setattr(
            cli_runtime, "tenant_home_dir",
            lambda tid: tmp_path / "home",
        )

        captured = {}

        def fake_run(cmd, **kw):
            captured["cmd"] = cmd
            return _completed(returncode=0, stdout="ok", stderr="")

        monkeypatch.setattr(cli_runtime, "run_cli_with_heartbeat", fake_run)

        goose_mod.execute_goose_chat(
            _make_input(
                message="do the thing",
                instruction_md_content="You are a careful agent.",
            ),
            session_dir=str(tmp_path),
        )
        t_idx = captured["cmd"].index("--text")
        prompt = captured["cmd"][t_idx + 1]
        assert "You are a careful agent." in prompt
        assert "do the thing" in prompt
        assert "# User Request" in prompt

    def test_per_turn_model_override(self, monkeypatch, tmp_path, wf):
        """``task_input.model`` overrides whatever the vault stored.
        Lets an agent pin a specific model variant per chat surface."""
        monkeypatch.setattr(
            wf, "_fetch_integration_credentials",
            lambda i, t: {"provider": "openai", "model": "gpt-4o"},
        )
        monkeypatch.setattr(
            cli_runtime, "tenant_home_dir",
            lambda tid: tmp_path / "home",
        )

        captured = {}

        def fake_run(cmd, **kw):
            captured["cmd"] = cmd
            return _completed(returncode=0, stdout="ok", stderr="")

        monkeypatch.setattr(cli_runtime, "run_cli_with_heartbeat", fake_run)

        goose_mod.execute_goose_chat(
            _make_input(model="gpt-4o-mini"),
            session_dir=str(tmp_path),
        )
        m_idx = captured["cmd"].index("--model")
        assert captured["cmd"][m_idx + 1] == "gpt-4o-mini"

    def test_session_resume_when_session_id_present(self, monkeypatch, tmp_path, wf):
        """A non-empty ``session_id`` adds ``--session-id <id> --resume``
        (bool flag, NOT value-taking) so ``goose run`` re-binds to the
        existing session store. ``--no-session`` MUST NOT appear when
        we're resuming."""
        monkeypatch.setattr(
            wf, "_fetch_integration_credentials",
            lambda i, t: {"provider": "anthropic"},
        )
        monkeypatch.setattr(
            cli_runtime, "tenant_home_dir",
            lambda tid: tmp_path / "home",
        )

        captured = {}

        def fake_run(cmd, **kw):
            captured["cmd"] = cmd
            return _completed(returncode=0, stdout="ok", stderr="")

        monkeypatch.setattr(cli_runtime, "run_cli_with_heartbeat", fake_run)

        goose_mod.execute_goose_chat(
            _make_input(session_id="sess-xyz-123"),
            session_dir=str(tmp_path),
        )
        # --session-id <id> appears, immediately followed by the bool
        # --resume flag (no value).
        assert "--session-id" in captured["cmd"]
        s_idx = captured["cmd"].index("--session-id")
        assert captured["cmd"][s_idx + 1] == "sess-xyz-123"
        assert captured["cmd"][s_idx + 2] == "--resume"
        # No --no-session when resuming.
        assert "--no-session" not in captured["cmd"]

    def test_tool_error_metadata_captured(self, monkeypatch, tmp_path, wf):
        """Stderr ``Error executing tool X: Y`` → ``metadata.tools_called``
        even on non-zero exit. Same contract as gemini/qwen."""
        monkeypatch.setattr(
            wf, "_fetch_integration_credentials",
            lambda i, t: {"provider": "anthropic"},
        )
        monkeypatch.setattr(
            cli_runtime, "tenant_home_dir",
            lambda tid: tmp_path / "home",
        )
        stderr_text = "Error executing tool fs:read_file: permission denied\n"
        monkeypatch.setattr(
            cli_runtime, "run_cli_with_heartbeat",
            lambda cmd, **kw: _completed(returncode=1, stdout="", stderr=stderr_text),
        )

        out = goose_mod.execute_goose_chat(
            _make_input(),
            session_dir=str(tmp_path),
        )
        assert out.success is False
        assert "exit 1" in out.error
        tools_called = out.metadata["tools_called"]
        assert any("fs:read_file" in t["name"] for t in tools_called)
        assert out.metadata["platform"] == "goose"

    def test_mcp_config_materialised_to_goose_dir(self, monkeypatch, tmp_path, wf):
        """The tenant's MCP source list lands in
        ``$HOME/.config/goose/mcp.json`` before the subprocess fires so
        Goose's MCP auto-discovery picks them up."""
        monkeypatch.setattr(
            wf, "_fetch_integration_credentials",
            lambda i, t: {"provider": "anthropic"},
        )
        home_dir = tmp_path / "tenant-home"
        monkeypatch.setattr(
            cli_runtime, "tenant_home_dir",
            lambda tid: home_dir,
        )
        monkeypatch.setattr(
            cli_runtime, "run_cli_with_heartbeat",
            lambda cmd, **kw: _completed(returncode=0, stdout="ok", stderr=""),
        )

        mcp_blob = json.dumps({
            "mcpServers": {
                "higgsfield": {"command": "higgsfield-mcp", "args": ["--mode", "stdio"]},
            },
        })
        goose_mod.execute_goose_chat(
            _make_input(mcp_config=mcp_blob),
            session_dir=str(tmp_path),
        )
        target = home_dir / ".config" / "goose" / "mcp.json"
        assert target.exists()
        written = json.loads(target.read_text())
        assert "higgsfield" in written["mcpServers"]

    def test_empty_mcp_config_writes_empty_stub(self, monkeypatch, tmp_path, wf):
        """No MCP sources wired → write an empty stub so Goose doesn't
        spawn the demo defaults shipped with the binary."""
        monkeypatch.setattr(
            wf, "_fetch_integration_credentials",
            lambda i, t: {"provider": "anthropic"},
        )
        home_dir = tmp_path / "tenant-home"
        monkeypatch.setattr(
            cli_runtime, "tenant_home_dir",
            lambda tid: home_dir,
        )
        monkeypatch.setattr(
            cli_runtime, "run_cli_with_heartbeat",
            lambda cmd, **kw: _completed(returncode=0, stdout="ok", stderr=""),
        )

        goose_mod.execute_goose_chat(
            _make_input(mcp_config=""),
            session_dir=str(tmp_path),
        )
        target = home_dir / ".config" / "goose" / "mcp.json"
        assert target.exists()
        assert json.loads(target.read_text()) == {"mcpServers": {}}

    def test_empty_stdout_returns_no_output_error(self, monkeypatch, tmp_path, wf):
        """Zero exit but empty stdout → soft failure so the resolver
        can still chain to the next CLI."""
        monkeypatch.setattr(
            wf, "_fetch_integration_credentials",
            lambda i, t: {"provider": "anthropic"},
        )
        monkeypatch.setattr(
            cli_runtime, "tenant_home_dir",
            lambda tid: tmp_path / "home",
        )
        monkeypatch.setattr(
            cli_runtime, "run_cli_with_heartbeat",
            lambda cmd, **kw: _completed(returncode=0, stdout="", stderr=""),
        )
        out = goose_mod.execute_goose_chat(
            _make_input(),
            session_dir=str(tmp_path),
        )
        assert out.success is False
        assert "no output" in out.error.lower()


class TestWriteMcpConfig:
    def test_malformed_json_falls_back_to_stub(self, tmp_path):
        """Incoming malformed JSON → empty stub instead of Goose choking
        on the file at startup."""
        goose_mod._write_mcp_config(str(tmp_path), "{not json")
        target = tmp_path / "mcp.json"
        assert target.exists()
        assert json.loads(target.read_text()) == {"mcpServers": {}}

    def test_valid_json_passed_through_verbatim(self, tmp_path):
        blob = json.dumps({"mcpServers": {"x": {"command": "y"}}})
        goose_mod._write_mcp_config(str(tmp_path), blob)
        target = tmp_path / "mcp.json"
        assert json.loads(target.read_text()) == json.loads(blob)
