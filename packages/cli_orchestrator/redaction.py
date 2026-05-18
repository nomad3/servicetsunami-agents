"""Secret redaction at the CLI execution boundary — see design §5.

This module is the single redactor every string flowing out of the
orchestrator passes through: ``stdout_summary``, ``stderr_summary``,
log messages, Temporal heartbeat details, RL state text. Phase 2's
``ResilientExecutor`` calls ``redact()`` once at the boundary; Phase 1
ships the primitive only so the function is reviewable in isolation.

Two scrub paths:

* ``redact(text)`` — text-mode scrub. Apply 8 priority-ordered rules
  (rule 8 split into 8a/8b per review I6 — the older greedy "any
  word + colon + value" rule is replaced with a line-anchored config-
  line shape and a header-line shape so legitimate prose like "the api
  key was rotated" survives unchanged).

* ``redact_json_structural(payload)`` — structural scrub for known-JSON
  payloads. Walks dicts and lists; replaces values of keys matching
  ``(?i)(token|key|secret|password|cookie|auth)`` with ``<redacted>``.

Plus two utilities the Phase 2 adapters import:

* ``cleanup_codex_home(path)`` — idempotent ``shutil.rmtree``. Codex's
  ``auth.json`` lives at ``~/.codex/auth.json`` and persists across
  runs unless explicitly removed. The Codex adapter's ``run()`` MUST
  call this in a ``finally`` block (review I7); Phase 1 only exposes
  the helper, the wiring lands in Phase 2.

* ``SENSITIVE_ENV_KEYS`` — extends ``skill_manager._SENSITIVE_ENV_KEYS``
  with platform-token names (``CLAUDE_CODE_OAUTH_TOKEN``,
  ``COPILOT_GITHUB_TOKEN``, ``CODEX_AUTH_JSON``, ``GEMINI_CLI_TOKEN``).
  Used by Phase 2 adapters to strip from subprocess env before spawn —
  defined here so Phase 1 review can verify the surface.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any, Pattern


# --------------------------------------------------------------------------
# Sensitive-env-key surface (Phase 2 adapters import this; not wired here)
# --------------------------------------------------------------------------

# Mirrored from skill_manager._SENSITIVE_ENV_KEYS plus platform-specific
# CLI tokens that the orchestrator must strip from subprocess env at the
# CLI execution boundary. Kept as a frozenset so tests can compare
# membership directly without worrying about ordering.
SENSITIVE_ENV_KEYS: frozenset[str] = frozenset({
    # Mirrored from skill_manager._SENSITIVE_ENV_KEYS — keep in sync if
    # that set grows. (We don't import it directly to keep this module
    # cheap to import; the test asserts membership symmetry.)
    "SECRET_KEY",
    "DATABASE_URL",
    "ENCRYPTION_KEY",
    "ANTHROPIC_API_KEY",
    "MCP_API_KEY",
    "API_INTERNAL_KEY",
    "GITHUB_TOKEN",
    "GITHUB_CLIENT_SECRET",
    "GOOGLE_CLIENT_SECRET",
    "MICROSOFT_CLIENT_SECRET",
    "LINKEDIN_CLIENT_SECRET",
    "GOOGLE_API_KEY",
    "HCA_SERVICE_KEY",
    "PLATFORM_CLAUDE_CODE_TOKEN",
    "PLATFORM_GEMINI_CLI_TOKEN",
    "PLATFORM_CODEX_AUTH_JSON",
    # Platform-specific CLI tokens — the new surface Phase 2 needs. The
    # orchestrator strips these from the subprocess env that ships to
    # downstream tools / hooks / structured logs.
    "CLAUDE_CODE_OAUTH_TOKEN",
    "COPILOT_GITHUB_TOKEN",
    "CODEX_AUTH_JSON",
    "GEMINI_CLI_TOKEN",
    # Wave 2d N1 — provider keys flowing through goose's subprocess env
    # for whichever LLM provider the tenant picked. Each one is the
    # documented upstream env var for that provider.
    "OPENAI_API_KEY",
    "GROQ_API_KEY",
    "DATABRICKS_TOKEN",
    "OPENROUTER_API_KEY",
    "GEMINI_API_KEY",
    "XAI_API_KEY",
    "DEEPSEEK_API_KEY",
})


# --------------------------------------------------------------------------
# Text redaction rules — priority-ordered (design §5)
# --------------------------------------------------------------------------

# Each tuple is (compiled_pattern, replacement). re.sub walks them in
# order, so the most-specific shapes (Authorization: Bearer …, GH PATs,
# JWTs) win before the broader fallbacks.

_RULES: list[tuple[Pattern[str], str]] = [
    # 1. Authorization: Bearer <token>
    (
        re.compile(r"(?i)(authorization:\s*bearer\s+)([\w\-\.]+)"),
        r"\1<redacted>",
    ),
    # 2. X-Internal-Key / X-Api-Key / X-Tenant-Id headers
    (
        re.compile(r"(?i)(x-(?:internal-key|api-key|tenant-id):\s*)([\w\-\.]+)"),
        r"\1<redacted>",
    ),
    # 3. https://<token>@github.com — the `workflows.py:1074` git URL leak
    (
        re.compile(r"https://([\w\-]{20,})@github\.com"),
        r"https://<redacted>@github.com",
    ),
    # 4. GitHub PAT shapes (ghp_, gho_, ghs_, ghr_).
    # Intentionally NO word-boundary anchors — random log padding can
    # butt up against the prefix without a separator (caught by the
    # property test). The "<prefix>_<long alnum>" shape is distinctive
    # enough that false positives in normal prose are not a concern.
    (
        re.compile(r"(ghp|gho|ghs|ghr)_[\w]{20,}"),
        r"<redacted-github-token>",
    ),
    # 5. Anthropic / OpenAI api keys.
    # Same rationale as rule 4 — drop boundary anchors. ``sk-`` followed
    # by 20+ url-safe chars is sk-ant-* (Anthropic) or sk-proj-* /
    # sk-* (OpenAI). The optional "ant-" sub-prefix keeps Anthropic
    # keys redacted under the same rule.
    (
        re.compile(r"sk-(?:ant-)?[\w\-]{20,}"),
        r"<redacted-api-key>",
    ),
    # 6. Cookie / Set-Cookie header lines
    (
        re.compile(r"(?i)(set-cookie|cookie):\s*[^\r\n]+"),
        r"<redacted-cookie>",
    ),
    # 7. JWT shape — three base64url-ish segments separated by dots.
    # Same rationale as rules 4/5 — drop boundary anchors. The triple
    # ``eyJ…\.eyJ…\.…`` shape is unique enough in real logs that
    # over-matching is not a worry; the negative-redaction property
    # test pins that prose containing words like "the token expired"
    # still survives unchanged.
    (
        re.compile(r"eyJ[\w\-]{10,}\.eyJ[\w\-]{10,}\.[\w\-]{10,}"),
        r"<redacted-jwt>",
    ),
    # 8a. Tightened (review I6): line-anchored config-line shape.
    # Only fires when the secret-name token is the FIRST non-whitespace
    # element on its line — so prose like "the api key was rotated" or
    # `keypair = ed25519` survives unchanged. Allow leading ``>`` for
    # quoted lines / git diffs.
    (
        re.compile(
            r"(?im)^[\s>]*(api[_-]?key|password|secret|"
            r"access[_-]?token|refresh[_-]?token|client[_-]?secret)"
            r"\s*[:=]\s*\S+",
        ),
        r"\1=<redacted>",
    ),
    # 8b. Tightened (review I6): header-line shape.
    # Fires anywhere in a line for the canonical authorization-style
    # headers. Not line-anchored because real HTTP traces tend to log
    # multiple headers on one line.
    (
        re.compile(
            r"(?i)\b(authorization|x-api-key|x-internal-key|x-tenant-id)\s*:\s*\S+",
        ),
        r"\1: <redacted>",
    ),
]


# Keys whose VALUES we redact in structural-JSON walks. Match is
# case-insensitive substring on the key — a key named "auth_token",
# "api_key", "X-Tenant-Id" all hit. The pre-compiled pattern is the
# only place this gets defined.
_STRUCTURAL_KEY_RE = re.compile(r"(?i)(token|key|secret|password|cookie|auth)")


# --------------------------------------------------------------------------
# Public entry points
# --------------------------------------------------------------------------

def redact(text: str | None) -> str:
    """Apply text-mode redaction rules in priority order.

    Args:
        text: arbitrary string from a subprocess / log / heartbeat
            detail. ``None`` is normalised to ``""`` (callers can pass
            in an unread response body without checking).

    Returns:
        The same string with secrets replaced by ``<redacted-*>``
        markers. Non-secret characters are preserved verbatim — this is
        load-bearing for the negative-redaction property test (review
        I6): over-redaction would mask a real prod incident with a
        fresh one.
    """
    if text is None:
        return ""
    out = text
    for pattern, replacement in _RULES:
        out = pattern.sub(replacement, out)
    return out


def redact_json_structural(payload: Any) -> Any:
    """Walk a JSON-shaped payload and redact values of sensitive keys.

    Recurses through ``dict`` and ``list``; non-container leaves pass
    through unchanged. A key matches "sensitive" if it contains
    ``token``, ``key``, ``secret``, ``password``, ``cookie``, or
    ``auth`` (case-insensitive). Matching values become the literal
    string ``"<redacted>"``.

    Returns a NEW structure — the input is never mutated. (We rely on
    callers being able to pass the result through the existing
    structured-logging machinery without worrying about aliasing.)
    """
    if isinstance(payload, dict):
        out: dict[Any, Any] = {}
        for k, v in payload.items():
            if isinstance(k, str) and _STRUCTURAL_KEY_RE.search(k):
                out[k] = "<redacted>"
            else:
                out[k] = redact_json_structural(v)
        return out
    if isinstance(payload, list):
        return [redact_json_structural(item) for item in payload]
    return payload


def cleanup_codex_home(path: str | Path) -> None:
    """Idempotent ``shutil.rmtree`` of a Codex home directory.

    Codex CLI writes its OAuth payload to ``<codex_home>/auth.json`` at
    ``_prepare_codex_home`` time (workflows.py:1470). Phase 2's Codex
    adapter MUST call this in its ``run()`` ``finally`` block — even on
    TIMEOUT / CancelledError. Phase 1 only ships the helper; wiring is
    Phase 2's job.

    Errors are swallowed (``ignore_errors=True``) so a missing or
    partially-removed directory never breaks the cleanup. The unit
    tests assert idempotency + tolerance to a missing dir.
    """
    if path is None:
        return
    target = Path(path)
    # ``shutil.rmtree(missing, ignore_errors=True)`` is a no-op, but we
    # short-circuit with ``exists()`` first to keep the call cheap when
    # the directory is gone. ``rmtree`` on a path that is a *file* (not
    # a directory) raises NotADirectoryError on most platforms even with
    # ignore_errors — guard explicitly.
    if not target.exists():
        return
    if target.is_file() or target.is_symlink():
        try:
            target.unlink()
        except OSError:
            pass
        return
    shutil.rmtree(target, ignore_errors=True)
