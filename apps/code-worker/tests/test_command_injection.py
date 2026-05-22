"""Adversarial tests proving _run does NOT expand shell metacharacters.

Each test mounts a payload containing a specific shell metacharacter
class into a user-derived argument (branch_name / commit_msg / tag).
The payload attempts to create a canary file. The test asserts the
canary file does NOT exist post-call → no shell expansion fired.

Spec:
  docs/superpowers/specs/2026-05-22-subproject-a-infra-secret-hardening-design.md
PR1 (F1 shell=True removal).

Plan:
  docs/superpowers/plans/2026-05-22-pr1-shell-true-removal.md
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

# Existing code-worker test convention: `import workflows as wf` then
# `wf._run(...)`. The conftest.py at apps/code-worker/tests/ adds the
# package root to sys.path so `workflows` resolves as a top-level
# module when pytest is invoked from `apps/code-worker/`.
import workflows as wf


@pytest.fixture
def canary_path(tmp_path: Path) -> Path:
    """Per-test canary file. Path uniquely identifies the injection class."""
    canary = tmp_path / "canary_should_not_exist.txt"
    if canary.exists():
        canary.unlink()
    yield canary
    if canary.exists():
        canary.unlink()


@pytest.fixture
def workspace_with_git(tmp_path: Path) -> Path:
    """Minimal git workspace so commands like `git status` succeed."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=ws, check=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=ws, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=ws, check=True)
    return ws


def test_dollar_paren_substitution_is_literal(
    canary_path: Path, workspace_with_git: Path
):
    """`$(command)` MUST NOT execute. Payload tries to touch a canary."""
    branch_name = f"feat/x$(touch {canary_path})y"
    # We use `git checkout -b <branch>` which is one of the F1 sinks.
    try:
        wf._run(
            ["git", "checkout", "-b", branch_name],
            cwd=str(workspace_with_git),
            timeout=10,
        )
    except RuntimeError:
        # Git may reject the branch name itself — that's fine. What we care
        # about is that the canary did NOT get created.
        pass
    assert not canary_path.exists(), (
        f"$(...) was expanded by the shell; canary at {canary_path} appeared. "
        "shell=True regression."
    )


def test_backtick_substitution_is_literal(
    canary_path: Path, workspace_with_git: Path
):
    """Backtick `command` MUST NOT execute. Some shells expand inside
    double-quoted strings — argv-list form must defeat this."""
    commit_msg = f"msg`touch {canary_path}`"
    # Create initial commit so `git commit` succeeds
    (workspace_with_git / "f.txt").write_text("x")
    wf._run(["git", "add", "-A"], cwd=str(workspace_with_git), timeout=10)
    try:
        wf._run(
            ["git", "commit", "-F", "-"],
            cwd=str(workspace_with_git),
            input=commit_msg,
            timeout=10,
        )
    except RuntimeError:
        pass
    assert not canary_path.exists(), (
        f"Backtick was expanded; canary at {canary_path} appeared."
    )


def test_semicolon_chain_is_literal(
    canary_path: Path, workspace_with_git: Path
):
    """`; command` MUST NOT chain to a new shell command."""
    branch_name = f"feat/x;touch {canary_path}"
    try:
        wf._run(
            ["git", "checkout", "-b", branch_name],
            cwd=str(workspace_with_git),
            timeout=10,
        )
    except RuntimeError:
        pass
    assert not canary_path.exists(), (
        f"; chain was expanded; canary at {canary_path} appeared."
    )


def test_double_ampersand_is_literal(
    canary_path: Path, workspace_with_git: Path
):
    """`&& command` MUST NOT chain to a new shell command."""
    commit_msg = f"msg && touch {canary_path}"
    (workspace_with_git / "g.txt").write_text("x")
    wf._run(["git", "add", "-A"], cwd=str(workspace_with_git), timeout=10)
    try:
        wf._run(
            ["git", "commit", "-F", "-"],
            cwd=str(workspace_with_git),
            input=commit_msg,
            timeout=10,
        )
    except RuntimeError:
        pass
    assert not canary_path.exists(), (
        f"&& was expanded; canary at {canary_path} appeared."
    )


def test_pipe_is_literal(
    canary_path: Path, workspace_with_git: Path
):
    """`| command` MUST NOT pipe to a new shell command."""
    branch_name = f"feat/x | touch {canary_path}"
    try:
        wf._run(
            ["git", "checkout", "-b", branch_name],
            cwd=str(workspace_with_git),
            timeout=10,
        )
    except RuntimeError:
        pass
    assert not canary_path.exists(), (
        f"| pipe was expanded; canary at {canary_path} appeared."
    )


def test_output_redirect_is_literal(
    canary_path: Path, workspace_with_git: Path
):
    """`> file` MUST NOT redirect to a new file."""
    commit_msg = f"msg > {canary_path}"
    (workspace_with_git / "h.txt").write_text("x")
    wf._run(["git", "add", "-A"], cwd=str(workspace_with_git), timeout=10)
    try:
        wf._run(
            ["git", "commit", "-F", "-"],
            cwd=str(workspace_with_git),
            input=commit_msg,
            timeout=10,
        )
    except RuntimeError:
        pass
    assert not canary_path.exists(), (
        f"> redirect was expanded; canary at {canary_path} appeared."
    )


def test_input_redirect_combined_with_output_is_literal(
    canary_path: Path, workspace_with_git: Path
):
    """Combined `< sourcefile > canary` test. If `<` were expanded as
    input redirection AND `>` as output redirection, the shell would
    pipe the source file's contents into canary_path. argv-form
    treats the whole string as one literal branch name — canary
    stays absent."""
    source = workspace_with_git / "source.txt"
    source.write_text("MARKER\n")
    branch_name = f"feat/x < {source} > {canary_path}"
    try:
        wf._run(
            ["git", "checkout", "-b", branch_name],
            cwd=str(workspace_with_git),
            timeout=10,
        )
    except RuntimeError:
        # Git rejects branch names with spaces / metachars — fine.
        pass
    assert not canary_path.exists(), (
        f"< / > redirection was expanded; canary at {canary_path} "
        f"appeared (would contain source.txt contents under shell=True)."
    )


def test_newline_in_commit_message_is_literal_multiline(
    workspace_with_git: Path,
):
    """Newlines in commit messages MUST NOT trigger a second shell
    command. They should produce a legitimate multi-line commit."""
    commit_msg = "subject line\n\nbody line one\nbody line two"
    (workspace_with_git / "i.txt").write_text("x")
    wf._run(["git", "add", "-A"], cwd=str(workspace_with_git), timeout=10)
    wf._run(
        ["git", "commit", "-F", "-"],
        cwd=str(workspace_with_git),
        input=commit_msg,
    )
    # Verify the commit message contains the newlines (multi-line)
    log = wf._run(
        ["git", "log", "-1", "--pretty=format:%B"],
        cwd=str(workspace_with_git),
        timeout=10,
    )
    assert "body line one" in log
    assert "body line two" in log
