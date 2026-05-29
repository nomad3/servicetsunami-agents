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

import os
import pty
import re
import select
import subprocess
import time
from collections.abc import Callable


_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_OSC_RE = re.compile(r"\x1b\].*?(?:\x07|\x1b\\)")


def clean_interactive_transcript(raw: str, prompt: str = "") -> str:
    """Return a readable answer from a Claude Code terminal transcript.

    This is a best-effort fallback, not a protocol parser. The interactive
    UI is meant for humans, so we strip ANSI/control noise and common box/
    prompt chrome while preserving useful assistant text and command output.
    """
    text = _OSC_RE.sub("", raw)
    text = _ANSI_RE.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    prompt = (prompt or "").strip()

    cleaned: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            if cleaned and cleaned[-1]:
                cleaned.append("")
            continue
        if prompt and stripped in {prompt, f"> {prompt}"}:
            continue
        if stripped in {"/exit", "exit"}:
            continue
        if stripped.startswith(("╭", "╰", "│", "┌", "└", "┃", "┗", "┏")):
            continue
        if stripped in {"?", ">", "Welcome to Claude Code"}:
            continue
        if stripped.startswith(("Claude Code", "By using Claude Code")):
            continue
        cleaned.append(stripped)

    return "\n".join(cleaned).strip()


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
) -> subprocess.CompletedProcess[str]:
    """Run Claude Code attached to a PTY and return a CompletedProcess.

    ``cmd`` should start an interactive Claude session, normally with the
    user's prompt as the positional argument. Since the interactive CLI
    does not exit after a turn, we send ``/exit`` after the terminal has
    been quiet for ``idle_exit_seconds``.
    """
    idle_exit_seconds = idle_exit_seconds if idle_exit_seconds is not None else float(
        os.environ.get("CLAUDE_CODE_INTERACTIVE_IDLE_EXIT_SECONDS", "8")
    )
    exit_grace_seconds = exit_grace_seconds if exit_grace_seconds is not None else float(
        os.environ.get("CLAUDE_CODE_INTERACTIVE_EXIT_GRACE_SECONDS", "10")
    )

    master_fd, slave_fd = pty.openpty()
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        env=env,
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        text=False,
        close_fds=True,
    )
    os.close(slave_fd)

    chunks: list[str] = []
    start = time.monotonic()
    last_output = start
    last_heartbeat = start
    exit_sent_at: float | None = None

    try:
        while True:
            now = time.monotonic()
            if heartbeat and now - last_heartbeat >= 30:
                heartbeat(f"{label} interactive ({int(now - start)}s elapsed)")
                last_heartbeat = now
            if now - start >= timeout:
                proc.kill()
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
                    if on_chunk:
                        on_chunk(text, "stdout")
                    continue

            if proc.poll() is not None:
                break

            idle_for = now - last_output
            if exit_sent_at is None and idle_for >= idle_exit_seconds:
                os.write(master_fd, b"\n/exit\n")
                exit_sent_at = now
                continue

            if exit_sent_at is not None and now - exit_sent_at >= exit_grace_seconds:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                break
    finally:
        try:
            os.close(master_fd)
        except OSError:
            pass

    try:
        returncode = proc.wait(timeout=1)
    except subprocess.TimeoutExpired:
        proc.kill()
        returncode = proc.wait(timeout=1)

    raw = "".join(chunks)
    return subprocess.CompletedProcess(
        args=cmd,
        returncode=returncode,
        stdout=clean_interactive_transcript(raw, prompt),
        stderr="" if returncode == 0 else raw,
    )


__all__ = [
    "clean_interactive_transcript",
    "run_claude_interactive_with_heartbeat",
]
