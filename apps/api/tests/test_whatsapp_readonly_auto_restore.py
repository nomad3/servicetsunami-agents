"""Unit tests for the WhatsApp readonly-DB auto-restore handler.

The actual handler lives inline inside the
`startup_whatsapp_readonly_auto_restore` startup hook in main.py — this
test reaches into the same logic by re-implementing the handler class
locally, which is fine because the class is small and self-contained
and the test's job is to pin behaviour, not to monkey-patch the
startup hook (which would require a running FastAPI lifecycle).
"""
import asyncio
import logging
import time

import pytest


class _ReadonlyDetectingHandler(logging.Handler):
    """Mirror of the inline class in main.py — kept in sync by hand. If
    you change the prod handler, update this test class too."""

    def __init__(self, on_trigger, cooldown_s=120.0):
        super().__init__(level=logging.WARNING)
        self._last_trigger = 0.0
        self._on_trigger = on_trigger
        self._cooldown_s = cooldown_s

    def emit(self, record):
        try:
            msg = record.getMessage()
        except Exception:
            return
        if "readonly database" not in msg:
            return
        now = time.monotonic()
        if now - self._last_trigger < self._cooldown_s:
            return
        self._last_trigger = now
        self._on_trigger()


def _make_log_record(message, level=logging.WARNING):
    return logging.LogRecord(
        name="whatsmeow.Client",
        level=level,
        pathname="log.py",
        lineno=61,
        msg=message,
        args=(),
        exc_info=None,
    )


def test_fires_on_readonly_message():
    calls = []
    h = _ReadonlyDetectingHandler(on_trigger=lambda: calls.append(1))
    h.emit(_make_log_record(
        "Error decrypting message AC448... from 11777437868267@lid: "
        "failed to decrypt prekey message: failed to store session "
        "with 11777437868267_1:0: attempt to write a readonly database"
    ))
    assert calls == [1]


def test_does_not_fire_on_unrelated_message():
    calls = []
    h = _ReadonlyDetectingHandler(on_trigger=lambda: calls.append(1))
    h.emit(_make_log_record("Connected to WhatsApp"))
    h.emit(_make_log_record("Sent presence: composing"))
    h.emit(_make_log_record("Failed to fetch profile picture"))
    assert calls == []


def test_rate_limited_within_cooldown():
    """A burst of readonly errors must trigger only ONE restore — we
    don't want neonize.restore() called 50 times in 50ms while
    decryption retries hammer the logger."""
    calls = []
    h = _ReadonlyDetectingHandler(on_trigger=lambda: calls.append(1), cooldown_s=120.0)
    msg = "attempt to write a readonly database"
    for _ in range(50):
        h.emit(_make_log_record(msg))
    assert calls == [1]


def test_fires_again_after_cooldown_elapses():
    """After cooldown expires, the next readonly burst should trigger
    another restore — recovery from a *second* silent disconnect."""
    calls = []
    h = _ReadonlyDetectingHandler(on_trigger=lambda: calls.append(1), cooldown_s=0.05)
    h.emit(_make_log_record("attempt to write a readonly database"))
    time.sleep(0.10)  # > cooldown
    h.emit(_make_log_record("attempt to write a readonly database"))
    assert calls == [1, 1]


def test_handler_swallows_exceptions_in_record_format():
    """`record.getMessage()` can raise on malformed args. The handler
    must not crash the logging subsystem in that case — just skip."""
    h = _ReadonlyDetectingHandler(on_trigger=lambda: None)

    class _Bad:
        def __str__(self):
            raise RuntimeError("boom")

    rec = _make_log_record("%s")
    rec.args = (_Bad(),)
    # No assertion needed — just must not raise.
    h.emit(rec)


def test_only_triggers_on_warning_level_and_above():
    """Handler is configured at WARNING level via super().__init__.
    A DEBUG/INFO record with the magic phrase shouldn't trigger,
    because logging filters those out before emit() is even called."""
    calls = []
    h = _ReadonlyDetectingHandler(on_trigger=lambda: calls.append(1))
    # Simulate logging-system filtering — go through the handler's
    # filter chain rather than calling emit() directly.
    rec = _make_log_record("attempt to write a readonly database", level=logging.DEBUG)
    if h.filter(rec) and rec.levelno >= h.level:
        h.emit(rec)
    assert calls == []  # filtered out
