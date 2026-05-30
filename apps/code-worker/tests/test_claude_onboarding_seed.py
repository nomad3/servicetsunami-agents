"""Tests for ``_ensure_claude_onboarding`` — the runner-side seed that
pre-completes Claude Code's first-run onboarding wizard for the interactive
TTY HOME, so it uses the stored subscription credential instead of starting a
fresh OAuth login the headless PTY can't finish.
"""
from __future__ import annotations

import json

from cli_executors.claude import _ensure_claude_onboarding


def _load(home) -> dict:
    return json.loads((home / ".claude.json").read_text())


def test_seeds_onboarding_and_trust_on_empty_home(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    cwd = "/var/agentprovision/workspaces/t/projects/repo"

    _ensure_claude_onboarding(str(home), cwd)

    data = _load(home)
    assert data["hasCompletedOnboarding"] is True
    assert data["projects"][cwd]["hasTrustDialogAccepted"] is True
    assert data["projects"][cwd]["hasCompletedProjectOnboarding"] is True


def test_preserves_existing_unrelated_keys(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".claude.json").write_text(
        json.dumps({"userID": "abc", "projects": {"/other": {"allowedTools": []}}})
    )

    _ensure_claude_onboarding(str(home), "/new/cwd")

    data = _load(home)
    assert data["userID"] == "abc"
    assert data["projects"]["/other"]["allowedTools"] == []
    assert data["hasCompletedOnboarding"] is True
    assert data["projects"]["/new/cwd"]["hasTrustDialogAccepted"] is True


def test_malformed_json_is_replaced(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".claude.json").write_text("{ not valid json")

    _ensure_claude_onboarding(str(home), None)

    data = _load(home)
    assert data["hasCompletedOnboarding"] is True


def test_idempotent_when_already_seeded(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    cwd = "/cwd"
    _ensure_claude_onboarding(str(home), cwd)
    first = (home / ".claude.json").read_text()

    # Second call must not change already-correct content.
    _ensure_claude_onboarding(str(home), cwd)
    assert (home / ".claude.json").read_text() == first


def test_missing_home_is_noop(tmp_path):
    # Empty HOME string must not raise and must not create stray files.
    _ensure_claude_onboarding("", "/cwd")
    assert list(tmp_path.iterdir()) == []


def test_no_trusted_cwd_still_seeds_onboarding(tmp_path):
    home = tmp_path / "home"
    home.mkdir()

    _ensure_claude_onboarding(str(home), None)

    data = _load(home)
    assert data["hasCompletedOnboarding"] is True
    assert "projects" not in data
