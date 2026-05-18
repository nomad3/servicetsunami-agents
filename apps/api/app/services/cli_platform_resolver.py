"""CLI platform resolver — autodetect + fallback chain.

Picks which CLI a chat turn should run on, based on:

1. Per-agent ``config.preferred_cli`` override (highest precedence —
   imported Microsoft agents set this to ``copilot_cli``).
2. Tenant ``tenant_features.default_cli_platform`` (admin-set).
3. **Autodetect** from connected integrations — pick whatever the tenant
   actually has credentials for. This is the new behavior: a tenant who
   connected only GitHub Copilot will route to ``copilot_cli`` even
   without setting any explicit default.
4. ``opencode`` (local Gemma 4) as the final floor when nothing else is
   wired and the tenant has no CLI subscription at all.

The resolver returns an *ordered chain*, not a single choice — the
caller (``agent_router.route_and_execute``) walks the chain on
quota/auth failures so a Copilot CLI rate-limit transparently falls
over to Claude Code (or whichever is next available).

Cooldowns
---------

When a CLI returns a quota or auth error, we mark it cool for
``_COOLDOWN_SECONDS`` so subsequent chat turns skip it and go straight
to the fallback. Cooldown lives in Redis (already in the stack); if
Redis is unavailable, the cooldown silently degrades to in-process —
re-trying a rate-limited CLI on every request is annoying but not
fatal, and one failed attempt is still cheaper than no fallback.
"""
from __future__ import annotations

import logging
import os
import re
import time
import uuid
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.tenant_features import TenantFeatures

logger = logging.getLogger(__name__)


# Default priority when no explicit preference matches. Ordered from
# "most likely paid + most capable" to "local fallback". Adjust here if
# product preferences change — there's no reason to spread this list
# across the codebase.
_DEFAULT_PRIORITY: tuple[str, ...] = (
    # 2026-05-05 product call: gemini first because most tenants only
    # have Google integrations connected (gmail / calendar / drive
    # auto-grant gemini_cli access for free), then codex / copilot_cli /
    # claude_code for tenants that pay for those CLI subscriptions, then
    # ``qwen_code`` (Wave 1b — Tongyi Qwen-Coder via BYOK API key),
    # ``kimi_k2`` (Moonshot AI — Wave 1c Lane B Chinese OSS coding model),
    # ``deepseek`` (DeepSeek V3/R1 — Wave 2a Lane B MIT coding + reasoning
    # model), ``glm`` (Zhipu AI GLM-4.6 — Wave 2b Lane B Apache 2.0 OSS),
    # and ``aider`` (Wave 2c — paul-gauthier/aider, Apache 2.0 BYOK to
    # ANY LiteLLM provider) slotted below the established subscriptions
    # so a tenant who's connected several CLIs gets the most-capable
    # subscription first and the BYOK alternates as fallbacks, and
    # finally opencode as the always-available local-Gemma floor.
    "gemini_cli",
    "codex",
    "copilot_cli",
    "claude_code",
    "qwen_code",
    "kimi_k2",
    "deepseek",
    "glm",
    "aider",
    # Wave 2d — Goose (Block) BYOK MCP-native Rust CLI. Slots below the
    # established subscriptions and the BYOK alternates because it only
    # works once the tenant has picked a provider on the goose card; if
    # they haven't, the executor returns the "not connected" friendly
    # message and the chain walks past without a cooldown.
    "goose",
    "opencode",
)

_VALID_PLATFORMS: frozenset[str] = frozenset(_DEFAULT_PRIORITY)

# Map CLI platform → integration_names that, when connected, prove the
# CLI can authenticate. ``opencode`` runs locally (no integration).
# Order within each tuple matters only for diagnostics — any one match
# is sufficient.
_CLI_TO_INTEGRATIONS: dict[str, tuple[str, ...]] = {
    "claude_code": ("claude_code",),
    "copilot_cli": ("github",),
    "codex": ("codex",),
    "gemini_cli": ("gemini_cli", "gmail", "google_drive", "google_calendar"),
    "qwen_code": ("qwen_code",),
    "kimi_k2": ("kimi_k2",),
    "deepseek": ("deepseek",),
    "glm": ("glm",),
    "aider": ("aider",),
    "goose": ("goose",),
    "opencode": (),  # local
}

# Process-local fallback when Redis is unavailable. Survives the worker
# lifetime (good enough — Temporal restart resets it).
_local_cooldown: dict[str, float] = {}

# Module-level Redis client. Built once on first access so we don't pay a
# TCP handshake + ping on every chat turn. Per-op try/except handles
# transient Redis failures by falling back to the in-process dict.
_redis_singleton = None
_redis_init_failed = False


def _cooldown_seconds() -> int:
    """Read the cooldown TTL at call-site so tests and admins can override
    via ``CLI_COOLDOWN_SECONDS`` without bouncing the process."""
    try:
        return int(os.environ.get("CLI_COOLDOWN_SECONDS", "600"))
    except ValueError:
        return 600


def _redis_client():
    """Module-level Redis client built once. Returns None if Redis is
    unreachable so the resolver degrades to the in-process dict.
    """
    global _redis_singleton, _redis_init_failed
    if _redis_singleton is not None:
        return _redis_singleton
    if _redis_init_failed:
        return None
    try:
        import redis  # type: ignore
        url = os.environ.get("REDIS_URL", "redis://redis:6379/0")
        client = redis.Redis.from_url(url, socket_timeout=0.5, socket_connect_timeout=0.5)
        client.ping()
        _redis_singleton = client
        return client
    except Exception as e:
        # Mark init failed so we don't pay the timeout on every call. A
        # process restart re-tries.
        logger.info("CLI cooldown: Redis unavailable, using in-process dict (%s)", e)
        _redis_init_failed = True
        return None


def _cooldown_key(tenant_id, platform: str) -> str:
    return f"cli_cooldown:{tenant_id}:{platform}"


def is_in_cooldown(tenant_id, platform: str) -> bool:
    """Return True if this CLI was recently quota'd / auth-failed."""
    # Local Gemma 4 is the universal floor — never cooled, never queried.
    if platform == "opencode":
        return False
    key = _cooldown_key(tenant_id, platform)
    client = _redis_client()
    if client is not None:
        try:
            return bool(client.exists(key))
        except Exception:
            # Fall through to in-process dict on any Redis op failure.
            pass
    expires_at = _local_cooldown.get(key)
    if expires_at is None:
        return False
    if expires_at < time.time():
        _local_cooldown.pop(key, None)
        return False
    return True


def mark_cooldown(tenant_id, platform: str, *, reason: str = "") -> None:
    """Mark this (tenant, platform) pair cool for the configured TTL."""
    if platform not in _VALID_PLATFORMS or platform == "opencode":
        # Don't cool down the local floor — it's the universal fallback.
        return
    ttl = _cooldown_seconds()
    key = _cooldown_key(tenant_id, platform)
    client = _redis_client()
    if client is not None:
        try:
            client.setex(key, ttl, reason or "1")
            logger.info(
                "CLI cooldown set: tenant=%s platform=%s ttl=%ds reason=%s",
                str(tenant_id)[:8], platform, ttl, reason or "n/a",
            )
            return
        except Exception:
            pass
    _local_cooldown[key] = time.time() + ttl
    logger.info(
        "CLI cooldown set (local fallback): tenant=%s platform=%s ttl=%ds reason=%s",
        str(tenant_id)[:8], platform, ttl, reason or "n/a",
    )


# DEPRECATED — Phase 2 cleanup. These three pattern tuples are kept
# here only so the legacy compiled-regex constants remain importable
# while downstream is moved over. Phase 1's ``classify_error`` is now
# a thin wrapper around the new
# ``app.services.cli_orchestrator.classify_with_legacy_label``; nothing
# in this module reads ``_QUOTA_PATTERNS`` / ``_AUTH_PATTERNS`` /
# ``_MISSING_CRED_PATTERNS`` anymore. Phase 2 deletes the constants
# alongside the wrapper.
_QUOTA_PATTERNS = re.compile(
    r"(quota[\s_-]?(exceeded|exhausted|limit)|rate[\s_-]?limit|insufficient[\s_-]?(quota|credit)|"
    r"credit[\s_-]?balance|out of (tokens|credits|quota)|too many requests|429)",
    re.IGNORECASE,
)
_AUTH_PATTERNS = re.compile(
    r"(unauthorized|invalid[\s_-]?(grant|token)|token[\s_-]?(expired|invalid)|401|403|"
    r"authentication[\s_-]?failed)",
    re.IGNORECASE,
)
_MISSING_CRED_PATTERNS = re.compile(
    r"(subscription is not connected|not connected\.?\s*integration|"
    r"not connected\..*Please connect|is not connected\..*subscription|"
    r"(?:Claude Code|Codex|Gemini CLI|GitHub|Copilot CLI|GitHub Copilot CLI) not connected\b)",
    re.IGNORECASE,
)


def classify_error(error: Optional[str]) -> Optional[str]:
    """Return ``"quota"`` | ``"auth"`` | ``"missing_credential"`` | None.

    Phase 1 wrapper — delegates to
    ``app.services.cli_orchestrator.classifier.classify_with_legacy_label``.
    The contract is unchanged:

    - ``"quota"`` and ``"auth"`` trigger chain fallback AND mark a 10-min
      cooldown so future turns skip the failing CLI directly.
    - ``"missing_credential"`` triggers chain fallback only — no cooldown,
      because the failure is stable (config issue, not transient) and
      cooling would mask a quick reconnect.
    - Anything else → ``None``: real failure, bubbles up.

    ``None`` / ``""`` input → ``None``.

    Phase 2 callers should import ``Status`` from
    ``app.services.cli_orchestrator`` and switch to the strongly-typed
    enum; this wrapper stays as the rollback seam.
    """
    if not error:
        return None
    # Lazy import to keep the module-load cost flat — the orchestrator
    # package pulls in ``re`` + ``shutil`` + a chunk of patterns, and
    # ``cli_platform_resolver`` is hot in the request path.
    from app.services.cli_orchestrator import classify_with_legacy_label

    _status, legacy_label = classify_with_legacy_label(error)
    return legacy_label


def _connected_clis(db: Session, tenant_id: uuid.UUID) -> tuple[set[str], bool]:
    """Which CLI platforms does this tenant have credentials for?

    Returns ``(available_clis, query_ok)``. When the integration query
    raises (transient DB hiccup, lock contention), ``query_ok=False`` and
    ``available_clis`` is just ``{"opencode"}``. The caller MUST treat
    ``query_ok=False`` as "trust the explicit platform" rather than
    blindly dropping to opencode-only — a transient DB error shouldn't
    silently downgrade every chat to local Gemma 4.
    """
    # Lazy import to avoid a circular import via integration_status →
    # integration_credential models at module-load time.
    from app.services.integration_status import get_connected_integrations

    try:
        connected_map = get_connected_integrations(db, tenant_id)
    except Exception as e:
        logger.warning(
            "CLI resolver: get_connected_integrations failed for tenant=%s: %s — "
            "trusting explicit platform; opencode floor still applies",
            str(tenant_id)[:8], e,
        )
        return {"opencode"}, False

    connected_names = {
        name for name, info in (connected_map or {}).items()
        if isinstance(info, dict) and info.get("connected")
    }

    available: set[str] = {"opencode"}  # local always works
    for cli, integrations in _CLI_TO_INTEGRATIONS.items():
        if not integrations:
            continue
        if any(name in connected_names for name in integrations):
            available.add(cli)
    return available, True


def connected_clis_for_tenant(db: Session, tenant_id: uuid.UUID) -> List[str]:
    """Returns CLIs that are user-pickable AND currently connected.

    Excludes ``opencode`` (the local-Gemma routing floor) — that's not a
    user choice. The internal ``_connected_clis`` still includes it for
    routing in ``resolve_cli_chain``; this public helper drops it because
    it's only used by the ``/integrations/connected-clis`` API powering
    the chat-header InlineCliPicker dropdown, where surfacing the routing
    floor as a selectable option would create a contract mismatch (the
    frontend's ``CLI_OPTIONS`` doesn't list it, so it would be silently
    stripped client-side — a rot-prone setup).

    Ordering matches ``_DEFAULT_PRIORITY`` so a UI offering these
    options shows them in the same order the backend would try them.

    On a transient DB error (``query_ok=False``), the underlying helper
    returns just ``{"opencode"}``; after the exclusion that surfaces as
    ``[]`` — the public list is empty rather than guessing, and the UI
    is expected to fall back to its all-options view.
    """
    available, _query_ok = _connected_clis(db, tenant_id)
    return [
        cli for cli in _DEFAULT_PRIORITY
        if cli in available and cli != "opencode"
    ]


def resolve_cli_chain(
    db: Session,
    tenant_id: uuid.UUID,
    *,
    explicit_platform: Optional[str] = None,
    skip_cooldown: bool = False,
) -> List[str]:
    """Return the ordered list of CLI platforms to try for this turn.

    ``explicit_platform`` is the per-agent / per-tenant preference (the
    output of the existing override resolution in agent_router). It
    becomes the head of the chain *if* the tenant actually has the
    credentials for it; otherwise it's dropped and the chain is built
    purely from autodetect.

    Cooldown'd platforms are filtered out unless ``skip_cooldown`` is
    True (used by tests). The local ``opencode`` floor is always last.
    """
    available, query_ok = _connected_clis(db, tenant_id)

    # Build priority order: explicit choice first if it's actually
    # available; then default priority; opencode last (always).
    chain: List[str] = []
    seen: set[str] = set()

    def _add(p: str, *, override_availability: bool = False) -> None:
        if p in seen or p not in _VALID_PLATFORMS:
            return
        if not override_availability and p not in available:
            return
        if not skip_cooldown and is_in_cooldown(tenant_id, p):
            return
        chain.append(p)
        seen.add(p)

    if explicit_platform:
        # When the integration query failed we don't actually know which
        # CLIs are connected; trust the explicit platform anyway so a
        # transient DB hiccup doesn't silently downgrade every chat to
        # local Gemma 4. Worst case the explicit platform also lacks
        # credentials and the dispatch will skip+chain via the
        # ``missing_credential`` classification.
        _add(explicit_platform, override_availability=not query_ok)

    for p in _DEFAULT_PRIORITY:
        if p == "opencode":
            continue  # always last
        _add(p)

    # opencode is the universal floor — never filtered by cooldown,
    # never absent, always last in the chain so a tenant with zero
    # subscriptions can still get a (degraded) reply.
    if "opencode" not in seen:
        chain.append("opencode")

    return chain
