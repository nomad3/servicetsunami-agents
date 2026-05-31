"""Tests for interactive-PTY prompt submission (Approach C).

Subscription Claude Code chat runs through an interactive PTY (native
``claude auth login`` creds; ``claude -p`` is blocked for subscription).
Claude Code v2.1.144's REPL does NOT auto-execute a positional ``[prompt]``
argument, so the executor must:

  1. (claude.py) write the turn blob to ``session_dir/turn_prompt.md`` and
     hand the runner a SINGLE-LINE trigger ("Read the file <abs> …") instead
     of the blob; the blob must NOT be appended positionally to ``cmd``.
  2. (claude_interactive.py) TYPE that trigger into the REPL once it is ready
     (banner seen + a quiet settle), gate the idle ``/exit`` on whether the
     trigger was submitted, and strip the trigger echo / Read chrome /
     ``[Pasted text +N lines]`` placeholder out of the returned transcript.

Print mode (``-p prompt``) must stay byte-identical.
"""
from __future__ import annotations

import os
import subprocess

import pytest

import cli_runtime
import workflows as wf
from cli_executors import claude_interactive
from cli_executors.claude_interactive import (
    clean_interactive_transcript,
    decide_pty_action,
)


TENANT_CLAUDE = "55555555-5555-4555-8555-555555555555"


def _make_input(**overrides):
    base = dict(
        platform="claude_code",
        message="hello",
        tenant_id=TENANT_CLAUDE,
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
def interactive_env(monkeypatch):
    """Force the interactive PTY branch via execution-mode env (avoids the
    ``__native_worker_login__`` worker-HOME credential-file guard)."""
    monkeypatch.setenv("CLAUDE_CODE_EXECUTION_MODE", "interactive")
    # Keep HOME redirection deterministic / off the workspaces volume.
    monkeypatch.setenv("CLAUDE_CODE_INTERACTIVE_HOME", "tenant")


# ════════════════════════════════════════════════════════════════════════
# Change 1 — claude.py interactive path
# ════════════════════════════════════════════════════════════════════════
class TestClaudeExecutorInteractiveSubmit:
    def _patch_credential(self, monkeypatch):
        monkeypatch.setattr(
            wf, "_fetch_claude_credential", lambda tid: ("token-xyz", "oauth")
        )

    def test_cmd_does_not_end_with_blob_positional(
        self, monkeypatch, tmp_path, interactive_env
    ):
        self._patch_credential(monkeypatch)
        session_dir = tmp_path / "session"
        session_dir.mkdir()

        captured: dict = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            return _completed(returncode=0, stdout="hi there")

        monkeypatch.setattr(
            claude_interactive, "run_claude_interactive_with_heartbeat", fake_run
        )

        task = _make_input(
            instruction_md_content="You are Luna. Be warm.",
            message="What is 2+2?",
        )
        out = wf._execute_claude_chat(task, session_dir=str(session_dir))

        assert out.success is True, out.error
        cmd = captured["cmd"]
        blob = "You are Luna. Be warm.\n\n# User Request\n\nWhat is 2+2?"
        # The full turn blob must NOT be appended positionally anymore.
        assert blob not in cmd
        assert cmd[-1] != blob
        # Print-mode switches must be absent in interactive mode.
        assert "-p" not in cmd
        assert "--no-session-persistence" not in cmd

    def test_turn_prompt_file_written_with_blob(
        self, monkeypatch, tmp_path, interactive_env
    ):
        self._patch_credential(monkeypatch)
        session_dir = tmp_path / "session"
        session_dir.mkdir()

        captured: dict = {}

        def fake_run(cmd, **kwargs):
            captured["kwargs"] = kwargs
            return _completed(0, stdout="ok")

        monkeypatch.setattr(
            claude_interactive, "run_claude_interactive_with_heartbeat", fake_run
        )

        task = _make_input(
            instruction_md_content="PERSONA: Luna",
            message="hello there",
        )
        wf._execute_claude_chat(task, session_dir=str(session_dir))

        # The turn blob now lives in a UNIQUE per-turn scratch dir
        # (``turn_<hex>/turn_prompt.md``), not directly under session_dir —
        # robust to Claude mangling a per-turn filename when it re-types it.
        answer_dir = captured["kwargs"]["answer_dir"]
        turn_file = os.path.join(answer_dir, "turn_prompt.md")
        assert os.path.isfile(turn_file)
        assert os.path.basename(answer_dir).startswith("turn_")
        assert os.path.dirname(answer_dir) == str(session_dir)
        body = open(turn_file).read()
        assert "PERSONA: Luna" in body
        assert "# User Request" in body
        assert "hello there" in body

    def test_runner_prompt_is_single_line_trigger_referencing_abs_path(
        self, monkeypatch, tmp_path, interactive_env
    ):
        self._patch_credential(monkeypatch)
        session_dir = tmp_path / "session"
        session_dir.mkdir()

        captured: dict = {}

        def fake_run(cmd, **kwargs):
            captured["kwargs"] = kwargs
            return _completed(0, stdout="ok")

        monkeypatch.setattr(
            claude_interactive, "run_claude_interactive_with_heartbeat", fake_run
        )

        task = _make_input(
            instruction_md_content="PERSONA",
            message="do the thing",
        )
        wf._execute_claude_chat(task, session_dir=str(session_dir))

        submit = captured["kwargs"]["prompt"]
        answer_dir = captured["kwargs"]["answer_dir"]
        turn_file = os.path.join(answer_dir, "turn_prompt.md")
        # Single line — Approach C's whole point.
        assert "\n" not in submit
        # References the absolute turn-file path so Claude's Read tool reaches it.
        assert turn_file in submit
        assert os.path.isabs(turn_file)
        # Imperative — answer directly, no confirmation prompt.
        assert "Read the file" in submit
        # The blob itself must NOT be in the typed trigger.
        assert "PERSONA" not in submit
        assert "do the thing" not in submit

    def test_trigger_instructs_read_turn_and_write_answer_file(
        self, monkeypatch, tmp_path, interactive_env
    ):
        """Defect 2: the single-line trigger must instruct Claude to BOTH read
        the turn file AND write its final answer out-of-band, and the runner
        must receive the scratch ``answer_dir`` so it can glob it back.

        Mangle-robust redesign (2026-05-30): the turn blob + answer both live in
        a UNIQUE per-turn scratch DIRECTORY (``turn_<hex>/``); the answer target
        is a SHORT, FIXED name (``answer.md``) in that dir. Claude intermittently
        drops chars from a 32-hex filename when re-typing it into its ``Write``
        call — a short fixed name + globbing the fresh dir survives that. The dir
        is unique per turn, so freshness (no stale replay) is preserved."""
        self._patch_credential(monkeypatch)
        session_dir = tmp_path / "session"
        session_dir.mkdir()

        captured: dict = {}

        def fake_run(cmd, **kwargs):
            captured["kwargs"] = kwargs
            return _completed(0, stdout="ok")

        monkeypatch.setattr(
            claude_interactive, "run_claude_interactive_with_heartbeat", fake_run
        )

        task = _make_input(
            instruction_md_content="PERSONA: Luna",
            message="What is 2+2?",
        )
        wf._execute_claude_chat(task, session_dir=str(session_dir))

        submit = captured["kwargs"]["prompt"]
        answer_dir = captured["kwargs"].get("answer_dir")
        # ``answer_file`` is replaced by the scratch dir — it must be gone.
        assert "answer_file" not in captured["kwargs"]
        turn_file = os.path.join(answer_dir, "turn_prompt.md")
        answer_file = os.path.join(answer_dir, "answer.md")
        # Single line still.
        assert "\n" not in submit
        # Read-turn-file instruction.
        assert "Read the file" in submit
        assert turn_file in submit
        # Unique per-turn scratch DIR (turn_<hex>), under session_dir.
        assert os.path.basename(answer_dir).startswith("turn_")
        assert os.path.dirname(answer_dir) == str(session_dir)
        assert os.path.isabs(answer_dir)
        # Answer target is a SHORT, FIXED name in that dir (mangle-robust), NOT
        # a 32-hex filename Claude could drop characters from.
        assert os.path.basename(answer_file) == "answer.md"
        # Write-answer-file instruction (Defect 2), naming the short file.
        assert answer_file in submit
        # FINDING 3 (Luna): the trigger asks for the COMPLETE final response, not
        # a terse stub — important results / file changes / errors / next steps.
        assert "COMPLETE" in submit
        assert "important results" in submit

    def test_answer_dir_unique_per_call(
        self, monkeypatch, tmp_path, interactive_env
    ):
        """Freshness guarantee: each interactive turn must build a DISTINCT
        scratch directory. ``session_dir`` is persistent per-tenant and reused
        every turn; a unique ``turn_<hex>/`` dir means any answer file the runner
        globs out of it is guaranteed to be THIS turn's — never a prior turn's
        leftover. Two calls → two different unique dirs."""
        self._patch_credential(monkeypatch)
        session_dir = tmp_path / "session"
        session_dir.mkdir()

        dirs: list[str] = []

        def fake_run(cmd, **kwargs):
            dirs.append(kwargs.get("answer_dir"))
            return _completed(0, stdout="ok")

        monkeypatch.setattr(
            claude_interactive, "run_claude_interactive_with_heartbeat", fake_run
        )

        task = _make_input(instruction_md_content="PERSONA", message="hi")
        wf._execute_claude_chat(task, session_dir=str(session_dir))
        wf._execute_claude_chat(task, session_dir=str(session_dir))

        assert len(dirs) == 2
        assert dirs[0] != dirs[1], "scratch dir must be unique per turn"
        for d in dirs:
            assert os.path.basename(d).startswith("turn_")
            assert os.path.dirname(d) == str(session_dir)
            assert os.path.isdir(d)

    def test_turn_prompt_and_claude_md_written_0600(
        self, monkeypatch, tmp_path, interactive_env
    ):
        """N2: the turn blob (persona + conversation history) is secret-grade,
        so ``turn_prompt.md`` (now inside the per-turn scratch dir) and
        ``CLAUDE.md`` must be mode 0o600. The scratch dir itself is 0o700."""
        import stat

        self._patch_credential(monkeypatch)
        session_dir = tmp_path / "session"
        session_dir.mkdir()

        captured: dict = {}

        def fake_run(cmd, **kwargs):
            captured["kwargs"] = kwargs
            return _completed(0, stdout="ok")

        monkeypatch.setattr(
            claude_interactive, "run_claude_interactive_with_heartbeat", fake_run
        )

        task = _make_input(
            instruction_md_content="PERSONA: Luna",
            message="secret history",
        )
        wf._execute_claude_chat(task, session_dir=str(session_dir))

        answer_dir = captured["kwargs"]["answer_dir"]
        # Scratch dir is 0o700 (only the worker user can list it).
        assert stat.S_IMODE(os.stat(answer_dir).st_mode) == 0o700
        # The turn blob lives in the scratch dir; CLAUDE.md under session_dir.
        for p in (
            os.path.join(answer_dir, "turn_prompt.md"),
            str(session_dir / "CLAUDE.md"),
        ):
            assert os.path.isfile(p), p
            mode = stat.S_IMODE(os.stat(p).st_mode)
            assert mode == 0o600, f"{p} mode is {oct(mode)}"

    def test_interactive_env_disables_startup_chrome(
        self, monkeypatch, tmp_path, interactive_env
    ):
        """Prong 1: on the interactive path the subprocess env must DISABLE the
        startup chrome that floods the PTY on a cold HOME and starves the submit
        — the auto-updater, the official-marketplace auto-install, and the broad
        non-essential traffic (telemetry / error-reporting / bug command). These
        are the continuous-output sources that reset the quiet-settle timer and
        caused intermittent exit-143. Print mode must NOT get these (verified in
        the print-mode test)."""
        self._patch_credential(monkeypatch)
        session_dir = tmp_path / "session"
        session_dir.mkdir()

        captured: dict = {}

        def fake_run(cmd, **kwargs):
            captured["env"] = kwargs.get("env")
            return _completed(0, stdout="ok")

        monkeypatch.setattr(
            claude_interactive, "run_claude_interactive_with_heartbeat", fake_run
        )

        task = _make_input(instruction_md_content="PERSONA", message="hi")
        wf._execute_claude_chat(task, session_dir=str(session_dir))

        env = captured["env"]
        assert env["DISABLE_AUTOUPDATER"] == "1"
        assert env["CLAUDE_CODE_DISABLE_OFFICIAL_MARKETPLACE_AUTOINSTALL"] == "1"
        assert env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"

    def test_onboarding_seed_covers_resolved_cwd_trust_flags(
        self, monkeypatch, tmp_path, interactive_env
    ):
        """Prong 1: the cold folder-trust dialog still blocked despite the prior
        seed because the seed used the unresolved cwd. Claude v2.1.x keys the
        trust check on the REALPATH of cwd (``getProjectPathForConfig`` →
        ``path.resolve``/realpath). Seed every trust flag it checks under the
        RESOLVED cwd key: ``hasTrustDialogAccepted``,
        ``hasCompletedProjectOnboarding``, ``projectOnboardingSeenCount`` (>0).
        Use a symlinked HOME+cwd so resolved != literal, proving we key on the
        real path."""
        import json as _json

        self._patch_credential(monkeypatch)
        # Real dirs + a symlink that points at them, so realpath(symlink)!=symlink.
        real_home = tmp_path / "real_home"
        real_home.mkdir()
        real_cwd = tmp_path / "real_cwd"
        real_cwd.mkdir()
        link_cwd = tmp_path / "link_cwd"
        link_cwd.symlink_to(real_cwd)

        session_dir = tmp_path / "session"
        session_dir.mkdir()

        # Force HOME to our real_home and cwd to the SYMLINK path.
        monkeypatch.setattr(
            cli_runtime, "tenant_home_dir", lambda tid: real_home
        )
        monkeypatch.setattr(
            cli_runtime, "resolve_cli_cwd", lambda task, fb: str(link_cwd)
        )

        captured: dict = {}

        def fake_run(cmd, **kwargs):
            captured["env"] = kwargs.get("env")
            return _completed(0, stdout="ok")

        monkeypatch.setattr(
            claude_interactive, "run_claude_interactive_with_heartbeat", fake_run
        )

        task = _make_input(instruction_md_content="PERSONA", message="hi")
        wf._execute_claude_chat(task, session_dir=str(session_dir))

        cfg = _json.loads((real_home / ".claude.json").read_text())
        projects = cfg.get("projects", {})
        resolved = os.path.realpath(str(link_cwd))
        assert resolved in projects, (
            f"trust seed must key on the RESOLVED cwd {resolved}; "
            f"got keys {list(projects)}"
        )
        proj = projects[resolved]
        assert proj.get("hasTrustDialogAccepted") is True
        assert proj.get("hasCompletedProjectOnboarding") is True
        assert isinstance(proj.get("projectOnboardingSeenCount"), int)
        assert proj["projectOnboardingSeenCount"] >= 1

    def test_print_mode_unchanged_appends_minus_p_and_no_turn_file(
        self, monkeypatch, tmp_path
    ):
        """Print path (default execution mode) must stay byte-identical:
        ``-p <blob>`` appended, NO turn_prompt.md written, runner is the
        non-interactive cli_runtime path."""
        monkeypatch.setenv("CLAUDE_CODE_EXECUTION_MODE", "print")
        self._patch_credential(monkeypatch)
        session_dir = tmp_path / "session"
        session_dir.mkdir()

        captured: dict = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["env"] = kwargs.get("env")
            return _completed(0, stdout='{"result": "hi"}')

        monkeypatch.setattr(cli_runtime, "run_cli_with_heartbeat", fake_run)
        # Interactive runner must NOT be called on the print path.
        monkeypatch.setattr(
            claude_interactive,
            "run_claude_interactive_with_heartbeat",
            lambda *a, **k: pytest.fail("interactive runner called on print path"),
        )

        task = _make_input(
            instruction_md_content="SYS",
            message="hello",
        )
        out = wf._execute_claude_chat(task, session_dir=str(session_dir))

        assert out.success is True
        cmd = captured["cmd"]
        assert "-p" in cmd
        p_idx = cmd.index("-p")
        # The blob is the positional arg right after -p.
        assert cmd[p_idx + 1] == "SYS\n\n# User Request\n\nhello"
        assert "--no-session-persistence" in cmd
        # No turn file or answer file in print mode (Defect 2 is interactive-only).
        assert not (session_dir / "turn_prompt.md").exists()
        assert not (session_dir / "answer.md").exists()
        # Print mode stays byte-identical: the interactive-only chrome-disable
        # env vars (Prong 1) must NOT be injected on the print path.
        env = captured["env"]
        assert "DISABLE_AUTOUPDATER" not in env
        assert "CLAUDE_CODE_DISABLE_OFFICIAL_MARKETPLACE_AUTOINSTALL" not in env
        assert "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC" not in env


# ════════════════════════════════════════════════════════════════════════
# Change 2a — clean_interactive_transcript tightening
# ════════════════════════════════════════════════════════════════════════
class TestCleanInteractiveTranscript:
    def test_strips_trigger_echo_pasted_placeholder_and_read_chrome(self):
        trigger = (
            "Read the file /scratch/turn_prompt.md and respond to the user "
            "request it contains. Reply directly — do not ask for confirmation."
        )
        raw = (
            "Welcome to Claude Code\n"
            f"> {trigger}\n"
            "[Pasted text +42 lines]\n"
            "⏺ Read(/scratch/turn_prompt.md)\n"
            "  ⎿ Read 120 lines\n"
            "The answer is 4.\n"
            "It is a simple sum.\n"
            "/exit\n"
        )
        out = clean_interactive_transcript(raw, trigger)

        assert "The answer is 4." in out
        assert "It is a simple sum." in out
        # Trigger echo gone.
        assert "Read the file /scratch/turn_prompt.md" not in out
        # Pasted-text placeholder gone.
        assert "[Pasted text" not in out
        # Read tool chrome gone.
        assert "Read(/scratch/turn_prompt.md)" not in out
        assert "Read 120 lines" not in out

    def test_preserves_answer_when_no_chrome(self):
        out = clean_interactive_transcript("Just the answer here.\n", "")
        assert out == "Just the answer here."

    def test_pasted_placeholder_dropped_regardless_of_count(self):
        raw = "[Pasted text +1 lines]\nReal reply.\n"
        out = clean_interactive_transcript(raw, "")
        assert "[Pasted text" not in out
        assert "Real reply." in out

    def test_never_raises_on_garbage(self):
        # Defensive contract — best-effort, never raises.
        out = clean_interactive_transcript("\x1b[0m\x00garbage\r\n", "trigger")
        assert isinstance(out, str)

    # ── I2: wrap-tolerant trigger-echo strip ─────────────────────────────
    def test_strips_wrapped_trigger_echo_across_multiple_lines(self):
        """When the PTY is narrow (e.g. an 80-col fallback) the ~185-char
        trigger echo wraps onto several physical rows, so the old exact-match
        strip leaks it. The cleaner must drop each wrapped fragment while
        preserving the real answer line."""
        trigger = (
            "Read the file /scratch/turn_prompt.md and respond to the user "
            "request it contains. Reply directly — do not ask for confirmation."
        )
        # Simulate an 80-col wrap: the single trigger split across 3 rows.
        raw = (
            "> Read the file /scratch/turn_prompt.md and respond to the user\n"
            "request it contains. Reply directly — do not ask for\n"
            "confirmation.\n"
            "The answer is 4.\n"
        )
        out = clean_interactive_transcript(raw, trigger)
        assert "The answer is 4." in out
        # No fragment of the wrapped trigger survives.
        assert "Read the file /scratch/turn_prompt.md" not in out
        assert "request it contains" not in out
        assert "do not ask for" not in out

    def test_wrap_strip_preserves_short_answer_fragments(self):
        """Wrap-tolerant stripping must NOT eat a legit short answer that
        happens to share a couple of words with the trigger."""
        trigger = (
            "Read the file /scratch/turn_prompt.md and respond to the user "
            "request it contains. Reply directly — do not ask for confirmation."
        )
        raw = "Read it.\nThe file is fine.\n"
        out = clean_interactive_transcript(raw, trigger)
        assert "The file is fine." in out

    # ── I3: _READ_RESULT_RE must require the tool gutter glyph ────────────
    def test_strips_gutter_read_result_line(self):
        raw = "⎿ Read 120 lines\nThe answer is 4.\n"
        out = clean_interactive_transcript(raw, "")
        assert "Read 120 lines" not in out
        assert "The answer is 4." in out

    def test_preserves_prose_starting_with_reading(self):
        """A prose answer that begins 'Reading…' has no gutter glyph and must
        survive (regression: the old `ing\\b` branch deleted it)."""
        raw = "Reading the logs, I found three errors:\n- one\n- two\n"
        out = clean_interactive_transcript(raw, "")
        assert "Reading the logs, I found three errors:" in out
        assert "- one" in out


# ════════════════════════════════════════════════════════════════════════
# Change 2b — runner submit decision (pure helper)
# ════════════════════════════════════════════════════════════════════════
class TestDecidePtyAction:
    """``decide_pty_action`` is the pure state-machine helper the PTY loop
    drives. It decides, per tick, whether to submit the trigger, send
    ``/exit``, SIGKILL, or keep waiting — without touching real file
    descriptors, so it is unit-testable."""

    def test_waits_before_first_output(self):
        action = decide_pty_action(
            now=0.5,
            start=0.0,
            last_output=0.0,
            seen_output=False,
            submitted=False,
            response_seen=False,
            exit_sent_at=None,
            first_output_seconds=90.0,
            submit_settle_seconds=1.0,
            idle_exit_seconds=8.0,
            exit_grace_seconds=10.0,
        )
        assert action == "wait"

    def test_sigkill_if_no_banner_within_first_output_cap(self):
        action = decide_pty_action(
            now=95.0,
            start=0.0,
            last_output=0.0,
            seen_output=False,
            submitted=False,
            response_seen=False,
            exit_sent_at=None,
            first_output_seconds=90.0,
            submit_settle_seconds=1.0,
            idle_exit_seconds=8.0,
            exit_grace_seconds=10.0,
        )
        assert action == "kill"

    def test_does_not_submit_until_settle_elapsed(self):
        # Banner seen at t=1.0; only 0.4s of quiet — under the 1.0s settle.
        action = decide_pty_action(
            now=1.4,
            start=0.0,
            last_output=1.0,
            seen_output=True,
            submitted=False,
            response_seen=False,
            exit_sent_at=None,
            first_output_seconds=90.0,
            submit_settle_seconds=1.0,
            idle_exit_seconds=8.0,
            exit_grace_seconds=10.0,
        )
        assert action == "wait"

    def test_submits_after_settle(self):
        # Banner seen at t=1.0; 1.2s of quiet since — settle satisfied. Phase 1
        # of the two-phase submit types the trigger TEXT first (Defect 1).
        action = decide_pty_action(
            now=2.2,
            start=0.0,
            last_output=1.0,
            seen_output=True,
            submitted=False,
            response_seen=False,
            exit_sent_at=None,
            first_output_seconds=90.0,
            submit_settle_seconds=1.0,
            idle_exit_seconds=8.0,
            exit_grace_seconds=10.0,
        )
        assert action == "submit_text"

    def test_idle_exit_suppressed_after_submit_until_response(self):
        # Submitted, but Claude has not yet responded; do NOT /exit on idle.
        # (resend window pushed past the horizon so this isolates the idle-exit
        # suppression behavior — the resend path is covered separately.)
        action = decide_pty_action(
            now=20.0,
            start=0.0,
            last_output=2.0,  # 18s quiet, well past idle_exit
            seen_output=True,
            submitted=True,
            response_seen=False,
            exit_sent_at=None,
            submitted_at=2.0,
            first_output_seconds=90.0,
            submit_settle_seconds=1.0,
            idle_exit_seconds=8.0,
            exit_grace_seconds=10.0,
            resend_after_seconds=60.0,
        )
        assert action == "wait"

    def test_sigkill_if_no_response_within_cap_after_submit(self):
        # Submitted at ~t=2; now t=95, no post-submit output → give up.
        action = decide_pty_action(
            now=95.0,
            start=0.0,
            last_output=2.0,
            seen_output=True,
            submitted=True,
            response_seen=False,
            exit_sent_at=None,
            submitted_at=2.0,
            first_output_seconds=90.0,
            submit_settle_seconds=1.0,
            idle_exit_seconds=8.0,
            exit_grace_seconds=10.0,
        )
        assert action == "kill"

    def test_post_submit_freeze_cap_kills_before_cold_cap(self):
        # STARTUP FREEZE: submitted at t=2, now t=40 (38s of dead silence post-
        # submit), no output at all. With a SHORT post-submit cap (35s) the
        # frozen launch is declared dead at 35s — far sooner than the 90s cold
        # cap — so the caller can relaunch a fresh process. resend already spent.
        action = decide_pty_action(
            now=40.0,
            start=0.0,
            last_output=2.0,
            seen_output=True,
            submitted=True,
            response_seen=False,
            exit_sent_at=None,
            submitted_at=2.0,
            first_output_seconds=90.0,
            submit_settle_seconds=1.0,
            idle_exit_seconds=8.0,
            exit_grace_seconds=10.0,
            resent=True,
            post_submit_first_output_seconds=35.0,
        )
        assert action == "kill"

    def test_post_submit_short_cap_falls_back_to_cold_cap_when_none(self):
        # PURE-FUNCTION seam: when ``post_submit_first_output_seconds`` is None,
        # ``decide_pty_action`` falls back to ``first_output_seconds`` (90s), so at
        # 38s dead-silence it keeps waiting. NOTE: in PRODUCTION the runner rewrites
        # None → the env default (35s) BEFORE calling this, so the 35s freeze cap
        # is live by design — this test pins the fallback contract, not prod
        # behavior. resend pushed out so this isolates the cap, not the resend.
        action = decide_pty_action(
            now=40.0,
            start=0.0,
            last_output=2.0,
            seen_output=True,
            submitted=True,
            response_seen=False,
            exit_sent_at=None,
            submitted_at=2.0,
            first_output_seconds=90.0,
            submit_settle_seconds=1.0,
            idle_exit_seconds=8.0,
            exit_grace_seconds=10.0,
            resend_after_seconds=200.0,
        )
        assert action == "wait"

    def test_post_submit_cap_still_resends_first(self):
        # The one-shot resend fires within the short cap window (15s < 35s), so a
        # submit eaten by a prompt is recovered before the freeze kill. At t=20
        # (18s post-submit, not yet resent) → resend, not kill.
        action = decide_pty_action(
            now=20.0,
            start=0.0,
            last_output=2.0,
            seen_output=True,
            submitted=True,
            response_seen=False,
            exit_sent_at=None,
            submitted_at=2.0,
            first_output_seconds=90.0,
            submit_settle_seconds=1.0,
            idle_exit_seconds=8.0,
            exit_grace_seconds=10.0,
            resend_after_seconds=15.0,
            resent=False,
            response_substantive=False,
            post_submit_first_output_seconds=35.0,
        )
        assert action == "resend"

    def test_answer_ready_short_circuits_freeze_gate_when_response_seen_false(self):
        # Codex IMPORTANT 3: ``response_seen`` is trust-filtered, so a REAL reply
        # whose first chunk contains a trust-word ("do you trust", "trust this
        # folder") never flips it. If that reply ALSO wrote its answer file, the
        # freeze gate (section 3) must YIELD to ``answer_ready`` and exit cleanly —
        # NOT kill it at the 35s post-submit cap. submitted t=2, now t=50 (past the
        # cap), response_seen False, answer_ready True + settled.
        action = decide_pty_action(
            now=50.0,
            start=0.0,
            last_output=40.0,
            seen_output=True,
            submitted=True,
            response_seen=False,
            exit_sent_at=None,
            submitted_at=2.0,
            first_output_seconds=90.0,
            submit_settle_seconds=1.0,
            idle_exit_seconds=8.0,
            exit_grace_seconds=10.0,
            answer_ready=True,
            answer_ready_at=45.0,  # 5s settled, past answer_settle_seconds (0.25)
            awaiting_answer_file=True,
            post_submit_first_output_seconds=35.0,
            resent=True,
        )
        assert action == "exit"

    def test_idle_exit_after_response_seen(self):
        # Legacy idle-/exit path (FINDING 2): response seen, the answer file
        # never arrived, AND we are PAST the bounded fallback cap since submit →
        # /exit (→ scraped-transcript fallback). Before the cap this would WAIT
        # to avoid killing the turn pre-write; covered separately.
        action = decide_pty_action(
            now=100.0,
            start=0.0,
            last_output=91.0,  # 9s of quiet
            seen_output=True,
            submitted=True,
            response_seen=True,
            exit_sent_at=None,
            submitted_at=2.0,  # 98s since submit — past the 90s fallback cap
            first_output_seconds=90.0,
            submit_settle_seconds=1.0,
            idle_exit_seconds=8.0,
            exit_grace_seconds=10.0,
        )
        assert action == "exit"

    def test_keeps_waiting_while_response_streams(self):
        # Response seen, but only 2s quiet — under idle_exit; keep reading.
        action = decide_pty_action(
            now=13.0,
            start=0.0,
            last_output=11.0,
            seen_output=True,
            submitted=True,
            response_seen=True,
            exit_sent_at=None,
            first_output_seconds=90.0,
            submit_settle_seconds=1.0,
            idle_exit_seconds=8.0,
            exit_grace_seconds=10.0,
        )
        assert action == "wait"

    def test_sigterm_after_exit_grace(self):
        # /exit already sent; grace window elapsed → escalate to SIGTERM.
        action = decide_pty_action(
            now=30.0,
            start=0.0,
            last_output=11.0,
            seen_output=True,
            submitted=True,
            response_seen=True,
            exit_sent_at=18.0,
            first_output_seconds=90.0,
            submit_settle_seconds=1.0,
            idle_exit_seconds=8.0,
            exit_grace_seconds=10.0,
        )
        assert action == "terminate"

    # ── Chrome-flood: input-box path must fire under SUSTAINED output ─────
    # Root cause of intermittent exit-143 (2026-05-30): on a cold/perturbed
    # HOME, Claude Code floods the PTY with continuous chrome (auto-updater,
    # marketplace auto-install, folder-trust dialog, "1 MCP server failed").
    # The flood NEVER quiets, so neither the quiet-settle nor (because the loop
    # kept reading + ``continue``-ing instead of deciding) the bounded ceiling
    # fired — the trigger was NEVER submitted. The durable fix: once the input
    # box is seen, submit after a short FIXED delay since it first rendered,
    # regardless of ongoing output.
    def test_submits_under_sustained_output_after_input_box_fixed_delay(self):
        """Input box rendered + a short FIXED delay elapsed since it appeared →
        ``submit_text`` EVEN WITH zero quiet (output still streaming). The quiet
        window is intentionally NOT required — the chrome flood defeats it."""
        action = decide_pty_action(
            now=2.5,
            start=0.0,
            last_output=2.49,  # ~0s quiet — output is STILL streaming (flood)
            seen_output=True,
            submitted=False,
            response_seen=False,
            exit_sent_at=None,
            first_output_seconds=90.0,
            submit_settle_seconds=1.0,
            idle_exit_seconds=8.0,
            exit_grace_seconds=10.0,
            input_box_seen=True,
            input_box_seen_at=1.0,  # box appeared at t=1.0; 1.5s ago
            first_output_at=0.4,
            input_box_submit_delay_seconds=1.0,
        )
        assert action == "submit_text"

    def test_no_submit_before_input_box_fixed_delay_under_flood(self):
        """Before the FIXED delay since the input box appeared, keep waiting even
        under sustained output (don't type into a box that just rendered)."""
        action = decide_pty_action(
            now=1.4,
            start=0.0,
            last_output=1.39,  # streaming
            seen_output=True,
            submitted=False,
            response_seen=False,
            exit_sent_at=None,
            first_output_seconds=90.0,
            submit_settle_seconds=1.0,
            idle_exit_seconds=8.0,
            exit_grace_seconds=10.0,
            input_box_seen=True,
            input_box_seen_at=1.0,  # only 0.4s ago — under the 1.0s fixed delay
            first_output_at=0.4,
            input_box_submit_delay_seconds=1.0,
        )
        assert action == "wait"

    # ── Post-submit resend: recover a submit eaten by a trust/update prompt ─
    def test_resend_when_no_answer_and_no_response_after_resend_window(self):
        """Submitted, but NO answer file AND no substantive response within
        ``resend_after_seconds`` → ``resend`` (re-type the trigger once). This
        recovers a submit consumed by a trust/auto-update prompt."""
        action = decide_pty_action(
            now=20.0,
            start=0.0,
            last_output=4.0,
            seen_output=True,
            submitted=True,
            response_seen=False,  # nothing substantive came back
            exit_sent_at=None,
            submitted_at=2.0,  # 18s since submit — past the 15s resend window
            first_output_seconds=90.0,
            submit_settle_seconds=1.0,
            idle_exit_seconds=8.0,
            exit_grace_seconds=10.0,
            answer_ready=False,
            awaiting_answer_file=True,
            resend_after_seconds=15.0,
            resent=False,
        )
        assert action == "resend"

    def test_no_resend_before_resend_window(self):
        """Under the resend window since submit → keep waiting, don't resend."""
        action = decide_pty_action(
            now=10.0,
            start=0.0,
            last_output=4.0,
            seen_output=True,
            submitted=True,
            response_seen=False,
            exit_sent_at=None,
            submitted_at=2.0,  # only 8s since submit — under the 15s window
            first_output_seconds=90.0,
            submit_settle_seconds=1.0,
            idle_exit_seconds=8.0,
            exit_grace_seconds=10.0,
            answer_ready=False,
            awaiting_answer_file=True,
            resend_after_seconds=15.0,
            resent=False,
        )
        assert action == "wait"

    def test_resend_fires_only_once(self):
        """The resend is capped at 1 — once ``resent`` is True, never resend
        again (fall through to the normal waiting/idle path instead)."""
        action = decide_pty_action(
            now=40.0,
            start=0.0,
            last_output=4.0,
            seen_output=True,
            submitted=True,
            response_seen=False,
            exit_sent_at=None,
            submitted_at=2.0,  # 38s since submit, well past the window
            first_output_seconds=90.0,
            submit_settle_seconds=1.0,
            idle_exit_seconds=8.0,
            exit_grace_seconds=10.0,
            answer_ready=False,
            awaiting_answer_file=True,
            resend_after_seconds=15.0,
            resent=True,  # already resent once
        )
        assert action != "resend"

    def test_no_resend_once_response_seen(self):
        """A substantive response IS streaming — do NOT resend (the submit took);
        the answer-file gate governs from here."""
        action = decide_pty_action(
            now=30.0,
            start=0.0,
            last_output=12.0,
            seen_output=True,
            submitted=True,
            response_seen=True,  # Claude is responding
            exit_sent_at=None,
            submitted_at=2.0,  # 28s since submit, past the resend window
            first_output_seconds=90.0,
            submit_settle_seconds=1.0,
            idle_exit_seconds=8.0,
            exit_grace_seconds=10.0,
            answer_ready=False,
            awaiting_answer_file=True,
            resend_after_seconds=15.0,
            resent=False,
        )
        assert action != "resend"

    # ── N1: readiness must not be starved by a chatty banner ─────────────
    def test_submits_quickly_when_input_box_seen(self):
        """Input-box marker seen → phase-1 ``submit_text`` after only a BRIEF
        settle, even if the chatty banner keeps the full quiet-settle from
        elapsing."""
        action = decide_pty_action(
            now=1.6,
            start=0.0,
            last_output=1.4,  # only 0.2s quiet — under the 1.0s full settle
            seen_output=True,
            submitted=False,
            response_seen=False,
            exit_sent_at=None,
            first_output_seconds=90.0,
            submit_settle_seconds=1.0,
            idle_exit_seconds=8.0,
            exit_grace_seconds=10.0,
            input_box_seen=True,
            first_output_at=0.4,
        )
        assert action == "submit_text"

    def test_input_box_seen_still_needs_brief_settle(self):
        """Even with the input-box marker, a still-streaming box (zero quiet)
        should wait a brief settle before typing."""
        action = decide_pty_action(
            now=1.41,
            start=0.0,
            last_output=1.4,  # ~0.01s quiet — under the brief settle
            seen_output=True,
            submitted=False,
            response_seen=False,
            exit_sent_at=None,
            first_output_seconds=90.0,
            submit_settle_seconds=1.0,
            idle_exit_seconds=8.0,
            exit_grace_seconds=10.0,
            input_box_seen=True,
            first_output_at=0.4,
        )
        assert action == "wait"

    def test_submits_on_bounded_ceiling_when_banner_never_quiets(self):
        """No input-box marker AND the banner emits faster than the full
        settle forever → the bounded ceiling since first output forces a
        submit so the turn isn't starved ~90s."""
        # first output at t=0.4; ceiling = max(1.0*3, 5.0) = 5.0 → fires at 5.4.
        action = decide_pty_action(
            now=5.5,
            start=0.0,
            last_output=5.2,  # 0.3s quiet — under full 1.0s settle, never quiets
            seen_output=True,
            submitted=False,
            response_seen=False,
            exit_sent_at=None,
            first_output_seconds=90.0,
            submit_settle_seconds=1.0,
            idle_exit_seconds=8.0,
            exit_grace_seconds=10.0,
            input_box_seen=False,
            first_output_at=0.4,
        )
        assert action == "submit_text"

    def test_no_ceiling_submit_before_ceiling_elapses(self):
        """Before the bounded ceiling, with no input-box marker and a chatty
        banner, keep waiting (don't submit prematurely)."""
        action = decide_pty_action(
            now=3.0,
            start=0.0,
            last_output=2.8,  # 0.2s quiet — under settle; ceiling (5.0) not hit
            seen_output=True,
            submitted=False,
            response_seen=False,
            exit_sent_at=None,
            first_output_seconds=90.0,
            submit_settle_seconds=1.0,
            idle_exit_seconds=8.0,
            exit_grace_seconds=10.0,
            input_box_seen=False,
            first_output_at=0.4,
        )
        assert action == "wait"

    # ── Defect 1: two-phase submit (text first, then Enter alone) ─────────
    # The REPL runs bracketed-paste mode; a long trigger glued to ``\r`` is
    # absorbed as paste and the ``\r`` becomes a literal newline, never Enter.
    # So readiness now yields ``submit_text`` (type the text), and only after
    # ``enter_delay_seconds`` of settle do we get ``submit_enter`` (the ``\r``).
    def test_ready_returns_submit_text_not_submit(self):
        """Once the input box is up + settled, the FIRST action is to write the
        trigger TEXT — never a glued text+Enter ``submit``."""
        action = decide_pty_action(
            now=1.6,
            start=0.0,
            last_output=1.4,
            seen_output=True,
            submitted=False,
            response_seen=False,
            exit_sent_at=None,
            first_output_seconds=90.0,
            submit_settle_seconds=1.0,
            idle_exit_seconds=8.0,
            exit_grace_seconds=10.0,
            input_box_seen=True,
            first_output_at=0.4,
            text_written=False,
            enter_delay_seconds=0.5,
        )
        assert action == "submit_text"

    def test_submit_enter_only_after_enter_delay(self):
        """After the text is written, the bare ``\\r`` (``submit_enter``) is
        withheld until ``enter_delay_seconds`` elapse — the settle that lets the
        REPL leave paste mode before Enter fires."""
        # Text written at t=2.0; only 0.3s later — under the 0.5s enter delay.
        action = decide_pty_action(
            now=2.3,
            start=0.0,
            last_output=2.0,
            seen_output=True,
            submitted=False,
            response_seen=False,
            exit_sent_at=None,
            first_output_seconds=90.0,
            submit_settle_seconds=1.0,
            idle_exit_seconds=8.0,
            exit_grace_seconds=10.0,
            input_box_seen=True,
            first_output_at=0.4,
            text_written=True,
            text_written_at=2.0,
            enter_delay_seconds=0.5,
        )
        assert action == "wait"

    def test_submit_enter_fires_after_enter_delay_elapsed(self):
        """Once ``enter_delay_seconds`` have passed since the text write, send
        the bare ``\\r`` as ``submit_enter``."""
        # Text written at t=2.0; now t=2.6 — 0.6s ≥ 0.5s enter delay.
        action = decide_pty_action(
            now=2.6,
            start=0.0,
            last_output=2.0,
            seen_output=True,
            submitted=False,
            response_seen=False,
            exit_sent_at=None,
            first_output_seconds=90.0,
            submit_settle_seconds=1.0,
            idle_exit_seconds=8.0,
            exit_grace_seconds=10.0,
            input_box_seen=True,
            first_output_at=0.4,
            text_written=True,
            text_written_at=2.0,
            enter_delay_seconds=0.5,
        )
        assert action == "submit_enter"

    def test_no_submit_text_before_readiness(self):
        """``submit_text`` must never fire before the banner is seen — typing
        into a not-yet-ready REPL drops the input."""
        action = decide_pty_action(
            now=0.5,
            start=0.0,
            last_output=0.0,
            seen_output=False,
            submitted=False,
            response_seen=False,
            exit_sent_at=None,
            first_output_seconds=90.0,
            submit_settle_seconds=1.0,
            idle_exit_seconds=8.0,
            exit_grace_seconds=10.0,
            text_written=False,
            enter_delay_seconds=0.5,
        )
        assert action == "wait"

    # ── FINDING 2: answer file is the completion signal ──────────────────
    # ``response_seen`` flips on the first post-submit byte (a ``Read(...)``
    # echo), which would arm the idle ``/exit`` BEFORE the answer file exists.
    # A quiet gap could then ``/exit`` and kill the turn pre-write. Fix: gate
    # completion on the answer file. When ``answer_ready`` (file present,
    # non-empty, size stable across a tick) → ``exit`` promptly. While NOT
    # ``answer_ready``, suppress the idle ``/exit`` until the bounded fallback
    # cap since submit (``first_output_seconds``), then fall through to the
    # existing idle path (→ scraped-transcript fallback).
    def test_exit_when_answer_ready_after_settle(self):
        """``answer_ready`` (file written + stable) → ``exit`` after the brief
        settle, even if the response only just started streaming (idle window
        not yet elapsed). The deliverable is in hand."""
        action = decide_pty_action(
            now=13.0,
            start=0.0,
            last_output=12.9,  # only 0.1s quiet — well under idle_exit
            seen_output=True,
            submitted=True,
            response_seen=True,
            exit_sent_at=None,
            submitted_at=2.0,
            first_output_seconds=90.0,
            submit_settle_seconds=1.0,
            idle_exit_seconds=8.0,
            exit_grace_seconds=10.0,
            answer_ready=True,
            answer_ready_at=12.6,  # ready 0.4s ago — past the brief settle
        )
        assert action == "exit"

    def test_answer_ready_waits_brief_settle_before_exit(self):
        """Just-appeared answer file (zero settle) waits a brief settle before
        ``exit`` — guards against exiting mid-flush of the answer file."""
        action = decide_pty_action(
            now=13.0,
            start=0.0,
            last_output=12.9,
            seen_output=True,
            submitted=True,
            response_seen=True,
            exit_sent_at=None,
            submitted_at=2.0,
            first_output_seconds=90.0,
            submit_settle_seconds=1.0,
            idle_exit_seconds=8.0,
            exit_grace_seconds=10.0,
            answer_ready=True,
            answer_ready_at=12.99,  # ready ~0.01s ago — under the brief settle
        )
        assert action == "wait"

    def test_no_idle_exit_while_waiting_for_answer_before_cap(self):
        """Response seen + idle window elapsed, but the answer file is NOT yet
        ready and we are still under the fallback cap since submit → keep
        WAITING, do NOT idle-``/exit`` (which would kill the turn pre-write).
        Requires ``awaiting_answer_file`` — the suppression only applies when an
        answer file is actually expected."""
        action = decide_pty_action(
            now=30.0,
            start=0.0,
            last_output=11.0,  # 19s quiet — way past the 8s idle window
            seen_output=True,
            submitted=True,
            response_seen=True,
            exit_sent_at=None,
            submitted_at=2.0,  # 28s since submit, under the 90s fallback cap
            first_output_seconds=90.0,
            submit_settle_seconds=1.0,
            idle_exit_seconds=8.0,
            exit_grace_seconds=10.0,
            answer_ready=False,
            awaiting_answer_file=True,
        )
        assert action == "wait"

    def test_idle_exit_falls_through_after_fallback_cap(self):
        """The answer file NEVER appears: once past the bounded fallback cap
        since submit, fall through to the existing idle-``/exit`` (→ scraped
        transcript fallback). No new hang path."""
        action = decide_pty_action(
            now=95.0,
            start=0.0,
            last_output=80.0,  # long idle
            seen_output=True,
            submitted=True,
            response_seen=True,
            exit_sent_at=None,
            submitted_at=2.0,  # 93s since submit, PAST the 90s fallback cap
            first_output_seconds=90.0,
            submit_settle_seconds=1.0,
            idle_exit_seconds=8.0,
            exit_grace_seconds=10.0,
            answer_ready=False,
            awaiting_answer_file=True,
        )
        assert action == "exit"

    def test_no_answer_file_expected_keeps_legacy_idle_exit(self):
        """When the caller passes NO answer file (``awaiting_answer_file``
        False), there's nothing to wait for — the legacy idle-``/exit`` applies
        immediately once the response is seen + idle window elapsed, even well
        under the fallback cap. This keeps the transcript-only path's timing."""
        action = decide_pty_action(
            now=20.0,
            start=0.0,
            last_output=11.0,  # 9s quiet — past the 8s idle window
            seen_output=True,
            submitted=True,
            response_seen=True,
            exit_sent_at=None,
            submitted_at=2.0,  # only 18s since submit — under the 90s cap
            first_output_seconds=90.0,
            submit_settle_seconds=1.0,
            idle_exit_seconds=8.0,
            exit_grace_seconds=10.0,
            awaiting_answer_file=False,
        )
        assert action == "exit"

    def test_sigterm_still_fires_after_exit_grace_with_answer_gate(self):
        """The existing kill caps still bound everything: once ``/exit`` is
        sent, SIGTERM escalation after the grace window is unchanged by the
        answer-file gate (no new hang path)."""
        action = decide_pty_action(
            now=30.0,
            start=0.0,
            last_output=11.0,
            seen_output=True,
            submitted=True,
            response_seen=True,
            exit_sent_at=18.0,
            first_output_seconds=90.0,
            submit_settle_seconds=1.0,
            idle_exit_seconds=8.0,
            exit_grace_seconds=10.0,
            answer_ready=True,
            answer_ready_at=12.0,
        )
        assert action == "terminate"


# ════════════════════════════════════════════════════════════════════════
# Change 2c — runner submit integration (fake PTY)
# ════════════════════════════════════════════════════════════════════════
class _FakePty:
    """A minimal fake of the os/pty/select/subprocess surface the runner
    uses, so we can assert WHAT bytes get written and WHEN, deterministically
    (monotonic time is faked, so no real sleeping)."""

    def __init__(
        self,
        script,
        exit_after_reads=None,
        short_write=False,
        answer_drop=None,
    ):
        # ``script`` is a list of byte chunks the PTY "emits" on successive
        # reads; an entry of None means "no data ready this tick".
        self._script = list(script)
        self.writes: list[bytes] = []
        self.write_times: list[float] = []
        self._t = 0.0
        self._closed = False
        self._exit_after_reads = exit_after_reads
        self._reads_done = 0
        self.master_fd = 11
        self.slave_fd = 12
        # When True, os.write only accepts the FIRST byte each call (simulates
        # a PTY short-write) so the drain helper (I1) must loop to deliver all.
        self._short_write = short_write
        self.ioctl_calls: list[tuple] = []
        # Defect 2: ``answer_drop`` is a ``(path, contents)`` pair. When the
        # first "long" write (the trigger text, >1 byte) lands we write
        # ``contents`` to ``path`` — simulating Claude reading the turn file and
        # writing its answer out-of-band.
        self._answer_drop = answer_drop
        self._answer_dropped = False
        # Drop the answer on the Nth multi-byte (trigger) write rather than the
        # first — lets a resend test prove the SECOND submit recovered the turn.
        self._answer_drop_on_nth = 1
        self._multibyte_writes = 0

    # time ----------------------------------------------------------------
    def monotonic(self):
        return self._t

    # pty -----------------------------------------------------------------
    def openpty(self):
        return self.master_fd, self.slave_fd

    # select --------------------------------------------------------------
    def select(self, rlist, wlist, xlist, timeout):
        # Advance fake time by the poll interval each tick.
        self._t += timeout if timeout else 0.05
        if self._script and self._script[0] is not None:
            return ([self.master_fd], [], [])
        # Not ready this tick — consume the leading ``None`` so the script
        # eventually advances to the next real chunk (the runner only calls
        # ``read`` when ``select`` reports ready, so ``read`` can't drain Nones).
        if self._script:
            self._script.pop(0)
        return ([], [], [])

    # os ------------------------------------------------------------------
    def read(self, fd, n):
        if self._script and self._script[0] is not None:
            chunk = self._script.pop(0)
            self._reads_done += 1
            return chunk
        return b""

    def write(self, fd, data):
        data = bytes(data)
        if len(data) > 1:
            self._multibyte_writes += 1
        # Defect 2: the trigger text is a multi-byte write — when the Nth such
        # write lands, drop the out-of-band answer file (Claude's Read + Write).
        # Default N=1 (the first trigger write); a resend test sets N=2 so the
        # answer only appears after the SECOND submit.
        if (
            self._answer_drop is not None
            and not self._answer_dropped
            and len(data) > 1
            and self._multibyte_writes >= self._answer_drop_on_nth
        ):
            path, contents = self._answer_drop
            with open(path, "w") as fh:
                fh.write(contents)
            self._answer_dropped = True
        if self._short_write and len(data) > 1:
            # Accept only the first byte; the drain helper must retry the rest.
            self.writes.append(data[:1])
            self.write_times.append(self._t)
            return 1
        self.writes.append(data)
        self.write_times.append(self._t)
        return len(data)

    def close(self, fd):
        self._closed = True

    def ioctl(self, fd, request, arg):
        # Record the TIOCSWINSZ payload (HHHH: rows, cols, x, y).
        self.ioctl_calls.append((fd, request, arg))
        return 0


class _FakeProc:
    def __init__(self, fake, poll_after_reads=None):
        self.pid = 4242
        self._fake = fake
        self._poll_after = poll_after_reads
        self.returncode = 0

    def poll(self):
        if self._poll_after is not None and self._fake._reads_done >= self._poll_after:
            return 0
        return None

    def wait(self, timeout=None):
        self.returncode = 0
        return 0


@pytest.fixture
def fake_pty_wiring(monkeypatch):
    """Patch the runner's pty/os/select/subprocess/time surface with fakes."""
    def _apply(script, poll_after_reads=None, short_write=False, answer_drop=None):
        fake = _FakePty(script, short_write=short_write, answer_drop=answer_drop)
        proc = _FakeProc(fake, poll_after_reads=poll_after_reads)
        captured: dict = {}

        def _popen(*a, **k):
            captured["env"] = k.get("env")
            return proc

        monkeypatch.setattr(claude_interactive.time, "monotonic", fake.monotonic)
        monkeypatch.setattr(claude_interactive.pty, "openpty", fake.openpty)
        monkeypatch.setattr(claude_interactive.select, "select", fake.select)
        monkeypatch.setattr(claude_interactive.os, "read", fake.read)
        monkeypatch.setattr(claude_interactive.os, "write", fake.write)
        monkeypatch.setattr(claude_interactive.os, "close", fake.close)
        monkeypatch.setattr(claude_interactive.fcntl, "ioctl", fake.ioctl)
        monkeypatch.setattr(
            claude_interactive.os, "getpgid", lambda pid: pid
        )
        monkeypatch.setattr(
            claude_interactive.os, "killpg", lambda pgid, sig: None
        )
        monkeypatch.setattr(
            claude_interactive.subprocess, "Popen", _popen
        )
        fake.popen_capture = captured
        return fake, proc

    return _apply


class TestRunnerSubmitsTrigger:
    def test_types_trigger_text_then_enter_separately(self, fake_pty_wiring):
        """Defect 1: the trigger TEXT and the Enter (``\\r``) must be SEPARATE
        writes — a glued ``text+\\r`` is absorbed by bracketed-paste mode and
        never submits. The text write must NOT carry a trailing ``\\r``, and a
        bare ``\\r`` write must follow it."""
        trigger = "Read the file /scratch/turn_prompt.md and respond."
        # banner, then quiet (None ticks) to satisfy settle + enter-delay, then
        # the post-submit answer, then quiet until idle /exit fires.
        script = [
            b"Welcome to Claude Code\n",  # banner (read 1)
            None, None, None, None, None, None, None, None,  # settle + enter delay
            b"The answer is 4.\n",         # post-submit response (read 2)
            None, None, None, None, None, None, None, None, None,  # idle
            None, None, None, None, None, None,
        ]
        fake, proc = fake_pty_wiring(script)

        result = claude_interactive.run_claude_interactive_with_heartbeat(
            ["claude"],
            prompt=trigger,
            label="Claude Code",
            timeout=1500,
            env={},
            cwd="/tmp",
            submit_settle_seconds=0.2,
            enter_delay_seconds=0.1,
            idle_exit_seconds=0.5,
            exit_grace_seconds=0.5,
            first_output_seconds=90.0,
        )

        # The trigger TEXT was typed exactly once, WITHOUT a trailing \r.
        text_writes = [w for w in fake.writes if trigger.encode() in w]
        assert text_writes, f"trigger text never typed; writes={fake.writes!r}"
        assert len(text_writes) == 1
        assert not text_writes[0].endswith(b"\r"), (
            f"text must not be glued to \\r; got {text_writes[0]!r}"
        )
        # A bare \r (Enter) was written on its own, AFTER the text.
        text_idx = fake.writes.index(text_writes[0])
        enter_writes = [
            i for i, w in enumerate(fake.writes) if w == b"\r" and i > text_idx
        ]
        assert enter_writes, f"bare \\r (Enter) never written; writes={fake.writes!r}"
        # An /exit was eventually sent (idle after the response).
        assert any(b"/exit" in w for w in fake.writes)
        # The answer survives cleaning (transcript fallback — no answer file).
        assert "The answer is 4." in result.stdout

    def test_submits_trigger_under_chrome_flood(self, fake_pty_wiring):
        """ROOT CAUSE regression: a CONTINUOUS chrome flood (auto-updater /
        marketplace / trust / "1 MCP server failed") that NEVER quiets must
        STILL get the trigger submitted via the input-box path. The old loop
        kept reading + ``continue``-ing under sustained output, so the submit
        decision was never reached and the trigger was never typed → exit 143.

        The fake emits the input-box marker then a non-stop stream of chrome
        chunks (no ``None`` quiet ticks at all). The runner must type the trigger
        TEXT under that sustained output."""
        trigger = "Read the file /scratch/turn_prompt.md and respond."
        # Banner + input box on read 1, then a relentless flood: every tick has
        # data ready (no None), so the loop never sees a quiet gap. After enough
        # flood ticks the proc is polled dead so the test terminates.
        flood = b'\x1b[2K\r* Auto-updating... 1 MCP server failed... Try "fix"\n'
        script = [b'Welcome to Claude Code\n\xe2\x9d\xaf Try "edit"\n'] + [flood] * 60
        fake, proc = fake_pty_wiring(script, poll_after_reads=55)

        result = claude_interactive.run_claude_interactive_with_heartbeat(
            ["claude"],
            prompt=trigger,
            label="Claude Code",
            timeout=1500,
            env={},
            cwd="/tmp",
            submit_settle_seconds=0.2,
            enter_delay_seconds=0.1,
            idle_exit_seconds=0.5,
            exit_grace_seconds=0.5,
            first_output_seconds=90.0,
        )

        # The trigger TEXT was typed despite the never-quieting flood.
        assert any(trigger.encode() in w for w in fake.writes), (
            f"trigger never submitted under flood; writes={fake.writes!r}"
        )
        # And the bare Enter followed it.
        assert any(w == b"\r" for w in fake.writes), fake.writes

    def test_resends_trigger_when_no_answer_after_window(
        self, fake_pty_wiring, tmp_path
    ):
        """Post-submit resend: the first submit is consumed by a prompt (no
        answer file, no substantive response). After ``resend_after_seconds``
        the runner RE-TYPES the trigger once. The fake drops the answer only
        after the SECOND trigger write, proving the resend was what recovered
        the turn."""
        scratch = tmp_path / "turn_zzz"
        scratch.mkdir()
        (scratch / "turn_prompt.md").write_text("the blob")
        answer_file = scratch / "answer.md"
        trigger = (
            f"Read {scratch / 'turn_prompt.md'} and respond. Write to {answer_file}."
        )
        # Banner + box, then a long stretch of quiet (the submit is "eaten" — no
        # response), enough None ticks to cross the resend window, then quiet for
        # the answer-file poll to pick up the dropped file after the resend.
        script = [b'Welcome to Claude Code\n\xe2\x9d\xaf \n'] + [None] * 200
        fake, proc = fake_pty_wiring(
            script, answer_drop=(str(answer_file), "Recovered by resend.")
        )
        # Only drop the answer on the SECOND multi-byte (trigger) write.
        fake._answer_drop_on_nth = 2

        result = claude_interactive.run_claude_interactive_with_heartbeat(
            ["claude"],
            prompt=trigger,
            label="Claude Code",
            timeout=1500,
            env={},
            cwd="/tmp",
            answer_dir=str(scratch),
            submit_settle_seconds=0.2,
            enter_delay_seconds=0.1,
            idle_exit_seconds=0.5,
            exit_grace_seconds=0.5,
            first_output_seconds=90.0,
            resend_after_seconds=2.0,
        )

        # The trigger TEXT was typed at least TWICE (initial + one resend).
        text_writes = [w for w in fake.writes if trigger.encode() in w]
        assert len(text_writes) >= 2, (
            f"resend never fired; trigger writes={len(text_writes)} writes={fake.writes!r}"
        )
        # Capped at one resend: never more than 2 trigger writes.
        assert len(text_writes) == 2, f"resend not capped at 1: {len(text_writes)} writes"
        # The answer dropped after the resend is what we return.
        assert result.stdout == "Recovered by resend."

    def test_does_not_type_trigger_before_banner(self, fake_pty_wiring):
        trigger = "Read the file /scratch/turn.md and respond."
        # No output ever (all None) until the proc is polled dead.
        script = [None, None, None, None, None]
        fake, proc = fake_pty_wiring(script, poll_after_reads=None)

        claude_interactive.run_claude_interactive_with_heartbeat(
            ["claude"],
            prompt=trigger,
            label="Claude Code",
            timeout=1500,
            env={},
            cwd="/tmp",
            submit_settle_seconds=0.2,
            idle_exit_seconds=0.5,
            exit_grace_seconds=0.5,
            first_output_seconds=1.0,  # short cap → SIGKILL fast
        )

        # Trigger was never typed because the banner never appeared.
        assert not any(trigger.encode() in w for w in fake.writes), fake.writes


# ════════════════════════════════════════════════════════════════════════
# B1 — PTY sized wide so the long trigger echo does not wrap
# ════════════════════════════════════════════════════════════════════════
class TestRunnerSizesPtyWide:
    def test_sets_wide_winsize_and_env(self, fake_pty_wiring):
        import struct
        import termios

        trigger = "Read the file /scratch/turn_prompt.md and respond."
        script = [
            b"Welcome to Claude Code\n",
            None, None, None, None, None, None, None, None,
            b"The answer is 4.\n",
            None, None, None, None, None, None,
        ]
        fake, proc = fake_pty_wiring(script)

        claude_interactive.run_claude_interactive_with_heartbeat(
            ["claude"],
            prompt=trigger,
            label="Claude Code",
            timeout=1500,
            env={},
            cwd="/tmp",
            submit_settle_seconds=0.2,
            enter_delay_seconds=0.1,
            idle_exit_seconds=0.5,
            exit_grace_seconds=0.5,
            first_output_seconds=90.0,
        )

        # The slave PTY was resized to a wide window before Popen.
        assert fake.ioctl_calls, "TIOCSWINSZ never called"
        fd, request, arg = fake.ioctl_calls[0]
        assert fd == fake.slave_fd
        assert request == termios.TIOCSWINSZ
        rows, cols, _x, _y = struct.unpack("HHHH", arg)
        assert cols == 200
        assert rows == 50

        # Env handed to the subprocess agrees with the ioctl.
        env = fake.popen_capture["env"]
        assert env["COLUMNS"] == "200"
        assert env["LINES"] == "50"
        assert env.get("TERM")  # set (default xterm-256color) if not provided


# ════════════════════════════════════════════════════════════════════════
# I1 — PTY writes are fully drained (no silent short-write truncation)
# ════════════════════════════════════════════════════════════════════════
class TestRunnerDrainsWrites:
    def test_short_write_still_delivers_full_trigger(self, fake_pty_wiring):
        trigger = "Read the file /scratch/turn_prompt.md and respond."
        script = [
            b"Welcome to Claude Code\n",
            None, None, None, None, None, None, None, None,
            b"The answer is 4.\n",
            None, None, None, None, None, None,
        ]
        # short_write=True → os.write accepts 1 byte/call; the drain helper
        # must loop until every trigger byte is written.
        fake, proc = fake_pty_wiring(script, short_write=True)

        claude_interactive.run_claude_interactive_with_heartbeat(
            ["claude"],
            prompt=trigger,
            label="Claude Code",
            timeout=1500,
            env={},
            cwd="/tmp",
            submit_settle_seconds=0.2,
            enter_delay_seconds=0.1,
            idle_exit_seconds=0.5,
            exit_grace_seconds=0.5,
            first_output_seconds=90.0,
        )

        # Reassemble everything written and confirm the full trigger TEXT landed
        # despite the PTY only accepting one byte per write call (Defect 1: text
        # and \r are separate writes, so the \r is no longer glued to the text).
        joined = b"".join(fake.writes)
        assert trigger.encode() in joined, joined
        # The bare \r (Enter) also landed as its own byte after the text.
        assert b"\r" in joined, joined


# ════════════════════════════════════════════════════════════════════════
# Defect 2 — answer read out-of-band from a file, not the TUI transcript
# ════════════════════════════════════════════════════════════════════════
class TestRunnerReadsAnswerFile:
    """Interactive Claude is a cursor-addressed TUI; the line-based cleaner
    can't reliably reconstruct the answer from spinner/redraw chrome. So when
    ``answer_dir`` is set, Claude writes its final answer into that fresh
    per-turn scratch dir and the runner GLOBS it back — normalizing the
    returncode to success (the answer was produced even if ``/exit`` left a
    non-zero code). The scraped transcript is a fallback only (no answer file
    ever lands in the dir, or it's empty).

    Mangle-robustness (the bug this fixes): Claude intermittently drops chars
    from a long hex filename when re-typing it into its ``Write`` call. Globbing
    ``answer*.md`` (and any non-``turn_prompt`` ``*.md`` as a fallback) catches
    the mangled name; the old exact-path poll waited forever on the un-mangled
    name → idle ``/exit`` → exit 143 → Gemini fallback."""

    def _scratch(self, tmp_path):
        d = tmp_path / "turn_abc123"
        d.mkdir()
        # The turn-prompt file is always present; the runner must never treat it
        # as the answer.
        (d / "turn_prompt.md").write_text("the turn blob")
        return d

    def test_returns_answer_file_contents_normalizing_returncode(
        self, fake_pty_wiring, tmp_path
    ):
        scratch = self._scratch(tmp_path)
        answer_file = scratch / "answer.md"
        trigger = (
            f"Read the file {scratch / 'turn_prompt.md'} and respond. Write ONLY "
            f"your final answer to {answer_file}."
        )
        # The fake writes the answer file once the trigger TEXT is typed
        # (simulating Claude's Read + Write). The TUI transcript is pure chrome.
        script = [
            b"Welcome to Claude Code\n",
            None, None, None, None, None, None, None, None,
            b"\x1b[2J\x1b[Hspinner frame chrome only\n",  # redraw noise, no answer
            None, None, None, None, None, None, None, None, None,
            None, None, None,
        ]
        fake, proc = fake_pty_wiring(
            script, answer_drop=(str(answer_file), "2+2 is 4, and I'm Luna.")
        )
        # Simulate a non-zero /exit returncode to prove normalization.
        proc.returncode = 143
        proc.wait = lambda timeout=None: 143

        result = claude_interactive.run_claude_interactive_with_heartbeat(
            ["claude"],
            prompt=trigger,
            label="Claude Code",
            timeout=1500,
            env={},
            cwd="/tmp",
            answer_dir=str(scratch),
            submit_settle_seconds=0.2,
            enter_delay_seconds=0.1,
            idle_exit_seconds=0.5,
            exit_grace_seconds=0.5,
            first_output_seconds=90.0,
        )

        # The clean answer comes from the file, not the TUI chrome.
        assert result.stdout == "2+2 is 4, and I'm Luna."
        assert "spinner frame chrome" not in result.stdout
        # Returncode normalized to success because the answer was produced.
        assert result.returncode == 0

    def test_mangled_hex_answer_filename_is_detected(
        self, fake_pty_wiring, tmp_path
    ):
        """THE REGRESSION: Claude was told to write ``answer_<32hex>.md`` but
        DROPPED chars from the hex when re-typing the filename into its ``Write``
        call (e.g. ``answer_ed15d92160f14f71bd2300f95ecd84fd.md`` →
        ``answer_ed15d92160f95ecd84fd.md``). The answer lands on disk under the
        MANGLED name. The old runner polled the exact un-mangled path → waited
        forever → idle ``/exit`` → exit 143 → Gemini fallback. Globbing
        ``answer*.md`` in the fresh per-turn dir catches the mangled name (the
        ``answer`` prefix survives the drop). This FAILS on the old exact-path
        code and PASSES now."""
        scratch = self._scratch(tmp_path)
        # The name the executor *asked* for (full 32-hex) — never written.
        asked = scratch / "answer_ed15d92160f14f71bd2300f95ecd84fd.md"
        # The name Claude *actually* wrote (chars dropped from the hex).
        mangled = scratch / "answer_ed15d92160f95ecd84fd.md"
        trigger = (
            f"Read {scratch / 'turn_prompt.md'} and respond. Write your COMPLETE "
            f"final response to {asked}."
        )
        script = [
            b"Welcome to Claude Code\n",
            None, None, None, None, None, None, None, None,
            b"\x1b[2Jchrome only\n",
            None, None, None, None, None, None, None, None, None,
            None, None, None,
        ]
        fake, proc = fake_pty_wiring(
            script, answer_drop=(str(mangled), "Mangled-name answer survives.")
        )

        result = claude_interactive.run_claude_interactive_with_heartbeat(
            ["claude"],
            prompt=trigger,
            label="Claude Code",
            timeout=1500,
            env={},
            cwd="/tmp",
            answer_dir=str(scratch),
            submit_settle_seconds=0.2,
            enter_delay_seconds=0.1,
            idle_exit_seconds=0.5,
            exit_grace_seconds=0.5,
            first_output_seconds=90.0,
        )

        assert result.stdout == "Mangled-name answer survives."
        assert result.returncode == 0
        assert not asked.exists()

    def test_fully_renamed_md_caught_by_fallback(
        self, fake_pty_wiring, tmp_path
    ):
        """Fallback: if Claude renames the answer entirely (no ``answer`` prefix,
        e.g. ``reply.md``) the runner still picks it up — any ``*.md`` in the
        fresh scratch dir that is NOT ``turn_prompt.md`` is this turn's answer."""
        scratch = self._scratch(tmp_path)
        reply = scratch / "reply.md"
        trigger = (
            f"Read {scratch / 'turn_prompt.md'} and respond. Write to "
            f"{scratch / 'answer.md'}."
        )
        script = [
            b"Welcome to Claude Code\n",
            None, None, None, None, None, None, None, None,
            b"\x1b[2Jchrome\n",
            None, None, None, None, None, None, None, None, None,
            None, None, None,
        ]
        fake, proc = fake_pty_wiring(
            script, answer_drop=(str(reply), "Renamed-file answer.")
        )

        result = claude_interactive.run_claude_interactive_with_heartbeat(
            ["claude"],
            prompt=trigger,
            label="Claude Code",
            timeout=1500,
            env={},
            cwd="/tmp",
            answer_dir=str(scratch),
            submit_settle_seconds=0.2,
            enter_delay_seconds=0.1,
            idle_exit_seconds=0.5,
            exit_grace_seconds=0.5,
            first_output_seconds=90.0,
        )

        assert result.stdout == "Renamed-file answer."

    def test_turn_prompt_is_never_returned_as_answer(
        self, fake_pty_wiring, tmp_path
    ):
        """The ``turn_prompt.md`` blob is always in the scratch dir and must
        NEVER be returned as the answer. With no answer file ever written, the
        runner falls back to the cleaned transcript — not the turn blob."""
        scratch = self._scratch(tmp_path)
        trigger = (
            f"Read {scratch / 'turn_prompt.md'} and respond. Write to "
            f"{scratch / 'answer.md'}."
        )
        script = [
            b"Welcome to Claude Code\n",
            None, None, None, None, None, None, None, None,
            b"The answer is 4.\n",
            None, None, None, None, None, None,
        ]
        fake, proc = fake_pty_wiring(script)  # no answer_drop

        result = claude_interactive.run_claude_interactive_with_heartbeat(
            ["claude"],
            prompt=trigger,
            label="Claude Code",
            timeout=1500,
            env={},
            cwd="/tmp",
            answer_dir=str(scratch),
            submit_settle_seconds=0.2,
            enter_delay_seconds=0.1,
            idle_exit_seconds=0.5,
            exit_grace_seconds=0.5,
            first_output_seconds=90.0,
        )

        assert "the turn blob" not in result.stdout
        assert "The answer is 4." in result.stdout

    def test_falls_back_to_transcript_when_no_answer_file_in_dir(
        self, fake_pty_wiring, tmp_path
    ):
        """No answer file lands in the scratch dir → use the cleaned transcript
        + real returncode (the existing best-effort path)."""
        scratch = self._scratch(tmp_path)
        trigger = f"Read {scratch / 'turn_prompt.md'} and respond."
        script = [
            b"Welcome to Claude Code\n",
            None, None, None, None, None, None, None, None,
            b"The answer is 4.\n",
            None, None, None, None, None, None,
        ]
        fake, proc = fake_pty_wiring(script)

        result = claude_interactive.run_claude_interactive_with_heartbeat(
            ["claude"],
            prompt=trigger,
            label="Claude Code",
            timeout=1500,
            env={},
            cwd="/tmp",
            answer_dir=str(scratch),
            submit_settle_seconds=0.2,
            enter_delay_seconds=0.1,
            idle_exit_seconds=0.5,
            exit_grace_seconds=0.5,
            first_output_seconds=90.0,
        )

        # Only turn_prompt.md remains in the dir — no answer was written.
        assert not (scratch / "answer.md").exists()
        # Falls back to the cleaned transcript.
        assert "The answer is 4." in result.stdout

    def test_falls_back_to_transcript_when_answer_file_empty(
        self, fake_pty_wiring, tmp_path
    ):
        """An empty answer file (Claude wrote nothing) → fall back to the
        cleaned transcript rather than returning an empty success."""
        scratch = self._scratch(tmp_path)
        (scratch / "answer.md").write_text("")  # empty
        trigger = f"Read {scratch / 'turn_prompt.md'} and respond."
        script = [
            b"Welcome to Claude Code\n",
            None, None, None, None, None, None, None, None,
            b"The answer is 4.\n",
            None, None, None, None, None, None,
        ]
        fake, proc = fake_pty_wiring(script)

        result = claude_interactive.run_claude_interactive_with_heartbeat(
            ["claude"],
            prompt=trigger,
            label="Claude Code",
            timeout=1500,
            env={},
            cwd="/tmp",
            answer_dir=str(scratch),
            submit_settle_seconds=0.2,
            enter_delay_seconds=0.1,
            idle_exit_seconds=0.5,
            exit_grace_seconds=0.5,
            first_output_seconds=90.0,
        )

        assert "The answer is 4." in result.stdout

    def test_newest_non_empty_match_is_picked(self, fake_pty_wiring, tmp_path):
        """When several candidate ``*.md`` files exist in the fresh dir, the
        NEWEST non-empty one is the answer. An empty ``answer.md`` plus a
        populated ``answer.md`` written later (the real reply) → the populated
        one wins; an empty candidate is never returned."""
        import time as _time

        scratch = self._scratch(tmp_path)
        # A stale-ish empty answer.md written before the run starts.
        empty = scratch / "answer.md"
        empty.write_text("")
        _time.sleep(0.01)
        later = scratch / "answer_final.md"
        trigger = f"Read {scratch / 'turn_prompt.md'} and respond. Write answer."
        script = [
            b"Welcome to Claude Code\n",
            None, None, None, None, None, None, None, None,
            b"\x1b[2Jchrome\n",
            None, None, None, None, None, None, None, None, None,
            None, None, None,
        ]
        fake, proc = fake_pty_wiring(
            script, answer_drop=(str(later), "The real, newest answer.")
        )

        result = claude_interactive.run_claude_interactive_with_heartbeat(
            ["claude"],
            prompt=trigger,
            label="Claude Code",
            timeout=1500,
            env={},
            cwd="/tmp",
            answer_dir=str(scratch),
            submit_settle_seconds=0.2,
            enter_delay_seconds=0.1,
            idle_exit_seconds=0.5,
            exit_grace_seconds=0.5,
            first_output_seconds=90.0,
        )

        assert result.stdout == "The real, newest answer."

    def test_scratch_dir_removed_after_read(self, fake_pty_wiring, tmp_path):
        """After reading the answer the runner removes the WHOLE per-turn scratch
        dir (best-effort) so the reused per-tenant ``session_dir`` doesn't
        accumulate ``turn_<hex>/`` dirs across many turns."""
        scratch = self._scratch(tmp_path)
        answer_file = scratch / "answer.md"
        trigger = (
            f"Read {scratch / 'turn_prompt.md'} and respond. Write your COMPLETE "
            f"final response to {answer_file}."
        )
        script = [
            b"Welcome to Claude Code\n",
            None, None, None, None, None, None, None, None,
            b"\x1b[2Jchrome\n",
            None, None, None, None, None, None, None, None, None,
            None, None, None,
        ]
        fake, proc = fake_pty_wiring(
            script, answer_drop=(str(answer_file), "The complete answer.")
        )

        result = claude_interactive.run_claude_interactive_with_heartbeat(
            ["claude"],
            prompt=trigger,
            label="Claude Code",
            timeout=1500,
            env={},
            cwd="/tmp",
            answer_dir=str(scratch),
            submit_settle_seconds=0.2,
            enter_delay_seconds=0.1,
            idle_exit_seconds=0.5,
            exit_grace_seconds=0.5,
            first_output_seconds=90.0,
        )

        assert result.stdout == "The complete answer."
        # The whole per-turn scratch dir is cleaned up after the read.
        assert not scratch.exists(), "scratch dir must be removed after read"

    def test_cleanup_failure_does_not_raise(
        self, fake_pty_wiring, tmp_path, monkeypatch
    ):
        """The post-read cleanup is best-effort — an error while removing the
        scratch dir must NEVER propagate (the answer is already in hand)."""
        scratch = self._scratch(tmp_path)
        (scratch / "answer.md").write_text("Already on disk.")
        trigger = (
            f"Read {scratch / 'turn_prompt.md'} and respond. Write answer."
        )
        script = [
            b"Welcome to Claude Code\n",
            None, None, None, None, None, None, None, None,
            b"chrome\n",
            None, None, None, None, None, None,
        ]
        fake, proc = fake_pty_wiring(script)

        def _boom_rmtree(path, *a, **k):
            raise OSError("cannot remove dir")

        monkeypatch.setattr(claude_interactive.shutil, "rmtree", _boom_rmtree)

        result = claude_interactive.run_claude_interactive_with_heartbeat(
            ["claude"],
            prompt=trigger,
            label="Claude Code",
            timeout=1500,
            env={},
            cwd="/tmp",
            answer_dir=str(scratch),
            submit_settle_seconds=0.2,
            enter_delay_seconds=0.1,
            idle_exit_seconds=0.5,
            exit_grace_seconds=0.5,
            first_output_seconds=90.0,
        )

        assert result.stdout == "Already on disk."
        assert result.returncode == 0
