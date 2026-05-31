"""Tests for ``_ensure_claude_onboarding`` — the runner-side seed that
pre-completes Claude Code's first-run onboarding wizard for the interactive
TTY HOME, so it uses the stored subscription credential instead of starting a
fresh OAuth login the headless PTY can't finish.
"""
from __future__ import annotations

import json
import os
import threading

import cli_executors.claude as claude_mod
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
    # Root-cause fix: the trust seed keys on the RESOLVED cwd (Claude resolves
    # symlinks before the projects[...] trust lookup). ``/var`` is a symlink to
    # ``/private/var`` on macOS, so the stored key is the realpath, not the
    # literal — proving we no longer key on the un-resolved path.
    key = os.path.realpath(cwd)
    assert data["projects"][key]["hasTrustDialogAccepted"] is True
    assert data["projects"][key]["hasCompletedProjectOnboarding"] is True
    # And the seen-count is forced ≥1 so project-onboarding doesn't re-arm.
    assert data["projects"][key]["projectOnboardingSeenCount"] >= 1


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


def test_malformed_json_is_preserved_not_clobbered(tmp_path):
    # An existing but unparseable config must NOT be overwritten — it could be
    # real state or a transient partial write.
    home = tmp_path / "home"
    home.mkdir()
    bad = "{ not valid json"
    (home / ".claude.json").write_text(bad)

    _ensure_claude_onboarding(str(home), "/cwd")

    assert (home / ".claude.json").read_text() == bad


def test_empty_file_is_seeded(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".claude.json").write_text("")

    _ensure_claude_onboarding(str(home), None)

    assert _load(home)["hasCompletedOnboarding"] is True


def test_non_dict_toplevel_is_reset(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".claude.json").write_text("[1, 2, 3]")

    _ensure_claude_onboarding(str(home), "/cwd")

    data = _load(home)
    assert data["hasCompletedOnboarding"] is True
    assert data["projects"]["/cwd"]["hasTrustDialogAccepted"] is True


def test_non_dict_projects_is_reset(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".claude.json").write_text(json.dumps({"projects": [1, 2]}))

    _ensure_claude_onboarding(str(home), "/cwd")

    data = _load(home)
    assert data["projects"]["/cwd"]["hasTrustDialogAccepted"] is True
    assert data["projects"]["/cwd"]["hasCompletedProjectOnboarding"] is True


def test_idempotent_when_already_seeded(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    cwd = "/cwd"
    _ensure_claude_onboarding(str(home), cwd)
    first = (home / ".claude.json").read_text()

    _ensure_claude_onboarding(str(home), cwd)
    assert (home / ".claude.json").read_text() == first


def test_missing_home_is_noop(tmp_path):
    _ensure_claude_onboarding("", "/cwd")
    assert list(tmp_path.iterdir()) == []


def test_no_trusted_cwd_still_seeds_onboarding(tmp_path):
    home = tmp_path / "home"
    home.mkdir()

    _ensure_claude_onboarding(str(home), None)

    data = _load(home)
    assert data["hasCompletedOnboarding"] is True
    assert "projects" not in data


def test_file_is_created_0600(tmp_path):
    # .claude.json is secret-grade (hook_templates.py SR-4) — the seed must
    # never widen it to a world-readable mode.
    home = tmp_path / "home"
    home.mkdir()

    _ensure_claude_onboarding(str(home), "/cwd")

    mode = (home / ".claude.json").stat().st_mode & 0o777
    assert mode == 0o600, oct(mode)


def test_no_temp_file_left_behind(tmp_path):
    home = tmp_path / "home"
    home.mkdir()

    _ensure_claude_onboarding(str(home), "/cwd")

    leftovers = [p.name for p in home.iterdir() if p.name != ".claude.json"]
    assert leftovers == [], leftovers


def test_write_failure_is_swallowed_and_preserves_file(tmp_path, monkeypatch):
    # The "never raises" contract: a write failure (e.g. read-only FS) must not
    # propagate and must not damage an existing config.
    home = tmp_path / "home"
    home.mkdir()
    existing = json.dumps({"userID": "keep"})
    (home / ".claude.json").write_text(existing)

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(claude_mod.tempfile, "mkstemp", boom)

    _ensure_claude_onboarding(str(home), "/cwd")  # must not raise

    assert (home / ".claude.json").read_text() == existing


def test_concurrent_calls_do_not_crash_or_corrupt(tmp_path):
    # mkstemp + os.replace must keep the file atomically valid and leave no
    # temp litter even when many interactive turns race on the same HOME.
    home = tmp_path / "home"
    home.mkdir()
    errors: list[BaseException] = []

    def worker(i: int) -> None:
        try:
            _ensure_claude_onboarding(str(home), f"/cwd/{i}")
        except BaseException as exc:  # noqa: BLE001 - we assert none occur
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    data = _load(home)  # raises if the file was ever left torn/invalid
    assert data["hasCompletedOnboarding"] is True
    leftovers = [p.name for p in home.iterdir() if p.name != ".claude.json"]
    assert leftovers == [], leftovers
