"""Tests for the ``execute_code_task`` Temporal activity (workflows.py:548-1028).

This is the 470-line orchestration activity that Phase 4 deliberately scoped
out — the big one. We test it as a regular async function with all
external boundaries (helpers ``_run``, ``_run_long_command``, ``_run_review_agent``,
``_fetch_claude_token``, ``subprocess.run`` for ``gh``/``git diff``,
filesystem writes, RL logging) mocked. The only logic actually exercised is
the orchestration flow itself.

Approach (matches Phase 4's chat_cli pattern): use ``monkeypatch`` to replace
each helper with a fake; record the call sequence; assert on the final
``CodeTaskResult`` and the dispatch order.
"""
from __future__ import annotations

import asyncio
import os
import subprocess

import pytest

import workflows as wf


# ── Helpers ──────────────────────────────────────────────────────────────

def _make_input(**overrides):
    base = dict(
        task_description="## Goal\nAdd a comment to main.py",
        tenant_id="tenant-aaa",
        context=None,
    )
    base.update(overrides)
    return wf.CodeTaskInput(**base)


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(
        args=["x"], returncode=returncode, stdout=stdout, stderr=stderr,
    )


@pytest.fixture
def stub_activity_heartbeat(monkeypatch):
    """Outside an activity context, ``activity.heartbeat`` raises. Stub it."""
    monkeypatch.setattr(wf.activity, "heartbeat", lambda *a, **kw: None)


@pytest.fixture
def stub_filesystem(monkeypatch, tmp_path):
    """Redirect WORKSPACE-relative file operations into tmp_path so the
    real /workspace path isn't touched. We patch ``os.makedirs`` and
    ``open``/``os.remove`` lightly via call recording rather than
    full sandboxing — most tests only care that they don't raise."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(wf, "WORKSPACE", str(workspace))
    # Pre-create .claude so plan_file write is a no-op when called.
    (workspace / ".claude").mkdir(exist_ok=True)
    return workspace


@pytest.fixture
def stub_run_helpers(monkeypatch):
    """Capture every call to ``_run`` / ``_run_long_command`` / ``_run_review_agent``
    so each test can assert on dispatch order. Sensible defaults: success.
    """
    state = {
        "run_calls": [],          # List[str]
        "long_calls": [],         # List[list[str]]
        "review_calls": [],       # List[role]
        "subprocess_run_calls": [],
        "rl_log_calls": [],
        "git_status_output": " M file.py",  # changes present by default
        "git_log_output": "- abc 123 commit msg",
        "diff_files": "apps/api/foo.py\napps/web/bar.js",
    }

    def fake_run(cmd, cwd=None, timeout=600, extra_env=None, input=None):
        state["run_calls"].append(cmd)
        # _run now accepts argv lists post-PR1 (F1 shell=True removal).
        # Normalize to a flat string for the substring match the canned-
        # outputs dispatcher uses.
        cmd_str = " ".join(cmd) if isinstance(cmd, (list, tuple)) else cmd
        if "git status --porcelain" in cmd_str:
            return state["git_status_output"]
        if "git diff --name-only" in cmd_str:
            return state["diff_files"]
        if "git log" in cmd_str:
            return state["git_log_output"]
        return ""

    def fake_long(cmd, *, cwd=None, timeout=None, extra_env=None,
                  heartbeat_message="", heartbeat_interval=30):
        state["long_calls"].append(list(cmd))
        # Default: success with valid Claude JSON output.
        return _completed(
            returncode=0,
            stdout='{"result": "I made the change.", "usage": {"input_tokens": 100}}',
            stderr="",
        )

    def fake_review(role, review_prompt, extra_env, timeout=None):
        state["review_calls"].append(role)
        return wf.AgentReview(
            agent_role=role,
            approved=True,
            verdict="APPROVED",
            issues=[],
            suggestions=[],
            summary="LGTM",
        )

    def fake_subprocess_run(cmd, **kwargs):
        state["subprocess_run_calls"].append(cmd)
        # The activity calls subprocess.run for `gh pr create` and for
        # `git diff --stat main` / `git diff main -- ...` inside the
        # review-context builder.
        if isinstance(cmd, list) and cmd[:2] == ["gh", "pr"]:
            return _completed(
                returncode=0,
                stdout="https://github.com/nomad3/agentprovision-agents/pull/42\n",
            )
        if isinstance(cmd, list) and cmd[:2] == ["git", "diff"]:
            return _completed(returncode=0, stdout="diff content")
        return _completed(returncode=0)

    def fake_log_rl(*args, **kwargs):
        state["rl_log_calls"].append((args, kwargs))

    monkeypatch.setattr(wf, "_run", fake_run)
    monkeypatch.setattr(wf, "_run_long_command", fake_long)
    monkeypatch.setattr(wf, "_run_review_agent", fake_review)
    monkeypatch.setattr(wf.subprocess, "run", fake_subprocess_run)
    monkeypatch.setattr(wf, "_log_code_task_rl", fake_log_rl)

    # Provide a default Claude token so the early bail-out doesn't trigger.
    monkeypatch.setattr(wf, "_fetch_claude_token", lambda tid: "fake-token")

    return state


# ── Happy path ────────────────────────────────────────────────────────────

class TestExecuteCodeTaskHappyPath:
    def test_full_flow_creates_pr_and_returns_success(
        self, stub_activity_heartbeat, stub_filesystem, stub_run_helpers,
    ):
        result = asyncio.run(wf.execute_code_task(_make_input()))

        assert result.success is True
        assert result.pr_url == "https://github.com/nomad3/agentprovision-agents/pull/42"
        assert result.branch.startswith("code/feat/add-a-comment-to-main")
        # Files-changed list comes from the `git diff --name-only` mock.
        assert "apps/api/foo.py" in result.files_changed
        assert "apps/web/bar.js" in result.files_changed

        # Sequence sanity:
        #  - 3 plan-review calls (Architect / Technical / Behavior Reviewer)
        #  - 3 impl-review calls (Architect Agent / Code Review / Behavior)
        assert len(stub_run_helpers["review_calls"]) == 6
        # Plan-review roles
        assert "Architect Reviewer" in stub_run_helpers["review_calls"]
        assert "Technical Reviewer" in stub_run_helpers["review_calls"]
        assert "Behavior Reviewer" in stub_run_helpers["review_calls"]
        # Impl-review roles
        assert "Architect Agent" in stub_run_helpers["review_calls"]
        assert "Code Review Agent" in stub_run_helpers["review_calls"]
        assert "Behavior Review Agent" in stub_run_helpers["review_calls"]

        # gh pr create must have been called.
        gh_calls = [
            c for c in stub_run_helpers["subprocess_run_calls"]
            if isinstance(c, list) and c[:2] == ["gh", "pr"]
        ]
        assert len(gh_calls) == 1

        # RL experience logged.
        assert len(stub_run_helpers["rl_log_calls"]) == 1

    def test_no_changes_returns_early_with_empty_pr(
        self, stub_activity_heartbeat, stub_filesystem, stub_run_helpers,
    ):
        # Empty git status → no changes → no commit/push/PR.
        stub_run_helpers["git_status_output"] = ""

        result = asyncio.run(wf.execute_code_task(_make_input()))

        assert result.success is True
        assert result.pr_url == ""
        assert result.files_changed == []
        assert "No changes" in result.summary

        # Crucially: no PR creation attempted, no RL log.
        gh_calls = [
            c for c in stub_run_helpers["subprocess_run_calls"]
            if isinstance(c, list) and c[:2] == ["gh", "pr"]
        ]
        assert gh_calls == []
        assert stub_run_helpers["rl_log_calls"] == []


# ── Branch: Claude credit-exhausted → Codex fallback ─────────────────────

class TestExecuteCodeTaskCodexFallback:
    def test_credit_exhausted_falls_back_to_codex(
        self, stub_activity_heartbeat, stub_filesystem, stub_run_helpers, monkeypatch,
    ):
        """When Claude returns non-zero with credit-exhausted text, the
        activity reroutes to ``_execute_codex_code_task``."""
        # First two long_command calls (plan + main impl) — second one fails.
        # Plan call (1st) succeeds, main impl (2nd) returns credit-exhausted.
        call_counter = {"n": 0}

        def fake_long(cmd, **kwargs):
            call_counter["n"] += 1
            if call_counter["n"] == 1:
                # Plan phase — succeed
                return _completed(returncode=0, stdout='{"result":"plan ok"}')
            # Main impl call — credit exhausted.
            return _completed(
                returncode=1,
                stdout="",
                stderr="Your credit balance is too low to continue",
            )

        monkeypatch.setattr(wf, "_run_long_command", fake_long)

        codex_called = {"n": 0}

        def fake_codex(task_input, prompt, session_dir):
            codex_called["n"] += 1
            return ("Codex implementation summary", {"input_tokens": 50})

        monkeypatch.setattr(wf, "_execute_codex_code_task", fake_codex)

        result = asyncio.run(wf.execute_code_task(_make_input()))

        # Codex was invoked exactly once.
        assert codex_called["n"] == 1
        # Final result is success and the PR body uses provider_label "Codex".
        assert result.success is True
        # PR-body label comes from the gh subprocess call.
        gh_call = next(
            c for c in stub_run_helpers["subprocess_run_calls"]
            if isinstance(c, list) and c[:2] == ["gh", "pr"]
        )
        body_idx = gh_call.index("--body") + 1
        assert "Codex" in gh_call[body_idx]


# ── Branch: Claude error that is NOT credit-exhausted → exception path ───

class TestExecuteCodeTaskFailurePaths:
    def test_non_credit_error_returns_failure_result(
        self, stub_activity_heartbeat, stub_filesystem, stub_run_helpers, monkeypatch,
    ):
        call_counter = {"n": 0}

        def fake_long(cmd, **kwargs):
            call_counter["n"] += 1
            if call_counter["n"] == 1:
                return _completed(returncode=0, stdout='{"result":"plan ok"}')
            # Main impl call — generic auth error (NOT credit/quota).
            return _completed(returncode=2, stdout="", stderr="auth failed")

        monkeypatch.setattr(wf, "_run_long_command", fake_long)

        result = asyncio.run(wf.execute_code_task(_make_input()))

        assert result.success is False
        assert "auth failed" in result.error
        assert result.pr_url == ""

    def test_token_fetch_failure_returns_failure_result(
        self, stub_activity_heartbeat, stub_filesystem, stub_run_helpers, monkeypatch,
    ):
        # Make _fetch_claude_token raise — the outer except catches it.
        def boom(tenant_id):
            raise RuntimeError("token vault unreachable")

        monkeypatch.setattr(wf, "_fetch_claude_token", boom)

        result = asyncio.run(wf.execute_code_task(_make_input()))

        assert result.success is False
        assert "token vault unreachable" in result.error

    def test_pr_creation_failure_propagates(
        self, stub_activity_heartbeat, stub_filesystem, stub_run_helpers, monkeypatch,
    ):
        # gh pr create returns non-zero — the activity raises, caught by
        # the outer except and returned as failure.
        def fake_subprocess_run(cmd, **kwargs):
            stub_run_helpers["subprocess_run_calls"].append(cmd)
            if isinstance(cmd, list) and cmd[:2] == ["gh", "pr"]:
                return _completed(returncode=1, stderr="rate limited")
            if isinstance(cmd, list) and cmd[:2] == ["git", "diff"]:
                return _completed(returncode=0, stdout="")
            return _completed(returncode=0)

        monkeypatch.setattr(wf.subprocess, "run", fake_subprocess_run)

        result = asyncio.run(wf.execute_code_task(_make_input()))

        assert result.success is False
        assert "gh pr create failed" in result.error


# ── Branch: Plan review fails consensus → still proceeds (logs warning) ──

class TestExecuteCodeTaskReviewConsensus:
    def test_plan_review_failure_does_not_abort(
        self, stub_activity_heartbeat, stub_filesystem, stub_run_helpers, monkeypatch,
    ):
        """When the plan-review council rejects, the activity logs a warning
        and proceeds with implementation anyway (line 663-672)."""
        review_idx = {"n": 0}

        def fake_review(role, review_prompt, extra_env, timeout=None):
            review_idx["n"] += 1
            # First 3 calls = plan review. Reject all of them.
            approved = review_idx["n"] > 3
            return wf.AgentReview(
                agent_role=role, approved=approved,
                verdict="APPROVED" if approved else "REJECTED",
                issues=["bad pattern"] if not approved else [],
                suggestions=[], summary="x",
            )

        monkeypatch.setattr(wf, "_run_review_agent", fake_review)

        result = asyncio.run(wf.execute_code_task(_make_input()))

        # Plan review failed (3 rejects) but impl review succeeded → success.
        assert result.success is True
        # Six review agents called (3 plan + 3 impl).
        assert review_idx["n"] == 6

    def test_impl_review_failure_triggers_correction_pass(
        self, stub_activity_heartbeat, stub_filesystem, stub_run_helpers, monkeypatch,
    ):
        """When post-impl review fails consensus, a correction pass is
        attempted (line 886-913) and review re-runs."""
        review_idx = {"n": 0}

        def fake_review(role, review_prompt, extra_env, timeout=None):
            review_idx["n"] += 1
            # Plan review (calls 1-3): approve. First impl review (4-6):
            # reject. Second impl review (7-9): approve.
            if review_idx["n"] <= 3:
                approved = True
            elif review_idx["n"] <= 6:
                approved = False
            else:
                approved = True
            return wf.AgentReview(
                agent_role=role, approved=approved,
                verdict="APPROVED" if approved else "REJECTED",
                issues=["fix this"] if not approved else [],
                suggestions=[], summary="x",
            )

        monkeypatch.setattr(wf, "_run_review_agent", fake_review)

        # Count long_command calls — the correction pass is a 3rd long
        # command (after plan + main impl).
        long_calls_before = len(stub_run_helpers["long_calls"])

        result = asyncio.run(wf.execute_code_task(_make_input()))

        # 9 review calls: 3 plan + 3 impl-1 + 3 impl-2.
        assert review_idx["n"] == 9
        # 3 long-command calls: plan + main impl + correction pass.
        long_calls_after = len(stub_run_helpers["long_calls"])
        assert long_calls_after - long_calls_before == 3
        # PR body must include the warning flag.
        gh_call = next(
            c for c in stub_run_helpers["subprocess_run_calls"]
            if isinstance(c, list) and c[:2] == ["gh", "pr"]
        )
        body_idx = gh_call.index("--body") + 1
        # 3rd review pass approved — body should NOT carry the warning flag.
        assert "did not reach full consensus" not in gh_call[body_idx]
        assert result.success is True

    def test_impl_review_still_failing_after_correction_marks_pr_warning(
        self, stub_activity_heartbeat, stub_filesystem, stub_run_helpers, monkeypatch,
    ):
        """Correction pass runs but second review still fails → PR body
        carries the ⚠ warning flag (line 955)."""
        def fake_review(role, review_prompt, extra_env, timeout=None):
            # Always reject.
            return wf.AgentReview(
                agent_role=role, approved=False, verdict="REJECTED",
                issues=["unfixable"], suggestions=[], summary="x",
            )

        monkeypatch.setattr(wf, "_run_review_agent", fake_review)

        result = asyncio.run(wf.execute_code_task(_make_input()))

        gh_call = next(
            c for c in stub_run_helpers["subprocess_run_calls"]
            if isinstance(c, list) and c[:2] == ["gh", "pr"]
        )
        body_idx = gh_call.index("--body") + 1
        assert "did not reach full consensus" in gh_call[body_idx]
        # PR still gets created — the activity does not abort on review failure.
        assert result.success is True


# ── Output parsing edge cases ────────────────────────────────────────────

class TestExecuteCodeTaskOutputParsing:
    def test_non_json_claude_output_handled_as_raw(
        self, stub_activity_heartbeat, stub_filesystem, stub_run_helpers, monkeypatch,
    ):
        """When the impl ``_run_long_command`` returns plain (non-JSON) text,
        the activity falls into the ``except json.JSONDecodeError`` branch
        and stores it under ``raw`` (line 750-751). PR is still created."""
        call_counter = {"n": 0}

        def fake_long(cmd, **kwargs):
            call_counter["n"] += 1
            if call_counter["n"] == 1:
                return _completed(returncode=0, stdout="plan complete")
            return _completed(returncode=0, stdout="raw plain text not JSON")

        monkeypatch.setattr(wf, "_run_long_command", fake_long)

        result = asyncio.run(wf.execute_code_task(_make_input()))

        assert result.success is True
        # claude_output is the raw stdout
        assert "raw plain text not JSON" in result.claude_output
