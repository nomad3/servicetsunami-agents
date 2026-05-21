"""Table-driven classification tests — design §2.

One named test per row in the §2 classification table. Test IDs are
explicit (no anonymous tuple positions) so when an upstream string
changes the failing test names the regression site exactly. Every row
in the design table appears here as a top-level case in
``CLASSIFICATION_CASES``.

Naming contract (the plan's ship gate):
    test_classify[<test_id>]
where ``<test_id>`` matches the ``_Rule.test_id`` field on the matching
rule (or an ``exception_*`` / ``fallthrough_*`` / ``success_*`` id for
the non-stderr rows). The pytest ``ids=lambda c: c[0]`` parametrisation
plumbs the id straight into the report.
"""
from __future__ import annotations

import asyncio
import subprocess
from typing import Optional

import pytest

from app.services.cli_orchestrator.classifier import (
    classify,
    classify_with_legacy_label,
)
from app.services.cli_orchestrator.status import Status


# (test_id, stderr, exit_code, exc, expected_status, expected_legacy_label)
CLASSIFICATION_CASES: list[
    tuple[str, Optional[str], Optional[int], Optional[BaseException], Status, Optional[str]]
] = [
    # ── 1. claude_code QUOTA — credit/usage/plan signals ────────────────
    (
        "claude_code_credit_balance_too_low_is_quota_exhausted",
        "Your credit balance is too low to continue this conversation.",
        1, None, Status.QUOTA_EXHAUSTED, "quota",
    ),
    # ── 2. claude_code QUOTA — subscription / hit-your-limit ────────────
    (
        "claude_code_subscription_required_is_quota_exhausted",
        "Subscription required for advanced models.",
        1, None, Status.QUOTA_EXHAUSTED, "quota",
    ),
    # ── 3. CLI not-connected → NEEDS_AUTH with "missing_credential" ─────
    (
        "cli_not_connected_is_needs_auth_with_missing_credential_label",
        "Claude Code subscription is not connected. Please connect your account in Settings.",
        1, None, Status.NEEDS_AUTH, "missing_credential",
    ),
    # ── 4. codex QUOTA — rate / quota / 429 ─────────────────────────────
    (
        "codex_rate_limit_is_quota_exhausted",
        "openai: rate limit exceeded; HTTP 429 too many requests",
        1, None, Status.QUOTA_EXHAUSTED, "quota",
    ),
    # ── 5. codex AUTH — 401 / invalid_grant / token_expired ─────────────
    (
        "codex_unauthorized_is_needs_auth",
        "401 Unauthorized: invalid_grant — token expired",
        1, None, Status.NEEDS_AUTH, "auth",
    ),
    # ── 5b. codex credential-loader connection refused ──────────────────
    # Real prod stderr 2026-05-21 on Simon's tenant. Same shape +
    # mechanism as the Gemini case (rule 6b from #628).
    (
        "codex_credential_load_failure_is_quota_exhausted",
        "Failed to load Codex credentials: [Errno 111] Connection refused",
        1, None, Status.QUOTA_EXHAUSTED, "quota",
    ),
    # ── 6. gemini QUOTA — quota_exceeded / resource_exhausted ───────────
    (
        "gemini_cli_quota_exceeded_is_quota_exhausted",
        "Gemini API: resource_exhausted — quota_exceeded for project",
        1, None, Status.QUOTA_EXHAUSTED, "quota",
    ),
    # ── 6b. gemini credential-loader connection refused ─────────────────
    # Real prod stderr observed 2026-05-20 on AgentProvision tenant.
    # Without this rule the chain treats it as generic failure, never
    # cooldowns Gemini, and re-picks it every chat turn while Codex
    # sits idle.
    (
        "gemini_cli_credential_load_failure_is_quota_exhausted",
        "ChatCliWorkflow result: success=False error=Failed to load "
        "Gemini credentials: [Errno 111] Connection refused response_len=0",
        1, None, Status.QUOTA_EXHAUSTED, "quota",
    ),
    # ── 6c. gemini upstream 5xx (Google API outage) ─────────────────────
    # Real prod stderr observed 2026-05-21 — Gemini CLI got 502 from
    # generativelanguage.googleapis.com mid-task. Generic rule #12
    # alone would classify as RETRYABLE_NETWORK_FAILURE with no
    # cooldown, leaving the chain stuck re-picking Gemini while Google
    # is down. This rule routes around the outage to Codex.
    (
        "gemini_cli_upstream_5xx_gaxios_is_quota_exhausted",
        "Attempt 1 failed with status 502. Retrying with backoff... "
        "_GaxiosError: <html>Error 502 (Server Error)!!1 ...",
        1, None, Status.QUOTA_EXHAUSTED, "quota",
    ),
    (
        "gemini_cli_upstream_5xx_googleapis_is_quota_exhausted",
        "POST https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-2.5-pro:generateContent — got 503 Service Unavailable",
        1, None, Status.QUOTA_EXHAUSTED, "quota",
    ),
    # ── 7. gemini WORKSPACE_UNTRUSTED ───────────────────────────────────
    (
        "gemini_cli_workspace_setup_is_workspace_untrusted",
        "Untrusted workspace — please run gemini workspace setup",
        1, None, Status.WORKSPACE_UNTRUSTED, None,
    ),
    # ── 8. gemini API_DISABLED ──────────────────────────────────────────
    (
        "gemini_cli_api_disabled_is_api_disabled",
        "API disabled — please enable the Gemini API in console.cloud.google.com",
        1, None, Status.API_DISABLED, None,
    ),
    # ── 9. gemini PERMISSION_DENIED → NEEDS_AUTH ────────────────────────
    (
        "gemini_cli_permission_denied_is_needs_auth",
        "permission_denied: caller does not have access",
        1, None, Status.NEEDS_AUTH, "auth",
    ),
    # ── 10. copilot QUOTA — subscription / not enabled / 429 ────────────
    (
        "copilot_cli_subscription_required_is_quota_exhausted",
        "GitHub Copilot is not enabled for this organization (HTTP 429)",
        1, None, Status.QUOTA_EXHAUSTED, "quota",
    ),
    # ── 11. copilot AUTH — not authorized / 401 / 403 ───────────────────
    (
        "copilot_cli_not_authorized_is_needs_auth",
        "gh copilot: 403 not authorized for this account",
        1, None, Status.NEEDS_AUTH, "auth",
    ),
    # ── 12. any RETRYABLE_NETWORK_FAILURE ───────────────────────────────
    (
        "any_econnreset_is_retryable_network_failure",
        "fetch failed: ECONNRESET (TLS handshake aborted) — HTTP 503",
        1, None, Status.RETRYABLE_NETWORK_FAILURE, None,
    ),
    # ── 13. exception: TimeoutError / subprocess.TimeoutExpired → TIMEOUT
    (
        "exception_timeout_is_timeout",
        None, None, asyncio.TimeoutError(),
        Status.TIMEOUT, None,
    ),
    # ── 14. exception: temporalio.exceptions.* → WORKFLOW_FAILED ────────
    # Implementation: lazy-imported. We use a plain CancelledError as a
    # stand-in for the WORKFLOW_FAILED branch — the classifier maps
    # CancelledError to WORKFLOW_FAILED unconditionally per design §2
    # (Temporal teardown is the only realistic source of cancellation
    # mid-activity in production, and CancelledError is what we
    # actually catch when temporalio is or isn't installed).
    (
        "exception_cancelled_is_workflow_failed",
        None, None, asyncio.CancelledError(),
        Status.WORKFLOW_FAILED, None,
    ),
    # ── 15. binary missing → PROVIDER_UNAVAILABLE ───────────────────────
    (
        "exception_filenotfound_is_provider_unavailable",
        None, None, FileNotFoundError("claude"),
        Status.PROVIDER_UNAVAILABLE, None,
    ),
    # ── 16. no rule matched → UNKNOWN_FAILURE ───────────────────────────
    (
        "fallthrough_unknown_string_is_unknown_failure",
        "Tool 'sql_query' raised: division by zero",
        1, None, Status.UNKNOWN_FAILURE, None,
    ),
    # ── 17. happy-path — no stderr / no exception → UNKNOWN_FAILURE ─────
    # The classifier itself never returns EXECUTION_SUCCEEDED because
    # success is signalled by the adapter (exit code 0, no exception)
    # without calling classify(). We pin "no signal" → UNKNOWN_FAILURE
    # explicitly so a future regression that flips that to a different
    # default fails this named test.
    (
        "fallthrough_empty_input_is_unknown_failure",
        None, None, None,
        Status.UNKNOWN_FAILURE, None,
    ),
    # ── 18 / 19. Phase 1.5 review I-A — bare-token narrowing ────────────
    # The legacy CODEX_CREDIT_ERROR_PATTERNS tuple in workflows.py has
    # ``billing`` and ``capacity`` as bare substrings. Acceptable on the
    # worker side where those tokens only show up in subprocess stderr,
    # but the classifier ALSO feeds apps/api's chat hot path via
    # cli_platform_resolver — bare tokens there false-positive on
    # casual prose. Rule was tightened to require an adjacent failure
    # word. These tests pin that the bare tokens DO NOT classify; the
    # anchored forms still do (covered by codex_rate_limit_is_quota_exhausted).
    (
        "bare_capacity_in_prose_is_unknown_failure",
        "the capacity planning meeting is at 3pm in conference room B",
        1, None, Status.UNKNOWN_FAILURE, None,
    ),
    (
        "bare_billing_in_prose_is_unknown_failure",
        "monthly billing invoice was generated and emailed",
        1, None, Status.UNKNOWN_FAILURE, None,
    ),
]


@pytest.mark.parametrize(
    "case",
    CLASSIFICATION_CASES,
    ids=lambda c: c[0],
)
def test_classify(case):
    """Each row of the §2 table is one named test."""
    test_id, stderr, exit_code, exc, expected_status, _expected_legacy = case
    actual = classify(stderr, exit_code=exit_code, exc=exc)
    assert actual == expected_status, (
        f"{test_id}: classify({stderr!r}, exit_code={exit_code}, exc={type(exc).__name__ if exc else None})"
        f" returned {actual!r}, expected {expected_status!r}"
    )


@pytest.mark.parametrize(
    "case",
    CLASSIFICATION_CASES,
    ids=lambda c: c[0],
)
def test_classify_with_legacy_label(case):
    """Same walk, plus the legacy label.

    Locks the wrapper contract used by ``cli_platform_resolver.
    classify_error`` and the three ``_is_*_credit_exhausted`` helpers
    in ``apps/code-worker/workflows.py``. A drift here would silently
    change chain-fallback behavior in production.
    """
    test_id, stderr, exit_code, exc, expected_status, expected_legacy = case
    actual_status, actual_legacy = classify_with_legacy_label(
        stderr, exit_code=exit_code, exc=exc
    )
    assert actual_status == expected_status, f"{test_id}: status mismatch"
    assert actual_legacy == expected_legacy, (
        f"{test_id}: legacy_label expected {expected_legacy!r}, got {actual_legacy!r}"
    )


def test_subprocess_timeout_expired_classifies_as_timeout():
    """``subprocess.TimeoutExpired`` is a separate exception type from
    ``TimeoutError`` and ``asyncio.TimeoutError`` — pin that the rule
    catches all three (the §2 design row 13)."""
    exc = subprocess.TimeoutExpired(cmd=["claude", "-p"], timeout=1500)
    assert classify(None, exc=exc) == Status.TIMEOUT
    status, legacy = classify_with_legacy_label(None, exc=exc)
    assert status == Status.TIMEOUT
    assert legacy is None


def test_exception_rule_wins_over_stderr_rule():
    """When both an exception and stderr are present, the exception
    rule wins. Mid-activity Temporal teardown will give us a
    ``CancelledError`` plus whatever stderr the subprocess emitted
    before it died — we want WORKFLOW_FAILED, not the stderr-derived
    status. (§2 row order locks this.)"""
    exc = asyncio.CancelledError()
    # stderr would otherwise classify as QUOTA_EXHAUSTED — verify the
    # exception path overrides it.
    stderr = "rate limit exceeded"
    assert classify(stderr, exc=exc) == Status.WORKFLOW_FAILED


def test_classify_handles_none_stderr_without_exception():
    """Defence: ``classify(None)`` does not raise — returns UNKNOWN_FAILURE."""
    assert classify(None) == Status.UNKNOWN_FAILURE
    assert classify_with_legacy_label(None) == (Status.UNKNOWN_FAILURE, None)
