"""Tests for the subprocess wrapper helpers in workflows.py.

Targets the previously-unexercised loop bodies:

  * ``_run``                 — line 177-190 (sync shell wrapper, raises on fail)
  * ``_run_long_command``    — line 193-244 (Popen + heartbeat loop)
  * ``_run_cli_with_heartbeat`` — line 247-317 (Popen + ThreadPool + heartbeat
    poll loop). Phase 4 stubbed this entire helper because heartbeats touch
    the Temporal activity context, which only exists inside the worker.

Strategy: patch ``workflows.activity.heartbeat`` to a counted-call no-op,
patch ``subprocess.Popen`` with a fake process whose ``communicate`` returns
fast, and assert on the heartbeat call counts and resulting CompletedProcess.
"""
from __future__ import annotations

import concurrent.futures
import subprocess

import pytest

import cli_runtime
import workflows as wf


# ── _run (177-190) ───────────────────────────────────────────────────────

class TestRunHelper:
    """The narrow shell-out wrapper that raises on non-zero exit."""

    def test_returns_stdout_stripped_on_success(self, monkeypatch):
        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="hello\n", stderr="",
            )

        monkeypatch.setattr(wf.subprocess, "run", fake_run)
        assert wf._run(["echo", "hello"]) == "hello"

    def test_raises_runtime_error_on_non_zero(self, monkeypatch):
        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(
                args=cmd, returncode=2, stdout="oops", stderr="bad thing",
            )

        monkeypatch.setattr(wf.subprocess, "run", fake_run)
        with pytest.raises(RuntimeError, match="Command failed"):
            wf._run(["false"])

    def test_passes_extra_env(self, monkeypatch):
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["env"] = kwargs.get("env")
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="ok", stderr="",
            )

        monkeypatch.setattr(wf.subprocess, "run", fake_run)
        wf._run(["noop"], extra_env={"FOO": "bar"})
        assert captured["env"]["FOO"] == "bar"


# ── _run_long_command (193-244) ──────────────────────────────────────────

class _FakeLongPopen:
    """Fake Popen whose ``poll()`` returns None for ``polls_until_exit``
    iterations, then ``returncode`` thereafter. ``communicate()`` returns
    canned stdout/stderr."""

    def __init__(self, *, polls_until_exit=2, returncode=0, stdout="ok", stderr=""):
        self._polls_remaining = polls_until_exit
        self.returncode = None
        self._final_returncode = returncode
        self._stdout = stdout
        self._stderr = stderr
        self.killed = False

    def poll(self):
        if self._polls_remaining > 0:
            self._polls_remaining -= 1
            return None
        self.returncode = self._final_returncode
        return self._final_returncode

    def communicate(self, timeout=None):
        if self.returncode is None:
            self.returncode = self._final_returncode
        return self._stdout, self._stderr

    def kill(self):
        self.killed = True


class TestRunLongCommand:
    """Drives the Popen + heartbeat loop in ``_run_long_command``."""

    @pytest.fixture(autouse=True)
    def _fast_loop(self, monkeypatch):
        """Make ``time.sleep`` instant and ``activity.heartbeat`` a counter."""
        monkeypatch.setattr(wf.time, "sleep", lambda *_a, **_kw: None)
        self.heartbeats: list[str] = []
        monkeypatch.setattr(
            wf.activity, "heartbeat", lambda msg=None: self.heartbeats.append(msg),
        )
        # Fake monotonic that grows by 1 per call so elapsed is deterministic.
        self._t = 0.0

        def fake_monotonic():
            self._t += 1.0
            return self._t

        monkeypatch.setattr(wf.time, "monotonic", fake_monotonic)
        yield

    def test_happy_path_returns_completed_process(self, monkeypatch):
        fake = _FakeLongPopen(polls_until_exit=2, returncode=0, stdout="done", stderr="")

        monkeypatch.setattr(
            wf.subprocess, "Popen", lambda *a, **kw: fake,
        )

        result = wf._run_long_command(
            ["echo", "hi"],
            cwd="/tmp",
            timeout=10_000,
            heartbeat_message="working",
            heartbeat_interval=1,
        )
        assert result.returncode == 0
        assert result.stdout == "done"
        # At least one heartbeat fired during the polling loop.
        assert any("working" in (h or "") for h in self.heartbeats)

    def test_non_zero_exit_raises(self, monkeypatch):
        fake = _FakeLongPopen(polls_until_exit=1, returncode=3, stdout="", stderr="boom")
        monkeypatch.setattr(wf.subprocess, "Popen", lambda *a, **kw: fake)

        with pytest.raises(RuntimeError, match="Command failed"):
            wf._run_long_command(
                ["bad"], cwd="/tmp", timeout=10_000,
                heartbeat_message="x", heartbeat_interval=1,
            )

    def test_timeout_kills_subprocess(self, monkeypatch):
        # Process never exits — poll always returns None.
        fake = _FakeLongPopen(polls_until_exit=10_000, returncode=0)
        monkeypatch.setattr(wf.subprocess, "Popen", lambda *a, **kw: fake)

        with pytest.raises(RuntimeError, match="timed out"):
            wf._run_long_command(
                ["sleeper"], cwd="/tmp", timeout=2,
                heartbeat_message="x", heartbeat_interval=1,
            )
        assert fake.killed is True


# ── _run_cli_with_heartbeat (247-317) ─────────────────────────────────────

class _FakeStream:
    """Tiny readable file-like for the line-reader threads.

    ``readline()`` returns the next line until exhausted, then "" to
    signal EOF — the canonical iter(readline, "") idiom in the helper
    terminates on that empty string.
    """

    def __init__(self, content: str = ""):
        # Split preserving newlines so each call returns a single line.
        self._lines = content.splitlines(keepends=True) if content else []
        self._idx = 0
        self.closed = False

    def readline(self):
        if self._idx >= len(self._lines):
            return ""
        ln = self._lines[self._idx]
        self._idx += 1
        return ln

    def close(self):
        self.closed = True


class _FakeChatPopen:
    """Fake Popen for the heartbeat wrapper (Phase 5: line-reader pump).

    The helper now drains ``stdout``/``stderr`` via two reader threads
    instead of ``communicate()``. We expose ``_FakeStream`` instances so
    those threads see canned output and reach EOF cleanly. The main
    thread loops on ``poll()`` to decide when the child exited — we
    flip ``returncode`` from None to the configured value after
    ``block_polls`` polls, so the helper has time to fire at least one
    "running..." heartbeat.

    For the timeout-expired test path, set ``raise_on_kill=False`` and
    keep ``returncode=None`` forever; the helper's main loop kills the
    process when ``time.monotonic()`` advances past ``timeout``.
    """

    def __init__(
        self,
        *,
        block_polls: int = 2,
        returncode: int = 0,
        stdout: str = "result",
        stderr: str = "",
        raise_timeout_expired: bool = False,
        # Kept for back-compat with older tests; not used internally.
        block_seconds: float = 0.0,
    ):
        self._block_polls = block_polls
        self._final_returncode = returncode
        self.stdout = _FakeStream(stdout)
        self.stderr = _FakeStream(stderr)
        self._raise_timeout_expired = raise_timeout_expired
        self._poll_calls = 0
        self.killed = False
        # When True: returncode is None forever, forcing the helper's
        # timeout branch to fire (used by the timeout test).
        if raise_timeout_expired:
            self.returncode = None
            self._final_returncode = None
        else:
            self.returncode = None
        # Retained but unused — silences any test that still passes it.
        self._block_seconds = block_seconds

    def poll(self):
        # Stay alive for `block_polls` calls, then flip to the final
        # returncode so the helper's main loop sees the exit.
        self._poll_calls += 1
        if self._raise_timeout_expired:
            return None
        if self._poll_calls <= self._block_polls:
            return None
        self.returncode = self._final_returncode
        return self._final_returncode

    def kill(self):
        self.killed = True
        # Drain threads need to see EOF — they already do, since
        # readline() has exhausted the canned content. Setting
        # returncode here lets the helper's exception handler unwind
        # cleanly.
        if self.returncode is None:
            self.returncode = -9

    # Back-compat shim for any test that still calls communicate().
    def communicate(self, timeout=None):
        if self._raise_timeout_expired:
            raise subprocess.TimeoutExpired(cmd=["fake"], timeout=timeout)
        return "".join(self.stdout._lines), "".join(self.stderr._lines)


class TestRunCliWithHeartbeat:
    """Phase 4 stubbed this helper out entirely. Phase 4.5 actually exercises
    the loop: heartbeat -> poll future -> heartbeat -> poll future -> done."""

    @pytest.fixture(autouse=True)
    def _patch_clocks(self, monkeypatch):
        self.heartbeats: list[str] = []
        monkeypatch.setattr(
            cli_runtime.activity, "heartbeat", lambda msg=None: self.heartbeats.append(msg),
        )
        self._t = 0.0

        def fake_monotonic():
            self._t += 1.0
            return self._t

        monkeypatch.setattr(cli_runtime.time, "monotonic", fake_monotonic)
        yield

    def test_loop_emits_heartbeats_until_subprocess_completes(self, monkeypatch):
        """Heartbeat must fire at start + on every future-result timeout iteration
        before the subprocess finally exits."""
        fake = _FakeChatPopen(block_seconds=0.05, returncode=0, stdout="OUT", stderr="")
        monkeypatch.setattr(cli_runtime.subprocess, "Popen", lambda *a, **kw: fake)

        # heartbeat_interval << block_seconds — each future.result(timeout=...)
        # times out quickly while ``communicate`` is still sleeping inside the
        # worker thread, forcing the loop to fire a "running..." heartbeat.
        result = cli_runtime.run_cli_with_heartbeat(
            ["fakecli"], label="Fake CLI",
            timeout=1000,
            env={}, cwd="/tmp",
            heartbeat_interval=0.005,
        )
        assert isinstance(result, subprocess.CompletedProcess)
        assert result.returncode == 0
        assert result.stdout == "OUT"
        # Starting heartbeat + at least one in-flight heartbeat.
        assert any("starting" in (h or "") for h in self.heartbeats)
        assert any("running" in (h or "") for h in self.heartbeats)

    def test_subprocess_timeout_kills_and_reraises(self, monkeypatch):
        fake = _FakeChatPopen(raise_timeout_expired=True)
        monkeypatch.setattr(cli_runtime.subprocess, "Popen", lambda *a, **kw: fake)

        with pytest.raises(subprocess.TimeoutExpired):
            cli_runtime.run_cli_with_heartbeat(
                ["fakecli"], label="X",
                timeout=1, env={}, cwd="/tmp",
                heartbeat_interval=0.001,
            )
        # The inner _wait_and_drain calls kill() before re-raising.
        assert fake.killed is True

    def test_unhandled_exception_kills_subprocess(self, monkeypatch):
        """Any non-timeout exception in the main loop must still kill the
        subprocess — the BaseException handler at the bottom of
        ``run_cli_with_heartbeat`` covers cancel + unexpected errors.

        Phase 5 (line-reader pump) note: the previous test patched
        ``communicate()`` to raise, but the new impl polls + drains via
        threads. Equivalent failure mode: ``poll()`` itself raises
        mid-loop. Same recovery contract: kill the subprocess, propagate
        the exception.
        """

        class _ExplodingPopen:
            killed = False

            def __init__(self):
                self.stdout = _FakeStream("")
                self.stderr = _FakeStream("")
                self.returncode = None
                self._poll_calls = 0

            def poll(self):
                self._poll_calls += 1
                # First call (right after Popen) returns None so the main
                # loop enters. Second call raises — fires BaseException
                # branch. Subsequent calls (inside the exception handler:
                # "if proc.poll() is None: kill") return None so the
                # handler proceeds to kill().
                if self._poll_calls == 2:
                    raise RuntimeError("boom")
                return None

            def kill(self):
                self.killed = True
                self.returncode = -9

        fake = _ExplodingPopen()
        monkeypatch.setattr(cli_runtime.subprocess, "Popen", lambda *a, **kw: fake)

        with pytest.raises(RuntimeError, match="boom"):
            cli_runtime.run_cli_with_heartbeat(
                ["fakecli"], label="X",
                timeout=10, env={}, cwd="/tmp",
                heartbeat_interval=0.001,
            )
        assert fake.killed is True
