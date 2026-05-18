"""Tests for the Aider CLI executor — Wave 2c.

Mirrors ``test_qwen_executor.py`` in shape: subprocess is mocked at
``cli_runtime.run_cli_with_heartbeat`` and we assert on the command,
env, and ChatCliResult shape. Aider is a Python CLI binary (no npm /
no HTTP) so the pattern matches the gemini/qwen executors rather than
the Kimi httpx pattern.

What's covered:
  * vault miss → friendly "not connected" error
  * vault returns model + api_key → env var is set under the
    LiteLLM-flavoured key (ANTHROPIC_API_KEY for anthropic/* model)
  * env-var fallback (ANTHROPIC_API_KEY in process env, no vault)
  * happy path — stdout parsed, model + tokens lifted into metadata
  * non-zero exit → CLI exit message + metadata.platform=aider
  * empty stdout → friendly "no output" error so resolver can chain
  * model override via task_input.model wins over vault model
  * provider-key derivation: openai/* → OPENAI_API_KEY,
    deepseek/* → DEEPSEEK_API_KEY, unknown → OPENAI_API_KEY (LiteLLM
    default-fallback semantics)
"""
from __future__ import annotations

import subprocess
import sys

import pytest

import cli_runtime
from cli_executors import aider as aider_mod


@pytest.fixture
def wf():
    """Re-import workflows on each test so a sibling test (e.g. the
    provider-adapter contract suite) popping ``workflows`` from
    ``sys.modules`` doesn't leave us patching a stale module."""
    import workflows  # noqa: F401
    return sys.modules["workflows"]


def _make_input(**overrides):
    import workflows  # noqa: F401
    base = dict(
        platform="aider",
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
        args=["aider"], returncode=returncode, stdout=stdout, stderr=stderr,
    )


# ── env_var_for_model unit tests ─────────────────────────────────────────


class TestEnvVarForModel:
    # Prefixed slugs — LiteLLM-style ``provider/model``.
    def test_anthropic_prefix(self):
        assert aider_mod._env_var_for_model("anthropic/claude-3-5-sonnet-20241022") == "ANTHROPIC_API_KEY"

    def test_openai_prefix(self):
        assert aider_mod._env_var_for_model("openai/gpt-4o") == "OPENAI_API_KEY"

    def test_deepseek_prefix(self):
        assert aider_mod._env_var_for_model("deepseek/deepseek-chat") == "DEEPSEEK_API_KEY"

    def test_gemini_prefix(self):
        assert aider_mod._env_var_for_model("gemini/gemini-2.0-flash") == "GEMINI_API_KEY"

    # Bare slugs — tenant pastes the model name without the LiteLLM
    # prefix. Each vendor token must route to the right provider env
    # var, NOT silently fall through to OPENAI_API_KEY.
    def test_bare_anthropic_claude_slug(self):
        assert aider_mod._env_var_for_model("claude-3-5-sonnet-20241022") == "ANTHROPIC_API_KEY"

    def test_bare_openai_gpt_slug(self):
        assert aider_mod._env_var_for_model("gpt-4o") == "OPENAI_API_KEY"

    def test_bare_openai_o3_slug(self):
        assert aider_mod._env_var_for_model("o3-mini") == "OPENAI_API_KEY"

    def test_bare_deepseek_slug(self):
        assert aider_mod._env_var_for_model("deepseek-chat") == "DEEPSEEK_API_KEY"

    def test_bare_gemini_slug(self):
        assert aider_mod._env_var_for_model("gemini-2.0-flash-exp") == "GEMINI_API_KEY"

    def test_bare_kimi_slug(self):
        assert aider_mod._env_var_for_model("kimi-k2-0905-preview") == "MOONSHOT_API_KEY"

    def test_bare_glm_slug(self):
        assert aider_mod._env_var_for_model("glm-4.6") == "ZHIPU_API_KEY"

    def test_bare_mistral_slug(self):
        assert aider_mod._env_var_for_model("mistral-large-latest") == "MISTRAL_API_KEY"

    def test_bare_command_slug(self):
        assert aider_mod._env_var_for_model("command-r-plus") == "COHERE_API_KEY"

    def test_bare_groq_llama_instruct_slug(self):
        assert aider_mod._env_var_for_model("llama-3.3-70b-instruct") == "GROQ_API_KEY"

    # Unknown / unrecognised slugs MUST return ``None`` — silent fall-
    # through to OPENAI_API_KEY masks misconfigured tenants as opaque
    # auth failures further downstream.
    def test_unknown_bare_slug_returns_none(self):
        assert aider_mod._env_var_for_model("random-unknown-model") is None

    def test_unknown_prefixed_slug_returns_none(self):
        assert aider_mod._env_var_for_model("vendor-x/model-y") is None

    def test_empty_model_returns_none(self):
        assert aider_mod._env_var_for_model("") is None

    # Dropped from the provider table (multi-credential providers) —
    # they now return ``None`` instead of pretending one env var is
    # enough.
    def test_dropped_bedrock_returns_none(self):
        assert aider_mod._env_var_for_model("bedrock/anthropic.claude-3-sonnet-20240229-v1:0") is None

    def test_dropped_azure_returns_none(self):
        assert aider_mod._env_var_for_model("azure/gpt-4o") is None

    def test_dropped_ollama_returns_none(self):
        assert aider_mod._env_var_for_model("ollama/qwen2.5-coder") is None


# ── executor tests ───────────────────────────────────────────────────────


class TestExecuteAiderChat:
    @pytest.fixture(autouse=True)
    def _clear_keys(self, monkeypatch):
        for var in (
            "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY",
            "GEMINI_API_KEY", "AIDER_MODEL_API_KEY",
        ):
            monkeypatch.delenv(var, raising=False)

    def test_no_credentials_returns_not_connected(self, monkeypatch, tmp_path, wf):
        """Vault returns empty dict + no env → friendly not-connected
        error so the resolver classifies as ``missing_credential`` and
        chain-walks past Aider without a 10-min cooldown."""
        monkeypatch.setattr(wf, "_fetch_integration_credentials", lambda i, t: {})
        out = aider_mod.execute_aider_chat(
            _make_input(),
            session_dir=str(tmp_path),
        )
        assert out.success is False
        assert "not connected" in out.error.lower()
        assert "aider" in out.error.lower()

    def test_creds_fetch_exception_returns_friendly_error(self, monkeypatch, tmp_path, wf):
        def boom(i, t):
            raise RuntimeError("network failed")
        monkeypatch.setattr(wf, "_fetch_integration_credentials", boom)
        out = aider_mod.execute_aider_chat(
            _make_input(),
            session_dir=str(tmp_path),
        )
        assert out.success is False
        assert "not connected" in out.error.lower()

    def test_happy_path_vault_anthropic_key(self, monkeypatch, tmp_path, wf):
        """Vault returns model=anthropic/claude-* + api_key. Executor
        must set ANTHROPIC_API_KEY (LiteLLM convention) on the
        subprocess env."""
        monkeypatch.setattr(
            wf, "_fetch_integration_credentials",
            lambda i, t: {
                "model": "anthropic/claude-3-5-sonnet-20241022",
                "api_key": "sk-ant-FAKE",
            },
        )
        captured: dict = {}

        def fake_run(cmd, **kw):
            captured["cmd"] = cmd
            captured["env"] = kw.get("env", {})
            return _completed(
                returncode=0,
                stdout=(
                    "Aider v0.65.0\n"
                    "Model: claude-3-5-sonnet-20241022 with diff edit format\n"
                    "Repo-map: disabled\n"
                    "Here is the answer you wanted.\n"
                    "\n"
                    "Tokens: 1.2k sent, 350 received.\n"
                    "Cost: $0.01 message, $0.01 session.\n"
                ),
                stderr="",
            )

        monkeypatch.setattr(cli_runtime, "run_cli_with_heartbeat", fake_run)

        out = aider_mod.execute_aider_chat(
            _make_input(),
            session_dir=str(tmp_path),
        )
        assert out.success is True
        assert "Here is the answer" in out.response_text
        # Footer + banner must be stripped from the response.
        assert "Tokens:" not in out.response_text
        assert "Aider v0.65.0" not in out.response_text
        assert out.metadata["platform"] == "aider"
        assert out.metadata["model"] == "anthropic/claude-3-5-sonnet-20241022"
        assert out.metadata.get("output_tokens") == 350

        # Command shape — note --message + --no-stream + --yes-always.
        assert captured["cmd"][0] == "aider"
        assert "--model" in captured["cmd"]
        assert "anthropic/claude-3-5-sonnet-20241022" in captured["cmd"]
        assert "--yes-always" in captured["cmd"]
        assert "--no-stream" in captured["cmd"]
        assert "--message" in captured["cmd"]
        # Env: ANTHROPIC_API_KEY is the LiteLLM-flavoured key for the
        # anthropic/* prefix. Generic alias also set for diagnostics.
        assert captured["env"].get("ANTHROPIC_API_KEY") == "sk-ant-FAKE"
        assert captured["env"].get("AIDER_MODEL_API_KEY") == "sk-ant-FAKE"
        # Telemetry and gitignore are disabled via documented CLI flags,
        # NOT env vars (Aider does not honour AIDER_ANALYTICS /
        # AIDER_GITIGNORE).
        assert "--analytics-disable" in captured["cmd"]
        assert "--no-gitignore" in captured["cmd"]

    def test_happy_path_env_fallback_anthropic(self, monkeypatch, tmp_path, wf):
        """No vault credentials, but ANTHROPIC_API_KEY is in the
        process env. The shared-operator-key path resolves."""
        monkeypatch.setattr(wf, "_fetch_integration_credentials", lambda i, t: {})
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env-FAKE")

        captured: dict = {}

        def fake_run(cmd, **kw):
            captured["env"] = kw.get("env", {})
            return _completed(returncode=0, stdout="ok", stderr="")

        monkeypatch.setattr(cli_runtime, "run_cli_with_heartbeat", fake_run)

        out = aider_mod.execute_aider_chat(
            _make_input(),
            session_dir=str(tmp_path),
        )
        assert out.success is True
        # Default model is anthropic/* so the executor reads ANTHROPIC_API_KEY.
        assert captured["env"].get("ANTHROPIC_API_KEY") == "sk-ant-env-FAKE"

    def test_deepseek_model_uses_deepseek_env_var(self, monkeypatch, tmp_path, wf):
        """A vault entry with model=deepseek/* MUST inject the key
        under DEEPSEEK_API_KEY, not ANTHROPIC_API_KEY."""
        monkeypatch.setattr(
            wf, "_fetch_integration_credentials",
            lambda i, t: {"model": "deepseek/deepseek-chat", "api_key": "sk-ds-FAKE"},
        )
        captured: dict = {}

        def fake_run(cmd, **kw):
            captured["env"] = kw.get("env", {})
            return _completed(returncode=0, stdout="hi", stderr="")

        monkeypatch.setattr(cli_runtime, "run_cli_with_heartbeat", fake_run)

        out = aider_mod.execute_aider_chat(
            _make_input(),
            session_dir=str(tmp_path),
        )
        assert out.success is True
        assert captured["env"].get("DEEPSEEK_API_KEY") == "sk-ds-FAKE"
        # ANTHROPIC_API_KEY must NOT be polluted with the deepseek key.
        assert captured["env"].get("ANTHROPIC_API_KEY") != "sk-ds-FAKE"

    def test_unknown_model_slug_returns_clear_error(self, monkeypatch, tmp_path, wf):
        """Bare-slug models that don't match any provider rule must
        produce a clear "Unknown model — use provider/model format"
        error instead of silently binding to OpenAI and surfacing a
        confusing auth error several seconds later."""
        monkeypatch.setattr(
            wf, "_fetch_integration_credentials",
            lambda i, t: {"model": "random-unknown-model", "api_key": "sk-FAKE"},
        )
        # If we reach run_cli_with_heartbeat we've failed the contract.
        def boom(*a, **kw):
            raise AssertionError("subprocess should not be spawned for unknown models")
        monkeypatch.setattr(cli_runtime, "run_cli_with_heartbeat", boom)

        out = aider_mod.execute_aider_chat(
            _make_input(),
            session_dir=str(tmp_path),
        )
        assert out.success is False
        assert "Unknown model" in out.error
        assert "provider/model" in out.error

    def test_stderr_redaction_strips_api_key_prefix(self, monkeypatch, tmp_path, wf):
        """LiteLLM error messages routinely echo the bad API key back.
        The redactor must scrub ``sk-ant-…`` / ``sk-…`` substrings
        before the snippet lands in ``ChatCliResult.error`` — otherwise
        the secret leaks into the UI and downstream logs."""
        monkeypatch.setattr(
            wf, "_fetch_integration_credentials",
            lambda i, t: {"model": "anthropic/claude-3-5-sonnet-20241022", "api_key": "sk-ant-FAKE"},
        )
        monkeypatch.setattr(
            cli_runtime, "run_cli_with_heartbeat",
            lambda cmd, **kw: _completed(
                returncode=1,
                stdout="",
                stderr="Invalid API key: sk-ant-abc123def456ghi789-this-is-secret",
            ),
        )
        out = aider_mod.execute_aider_chat(
            _make_input(),
            session_dir=str(tmp_path),
        )
        assert out.success is False
        # The secret prefix MUST NOT appear in the surfaced error.
        assert "sk-ant-abc123def456" not in out.error
        assert "sk-ant-abc" not in out.error

    def test_non_zero_exit_returns_friendly_error(self, monkeypatch, tmp_path, wf):
        monkeypatch.setattr(
            wf, "_fetch_integration_credentials",
            lambda i, t: {"model": "anthropic/claude-3-5-sonnet-20241022", "api_key": "sk-ant-FAKE"},
        )
        monkeypatch.setattr(
            cli_runtime, "run_cli_with_heartbeat",
            lambda cmd, **kw: _completed(
                returncode=2,
                stdout="",
                stderr="aider: error: unrecognised model 'anthropic/typo-here'",
            ),
        )
        out = aider_mod.execute_aider_chat(
            _make_input(),
            session_dir=str(tmp_path),
        )
        assert out.success is False
        assert "exit 2" in out.error
        assert out.metadata["platform"] == "aider"

    def test_empty_stdout_returns_no_output_error(self, monkeypatch, tmp_path, wf):
        monkeypatch.setattr(
            wf, "_fetch_integration_credentials",
            lambda i, t: {"model": "anthropic/claude-3-5-sonnet-20241022", "api_key": "sk-ant-FAKE"},
        )
        monkeypatch.setattr(
            cli_runtime, "run_cli_with_heartbeat",
            lambda cmd, **kw: _completed(returncode=0, stdout="", stderr=""),
        )
        out = aider_mod.execute_aider_chat(
            _make_input(),
            session_dir=str(tmp_path),
        )
        assert out.success is False
        assert "no output" in out.error.lower()

    def test_per_turn_model_override_wins(self, monkeypatch, tmp_path, wf):
        """``ChatCliInput.model`` is the per-turn override — should
        beat the vault-stored model on the command line."""
        monkeypatch.setattr(
            wf, "_fetch_integration_credentials",
            lambda i, t: {"model": "anthropic/claude-3-5-sonnet-20241022", "api_key": "sk-ant-FAKE"},
        )
        captured: dict = {}

        def fake_run(cmd, **kw):
            captured["cmd"] = cmd
            return _completed(returncode=0, stdout="ok", stderr="")

        monkeypatch.setattr(cli_runtime, "run_cli_with_heartbeat", fake_run)

        out = aider_mod.execute_aider_chat(
            _make_input(model="anthropic/claude-haiku-4-5-20251001"),
            session_dir=str(tmp_path),
        )
        assert out.success is True
        # The override slug appears on the command line.
        assert "anthropic/claude-haiku-4-5-20251001" in captured["cmd"]
        # The vault default does NOT.
        assert "anthropic/claude-3-5-sonnet-20241022" not in captured["cmd"]

    def test_instruction_md_prepended_to_message(self, monkeypatch, tmp_path, wf):
        """When ``instruction_md_content`` is set, the executor must
        prepend it to the user message — same shape as the other
        executors."""
        monkeypatch.setattr(
            wf, "_fetch_integration_credentials",
            lambda i, t: {"model": "anthropic/claude-3-5-sonnet-20241022", "api_key": "sk-ant-FAKE"},
        )
        captured: dict = {}

        def fake_run(cmd, **kw):
            captured["cmd"] = cmd
            return _completed(returncode=0, stdout="ok", stderr="")

        monkeypatch.setattr(cli_runtime, "run_cli_with_heartbeat", fake_run)

        out = aider_mod.execute_aider_chat(
            _make_input(message="do the thing", instruction_md_content="You are a helpful coder."),
            session_dir=str(tmp_path),
        )
        assert out.success is True
        # Find the --message arg; it follows the --message flag in cmd.
        idx = captured["cmd"].index("--message")
        composed = captured["cmd"][idx + 1]
        assert "You are a helpful coder." in composed
        assert "do the thing" in composed


# ── stdout extraction unit tests ─────────────────────────────────────────


class TestExtractResponse:
    def test_strips_banner_and_footer(self):
        stdout = (
            "Aider v0.65.0\n"
            "Model: claude with diff edit format\n"
            "Repo-map: using 1024 tokens\n"
            "Here is the answer you wanted.\n"
            "More words here.\n"
            "Tokens: 1.2k sent, 350 received.\n"
            "Cost: $0.01 message, $0.01 session.\n"
        )
        text, meta = aider_mod._extract_response(stdout)
        assert "Here is the answer" in text
        assert "More words" in text
        assert "Tokens:" not in text
        assert "Aider v0.65.0" not in text
        assert meta.get("output_tokens") == 350

    def test_strips_ansi_codes(self):
        stdout = (
            "\x1b[33mAider v0.65.0\x1b[0m\n"
            "Repo-map: x\n"
            "\x1b[36mhello world\x1b[0m\n"
            "Tokens: 100 sent, 50 received.\n"
        )
        text, _ = aider_mod._extract_response(stdout)
        assert "hello world" in text
        assert "\x1b[" not in text

    def test_no_footer_returns_body(self):
        stdout = "Repo-map: x\nplain response\n"
        text, meta = aider_mod._extract_response(stdout)
        assert "plain response" in text
        assert meta == {}

    def test_empty_returns_empty(self):
        text, meta = aider_mod._extract_response("")
        assert text == ""
        assert meta == {}

    def test_strips_banner_without_repo_map_or_rule(self):
        """With ``--no-pretty`` Aider emits no ``─`` rule; if cwd isn't
        a git repo there's no ``Repo-map:`` line either. The parser
        must still strip the banner cleanly. Regression for the prior
        delimiter-scan-only implementation that left the banner intact
        whenever neither marker appeared."""
        stdout = (
            "Aider v0.78.0\n"
            "Model: gpt-4o with diff edit format\n"
            "Hello, this is the body.\n"
            "Tokens: 100 sent, 50 received.\n"
        )
        text, meta = aider_mod._extract_response(stdout)
        assert text == "Hello, this is the body."
        assert "Aider v0.78.0" not in text
        assert "Model:" not in text
        assert meta.get("output_tokens") == 50

    def test_empty_body_returns_empty_not_banner(self):
        """When the body slice is empty (banner-only output), the
        parser must return ``""`` — NOT fall back to the banner. The
        executor's ``if not response_text:`` branch then surfaces a
        clean "no output" error so the resolver can chain."""
        stdout = "Aider v0.65.0\nTokens: 100 sent, 50 received.\n"
        text, _ = aider_mod._extract_response(stdout)
        assert text == ""
