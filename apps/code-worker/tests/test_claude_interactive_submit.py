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

        monkeypatch.setattr(
            claude_interactive,
            "run_claude_interactive_with_heartbeat",
            lambda cmd, **kw: _completed(0, stdout="ok"),
        )

        task = _make_input(
            instruction_md_content="PERSONA: Luna",
            message="hello there",
        )
        wf._execute_claude_chat(task, session_dir=str(session_dir))

        turn_file = session_dir / "turn_prompt.md"
        assert turn_file.is_file()
        body = turn_file.read_text()
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
        turn_file = str(session_dir / "turn_prompt.md")
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
        the turn file AND write its final answer out-of-band to the answer
        file, and the runner must receive ``answer_file`` so it reads it back.

        FINDING 1 (stale answer replay): the answer file is a UNIQUE per-turn
        name (``answer_<hex>.md``), not the fixed ``answer.md`` — so any
        non-empty content is guaranteed to be THIS turn's reply, never a prior
        turn's leftover in the reused per-tenant ``session_dir``."""
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
        turn_file = str(session_dir / "turn_prompt.md")
        answer_file = captured["kwargs"].get("answer_file")
        # Single line still.
        assert "\n" not in submit
        # Read-turn-file instruction.
        assert "Read the file" in submit
        assert turn_file in submit
        # FINDING 1: unique per-turn answer filename (answer_<hex>.md), NOT the
        # fixed answer.md — guards against the prior turn's file being replayed.
        assert os.path.basename(answer_file).startswith("answer_")
        assert os.path.basename(answer_file).endswith(".md")
        assert os.path.basename(answer_file) != "answer.md"
        # Write-answer-file instruction (Defect 2), naming the unique file.
        assert answer_file in submit
        # FINDING 3 (Luna): the trigger asks for the COMPLETE final response, not
        # a terse stub — important results / file changes / errors / next steps.
        assert "COMPLETE" in submit
        assert "important results" in submit
        # The runner is handed the answer-file path to read back.
        assert os.path.isabs(answer_file)
        # The answer file lives under session_dir (ephemeral scratch).
        assert os.path.dirname(answer_file) == str(session_dir)

    def test_answer_file_unique_per_call(
        self, monkeypatch, tmp_path, interactive_env
    ):
        """FINDING 1: each interactive turn must build a DISTINCT answer
        filename. ``session_dir`` is persistent per-tenant and reused every
        turn, so a fixed ``answer.md`` lets a turn that fails to write its
        answer return the PRIOR turn's file as a fresh success. Two calls →
        two different unique names proves the stale-replay window is closed."""
        self._patch_credential(monkeypatch)
        session_dir = tmp_path / "session"
        session_dir.mkdir()

        names: list[str] = []

        def fake_run(cmd, **kwargs):
            names.append(kwargs.get("answer_file"))
            return _completed(0, stdout="ok")

        monkeypatch.setattr(
            claude_interactive, "run_claude_interactive_with_heartbeat", fake_run
        )

        task = _make_input(instruction_md_content="PERSONA", message="hi")
        wf._execute_claude_chat(task, session_dir=str(session_dir))
        wf._execute_claude_chat(task, session_dir=str(session_dir))

        assert len(names) == 2
        assert names[0] != names[1], "answer filename must be unique per turn"
        for name in names:
            assert os.path.basename(name).startswith("answer_")
            assert os.path.basename(name).endswith(".md")
            assert os.path.dirname(name) == str(session_dir)

    def test_turn_prompt_and_claude_md_written_0600(
        self, monkeypatch, tmp_path, interactive_env
    ):
        """N2: the turn blob (persona + conversation history) is secret-grade,
        so ``turn_prompt.md`` and ``CLAUDE.md`` must be mode 0o600."""
        import stat

        self._patch_credential(monkeypatch)
        session_dir = tmp_path / "session"
        session_dir.mkdir()

        monkeypatch.setattr(
            claude_interactive,
            "run_claude_interactive_with_heartbeat",
            lambda cmd, **kw: _completed(0, stdout="ok"),
        )

        task = _make_input(
            instruction_md_content="PERSONA: Luna",
            message="secret history",
        )
        wf._execute_claude_chat(task, session_dir=str(session_dir))

        for name in ("turn_prompt.md", "CLAUDE.md"):
            p = session_dir / name
            assert p.is_file(), name
            mode = stat.S_IMODE(p.stat().st_mode)
            assert mode == 0o600, f"{name} mode is {oct(mode)}"

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
        action = decide_pty_action(
            now=20.0,
            start=0.0,
            last_output=2.0,  # 18s quiet, well past idle_exit
            seen_output=True,
            submitted=True,
            response_seen=False,
            exit_sent_at=None,
            first_output_seconds=90.0,
            submit_settle_seconds=1.0,
            idle_exit_seconds=8.0,
            exit_grace_seconds=10.0,
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
        # Defect 2: the trigger text is the first multi-byte write — when it
        # lands, drop the out-of-band answer file (Claude's Read + Write).
        if (
            self._answer_drop is not None
            and not self._answer_dropped
            and len(data) > 1
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
    ``answer_file`` is set, Claude writes its final answer there and the runner
    reads it back — normalizing the returncode to success (the answer was
    produced even if ``/exit`` left a non-zero code). The scraped transcript is
    a fallback only (``answer_file`` absent or empty)."""

    def test_returns_answer_file_contents_normalizing_returncode(
        self, fake_pty_wiring, tmp_path
    ):
        answer_file = tmp_path / "answer.md"
        trigger = (
            f"Read the file /scratch/turn_prompt.md and respond. Write ONLY your "
            f"final answer to {answer_file}."
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
            answer_file=str(answer_file),
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

    def test_falls_back_to_transcript_when_answer_file_absent(
        self, fake_pty_wiring, tmp_path
    ):
        """No answer file on disk → use the cleaned transcript + real
        returncode (the existing best-effort path)."""
        answer_file = tmp_path / "answer.md"  # never created
        trigger = "Read the file /scratch/turn_prompt.md and respond."
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
            answer_file=str(answer_file),
            submit_settle_seconds=0.2,
            enter_delay_seconds=0.1,
            idle_exit_seconds=0.5,
            exit_grace_seconds=0.5,
            first_output_seconds=90.0,
        )

        assert not answer_file.exists()
        # Falls back to the cleaned transcript.
        assert "The answer is 4." in result.stdout

    def test_falls_back_to_transcript_when_answer_file_empty(
        self, fake_pty_wiring, tmp_path
    ):
        """An empty answer file (Claude wrote nothing) → fall back to the
        cleaned transcript rather than returning an empty success."""
        answer_file = tmp_path / "answer.md"
        answer_file.write_text("")  # empty
        trigger = "Read the file /scratch/turn_prompt.md and respond."
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
            answer_file=str(answer_file),
            submit_settle_seconds=0.2,
            enter_delay_seconds=0.1,
            idle_exit_seconds=0.5,
            exit_grace_seconds=0.5,
            first_output_seconds=90.0,
        )

        assert "The answer is 4." in result.stdout

    def test_answer_file_unlinked_after_read(self, fake_pty_wiring, tmp_path):
        """FINDING 1: after reading a non-empty answer file the runner must
        UNLINK it (best-effort) so the reused per-tenant ``session_dir`` doesn't
        accumulate per-turn answer files across many turns."""
        answer_file = tmp_path / "answer_deadbeef.md"
        trigger = (
            f"Read the file /scratch/turn_prompt.md and respond. Write your "
            f"COMPLETE final response to {answer_file}."
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
            answer_file=str(answer_file),
            submit_settle_seconds=0.2,
            enter_delay_seconds=0.1,
            idle_exit_seconds=0.5,
            exit_grace_seconds=0.5,
            first_output_seconds=90.0,
        )

        assert result.stdout == "The complete answer."
        # The unique per-turn answer file is cleaned up after the read.
        assert not answer_file.exists(), "answer file must be unlinked after read"

    def test_unlink_failure_does_not_raise(
        self, fake_pty_wiring, tmp_path, monkeypatch
    ):
        """FINDING 1: the post-read unlink is best-effort — an OSError while
        removing the answer file must NEVER propagate (the answer is already in
        hand). Patch ``os.unlink`` to raise and assert the result still comes
        back clean."""
        answer_file = tmp_path / "answer_cafef00d.md"
        answer_file.write_text("Already on disk.")
        trigger = (
            f"Read /scratch/turn_prompt.md and respond. Write to {answer_file}."
        )
        script = [
            b"Welcome to Claude Code\n",
            None, None, None, None, None, None, None, None,
            b"chrome\n",
            None, None, None, None, None, None,
        ]
        fake, proc = fake_pty_wiring(script)

        import os as _os

        real_unlink = _os.unlink

        def _boom_unlink(path, *a, **k):
            if str(path) == str(answer_file):
                raise OSError("cannot unlink")
            return real_unlink(path, *a, **k)

        monkeypatch.setattr(claude_interactive.os, "unlink", _boom_unlink)

        result = claude_interactive.run_claude_interactive_with_heartbeat(
            ["claude"],
            prompt=trigger,
            label="Claude Code",
            timeout=1500,
            env={},
            cwd="/tmp",
            answer_file=str(answer_file),
            submit_settle_seconds=0.2,
            enter_delay_seconds=0.1,
            idle_exit_seconds=0.5,
            exit_grace_seconds=0.5,
            first_output_seconds=90.0,
        )

        assert result.stdout == "Already on disk."
        assert result.returncode == 0

    def test_stale_different_named_file_never_read(
        self, fake_pty_wiring, tmp_path
    ):
        """FINDING 1: a leftover answer file from a PRIOR turn (a DIFFERENT
        unique name) must NEVER be read as this turn's answer. The runner is
        handed only THIS turn's unique ``answer_file``; a stale neighbour with a
        different name is ignored, so an empty this-turn file falls back to the
        transcript rather than replaying the stale success."""
        # A prior turn left this behind in the reused session_dir.
        stale = tmp_path / "answer_oldturn1234.md"
        stale.write_text("STALE answer from the previous turn.")
        # This turn's unique file — never written (the turn failed to write it).
        this_turn = tmp_path / "answer_newturn5678.md"
        trigger = (
            f"Read /scratch/turn_prompt.md and respond. Write to {this_turn}."
        )
        script = [
            b"Welcome to Claude Code\n",
            None, None, None, None, None, None, None, None,
            b"The answer is 4.\n",  # only the transcript carries the reply
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
            answer_file=str(this_turn),
            submit_settle_seconds=0.2,
            enter_delay_seconds=0.1,
            idle_exit_seconds=0.5,
            exit_grace_seconds=0.5,
            first_output_seconds=90.0,
        )

        # The stale file's content must NOT appear — only this turn's transcript.
        assert "STALE answer" not in result.stdout
        assert "The answer is 4." in result.stdout
        # The stale neighbour is untouched (we only unlink our own file).
        assert stale.exists()
