"""Tests for the gRPC retry policy on the memory-core client.

The Python gRPC client uses C-Ares for DNS resolution which intermittently
fails right after a docker-compose redeploy (the DNS cache hasn't warmed
yet, getent works but C-Ares times out). Before the retry policy was
added, every such hiccup immediately failed over to the Python recall
path — slower than the Rust path and missing Rust-only fields.

Live diagnostic 2026-05-07: a chat turn at 13:54:38 logged
`Rust recall failed (will reconnect next call): UNAVAILABLE — errors
resolving memory-core:50052: Domain name not found`. DNS was working
fine 30 seconds before AND 30 seconds after — pure C-Ares hiccup.

This test validates the retry config is wired up correctly so that
transient UNAVAILABLE errors auto-retry instead of failing the whole
turn. The actual retry behaviour is enforced by gRPC's runtime
(not unit-testable in pure Python without a fake server) — we just pin
the channel options so a future refactor doesn't accidentally drop
them.
"""
import json
import os
from unittest.mock import patch, MagicMock

import pytest


def _stub_grpc_module():
    """Minimal grpc replacement so _get_grpc_stub can run without the
    real grpc lib needing to talk to anything."""
    fake = MagicMock()
    captured_options = {}

    def _insecure_channel(url, options=None):
        captured_options['url'] = url
        captured_options['options'] = options or []
        return MagicMock(name='channel')

    fake.insecure_channel.side_effect = _insecure_channel
    return fake, captured_options


@pytest.fixture(autouse=True)
def _reset_grpc_module_singleton():
    """Reset the recall module's cached singleton between tests."""
    import sys
    import app.memory  # ensures the module package is loaded first
    # The submodule itself, not the `recall` function re-exported from
    # __init__.py — sys.modules has the real submodule under this key.
    recall = sys.modules['app.memory.recall']
    recall._grpc_channel = None
    recall._grpc_stub = None
    yield
    recall._grpc_channel = None
    recall._grpc_stub = None


def test_grpc_channel_has_retry_options_wired():
    """A future refactor must not silently drop the retry policy —
    every transient C-Ares hiccup turns into a Rust→Python fallback
    if we lose this."""
    fake_grpc, captured = _stub_grpc_module()
    with patch.dict(os.environ, {'MEMORY_CORE_URL': 'memory-core:50052'}):
        with patch('app.memory.recall._grpc', fake_grpc):
            import sys
            recall_mod = sys.modules['app.memory.recall']
            recall_mod._get_grpc_stub()

    options = dict(captured.get('options') or [])
    # Retry switch must be on
    assert options.get('grpc.enable_retries') == 1, (
        "grpc.enable_retries must be 1 — without it the service_config "
        "retryPolicy has no effect."
    )
    # Service config must include a UNAVAILABLE retry rule
    sc = options.get('grpc.service_config')
    assert sc, 'grpc.service_config option missing'
    parsed = json.loads(sc)
    method_configs = parsed.get('methodConfig') or []
    assert method_configs, 'methodConfig missing from service_config'
    policy = method_configs[0].get('retryPolicy', {})
    assert 'UNAVAILABLE' in policy.get('retryableStatusCodes', []), (
        'UNAVAILABLE must be retryable — that is the exact status code '
        'the C-Ares DNS hiccup surfaces as.'
    )
    # Three attempts total (1 initial + 2 retries) keeps worst-case
    # latency bounded (~350ms with 50/100/200ms backoffs)
    assert policy.get('maxAttempts', 0) >= 2
    assert policy.get('maxAttempts', 0) <= 5  # don't go bananas


def test_grpc_keepalive_options_preserved():
    """Existing keepalive options must NOT regress when retries are
    added — they're load-bearing for connection liveness across
    long idle gaps between chat turns."""
    fake_grpc, captured = _stub_grpc_module()
    with patch.dict(os.environ, {'MEMORY_CORE_URL': 'memory-core:50052'}):
        with patch('app.memory.recall._grpc', fake_grpc):
            import sys
            recall_mod = sys.modules['app.memory.recall']
            recall_mod._get_grpc_stub()

    options = dict(captured.get('options') or [])
    assert options.get('grpc.keepalive_time_ms') == 30000
    assert options.get('grpc.keepalive_timeout_ms') == 5000
    assert options.get('grpc.keepalive_permit_without_calls') == 1
    assert options.get('grpc.max_receive_message_length') == 16 * 1024 * 1024


def test_no_url_returns_none_without_attempting_connection():
    """If MEMORY_CORE_URL is unset (e.g. local dev without the Rust
    service running), _get_grpc_stub should bail cleanly — not try
    to connect to an empty hostname and crash."""
    fake_grpc, _ = _stub_grpc_module()
    with patch.dict(os.environ, {}, clear=True):
        with patch('app.memory.recall._grpc', fake_grpc):
            import sys
            recall_mod = sys.modules['app.memory.recall']
            assert recall_mod._get_grpc_stub() is None
    fake_grpc.insecure_channel.assert_not_called()
