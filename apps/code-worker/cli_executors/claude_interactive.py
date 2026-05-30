"""PTY-backed Claude Code interactive runner.

Claude Code's machine-readable JSON output is tied to ``claude -p``.
When a tenant needs to use a native Claude Code subscription session
instead of the print/API path, the worker has to drive the normal TTY UI
and treat the terminal transcript as the result.

This module is intentionally small and stdlib-only: production images do
not currently include tmux/expect, and adding another daemon just to keep
Claude attached would be heavier than a per-turn PTY bridge.
"""
from __future__ import annotations

import errno
import fcntl
import os
import pty
import re
import select
import signal
import struct
import subprocess
import termios
import time
from collections.abc import Callable


_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_OSC_RE = re.compile(r"\x1b\].*?(?:\x07|\x1b\\)")
# Approach C transcript chrome: the bracketed-paste placeholder the REPL emits
# when input collapses, and the Read tool-call chrome around the turn-file read.
_PASTED_RE = re.compile(r"^\[Pasted text\b.*\]$")
_READ_CALL_RE = re.compile(r"^[⏺·•*-]?\s*Read\(.*\)\s*$")
# I3: the Read tool-result line is ALWAYS prefixed by the tool gutter glyph
# (``⎿``/``|``). Requiring it — rather than making it optional — stops the
# ``ing\b`` branch from deleting legit prose answers that merely START with
# "Reading…" (e.g. "Reading the logs, I found three errors:").
_READ_RESULT_RE = re.compile(r"^[⎿|]\s*Read(?:\s+\d+\s+line|ing\b).*$")
# Folder-trust dialog markers — if one appears before we submit we send a bare
# Enter to accept (belt-and-suspenders; #732 normally pre-seeds trust).
_TRUST_RE = re.compile(r"\b(do you trust|trust this folder|trust the files)\b", re.I)
# N1: the REPL has rendered its input box once we see the prompt caret ``❯`` or
# the ``Try "…"`` placeholder. Seeing either lets us submit after a brief settle
# instead of waiting for the full quiet-settle a chatty banner keeps resetting.
_INPUT_BOX_RE = re.compile(r'❯|Try "')

# I2: a wrapped trigger echo fragment must be at least this many normalized
# characters before we'll drop it, so a short real answer that happens to
# share a couple of words with the trigger is never eaten.
_MIN_TRIGGER_FRAGMENT_CHARS = 12


def _normalize_ws(s: str) -> str:
    """Lower-case + collapse runs of whitespace to single spaces (for
    wrap-tolerant comparison — a hard wrap turns one space into a newline)."""
    return " ".join(s.split()).lower()


def _is_trigger_fragment(line: str, norm_trigger: str) -> bool:
    """True if ``line`` (whitespace-normalized) is a non-trivial fragment of the
    normalized trigger — i.e. a physical row of a wrapped trigger echo.

    Guards against eating real answers: the fragment must be reasonably long
    (``_MIN_TRIGGER_FRAGMENT_CHARS``) AND a genuine substring of the trigger.
    """
    if not norm_trigger:
        return False
    norm_line = _normalize_ws(line)
    # A leading REPL echo prefix ("> ") is chrome, not part of the trigger.
    if norm_line.startswith("> "):
        norm_line = norm_line[2:].strip()
    if len(norm_line) < _MIN_TRIGGER_FRAGMENT_CHARS:
        return False
    return norm_line in norm_trigger


def clean_interactive_transcript(raw: str, prompt: str = "") -> str:
    """Return a readable answer from a Claude Code terminal transcript.

    This is a best-effort fallback, not a protocol parser. The interactive
    UI is meant for humans, so we strip ANSI/control noise and common box/
    prompt chrome while preserving useful assistant text and command output.

    Approach C (plan 2026-05-30) submits a single-line trigger that makes
    Claude ``Read`` a turn-file, so we also drop (a) the trigger echo, (b) any
    ``[Pasted text +N lines]`` placeholder, and (c) the ``Read`` tool chrome,
    leaving just Claude's reply. Never raises.
    """
    try:
        text = _OSC_RE.sub("", raw)
        text = _ANSI_RE.sub("", text)
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        prompt = (prompt or "").strip()
        # I2: precompute the normalized trigger so a wrapped (multi-line) echo
        # is stripped even when no single line matches the trigger verbatim.
        norm_trigger = _normalize_ws(prompt) if prompt else ""

        cleaned: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                if cleaned and cleaned[-1]:
                    cleaned.append("")
                continue
            if prompt and stripped in {prompt, f"> {prompt}"}:
                continue
            # Wrap-tolerant trigger-echo strip (a narrow PTY wraps the ~185-char
            # trigger onto several rows; the exact-match above misses those).
            if _is_trigger_fragment(stripped, norm_trigger):
                continue
            if stripped in {"/exit", "exit"}:
                continue
            if stripped.startswith(("╭", "╰", "│", "┌", "└", "┃", "┗", "┏")):
                continue
            if stripped in {"?", ">", "Welcome to Claude Code"}:
                continue
            if stripped.startswith(("Claude Code", "By using Claude Code")):
                continue
            # Approach C chrome.
            if _PASTED_RE.match(stripped):
                continue
            if _READ_CALL_RE.match(stripped) or _READ_RESULT_RE.match(stripped):
                continue
            cleaned.append(stripped)

        return "\n".join(cleaned).strip()
    except Exception:  # noqa: BLE001 - best-effort cleaner must never raise
        return (raw or "").strip()


def decide_pty_action(
    *,
    now: float,
    start: float,
    last_output: float,
    seen_output: bool,
    submitted: bool,
    response_seen: bool,
    exit_sent_at: float | None,
    first_output_seconds: float,
    submit_settle_seconds: float,
    idle_exit_seconds: float,
    exit_grace_seconds: float,
    submitted_at: float | None = None,
    input_box_seen: bool = False,
    first_output_at: float | None = None,
    text_written: bool = False,
    text_written_at: float | None = None,
    enter_delay_seconds: float = 0.5,
    answer_ready: bool = False,
    answer_ready_at: float | None = None,
    answer_settle_seconds: float = 0.25,
    awaiting_answer_file: bool = False,
) -> str:
    """Decide the next PTY action for one loop tick (pure — no I/O).

    Returns one of: ``"wait"`` (keep reading), ``"submit_text"`` (type the
    trigger text — phase 1), ``"submit_enter"`` (write a bare ``\\r`` — phase 2),
    ``"exit"`` (send ``/exit``), ``"terminate"`` (escalate to SIGTERM after the
    exit grace), or ``"kill"`` (SIGKILL — nothing rendered in time).

    State machine (post-#735, Approach C, Defect-1 two-phase submit):
      1. Readiness — wait for the banner; if none within ``first_output_seconds``
         → kill. Once seen, decide submit-readiness (see #2).
      2. Submit phase 1 (``submit_text``, once) — when NOT ``text_written`` and
         readiness is satisfied by EITHER (a) the input box was seen
         (``input_box_seen`` — ``❯`` / ``Try "`` placeholder rendered) followed
         by a brief settle, OR (b) ``submit_settle_seconds`` of pure quiet, OR
         (c) a bounded ceiling since first output (``max(settle*3, 5.0)``). The
         ceiling stops a chatty banner / blinking spinner from resetting the
         quiet timer forever and starving submit ~90s (N1). All three are well
         under ``first_output_seconds``.
      2b. Submit phase 2 (``submit_enter``, once) — DEFECT 1: the REPL runs
         bracketed-paste mode, so a long trigger glued to ``\\r`` in one write
         is swallowed as paste and the ``\\r`` becomes a newline, never Enter.
         After the text is written we wait ``enter_delay_seconds`` (the REPL's
         paste-settle window) and only THEN write the bare ``\\r`` that actually
         submits the turn.
      3. Await response — after submit, suppress the idle ``/exit`` until the
         FIRST post-submit output; if none within ``first_output_seconds`` after
         submit → kill.
      4. Completion — FINDING 2: the answer FILE is the completion signal, not
         the first post-submit byte. ``response_seen`` flips on the first byte
         (a ``Read(...)`` echo) which would arm the idle ``/exit`` BEFORE the
         answer file exists; a quiet gap could then ``/exit`` and kill the turn
         pre-write. So:
           - When ``answer_ready`` (file present, non-empty, size STABLE across
             one tick) and a brief ``answer_settle_seconds`` has passed since it
             appeared → ``exit`` promptly (the deliverable is in hand).
           - While NOT ``answer_ready``, do NOT let the idle ``/exit`` fire —
             keep waiting — UNTIL a bounded fallback cap since submit (reuse
             ``first_output_seconds``); only past that cap do we fall through to
             the legacy idle-``/exit`` (→ scraped-transcript fallback for turns
             where Claude never wrote the file). The outer ``timeout`` + the
             post-``/exit`` SIGTERM/SIGKILL still bound everything (no new hang).
         Then SIGTERM after ``exit_grace_seconds``.
    """
    # 4b. Exit already sent — escalate after the grace window.
    if exit_sent_at is not None:
        if now - exit_sent_at >= exit_grace_seconds:
            return "terminate"
        return "wait"

    # 1. Pre-banner readiness gate.
    if not seen_output:
        if now - start >= first_output_seconds:
            return "kill"
        return "wait"

    # 2/2b. Not yet submitted: two-phase submit. Phase 1 types the trigger text
    #       once the REPL is ready; phase 2 sends a bare \r after the paste
    #       settle (Defect 1). The Enter must be a SEPARATE write or the REPL
    #       absorbs the text+\r as one bracketed paste and never submits.
    if not submitted:
        if not text_written:
            # Phase 1 — readiness gate (N1): any of three paths so a
            # never-quieting banner can't starve us; a half-rendered box still
            # gets a brief settle.
            quiet = now - last_output
            brief_settle = min(submit_settle_seconds, 0.15)
            if input_box_seen and quiet >= brief_settle:
                return "submit_text"
            if quiet >= submit_settle_seconds:
                return "submit_text"
            ceiling = max(submit_settle_seconds * 3, 5.0)
            if first_output_at is not None and now - first_output_at >= ceiling:
                return "submit_text"
            return "wait"
        # Phase 2 — Enter alone, but only after the paste-settle window so the
        # REPL has left bracketed-paste mode.
        baseline = text_written_at if text_written_at is not None else now
        if now - baseline >= enter_delay_seconds:
            return "submit_enter"
        return "wait"

    # 3. Submitted but no response yet: suppress idle /exit, bound the wait.
    if not response_seen:
        baseline = submitted_at if submitted_at is not None else start
        if now - baseline >= first_output_seconds:
            return "kill"
        return "wait"

    # 4a. Completion (FINDING 2): the answer FILE — not the first post-submit
    #     byte — is the signal. Exit promptly once the file is written + stable.
    if answer_ready:
        baseline = answer_ready_at if answer_ready_at is not None else now
        if now - baseline >= answer_settle_seconds:
            return "exit"
        return "wait"

    # 4b. Answer not yet on disk: suppress the legacy idle /exit (which could
    #     kill the turn before the file is written) until a bounded fallback cap
    #     since submit. Past the cap, fall through to the legacy idle path so a
    #     turn where Claude NEVER wrote the file still ends (→ transcript scrape)
    #     instead of hanging until the outer timeout. ONLY when we're actually
    #     awaiting a file — if the caller passed no ``answer_file`` there's
    #     nothing to wait for, so the legacy idle /exit applies immediately.
    if awaiting_answer_file:
        baseline = submitted_at if submitted_at is not None else start
        if now - baseline < first_output_seconds:
            return "wait"

    # Fallback (cap exceeded, or no answer file expected): legacy idle /exit.
    if now - last_output >= idle_exit_seconds:
        return "exit"
    return "wait"


def _signal_tree(pgid: int, sig: int) -> None:
    """Signal Claude's whole process group (cached PGID) so children it spawned
    (MCP servers, helper procs) are reaped too — not just the top-level PID.

    Takes the PGID captured while the leader was alive, NOT a live
    ``os.getpgid(pid)`` lookup: if Claude exits before one of its children, that
    lookup raises ``ProcessLookupError`` even though the group is still alive,
    and we'd leak the orphaned children this is meant to reap."""
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def _write_all(fd: int, data: bytes) -> None:
    """Write EVERY byte of ``data`` to ``fd`` (I1).

    A single ``os.write`` to a PTY master can short-write, silently truncating
    the trigger while the caller's ``submitted`` latch flips true (never
    retried) — so a partial trigger would never submit. Loop over a
    ``memoryview`` until the buffer is drained. ``EAGAIN`` (non-blocking master)
    is retried; other ``OSError``s propagate to the caller's existing guard."""
    if not data:
        return
    view = memoryview(data)
    while view:
        try:
            n = os.write(fd, view)
        except OSError as exc:
            if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                continue
            raise
        if n <= 0:
            break
        view = view[n:]


def run_claude_interactive_with_heartbeat(
    cmd: list[str],
    *,
    prompt: str,
    label: str,
    timeout: int,
    env: dict[str, str],
    cwd: str,
    on_chunk: Callable[[str, str], None] | None = None,
    heartbeat: Callable[[str], None] | None = None,
    idle_exit_seconds: float | None = None,
    exit_grace_seconds: float | None = None,
    first_output_seconds: float | None = None,
    submit_settle_seconds: float | None = None,
    enter_delay_seconds: float | None = None,
    answer_file: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run Claude Code attached to a PTY and return a CompletedProcess.

    The interactive REPL (Claude Code v2.1.144) does NOT auto-execute a
    positional ``[prompt]`` arg, so the caller no longer appends one. Instead
    ``prompt`` is a single-line trigger we TYPE into the REPL once it's ready
    (Approach C, plan 2026-05-30): after the banner appears we wait a short
    ``submit_settle_seconds`` quiet window (input box rendered + stable).

    DEFECT 1 (smoke-proven): the trigger TEXT and the Enter must be SEPARATE
    writes. The REPL runs bracketed-paste mode, so a long line glued to ``\r``
    in one write is absorbed as paste and the ``\r`` becomes a newline inside
    the box — never Enter, so the turn never submits (returncode 143). We write
    the text, wait ``enter_delay_seconds`` (env
    ``CLAUDE_CODE_INTERACTIVE_SUBMIT_ENTER_DELAY_SECONDS``, default 0.5), then
    write a bare ``\r`` that actually submits. Submission is gated so we never
    type into a not-yet-ready REPL.

    DEFECT 2 (smoke-proven): interactive Claude is a cursor-addressed TUI
    (spinner frames, redraws); the line-based ``clean_interactive_transcript``
    can't reliably reconstruct the answer from the chrome. When ``answer_file``
    is set, the trigger instructs Claude to write its final answer there, and we
    read it back after the read loop — normalizing the returncode to success
    (the answer was produced even if ``/exit`` left a non-zero code). The
    scraped transcript is a fallback only (``answer_file`` absent or empty).

    Since the interactive CLI does not exit after a turn, we send ``/exit`` once
    the terminal has been quiet for ``idle_exit_seconds`` — but only *after* a
    post-submit response is seen. Before the banner the idle countdown is
    suppressed and we wait up to ``first_output_seconds``; after submit we again
    suppress it until the first post-submit output (bounded by the same cap).
    Without these gates a slow launch / pre-response quiet was ``/exit``'d and
    the turn died with an empty transcript. Process-group cleanup (#735) is
    unchanged.
    """
    idle_exit_seconds = idle_exit_seconds if idle_exit_seconds is not None else float(
        os.environ.get("CLAUDE_CODE_INTERACTIVE_IDLE_EXIT_SECONDS", "8")
    )
    exit_grace_seconds = exit_grace_seconds if exit_grace_seconds is not None else float(
        os.environ.get("CLAUDE_CODE_INTERACTIVE_EXIT_GRACE_SECONDS", "10")
    )
    first_output_seconds = first_output_seconds if first_output_seconds is not None else float(
        os.environ.get("CLAUDE_CODE_INTERACTIVE_FIRST_OUTPUT_SECONDS", "90")
    )
    submit_settle_seconds = submit_settle_seconds if submit_settle_seconds is not None else float(
        os.environ.get("CLAUDE_CODE_INTERACTIVE_SUBMIT_SETTLE_SECONDS", "1.0")
    )
    enter_delay_seconds = enter_delay_seconds if enter_delay_seconds is not None else float(
        os.environ.get("CLAUDE_CODE_INTERACTIVE_SUBMIT_ENTER_DELAY_SECONDS", "0.5")
    )
    # Defect 1: text and Enter are written in two phases, so keep them apart.
    text_bytes = (prompt or "").encode()

    master_fd, slave_fd = pty.openpty()
    # B1: size the PTY wide BEFORE launch. A default 80-col window wraps the
    # ~185-char single-line trigger onto ~3 physical rows, so the exact-match
    # echo strip in clean_interactive_transcript() leaks the trigger into the
    # returned transcript. Set a 200x50 window on the slave + matching env so
    # apps that read COLUMNS/LINES agree with the ioctl. Guarded — harmless on
    # odd platforms where the ioctl isn't supported.
    try:
        fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 200, 0, 0))
    except OSError:
        pass
    env["COLUMNS"] = "200"
    env["LINES"] = "50"
    env.setdefault("TERM", "xterm-256color")
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        env=env,
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        text=False,
        close_fds=True,
        # Own session/process group so we can signal Claude AND any children it
        # spawned (MCP servers, helper procs) as a unit — a bare proc.kill()
        # would orphan them and leak resources on repeated timeouts.
        start_new_session=True,
    )
    os.close(slave_fd)
    # Capture the PGID now, while the leader is alive, and reuse it for every
    # cleanup signal — a later os.getpgid() off a dead leader would miss a
    # still-running child group. start_new_session makes pgid == proc.pid.
    try:
        pgid = os.getpgid(proc.pid)
    except OSError:
        pgid = proc.pid

    chunks: list[str] = []
    start = time.monotonic()
    last_output = start
    last_heartbeat = start
    exit_sent_at: float | None = None
    seen_output = False
    # Approach C submission state (Defect 1: two-phase — text, then Enter).
    text_written = False
    text_written_at: float | None = None
    submitted = False
    submitted_at: float | None = None
    response_seen = False
    trust_acked = False
    # N1 readiness state: when the input box first rendered, and the timestamp
    # of the first output (for the bounded submit ceiling).
    input_box_seen = False
    first_output_at: float | None = None
    # FINDING 2 answer-file completion state: the answer FILE (not the first
    # post-submit byte) signals the turn is done. ``answer_ready`` flips once
    # the file is present, non-empty, and its size is STABLE across one tick;
    # ``answer_ready_at`` notes when, so a brief settle precedes ``/exit``.
    # ``_prev_answer_size`` carries the prior tick's size for the stability
    # check. ``-1`` = not yet observed (distinct from a real 0-byte file).
    answer_ready = False
    answer_ready_at: float | None = None
    _prev_answer_size = -1

    # Empty-prompt guard: nothing to type → skip straight to submitted so the
    # loop runs the response/idle gates rather than waiting to type forever.
    if not text_bytes:
        text_written = True
        submitted = True

    try:
        while True:
            now = time.monotonic()
            if heartbeat and now - last_heartbeat >= 30:
                heartbeat(f"{label} interactive ({int(now - start)}s elapsed)")
                last_heartbeat = now
            if now - start >= timeout:
                _signal_tree(pgid, signal.SIGKILL)
                break

            ready, _, _ = select.select([master_fd], [], [], 0.25)
            if ready:
                try:
                    data = os.read(master_fd, 8192)
                except OSError:
                    data = b""
                if data:
                    text = data.decode(errors="replace")
                    chunks.append(text)
                    last_output = now
                    seen_output = True
                    if first_output_at is None:
                        first_output_at = now
                    # N1: note the input box rendering so submit can fire after a
                    # brief settle rather than the full (banner-resettable) quiet.
                    if not input_box_seen and _INPUT_BOX_RE.search(text):
                        input_box_seen = True
                    # First output AFTER submit = Claude is responding; this
                    # un-suppresses the idle `/exit`.
                    if submitted and not response_seen:
                        response_seen = True
                    if on_chunk:
                        on_chunk(text, "stdout")
                    # Belt-and-suspenders: a folder-trust dialog may precede the
                    # input box despite #732's seed. Accept it once with a bare
                    # Enter so the input box renders before we type the trigger.
                    if not submitted and not trust_acked and _TRUST_RE.search(text):
                        try:
                            _write_all(master_fd, b"\r")
                        except OSError:
                            pass
                        trust_acked = True
                    continue

            if proc.poll() is not None:
                break

            # FINDING 2: poll the answer file once submitted. It is the
            # completion signal — present + non-empty + size STABLE across one
            # tick. Stability guards against exiting mid-flush of a large reply.
            # Polled BEFORE the exit decision so ``answer_ready`` reflects this
            # tick. Best-effort: a stat error just defers readiness.
            if answer_file and submitted and not answer_ready:
                try:
                    size = os.path.getsize(answer_file)
                except OSError:
                    size = -1
                if size > 0 and size == _prev_answer_size:
                    answer_ready = True
                    answer_ready_at = now
                _prev_answer_size = size

            action = decide_pty_action(
                now=now,
                start=start,
                last_output=last_output,
                seen_output=seen_output,
                submitted=submitted,
                response_seen=response_seen,
                exit_sent_at=exit_sent_at,
                submitted_at=submitted_at,
                first_output_seconds=first_output_seconds,
                submit_settle_seconds=submit_settle_seconds,
                idle_exit_seconds=idle_exit_seconds,
                exit_grace_seconds=exit_grace_seconds,
                input_box_seen=input_box_seen,
                first_output_at=first_output_at,
                text_written=text_written,
                text_written_at=text_written_at,
                enter_delay_seconds=enter_delay_seconds,
                answer_ready=answer_ready,
                answer_ready_at=answer_ready_at,
                awaiting_answer_file=bool(answer_file),
            )

            if action == "wait":
                continue
            # Defect 1, phase 1: write the trigger TEXT (no \r — gluing it makes
            # bracketed-paste swallow the Enter).
            if action == "submit_text":
                if text_bytes:
                    _write_all(master_fd, text_bytes)
                text_written = True
                text_written_at = now
                # Reset the idle baseline so the enter-delay window (not a stale
                # pre-text quiet) governs the next decision.
                last_output = now
                continue
            # Defect 1, phase 2: write a bare \r ALONE after the paste settle —
            # this is the keystroke that actually submits the turn.
            if action == "submit_enter":
                _write_all(master_fd, b"\r")
                submitted = True
                submitted_at = now
                # Reset the idle baseline so the post-submit response gate (not a
                # stale pre-submit quiet) governs the next decision.
                last_output = now
                continue
            if action == "exit":
                _write_all(master_fd, b"\n/exit\n")
                exit_sent_at = now
                continue
            if action == "terminate":
                _signal_tree(pgid, signal.SIGTERM)
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    _signal_tree(pgid, signal.SIGKILL)
                break
            if action == "kill":
                _signal_tree(pgid, signal.SIGKILL)
                break
    finally:
        try:
            os.close(master_fd)
        except OSError:
            pass

    try:
        returncode = proc.wait(timeout=1)
    except subprocess.TimeoutExpired:
        _signal_tree(pgid, signal.SIGKILL)
        returncode = proc.wait(timeout=1)

    raw = "".join(chunks)

    # Defect 2: prefer the out-of-band answer file. Interactive Claude is a
    # cursor-addressed TUI, so the cleaned transcript is a best-effort fallback
    # only. If Claude wrote a non-empty answer file we return that and normalize
    # the returncode to success — the answer was produced even if ``/exit`` left
    # a non-zero code (e.g. 143 from the SIGTERM teardown).
    if answer_file:
        try:
            with open(answer_file, encoding="utf-8", errors="replace") as fh:
                answer = fh.read().strip()
        except OSError:
            answer = ""
        # FINDING 1: unlink this turn's unique answer file (best-effort, never
        # raise) so the persistent per-tenant ``session_dir`` doesn't accumulate
        # one answer_<hex>.md per turn. Done whether or not it had content — an
        # empty file is still this turn's leftover. The unique name means we only
        # ever remove OUR file, never a concurrent turn's.
        try:
            os.unlink(answer_file)
        except OSError:
            pass
        if answer:
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout=answer,
                stderr="",
            )

    return subprocess.CompletedProcess(
        args=cmd,
        returncode=returncode,
        stdout=clean_interactive_transcript(raw, prompt),
        stderr="" if returncode == 0 else raw,
    )


__all__ = [
    "clean_interactive_transcript",
    "decide_pty_action",
    "run_claude_interactive_with_heartbeat",
]
