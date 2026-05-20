"""Single-source CLI error classifier — see design §2 + Phase-1 plan.

The classifier turns ``(stderr, exit_code, exc)`` into a normalised
``Status``. It is the only place new failure-class strings get added —
every consumer (router, RL writer, council, chat-error-footer) reads
``Status`` instead of grepping subprocess output independently.

Two public functions:

* ``classify(stderr, exit_code=None, exc=None) -> Status`` — for new
  call sites that want the normalised enum.

* ``classify_with_legacy_label(...)`` — same walk, plus the legacy
  string label (``"quota"`` / ``"auth"`` / ``"missing_credential"`` /
  ``None``) that ``cli_platform_resolver.classify_error`` and the three
  ``_is_*_credit_exhausted`` wrappers in the code-worker historically
  returned. Phase 1 keeps that public seam intact so the wrappers are
  literally one-liners; Phase 2 callers can switch to ``classify``.

Order of evaluation (locked by the unit tests in
``tests/cli_orchestrator/test_classification.py``):

  1. Exception rules — TimeoutError, subprocess.TimeoutExpired,
     FileNotFoundError("<cli>"), temporalio.exceptions.* + CancelledError.
  2. Stderr rules — platform-specific regexes in declaration order, then
     the platform-agnostic network-failure rule.
  3. Fallthrough → ``Status.UNKNOWN_FAILURE`` (legacy_label=``None``).

Rules earlier in the list win. The platform tag on each rule is
informational — the actual matching is just the regex against the
provided stderr string. (Phase 2 will narrow per-adapter when each
adapter only feeds its own stderr through the classifier.)
"""
from __future__ import annotations

import asyncio
import re
import subprocess
from dataclasses import dataclass
from typing import Optional, Pattern

from .status import Status


# --------------------------------------------------------------------------
# Rule schema
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class _Rule:
    """A single stderr classification rule.

    ``platform`` is the design-§2 column ("claude_code", "codex",
    "gemini_cli", "copilot_cli", "any") — informational only, kept for
    grepability and future per-adapter narrowing.

    ``pattern`` is a precompiled case-insensitive regex.

    ``status`` is the normalised ``Status`` we return on a match.

    ``legacy_label`` is the value the legacy ``classify_error`` wrappers
    must keep returning. ``None`` for any rule that didn't exist in the
    legacy contract (network failures, workspace trust, api-disabled,
    etc. — those rules return ``Status.<X>`` but the legacy bucket maps
    them to ``None`` so the wrapper preserves "no chain skip" behavior).

    ``test_id`` is the explicit name used in the parametrized
    classification test — when an upstream string changes the failing
    test names the regression site exactly.
    """

    platform: str
    pattern: Pattern[str]
    status: Status
    legacy_label: Optional[str]
    test_id: str


# --------------------------------------------------------------------------
# Stderr rules — design §2 table, in declaration order
# --------------------------------------------------------------------------

# Note: row order matters. Earlier rules win. The platform-agnostic
# network rule lives last among stderr rules so a more-specific
# platform rule still takes precedence on overlapping strings.

_STDERR_RULES: list[_Rule] = [
    # 1. claude_code quota — primary credit / usage / plan signals
    _Rule(
        platform="claude_code",
        pattern=re.compile(
            r"credit\s*balance\s*is\s*too\s*low"
            r"|usage\s*limit\s*reached"
            r"|monthly\s*usage\s*limit"
            r"|max\s*plan\s*limit"
            r"|out\s*of\s*credits"
            # Phase 1.5 parity: legacy CLAUDE_CREDIT_ERROR_PATTERNS in
            # apps/code-worker/workflows.py also flagged "out of extra
            # usage" as a quota fragment. The corpus parity test
            # (test_credit_exhausted_parity.py) gates this; without it
            # step 5's helper rewrite would silently regress.
            r"|out\s*of\s*extra\s*usage"
            r"|insufficient\s*credits",
            re.IGNORECASE,
        ),
        status=Status.QUOTA_EXHAUSTED,
        legacy_label="quota",
        test_id="claude_code_credit_balance_too_low_is_quota_exhausted",
    ),
    # 2. claude_code quota — subscription / plan-cap secondary signals
    _Rule(
        platform="claude_code",
        pattern=re.compile(
            r"subscription\s*required|hit\s*your\s*limit",
            re.IGNORECASE,
        ),
        status=Status.QUOTA_EXHAUSTED,
        legacy_label="quota",
        test_id="claude_code_subscription_required_is_quota_exhausted",
    ),
    # 3. CLI not-connected / missing-credential — the §2 row
    # ``claude_code: not connected|please connect your`` is broadened to
    # match the legacy ``_MISSING_CRED_PATTERNS`` regex so existing
    # callers keep their behaviour. The regex anchors on integration
    # context (the words "integration", "Please connect", "subscription")
    # OR on a CLI-name word boundary, which prevents false-positives on
    # user prose like "the database is not connected". Returns
    # ``Status.NEEDS_AUTH`` with legacy label ``"missing_credential"`` so
    # the legacy wrapper triggers chain-skip-without-cooldown (a quick
    # reconnect must not be masked by a 10-min cooldown).
    _Rule(
        platform="any",
        pattern=re.compile(
            r"(?:subscription is not connected"
            r"|not connected\.?\s*integration"
            r"|not connected\..*Please connect"
            r"|is not connected\..*subscription"
            r"|(?:Claude Code|Codex|Gemini CLI|GitHub|Copilot CLI|GitHub Copilot CLI)"
            r"\s*not connected\b"
            r"|please connect your)",
            re.IGNORECASE,
        ),
        status=Status.NEEDS_AUTH,
        legacy_label="missing_credential",
        test_id="cli_not_connected_is_needs_auth_with_missing_credential_label",
    ),
    # 4. codex quota — rate / quota / billing / 429
    _Rule(
        platform="codex",
        pattern=re.compile(
            r"rate[\s_-]?limit"
            r"|usage\s*limit"
            r"|quota[\s_-]?(exceeded|exhausted|limit)"
            r"|insufficient[\s_-]?(quota|credit)"
            r"|credit[\s_-]?balance"
            r"|out\s*of\s*(tokens|credits|quota)"
            r"|too\s*many\s*requests"
            # Phase 1.5 parity widening, anchored (review I-A):
            # the legacy CODEX_CREDIT_ERROR_PATTERNS bare substrings
            # ("billing", "capacity") are too loose for the apps/api
            # chat hot path — bare "the meeting is at capacity" or
            # "monthly billing invoice generated" would trigger
            # cooldown + chain-skip. Anchored to require an adjacent
            # failure word so plain user prose doesn't trip them.
            r"|billing[\s_-]?(error|issue|failed|required|quota|problem)"
            r"|capacity[\s_-]?(exceeded|exhausted|reached|error|limit)"
            r"|token\s*limit\s*exceeded"
            r"|\b429\b",
            re.IGNORECASE,
        ),
        status=Status.QUOTA_EXHAUSTED,
        legacy_label="quota",
        test_id="codex_rate_limit_is_quota_exhausted",
    ),
    # 5. codex auth — unauthorized / invalid grant / token expired / 401 / 403
    _Rule(
        platform="codex",
        pattern=re.compile(
            r"unauthorized"
            r"|invalid[\s_-]?(grant|token)"
            r"|token[\s_-]?(expired|invalid)"
            r"|\b40[13]\b"
            r"|authentication[\s_-]?failed",
            re.IGNORECASE,
        ),
        status=Status.NEEDS_AUTH,
        legacy_label="auth",
        test_id="codex_unauthorized_is_needs_auth",
    ),
    # 6. gemini quota — quota_exceeded / resource_exhausted
    _Rule(
        platform="gemini_cli",
        pattern=re.compile(
            r"quota[\s_-]?(exceeded|exhausted)|resource[\s_-]?exhausted",
            re.IGNORECASE,
        ),
        status=Status.QUOTA_EXHAUSTED,
        legacy_label="quota",
        test_id="gemini_cli_quota_exceeded_is_quota_exhausted",
    ),
    # 6b. gemini credential-loader connection failure. The Gemini CLI's
    # OAuth refresh path tries to reach a local credential server; when
    # that server is down (sidecar crash, network-namespace issue) the
    # CLI emits "Failed to load Gemini credentials: [Errno 111]
    # Connection refused" and the whole turn dies. Observed in prod
    # 2026-05-20 on the AgentProvision tenant. Without a rule here, the
    # CLI chain treats it as a generic failure, doesn't set a cooldown,
    # and keeps re-picking Gemini every chat turn. Classifying as
    # QUOTA_EXHAUSTED (despite not being a real quota) gives us the
    # desired behaviour: 600s cooldown + chain skip to Codex. Status is
    # semantic shorthand; the legacy_label "quota" is the trigger the
    # cooldown-aware router consumes.
    _Rule(
        platform="gemini_cli",
        pattern=re.compile(
            r"failed\s+to\s+load\s+gemini\s+credentials"
            # ECONNREFUSED variants in case the upstream message
            # wording shifts but the connection-refused signature is
            # still gemini-shaped:
            r"|gemini.*\[errno\s*111\]\s*connection\s*refused"
            r"|gemini.*credentials.*connection\s*refused",
            re.IGNORECASE,
        ),
        status=Status.QUOTA_EXHAUSTED,
        legacy_label="quota",
        test_id="gemini_cli_credential_load_failure_is_quota_exhausted",
    ),
    # 7. gemini workspace trust
    _Rule(
        platform="gemini_cli",
        pattern=re.compile(
            r"workspace[\s_-]?(setup|trust)|untrusted\s*workspace",
            re.IGNORECASE,
        ),
        status=Status.WORKSPACE_UNTRUSTED,
        legacy_label=None,
        test_id="gemini_cli_workspace_setup_is_workspace_untrusted",
    ),
    # 8. gemini api disabled (GCP API not enabled in console)
    _Rule(
        platform="gemini_cli",
        pattern=re.compile(
            r"api[\s_-]?disabled|enable.*api.*console\.cloud",
            re.IGNORECASE,
        ),
        status=Status.API_DISABLED,
        legacy_label=None,
        test_id="gemini_cli_api_disabled_is_api_disabled",
    ),
    # 9. gemini permission denied → auth (must come AFTER api-disabled)
    _Rule(
        platform="gemini_cli",
        pattern=re.compile(
            r"permission[\s_-]?denied|access[\s_-]?denied",
            re.IGNORECASE,
        ),
        status=Status.NEEDS_AUTH,
        legacy_label="auth",
        test_id="gemini_cli_permission_denied_is_needs_auth",
    ),
    # 10. copilot quota — subscription / not enabled / forbidden /
    # rate / quota / billing / 429. Phase 1.5 corpus-parity widening:
    # legacy COPILOT_CREDIT_ERROR_PATTERNS in apps/code-worker/workflows.py
    # also flag rate-limit / quota-exceeded / insufficient_quota /
    # out-of-credits / too-many-requests as credit signals. Corpus
    # parity test (test_credit_exhausted_parity.py) gates this; step 5's
    # helper rewrite would otherwise silently change behaviour.
    _Rule(
        platform="copilot_cli",
        pattern=re.compile(
            r"copilot\s*is\s*not\s*enabled"
            r"|subscription\s*required"
            r"|forbidden"
            r"|rate[\s_-]?limit"
            r"|usage\s*limit"
            r"|quota[\s_-]?exceeded"
            r"|insufficient[\s_-]?quota"
            r"|out\s*of\s*credits"
            r"|too\s*many\s*requests"
            r"|\b429\b",
            re.IGNORECASE,
        ),
        status=Status.QUOTA_EXHAUSTED,
        legacy_label="quota",
        test_id="copilot_cli_subscription_required_is_quota_exhausted",
    ),
    # 11. copilot auth — not authorized / 401 / 403.
    # NOTE on legacy union: COPILOT_CREDIT_ERROR_PATTERNS also lumped
    # "not authorized" into its credit-exhausted bucket, but only to
    # trigger CLI fallback chaining (auth failure → switch CLI). Other
    # consumers (chat error footer, RL writer, council) want auth and
    # quota distinct, so this rule keeps NEEDS_AUTH semantically
    # correct and the worker helper for copilot in step 5 uses
    # ``classify(...) in (QUOTA_EXHAUSTED, NEEDS_AUTH)`` to preserve
    # the legacy union behaviour. See test_credit_exhausted_parity.py.
    _Rule(
        platform="copilot_cli",
        pattern=re.compile(
            r"not\s*authorized|\b40[13]\b",
            re.IGNORECASE,
        ),
        status=Status.NEEDS_AUTH,
        legacy_label="auth",
        test_id="copilot_cli_not_authorized_is_needs_auth",
    ),
    # 12. any — retryable network failures (ECONNRESET, 502, 503, TLS)
    _Rule(
        platform="any",
        pattern=re.compile(
            r"econnreset|etimedout|\b50[23]\b|tls\s*handshake",
            re.IGNORECASE,
        ),
        status=Status.RETRYABLE_NETWORK_FAILURE,
        legacy_label=None,
        test_id="any_econnreset_is_retryable_network_failure",
    ),
]


# --------------------------------------------------------------------------
# Exception rules — evaluated BEFORE stderr rules
# --------------------------------------------------------------------------

# (exception types tuple, status, legacy_label, test_id)
_EXCEPTION_RULES: list[
    tuple[tuple[type[BaseException], ...], Status, Optional[str], str]
] = [
    (
        (asyncio.TimeoutError, subprocess.TimeoutExpired, TimeoutError),
        Status.TIMEOUT,
        None,
        "exception_timeout_is_timeout",
    ),
    (
        (FileNotFoundError,),
        Status.PROVIDER_UNAVAILABLE,
        None,
        "exception_filenotfound_is_provider_unavailable",
    ),
]


# --------------------------------------------------------------------------
# Public entry points
# --------------------------------------------------------------------------

def classify(
    stderr: Optional[str],
    exit_code: Optional[int] = None,
    exc: Optional[BaseException] = None,
) -> Status:
    """Classify a CLI execution outcome.

    Args:
        stderr: combined stderr / error-message string from the
            subprocess (or None).
        exit_code: subprocess exit code (or None when only an exception
            was raised before exit).
        exc: the captured exception, if any. Exception rules win over
            stderr rules — if the activity got a ``CancelledError`` the
            stderr never matters.

    Returns:
        The matching ``Status``. Falls through to
        ``Status.UNKNOWN_FAILURE`` when no rule matches.

    Notes:
        ``exit_code`` is currently unused but kept on the signature so
        Phase 2 adapters can pass it through without a breaking change
        when we add exit-code-based rules (e.g. ``which gemini`` returns
        1 → ``PROVIDER_UNAVAILABLE`` is currently caught via
        FileNotFoundError; non-zero ``which`` will be added as an
        exit-code rule in Phase 2 once the ``ProviderAdapter`` decides
        which subprocess invocations should be classified that way).
    """
    # 1. Exception rules first — they're the most specific signal we
    # have. A Temporal teardown swallows the subprocess exit and gives
    # us only the exception class, so we MUST match on type before
    # reading stderr.
    if exc is not None:
        # Lazy temporalio import — apps/api can't depend on temporalio
        # at import time (the API container has no temporal SDK in
        # certain test profiles). We probe with try/except so the
        # WORKFLOW_FAILED branch is opt-in based on whether
        # temporalio is actually installed in the runtime.
        try:
            import temporalio.exceptions as _texc  # type: ignore

            workflow_exc_types: tuple[type[BaseException], ...] = (
                _texc.ApplicationError,
                _texc.ActivityError,
            )
        except ImportError:
            workflow_exc_types = ()

        # CancelledError is always available — it's part of stdlib
        # asyncio. Treating it the same as Temporal's wrapped
        # cancellation is correct: by the time we see it, the activity
        # is already gone.
        cancel_types: tuple[type[BaseException], ...] = (asyncio.CancelledError,)

        if workflow_exc_types and isinstance(exc, workflow_exc_types):
            return Status.WORKFLOW_FAILED
        if isinstance(exc, cancel_types):
            return Status.WORKFLOW_FAILED

        for exc_types, status, _legacy, _test_id in _EXCEPTION_RULES:
            if isinstance(exc, exc_types):
                return status

    # 2. Stderr regex rules — declaration order, first match wins.
    if stderr:
        for rule in _STDERR_RULES:
            if rule.pattern.search(stderr):
                return rule.status

    # 3. Fallthrough.
    return Status.UNKNOWN_FAILURE


def classify_with_legacy_label(
    stderr: Optional[str],
    exit_code: Optional[int] = None,
    exc: Optional[BaseException] = None,
) -> tuple[Status, Optional[str]]:
    """Same walk as ``classify``, plus the legacy string label.

    The legacy label is one of ``"quota"`` / ``"auth"`` /
    ``"missing_credential"`` / ``None``. Used by the Phase-1 thin
    wrappers in ``cli_platform_resolver.classify_error`` and
    ``code-worker.workflows._is_*_credit_exhausted`` so the public seam
    is unchanged. Phase 2 callers should switch to ``classify`` and
    drop the legacy label entirely.
    """
    if exc is not None:
        try:
            import temporalio.exceptions as _texc  # type: ignore

            workflow_exc_types: tuple[type[BaseException], ...] = (
                _texc.ApplicationError,
                _texc.ActivityError,
            )
        except ImportError:
            workflow_exc_types = ()

        cancel_types: tuple[type[BaseException], ...] = (asyncio.CancelledError,)

        if workflow_exc_types and isinstance(exc, workflow_exc_types):
            return Status.WORKFLOW_FAILED, None
        if isinstance(exc, cancel_types):
            return Status.WORKFLOW_FAILED, None

        for exc_types, status, legacy, _test_id in _EXCEPTION_RULES:
            if isinstance(exc, exc_types):
                return status, legacy

    if stderr:
        for rule in _STDERR_RULES:
            if rule.pattern.search(stderr):
                return rule.status, rule.legacy_label

    return Status.UNKNOWN_FAILURE, None
