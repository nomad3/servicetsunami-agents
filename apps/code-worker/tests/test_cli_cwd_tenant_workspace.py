"""Tests for tenant-workspace cwd scoping (task #259).

Every CLI subprocess we spawn (claude_code, codex, gemini_cli,
copilot_cli, opencode CLI fallback) must run with its working
directory rooted inside the tenant's persistent workspace projects
directory — not the worker-private ``/workspace`` scratch volume or
``/app``. That's how files the agent writes via Write/Edit tools end
up in the shared ``workspaces`` named volume and surface in the
dashboard's FileTreePanel (see ``apps/api/app/api/v1/workspace.py``).

These tests mock the subprocess wrapper and assert that the ``cwd``
kwarg passed through contains the tenant_id and lives under the
WORKSPACES_ROOT we override into pytest tmp_path. They also assert
the ``WORKSPACE`` env var is set so child CLIs that read it (e.g.
agent prompts that reference ``$WORKSPACE/foo``) see the same path.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

import cli_executors.opencode
import cli_runtime
import workflows as wf


# ── shared helpers ───────────────────────────────────────────────────────

# Canonical UUID stand-ins (review I1 added a UUID guard inside
# ``tenant_workspace_dir`` — non-UUID strings now short-circuit to the
# fallback). Each is a stable, recognisably-named UUIDv4 so failures
# still print a human-readable hint about which test owns the value.
TENANT_UUID = "11111111-1111-4111-8111-111111111111"
TENANT_X = "22222222-2222-4222-8222-222222222222"
TENANT_Y = "33333333-3333-4333-8333-333333333333"
TENANT_Z = "44444444-4444-4444-8444-444444444444"
TENANT_CLAUDE = "55555555-5555-4555-8555-555555555555"
TENANT_CODEX = "66666666-6666-4666-8666-666666666666"
TENANT_GEMINI = "77777777-7777-4777-8777-777777777777"
TENANT_COPILOT = "88888888-8888-4888-8888-888888888888"
TENANT_OPENCODE = "99999999-9999-4999-8999-999999999999"
TENANT_VIS = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


def _make_input(**overrides):
    base = dict(
        platform="claude_code",
        message="hello",
        tenant_id=TENANT_UUID,
        instruction_md_content="",
        mcp_config="",
        image_b64="",
        image_mime="",
        session_id="",
        model="",
        allowed_tools="",
        chat_session_id="sess-1234567890",
    )
    base.update(overrides)
    return wf.ChatCliInput(**base)


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(
        args=["x"], returncode=returncode, stdout=stdout, stderr=stderr,
    )


@pytest.fixture
def fake_workspaces_root(tmp_path, monkeypatch):
    """Redirect WORKSPACES_ROOT to a pytest tmp dir.

    This is the *parent* of <tenant_id>/projects — must exist so
    ``resolve_cli_cwd`` short-circuits onto the new path rather than
    falling back to ``session_dir``.
    """
    root = tmp_path / "workspaces"
    root.mkdir()
    monkeypatch.setattr(cli_runtime, "WORKSPACES_ROOT", Path(root))
    return root


# ── tenant_workspace_dir / resolve_cli_cwd unit tests ───────────────────

class TestResolveCliCwd:
    def test_creates_tenant_projects_session_subdir(self, fake_workspaces_root):
        out = cli_runtime.tenant_workspace_dir(TENANT_X, "sess-abc12345")
        assert out.is_dir()
        # session_id truncated to first 8 chars in the directory name.
        assert out.name == "session-sess-abc"
        assert out.parent.name == "projects"
        assert out.parent.parent.name == TENANT_X

    def test_session_id_none_uses_shared_projects_root(self, fake_workspaces_root):
        out = cli_runtime.tenant_workspace_dir(TENANT_Y, None)
        assert out.name == "projects"
        assert out.parent.name == TENANT_Y

    def test_resolve_falls_back_when_root_missing(self, monkeypatch, tmp_path):
        # Point WORKSPACES_ROOT at a path that doesn't exist.
        monkeypatch.setattr(cli_runtime, "WORKSPACES_ROOT", Path(tmp_path / "nope"))
        task = _make_input(tenant_id=TENANT_UUID)
        out = cli_runtime.resolve_cli_cwd(task, fallback="/fallback/here")
        assert out == "/fallback/here"

    def test_resolve_falls_back_when_tenant_id_empty(self, fake_workspaces_root):
        task = _make_input(tenant_id="")
        out = cli_runtime.resolve_cli_cwd(task, fallback="/fallback")
        assert out == "/fallback"

    def test_resolve_returns_tenant_projects_session_path(self, fake_workspaces_root):
        task = _make_input(tenant_id=TENANT_Z, chat_session_id="abcdef1234")
        out = cli_runtime.resolve_cli_cwd(task, fallback="/should-not-use")
        # Path lives under the fake root + tenant + projects + session-XXX.
        assert str(fake_workspaces_root) in out
        assert TENANT_Z in out
        assert "projects" in out
        assert "session-abcdef12" in out

    # ── review I1: UUID guard ───────────────────────────────────────
    def test_tenant_workspace_dir_rejects_non_uuid(self, fake_workspaces_root):
        """Non-UUID tenant_id must raise ValueError before mkdir runs."""
        with pytest.raises(ValueError):
            cli_runtime.tenant_workspace_dir("../escape", "sess-1")
        # And the escape sibling must NOT exist on disk.
        escape = fake_workspaces_root.parent / "escape"
        assert not escape.exists()

    def test_resolve_falls_back_on_path_traversal_tenant_id(
        self, fake_workspaces_root,
    ):
        """``tenant_id='../escape'`` must collapse to the caller-provided
        fallback, never to a path outside WORKSPACES_ROOT."""
        task = _make_input(tenant_id="../escape")
        out = cli_runtime.resolve_cli_cwd(task, fallback="/safe/fallback")
        assert out == "/safe/fallback"

    def test_resolve_falls_back_on_garbage_tenant_id(self, fake_workspaces_root):
        task = _make_input(tenant_id="not-a-uuid-at-all")
        out = cli_runtime.resolve_cli_cwd(task, fallback="/safe")
        assert out == "/safe"


# ── per-executor cwd assertions ──────────────────────────────────────────

class TestClaudeChatCwdScoped:
    def test_subprocess_cwd_and_workspace_env_point_at_tenant_dir(
        self, monkeypatch, tmp_path, fake_workspaces_root,
    ):
        # Stub credential fetch + write path.
        monkeypatch.setattr(
            wf, "_fetch_claude_credential",
            lambda tid: ("token-xyz", "oauth"),
        )
        session_dir = tmp_path / "session"
        session_dir.mkdir()

        captured: dict = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            # Mimic happy-path single-line JSON output.
            return _completed(
                returncode=0,
                stdout='{"result": "hello", "model": "claude"}',
            )

        monkeypatch.setattr(cli_runtime, "run_cli_with_heartbeat", fake_run)

        task = _make_input(tenant_id=TENANT_CLAUDE, chat_session_id="ses12345abc")
        out = wf._execute_claude_chat(task, session_dir=str(session_dir))

        assert out.success is True, out.error
        cwd = captured["kwargs"]["cwd"]
        assert TENANT_CLAUDE in cwd
        assert "projects" in cwd
        assert "session-ses12345" in cwd
        env = captured["kwargs"]["env"]
        assert env["WORKSPACE"] == cwd

    def test_falls_back_when_workspaces_root_absent(
        self, monkeypatch, tmp_path,
    ):
        """Without the volume mount, must run in the legacy fallback dir."""
        monkeypatch.setattr(cli_runtime, "WORKSPACES_ROOT", Path(tmp_path / "missing"))
        monkeypatch.setattr(
            wf, "_fetch_claude_credential",
            lambda tid: ("token", "oauth"),
        )
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["kwargs"] = kwargs
            return _completed(returncode=0, stdout='{"result": "ok"}')

        monkeypatch.setattr(cli_runtime, "run_cli_with_heartbeat", fake_run)

        # Ensure WORKSPACE legacy path doesn't exist either so we
        # exercise the session_dir branch deterministically.
        monkeypatch.setattr(wf.os.path, "isdir", lambda p: p == str(session_dir))

        task = _make_input(tenant_id=TENANT_CLAUDE)
        wf._execute_claude_chat(task, session_dir=str(session_dir))
        cwd = captured["kwargs"]["cwd"]
        # Fallback path = session_dir (legacy /workspace not present in test).
        assert cwd == str(session_dir)


class TestCodexChatCwdScoped:
    def test_subprocess_cwd_set_to_tenant_workspace(
        self, monkeypatch, tmp_path, fake_workspaces_root,
    ):
        monkeypatch.setattr(
            wf, "_fetch_integration_credentials",
            lambda i, t: {"auth_json": {"OPENAI_API_KEY": "sk-fake"}},
        )
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        (session_dir / "codex-last-message.txt").write_text("answer")

        captured: dict = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            return _completed(returncode=0)

        monkeypatch.setattr(cli_runtime, "run_cli_with_heartbeat", fake_run)

        task = _make_input(
            platform="codex", tenant_id=TENANT_CODEX, chat_session_id="sess-c0dec0de",
        )
        wf._execute_codex_chat(task, session_dir=str(session_dir), image_path="")

        cwd = captured["kwargs"]["cwd"]
        assert TENANT_CODEX in cwd
        assert "projects" in cwd
        # ``-C <cli_cwd>`` also passed to codex so its project-root logic
        # picks up the tenant dir (otherwise it'd default to legacy WORKSPACE).
        assert "-C" in captured["cmd"]
        c_index = captured["cmd"].index("-C")
        assert captured["cmd"][c_index + 1] == cwd
        # WORKSPACE env var also propagated.
        assert captured["kwargs"]["env"]["WORKSPACE"] == cwd
        # CODEX_HOME still set (HOME-relative auth lookup unaffected).
        assert "CODEX_HOME" in captured["kwargs"]["env"]
        # Review B1: ``--skip-git-repo-check`` MUST be present whenever
        # we're routed to the tenant workspace (which is a freshly
        # ``mkdir``'d non-git dir). Without it codex refuses to run.
        assert "--skip-git-repo-check" in captured["cmd"]


class TestGeminiChatCwdScoped:
    def test_subprocess_cwd_set_to_tenant_workspace(
        self, monkeypatch, tmp_path, fake_workspaces_root,
    ):
        monkeypatch.setenv("GEMINI_API_KEY", "AIza-FAKE")
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        monkeypatch.setattr(
            wf, "_prepare_gemini_home_apikey",
            lambda sd, mcp: str(session_dir),
        )

        captured: dict = {}

        def fake_run(cmd, **kwargs):
            captured["kwargs"] = kwargs
            return _completed(
                returncode=0,
                stdout='{"result": "Hi"}',
            )

        monkeypatch.setattr(cli_runtime, "run_cli_with_heartbeat", fake_run)

        task = _make_input(
            platform="gemini_cli",
            tenant_id=TENANT_GEMINI,
            chat_session_id="sess-gem11111",
        )
        wf._execute_gemini_chat(task, session_dir=str(session_dir), image_path="")

        cwd = captured["kwargs"]["cwd"]
        assert TENANT_GEMINI in cwd
        assert "projects" in cwd
        # WORKSPACE env propagated.
        assert captured["kwargs"]["env"]["WORKSPACE"] == cwd
        # HOME points at the per-tenant ``home/`` dir on the persistent
        # workspaces volume (task #267 Phase 1). The CLI keeps reading
        # ~/.gemini/oauth_creds.json — just from the volume now instead
        # of the writable layer.
        home_env = captured["kwargs"]["env"]["HOME"]
        assert TENANT_GEMINI in home_env
        assert home_env.endswith("/home")


class TestCopilotChatCwdScoped:
    def test_subprocess_cwd_set_to_tenant_workspace(
        self, monkeypatch, tmp_path, fake_workspaces_root,
    ):
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_fake")
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        monkeypatch.setattr(wf, "_prepare_copilot_home", lambda sd, mcp: str(session_dir))

        captured: dict = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            return _completed(
                returncode=0,
                stdout=json.dumps({
                    "type": "assistant.message",
                    "data": {"content": "ok", "outputTokens": 1},
                }),
            )

        monkeypatch.setattr(cli_runtime, "run_cli_with_heartbeat", fake_run)

        task = _make_input(
            platform="copilot_cli",
            tenant_id=TENANT_COPILOT,
            chat_session_id="sess-cop11111",
        )
        wf._execute_copilot_chat(task, str(session_dir))

        cwd = captured["kwargs"]["cwd"]
        assert TENANT_COPILOT in cwd
        assert "projects" in cwd
        assert captured["kwargs"]["env"]["WORKSPACE"] == cwd
        # --add-dir <cli_cwd> appended so Copilot's path-allowlist
        # treats the tenant workspace as in-scope for tool writes.
        assert "--add-dir" in captured["cmd"]
        # COPILOT_HOME and COPILOT_GITHUB_TOKEN untouched.
        assert "COPILOT_HOME" in captured["kwargs"]["env"]
        assert captured["kwargs"]["env"]["COPILOT_GITHUB_TOKEN"] == "ghp_fake"


class TestOpencodeChatCliCwdScoped:
    """The CLI fallback (`opencode run`) is the only opencode path that
    actually spawns a subprocess from the chat executor — the primary
    happy path is HTTP to the in-process server. We assert the fallback
    still scopes its cwd to the tenant workspace."""

    def test_subprocess_run_called_with_tenant_cwd(
        self, monkeypatch, tmp_path, fake_workspaces_root,
    ):
        session_dir = tmp_path / "session"
        session_dir.mkdir()

        captured: dict = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            return _completed(
                returncode=0,
                stdout='{"response": "ok"}',
            )

        # subprocess.run is the call site inside _execute_opencode_chat_cli.
        monkeypatch.setattr(
            cli_executors.opencode.subprocess, "run", fake_run,
        )

        task = _make_input(
            platform="opencode",
            tenant_id=TENANT_OPENCODE,
            chat_session_id="sess-oc1234567",
        )
        out = cli_executors.opencode._execute_opencode_chat_cli(
            task, str(session_dir),
        )
        assert out.success is True
        cwd = captured["kwargs"]["cwd"]
        assert TENANT_OPENCODE in cwd
        assert "projects" in cwd
        assert captured["kwargs"]["env"]["WORKSPACE"] == cwd


# ── integration-style assertion: file written in cwd is readable by api ──

class TestWrittenFileVisibleUnderWorkspacesRoot:
    """The whole point of task #259: a file the CLI writes into its cwd
    must live under WORKSPACES_ROOT/<tenant_id>/projects/... so that
    ``GET /api/v1/workspace/tree`` (which reads from the same root) can
    list it.

    We don't actually call the api — we just synthesize a write that a
    real CLI would do in the resolved cwd, and assert the file path is
    inside the api's expected ``<root>/<tenant>/projects/`` subtree.
    """

    def test_synthetic_write_lands_under_tenant_projects(
        self, fake_workspaces_root,
    ):
        task = _make_input(tenant_id=TENANT_VIS, chat_session_id="ses-AAA11111")
        cwd = cli_runtime.resolve_cli_cwd(task, fallback="/nope")
        # Simulate a CLI tool writing a plan file inside cwd.
        plan = Path(cwd) / "plan.md"
        plan.write_text("# plan\n")
        # The dashboard's api looks for files under
        #   <WORKSPACES_ROOT> / <tenant_id> / projects / ...
        # so we assert the relative-from-root path matches that shape.
        rel = plan.relative_to(fake_workspaces_root)
        parts = rel.parts
        assert parts[0] == TENANT_VIS
        assert parts[1] == "projects"
        assert parts[-1] == "plan.md"
