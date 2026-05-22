# PR1 — F1 shell=True removal in code-worker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate the `shell=True` command-injection sink in `apps/code-worker/workflows.py` so a malicious chat `task_description` / `commit_msg` / `branch_name` cannot execute arbitrary shell. Spec: `docs/superpowers/specs/2026-05-22-subproject-a-infra-secret-hardening-design.md` (§5 PR1).

**Architecture:** Refactor `_run(cmd: str, shell=True)` to `_run(argv: list[str], shell=False)`. Every call site passes a list. `git commit -m "..."` switches to `git commit -F -` reading the message from stdin (user-derived text never enters argv). `&&`-chained command at line 622 splits into three sequential calls (Python raises on failure preserves the short-circuit). Adversarial test file (`test_command_injection.py`) covers 8 shell-metachar classes with canary-file assertions.

**Tech Stack:** Python 3.11, `subprocess`, pytest. No new dependencies.

**Cluster impact:** Touches only `apps/code-worker/`. No api restart. Code-worker container rebuild + restart (~30s). Chat dispatch falls through to `opencode` during the restart window — no chat outage (verified at the PR1 §6 gate via `docker stop code-worker` smoke).

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `apps/code-worker/workflows.py` | modify | `_run` signature change; every caller updated to argv-list; commit-via-stdin path |
| `apps/code-worker/tests/test_command_injection.py` | create | 8 adversarial tests, one per shell-metachar class. Canary-file assertions prove no shell expansion |

Decomposition note: keeping the change contained to `workflows.py` matches the spec's PR1 scope. Other code-worker files (`cli_runtime.py`, `cli_executors/*`) already use argv-list `subprocess.run` and don't need touching.

---

## Task 1: Adversarial test scaffolding (RED phase setup)

**Files:**
- Create: `apps/code-worker/tests/test_command_injection.py`

- [ ] **Step 1.1: Create the test file with the `$()` command-substitution case**

```python
"""Adversarial tests proving _run does NOT expand shell metacharacters.

Each test mounts a payload containing a specific shell metacharacter class
into a user-derived argument (branch_name / commit_msg / tag). The payload
attempts to create a canary file. The test asserts the canary file does NOT
exist post-call → no shell expansion fired.

Spec: docs/superpowers/specs/2026-05-22-subproject-a-infra-secret-hardening-design.md
PR1 (F1 shell=True removal).
"""
from __future__ import annotations

import os
import pytest
import subprocess
import tempfile
from pathlib import Path

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
        )
    except RuntimeError:
        # Git may reject the branch name itself — that's fine. What we care
        # about is that the canary did NOT get created.
        pass
    assert not canary_path.exists(), (
        f"$(...) was expanded by the shell; canary at {canary_path} appeared. "
        "shell=True regression."
    )
```

- [ ] **Step 1.2: Run the test to confirm it FAILS (RED phase)**

```bash
cd apps/code-worker
pytest tests/test_command_injection.py::test_dollar_paren_substitution_is_literal -v
```

Expected: FAIL with either (a) `TypeError`/argv shape error if signature mismatch (the current `_run(cmd: str, shell=True)` chokes on receiving a list — Python may raise `TypeError` when shell=True meets a list arg on some platforms), OR (b) the canary file appears because `shell=True` expanded `$(touch ...)`.

The whole point of RED first: if this test PASSES against today's `shell=True` code, the test is wrong — fix the test, not the implementation.

- [ ] **Step 1.3: Commit the failing test**

```bash
git add apps/code-worker/tests/test_command_injection.py
git commit -m "test(code-worker): adversarial command-injection test scaffolding (RED)"
```

---

## Task 2: Refactor `_run` to argv-list + shell=False

**Files:**
- Modify: `apps/code-worker/workflows.py:173-186`

- [ ] **Step 2.1: Read the current `_run` implementation**

```bash
sed -n '173,186p' apps/code-worker/workflows.py
```

Expected: see the current `_run(cmd: str, ..., shell=True)`.

- [ ] **Step 2.2: Rewrite `_run` to accept argv-list and optional stdin input**

Replace lines 173-186 with:

```python
def _run(
    argv: list[str],
    cwd: str = WORKSPACE,
    timeout: int = 600,
    extra_env: dict | None = None,
    input: str | None = None,
) -> str:
    """Run a subprocess from an argv list and return stdout.

    Uses ``shell=False`` always — shell metacharacters in argv elements
    become literal text, never expanded. For commands that need to read
    a long/user-derived string (e.g. git commit messages), pass it via
    ``input=`` which is fed to the subprocess on stdin. The argv stays
    free of user data.

    Raises RuntimeError on non-zero exit.

    Spec: docs/superpowers/specs/2026-05-22-subproject-a-infra-secret-hardening-design.md
    PR1 (F1 shell=True removal).
    """
    logger.info("Running: %s", " ".join(argv))
    env = None
    if extra_env:
        env = {**os.environ, **extra_env}
    result = subprocess.run(
        argv,
        shell=False,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        input=input,
    )
    if result.returncode != 0:
        error_detail = result.stderr or result.stdout
        logger.error(
            "Command failed: %s\nstderr: %s\nstdout: %s",
            " ".join(argv), result.stderr, result.stdout[:2000],
        )
        raise RuntimeError(
            f"Command failed: {' '.join(argv)}\n{error_detail}"
        )
    return result.stdout.strip()
```

- [ ] **Step 2.3: Verify Step 1's RED test still fails (it should — callers haven't been updated yet)**

```bash
cd apps/code-worker
pytest tests/test_command_injection.py::test_dollar_paren_substitution_is_literal -v
```

Expected: FAIL — the callers (line 626 etc.) still pass strings, not lists. Python will raise `TypeError` somewhere. That's fine; means we have more work.

- [ ] **Step 2.4: Commit the `_run` signature change (callers still broken — that's intentional)**

```bash
git add apps/code-worker/workflows.py
git commit -m "refactor(code-worker): _run accepts argv list + optional stdin (callers TBD)"
```

---

## Task 3: Update all call sites — first pass, no-interpolation ones

**TDD note**: these five call sites take no user input (literal command strings only). They are **pure refactors** — no behavior change, no new threat surface to test. The existing code-worker test suite is the regression guard (Step 3.6 enforces "all existing tests still pass"). The adversarial test file targets only the user-derived sinks in Task 4 — adding canary tests here would be cargo-cult TDD.

Convert them to argv-list first; verify they still work via the existing test suite. This proves the signature change is benign before we tackle the dangerous call sites.

**Files:**
- Modify: `apps/code-worker/workflows.py:622, 810, 990, 996, 1072`

- [ ] **Step 3.1: Convert line 622 — `&&` chain → three sequential calls**

Find:
```python
_run("git fetch origin && git checkout main && git pull origin main")
```

Replace with:
```python
_run(["git", "fetch", "origin"])
_run(["git", "checkout", "main"])
_run(["git", "pull", "origin", "main"])
```

The `&&` short-circuit is preserved by Python's exception flow: `_run` raises `RuntimeError` on non-zero exit, so subsequent statements don't execute.

- [ ] **Step 3.2: Convert line 810**

Find:
```python
status = _run("git status --porcelain")
```

Replace with:
```python
status = _run(["git", "status", "--porcelain"])
```

- [ ] **Step 3.3: Convert line 990**

Find:
```python
_run("git add -A")
```

Replace with:
```python
_run(["git", "add", "-A"])
```

- [ ] **Step 3.4: Convert line 996**

Find:
```python
files_changed = _run("git diff --name-only main").split("\n")
```

Replace with:
```python
files_changed = _run(["git", "diff", "--name-only", "main"]).split("\n")
```

- [ ] **Step 3.5: Convert line 1072**

Find:
```python
_run("git checkout main", timeout=10)
```

Replace with:
```python
_run(["git", "checkout", "main"], timeout=10)
```

- [ ] **Step 3.6: Verify these conversions don't break the existing code-worker test suite**

```bash
cd apps/code-worker
pytest tests/ -k "not command_injection" -v 2>&1 | tail -30
```

Expected: ALL existing tests PASS. (The new `test_command_injection.py` should still FAIL — Task 4 hasn't started.)

- [ ] **Step 3.7: Commit the safe-callers migration**

```bash
git add apps/code-worker/workflows.py
git commit -m "refactor(code-worker): convert no-interpolation _run callers to argv-list"
```

---

## Task 4: Update dangerous call sites — the user-derived interpolations

These four call sites are the actual F1 sinks. Each takes user/task-derived text.

**Files:**
- Modify: `apps/code-worker/workflows.py:626, 992, 993, 1004`

- [ ] **Step 4.1: Convert line 626 — `git checkout -b {branch_name}`**

Find:
```python
_run(f"git checkout -b {branch_name}")
```

Replace with:
```python
_run(["git", "checkout", "-b", branch_name])
```

`branch_name` is now a single argv element; any shell metacharacters in it become literal text (git itself may reject the branch name, which is fine — we just guarantee no shell expansion).

- [ ] **Step 4.2: Convert line 992 — `git commit -m "{tag}: {commit_msg}"` → stdin form**

Find:
```python
_run(f'git commit -m "{tag}: {commit_msg}"')
```

Replace with:
```python
_run(
    ["git", "commit", "-F", "-"],
    input=f"{tag}: {commit_msg}",
)
```

`-F -` tells git to read the commit message from stdin. The user-derived `tag` + `commit_msg` never enter argv.

- [ ] **Step 4.3: Convert line 993 — `git push origin {branch_name}`**

Find:
```python
_run(f'git push origin {branch_name}')
```

Replace with:
```python
_run(["git", "push", "origin", branch_name])
```

- [ ] **Step 4.4: Convert line 1004 — `git log main..{branch_name} ...`**

Find:
```python
commit_log = _run(f"git log main..{branch_name} --pretty=format:'- %h %s' --reverse")
```

Replace with:
```python
commit_log = _run([
    "git", "log",
    f"main..{branch_name}",
    "--pretty=format:- %h %s",
    "--reverse",
])
```

Note: `main..{branch_name}` is a single argv element. `branch_name` is f-string-interpolated INTO that one string, but since the whole thing is one argv element, shell metacharacters inside `branch_name` cannot break out into a new command. Worst case is git rejecting the revision-range syntax.

(Removed the single-quote wrapping around `--pretty=format` — argv doesn't need shell-style quoting; the format string is one literal argument. Verified at `apps/code-worker/workflows.py:1023` that `commit_log` is interpolated into the PR body markdown as plain text — no downstream parser expects the literal `'` characters around `- %h %s`.)

- [ ] **Step 4.5: Run the `$()` adversarial test — should now PASS (GREEN)**

```bash
cd apps/code-worker
pytest tests/test_command_injection.py::test_dollar_paren_substitution_is_literal -v
```

Expected: PASS. Canary file does NOT exist post-call.

- [ ] **Step 4.6: Run the full code-worker test suite to confirm no regression**

```bash
cd apps/code-worker
pytest tests/ -v 2>&1 | tail -30
```

Expected: ALL tests pass (including the one we just GREENed). If an existing test breaks, an argv conversion was wrong — investigate the failing test first.

- [ ] **Step 4.7: Commit the dangerous-callers migration**

```bash
git add apps/code-worker/workflows.py
git commit -m "fix(F1 P0): convert user-derived _run callers to argv-list + stdin commit"
```

---

## Task 5: Add the remaining 7 adversarial tests

**Files:**
- Modify: `apps/code-worker/tests/test_command_injection.py`

- [ ] **Step 5.1: Add backtick command-substitution test**

```python
def test_backtick_substitution_is_literal(
    canary_path: Path, workspace_with_git: Path
):
    """Backtick `command` MUST NOT execute. Some shells expand inside
    double-quoted strings — argv-list form must defeat this."""
    commit_msg = f"msg`touch {canary_path}`"
    # Create initial commit so `git commit` succeeds
    (workspace_with_git / "f.txt").write_text("x")
    wf._run(["git", "add", "-A"], cwd=str(workspace_with_git))
    try:
        wf._run(
            ["git", "commit", "-F", "-"],
            cwd=str(workspace_with_git),
            input=commit_msg,
        )
    except RuntimeError:
        pass
    assert not canary_path.exists(), (
        f"Backtick was expanded; canary at {canary_path} appeared."
    )
```

- [ ] **Step 5.2: Add semicolon-chain test**

```python
def test_semicolon_chain_is_literal(
    canary_path: Path, workspace_with_git: Path
):
    """`; command` MUST NOT chain to a new shell command."""
    branch_name = f"feat/x;touch {canary_path}"
    try:
        wf._run(
            ["git", "checkout", "-b", branch_name],
            cwd=str(workspace_with_git),
        )
    except RuntimeError:
        pass
    assert not canary_path.exists(), (
        f"; chain was expanded; canary at {canary_path} appeared."
    )
```

- [ ] **Step 5.3: Add `&&`-chain test**

```python
def test_double_ampersand_is_literal(
    canary_path: Path, workspace_with_git: Path
):
    """`&& command` MUST NOT chain to a new shell command."""
    commit_msg = f"msg && touch {canary_path}"
    (workspace_with_git / "g.txt").write_text("x")
    wf._run(["git", "add", "-A"], cwd=str(workspace_with_git))
    try:
        wf._run(
            ["git", "commit", "-F", "-"],
            cwd=str(workspace_with_git),
            input=commit_msg,
        )
    except RuntimeError:
        pass
    assert not canary_path.exists(), (
        f"&& was expanded; canary at {canary_path} appeared."
    )
```

- [ ] **Step 5.4: Add pipe test**

```python
def test_pipe_is_literal(
    canary_path: Path, workspace_with_git: Path
):
    """`| command` MUST NOT pipe to a new shell command."""
    branch_name = f"feat/x | touch {canary_path}"
    try:
        wf._run(
            ["git", "checkout", "-b", branch_name],
            cwd=str(workspace_with_git),
        )
    except RuntimeError:
        pass
    assert not canary_path.exists(), (
        f"| pipe was expanded; canary at {canary_path} appeared."
    )
```

- [ ] **Step 5.5: Add output-redirect test**

```python
def test_output_redirect_is_literal(
    canary_path: Path, workspace_with_git: Path
):
    """`> file` MUST NOT redirect to a new file."""
    commit_msg = f"msg > {canary_path}"
    (workspace_with_git / "h.txt").write_text("x")
    wf._run(["git", "add", "-A"], cwd=str(workspace_with_git))
    try:
        wf._run(
            ["git", "commit", "-F", "-"],
            cwd=str(workspace_with_git),
            input=commit_msg,
        )
    except RuntimeError:
        pass
    assert not canary_path.exists(), (
        f"> redirect was expanded; canary at {canary_path} appeared."
    )
```

- [ ] **Step 5.6: Add input-redirect-AND-output-redirect canary test**

The `<` (input redirect) class is hard to canary in isolation — input redirection produces no observable filesystem side effect by itself. Instead, combine `<` with `>` so a successful shell expansion would write something to a canary path. If shell=False holds, the whole expression is a literal git argument and the canary stays absent.

```python
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
        )
    except RuntimeError:
        # Git rejects branch names with spaces / metachars — fine.
        pass
    assert not canary_path.exists(), (
        f"< / > redirection was expanded; canary at {canary_path} "
        f"appeared (would contain source.txt contents under shell=True)."
    )
```

- [ ] **Step 5.7: Add newline-in-commit-message test**

```python
def test_newline_in_commit_message_is_literal_multiline(
    workspace_with_git: Path,
):
    """Newlines in commit messages MUST NOT trigger a second shell
    command. They should produce a legitimate multi-line commit."""
    commit_msg = "subject line\n\nbody line one\nbody line two"
    (workspace_with_git / "i.txt").write_text("x")
    wf._run(["git", "add", "-A"], cwd=str(workspace_with_git))
    wf._run(
        ["git", "commit", "-F", "-"],
        cwd=str(workspace_with_git),
        input=commit_msg,
    )
    # Verify the commit message contains the newlines (multi-line)
    log = wf._run(
        ["git", "log", "-1", "--pretty=format:%B"],
        cwd=str(workspace_with_git),
    )
    assert "body line one" in log
    assert "body line two" in log
```

- [ ] **Step 5.8: Run all 8 adversarial tests + confirm all GREEN**

```bash
cd apps/code-worker
pytest tests/test_command_injection.py -v
```

Expected: 8 PASSED.

- [ ] **Step 5.9: Commit the full adversarial test suite**

```bash
git add apps/code-worker/tests/test_command_injection.py
git commit -m "test(code-worker): full adversarial test suite for shell-metachar classes"
```

---

## Task 6: Cluster-safety verification (spec §6 PR1 gate)

This task does NOT change code. It verifies the §6 PR1 gate from the spec: code-worker is restart-safe and chat falls through to opencode during a stop.

**Files:** none (operational test)

- [ ] **Step 6.1: Verify the opencode fallback is wired in cli_platform_resolver**

```bash
grep -nE "opencode" apps/api/app/services/cli_platform_resolver.py | head
```

Expected: at least one match showing `opencode` listed in `_DEFAULT_PRIORITY` as the local-fallback floor.

- [ ] **Step 6.2: Stop the code-worker container, send a chat smoke, restart**

This is a live cluster test. Operator should run this AFTER the PR1 deploy has completed AND before merging PR2.

```bash
# Resolve the actual container name first — production sometimes uses
# a different docker-compose project name.
CONTAINER=$(docker ps --format '{{.Names}}' | grep code-worker | head -1)
test -n "$CONTAINER" || { echo "no code-worker container running"; exit 1; }
docker stop "$CONTAINER"
sleep 5
# Send a chat smoke via Chrome MCP / curl. The request should still get a
# response (via the opencode fallback). Capture timestamps.
docker start "$CONTAINER"
docker logs "$CONTAINER" --tail 20
```

Expected: chat smoke returns a response WITHOUT code-worker running. If chat hangs → opencode fallback is broken → DO NOT proceed to PR2 until that's fixed. Note: this is the §6 gate; failure here blocks PR2's deploy window.

- [ ] **Step 6.3: Mark the §6 PR1 gate as PASSED in the spec's verification log**

After Step 6.2 succeeds:

```bash
# Manual: edit docs/superpowers/specs/2026-05-22-subproject-a-infra-secret-hardening-design.md §6
# Add a note: "PR1 gate PASSED <timestamp> — code-worker stop test confirmed opencode fallback path."
# Commit that update separately from the PR1 PR.
```

---

## Task 7: Push PR + Luna review + CI + merge

**Files:** none (PR creation + ops)

- [ ] **Step 7.1: Verify branch state + push**

```bash
cd /Users/nomade/Documents/GitHub/agentprovision-agents
BRANCH=$(git branch --show-current)
echo "Pushing branch: $BRANCH"
# Expected: fix/F1-shell-true-removal (or similar — verify before commit)
git push -u origin "$BRANCH"
```

- [ ] **Step 7.2: Open PR assigned to nomad3**

```bash
gh pr create --assignee nomad3 \
  --title "fix(F1 P0): remove shell=True from code-worker._run (Sub-project A PR1)" \
  --body "$(cat <<'EOF'
## Summary

PR1 of Sub-project A (P0 infra/secret hardening — spec at docs/superpowers/specs/2026-05-22-subproject-a-infra-secret-hardening-design.md).

Closes the F1 RCE chain: `apps/code-worker/workflows.py:_run` switched from `shell=True` to argv-list + `shell=False`. `git commit -m "..."` now reads from stdin via `-F -` so user-derived text never enters argv. Every `_run` caller migrated.

## Verified

- 8 adversarial tests cover `$()`, backtick, `;`, `&&`, `|`, `>`, `<`, and newline injection classes. All GREEN.
- Existing code-worker test suite still passes.
- §6 PR1 gate (code-worker stop test → opencode fallback) verified live.

## Test plan

- [x] `pytest apps/code-worker/tests/test_command_injection.py -v` → 8 passed
- [x] `pytest apps/code-worker/tests/` → all passed (no regression)
- [x] Code-worker stop test → chat fell through to opencode

## Cluster impact

- Touches only apps/code-worker/. No api restart. Code-worker container rebuild only.
- During code-worker restart: chat dispatch falls through to opencode (the local-Gemma floor). No user-visible outage.

## Follow-ups (PR2-PR5 of Sub-project A)

PR2-PR5 wait for Simon's hands-on Keychain involvement on the Mac runner.
EOF
)"
```

- [ ] **Step 7.3: Request Luna review via Agent dispatch**

Use the standing pattern: dispatch a Luna-persona subagent with the PR diff + spec section as context. Address every BLOCKER/IMPORTANT/NIT in-PR per the standing rule (memory `feedback_address_all_review_findings.md`).

- [ ] **Step 7.4: Wait for CI green via Monitor tool**

```bash
# Monitor PR aggregate test status until 'pass'.
```

- [ ] **Step 7.5: Squash-merge once CI green + Luna approves**

```bash
gh pr merge <PR#> --squash --delete-branch=false
```

- [ ] **Step 7.6: Mark task #366 (F1) as completed**

```bash
# Via TaskUpdate.
```

- [ ] **Step 7.7: Log decision to Luna's tenant memory**

```bash
alpha remember --kind decision "F1 P0 shell=True RCE in code-worker closed via PR #<num> 2026-05-XX. 8 adversarial tests landed; §6 PR1 gate verified. PR2 (F7a kid plumbing) is next in Sub-project A; waits for Simon's return for the F2 + F7 Keychain hands-on steps."
```

---

## Stopping condition

PR1 is complete when:
1. All 8 adversarial tests pass
2. The full code-worker test suite passes
3. The §6 PR1 gate verifies live (code-worker stop → opencode fallback)
4. PR is merged on `main` with CI green
5. Task #366 marked completed
6. Luna decision-memory entry written

Then `await Simon` before starting PR2 (which is F7a kid plumbing — code change but requires care because it touches api JWT signing).
