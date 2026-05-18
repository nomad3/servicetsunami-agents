"""Tests for the Kimi K2 (Moonshot AI) CLI executor — Wave 1c.

Mirrors the dispatch-side smoke tests for ``_execute_claude_chat`` /
``_execute_codex_chat`` in ``test_execute_chat_cli.py``. We mock the
credential vault, ``cli_runtime.run_cli_with_heartbeat``, and the npm
binary resolution so the test never spawns a subprocess or touches the
network.
"""
from __future__ import annotations

import os
import subprocess as sp

import pytest

import cli_runtime
import workflows as wf
from cli_executors import kimi as kimi_module


def _make_input(**overrides) -> wf.ChatCliInput:
    base = dict(
        platform="kimi_k2",
        message="hello kimi",
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
def _isolate_kimi_env(monkeypatch):
    """Strip any inherited MOONSHOT_API_KEY / OPENAI_* env so tests don't
    accidentally pick up a real key from the dev shell."""
    for var in ("MOONSHOT_API_KEY", "MOONSHOT_BASE_URL", "OPENAI_API_KEY", "OPENAI_BASE_URL"):
        monkeypatch.delenv(var, raising=False)
    # Default: simulate the global ``kimi`` binary being on PATH so the
    # argv prefix is deterministic. Tests that exercise the npx fallback
    # override this with their own which() stub.
    monkeypatch.setattr(kimi_module.shutil, "which", lambda name: "/usr/local/bin/kimi")
    yield


@pytest.fixture
def _stub_creds(monkeypatch):
    """Default: vault returns an api_key. Tests that need a miss override."""
    def _fake(integration_name, tenant_id):
        assert integration_name == "kimi_k2"
        return {"api_key": "sk-moonshot-FAKE-TEST-KEY"}
    monkeypatch.setattr(wf, "_fetch_integration_credentials", _fake)
    yield


# ── happy path ─────────────────────────────────────────────────────────


class TestHappyPath:
    def test_parses_json_response_with_usage(self, monkeypatch, tmp_path, _stub_creds):
        captured_cmd = {}

        def _fake_run(cmd, **kwargs):
            captured_cmd["cmd"] = cmd
            captured_cmd["env"] = kwargs.get("env") or {}
            captured_cmd["cwd"] = kwargs.get("cwd")
            return sp.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout=(
                    '{"result": "hello back",'
                    ' "usage": {"prompt_tokens": 5, "completion_tokens": 7},'
                    ' "model": "kimi-k2-instruct"}'
                ),
                stderr="",
            )

        monkeypatch.setattr(cli_runtime, "run_cli_with_heartbeat", _fake_run)

        out = wf._execute_kimi_chat(_make_input(), session_dir=str(tmp_path))

        assert out.success is True
        assert out.response_text == "hello back"
        assert out.metadata["platform"] == "kimi_k2"
        assert out.metadata["input_tokens"] == 5
        assert out.metadata["output_tokens"] == 7
        assert out.metadata["model"] == "kimi-k2-instruct"

        # argv starts with the resolved binary and carries the prompt.
        assert captured_cmd["cmd"][0] == "kimi"
        assert "-p" in captured_cmd["cmd"]
        assert "hello kimi" in captured_cmd["cmd"]
        # default model + json output format flags.
        assert "--model" in captured_cmd["cmd"]
        assert "kimi-k2-instruct" in captured_cmd["cmd"]
        assert "--output-format" in captured_cmd["cmd"]
        assert "json" in captured_cmd["cmd"]

    def test_env_propagates_api_key_and_base_url(self, monkeypatch, tmp_path, _stub_creds):
        captured = {}

        def _fake_run(cmd, **kwargs):
            captured["env"] = kwargs.get("env") or {}
            return sp.CompletedProcess(args=cmd, returncode=0, stdout='{"result": "ok"}', stderr="")

        monkeypatch.setattr(cli_runtime, "run_cli_with_heartbeat", _fake_run)

        wf._execute_kimi_chat(_make_input(), session_dir=str(tmp_path))

        env = captured["env"]
        # Both MOONSHOT_* (kimi-native) and OPENAI_* (compat fallback) must be exported.
        assert env["MOONSHOT_API_KEY"] == "sk-moonshot-FAKE-TEST-KEY"
        assert env["OPENAI_API_KEY"] == "sk-moonshot-FAKE-TEST-KEY"
        assert env["MOONSHOT_BASE_URL"].startswith("https://api.moonshot.")
        assert env["OPENAI_BASE_URL"] == env["MOONSHOT_BASE_URL"]
        # HOME redirected to tenant home (or session_dir fallback).
        assert env["HOME"]

    def test_non_json_stdout_returned_as_plain_text(self, monkeypatch, tmp_path, _stub_creds):
        monkeypatch.setattr(
            cli_runtime, "run_cli_with_heartbeat",
            lambda cmd, **kw: sp.CompletedProcess(
                args=cmd, returncode=0, stdout="plain answer", stderr="",
            ),
        )
        out = wf._execute_kimi_chat(_make_input(), session_dir=str(tmp_path))
        assert out.success is True
        assert out.response_text == "plain answer"
        assert out.metadata["platform"] == "kimi_k2"

    def test_per_tenant_base_url_and_model_override(self, monkeypatch, tmp_path):
        # Vault returns a Chinese-tier base URL and a future model name.
        def _fake(integration_name, tenant_id):
            return {
                "api_key": "sk-moonshot-OVERRIDE",
                "base_url": "https://api.moonshot.cn/v1",
                "model": "kimi-k2-pro",
            }
        monkeypatch.setattr(wf, "_fetch_integration_credentials", _fake)

        captured = {}

        def _fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["env"] = kwargs.get("env") or {}
            return sp.CompletedProcess(
                args=cmd, returncode=0,
                stdout='{"result": "ok", "model": "kimi-k2-pro"}', stderr="",
            )

        monkeypatch.setattr(cli_runtime, "run_cli_with_heartbeat", _fake_run)
        out = wf._execute_kimi_chat(_make_input(), session_dir=str(tmp_path))
        assert out.success is True
        assert captured["env"]["MOONSHOT_BASE_URL"] == "https://api.moonshot.cn/v1"
        assert "kimi-k2-pro" in captured["cmd"]


# ── credential resolution ──────────────────────────────────────────────


class TestCredentialResolution:
    def test_missing_credentials_returns_friendly_error(self, monkeypatch, tmp_path):
        def _miss(*_a, **_kw):
            raise RuntimeError("integration not connected")
        monkeypatch.setattr(wf, "_fetch_integration_credentials", _miss)

        out = wf._execute_kimi_chat(_make_input(), session_dir=str(tmp_path))

        assert out.success is False
        assert "not connected" in out.error.lower()

    def test_env_var_fallback_when_vault_empty(self, monkeypatch, tmp_path):
        """A tenant who hasn't filled in the Integrations card can still
        route to Kimi when the operator has wired a shared
        ``MOONSHOT_API_KEY`` into the worker container env."""
        # Vault returns an empty dict (no api_key) — simulate "card created
        # but never saved a key".
        monkeypatch.setattr(wf, "_fetch_integration_credentials", lambda *_a, **_kw: {})
        monkeypatch.setenv("MOONSHOT_API_KEY", "sk-shared-operator-key")

        captured_env = {}

        def _fake_run(cmd, **kwargs):
            captured_env.update(kwargs.get("env") or {})
            return sp.CompletedProcess(args=cmd, returncode=0, stdout='{"result": "ok"}', stderr="")

        monkeypatch.setattr(cli_runtime, "run_cli_with_heartbeat", _fake_run)

        out = wf._execute_kimi_chat(_make_input(), session_dir=str(tmp_path))
        assert out.success is True
        assert captured_env["MOONSHOT_API_KEY"] == "sk-shared-operator-key"


# ── failure paths ──────────────────────────────────────────────────────


class TestFailurePaths:
    def test_non_zero_exit_returns_error_with_truncated_stderr(
        self, monkeypatch, tmp_path, _stub_creds,
    ):
        monkeypatch.setattr(
            cli_runtime, "run_cli_with_heartbeat",
            lambda cmd, **kw: sp.CompletedProcess(
                args=cmd, returncode=3, stdout="", stderr="boom: rate limit exceeded",
            ),
        )
        out = wf._execute_kimi_chat(_make_input(), session_dir=str(tmp_path))
        assert out.success is False
        assert "exit 3" in out.error
        assert "rate limit" in out.error
        assert out.metadata == {"platform": "kimi_k2"}

    def test_empty_stdout_returns_error(self, monkeypatch, tmp_path, _stub_creds):
        monkeypatch.setattr(
            cli_runtime, "run_cli_with_heartbeat",
            lambda cmd, **kw: sp.CompletedProcess(
                args=cmd, returncode=0, stdout="", stderr="",
            ),
        )
        out = wf._execute_kimi_chat(_make_input(), session_dir=str(tmp_path))
        assert out.success is False
        assert "no output" in out.error.lower()


# ── binary resolution ──────────────────────────────────────────────────


class TestBinaryResolution:
    def test_uses_global_kimi_binary_when_on_path(self, monkeypatch):
        monkeypatch.setattr(kimi_module.shutil, "which", lambda name: "/usr/local/bin/kimi")
        assert kimi_module._resolve_cli_binary() == ["kimi"]

    def test_falls_back_to_npx_when_binary_missing(self, monkeypatch):
        monkeypatch.setattr(kimi_module.shutil, "which", lambda name: None)
        argv = kimi_module._resolve_cli_binary()
        assert argv[0] == "npx"
        assert "@moonshotai/kimi-cli" in argv


# ── workflows.py dispatch integration ──────────────────────────────────


class TestDispatchIntegration:
    """Confirm execute_chat_cli routes ``platform="kimi_k2"`` to the
    Kimi executor — the explicit dispatch arm wired in workflows.py."""

    @pytest.fixture(autouse=True)
    def _isolate_session_dir(self, monkeypatch, tmp_path):
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
        monkeypatch.setattr(wf, "_fetch_github_token", lambda tid: None)
        monkeypatch.setattr(wf.subprocess, "run", lambda *a, **kw: None)
        yield

    def test_dispatcher_routes_kimi_platform_to_kimi_executor(self, monkeypatch):
        sentinel = wf.ChatCliResult(response_text="OK-kimi", success=True)
        calls: list = []

        def fake_kimi(*args, **kwargs):
            calls.append(args)
            return sentinel

        monkeypatch.setattr(wf, "_execute_kimi_chat", fake_kimi)
        out = wf.execute_chat_cli(_make_input(platform="kimi_k2"))
        assert out is sentinel
        assert len(calls) == 1
