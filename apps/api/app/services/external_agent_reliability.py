"""Reliability shim around ExternalAgentAdapter.dispatch.

Wraps each external dispatch with:
  * Exponential backoff retry — 3 attempts, coefficient 2 — matching the
    Temporal RetryPolicy(maximum_attempts=3) semantics that
    coalition_workflow.py:27 already uses, so the platform's retry
    vocabulary stays uniform. Per-protocol timeouts live in the adapter
    (metadata_['timeout']); the shim doesn't duplicate them.
  * Retry classification — only retries transient errors. ``NonRetryableExternalError``
    short-circuits immediately (4xx auth/validation, missing-tool, etc.).
  * Redis-backed circuit breaker keyed on ``agent:breaker:{external_agent_id}``.
    Open after 5 consecutive failures; auto half-open after 60s (key TTL
    expires → next call is a probe); one successful probe closes it.
    There is no explicit half-open state machine — a thundering herd of
    probes when TTL flips is acceptable for v1.
  * Optional fallback dispatch to another external agent specified in
    ``metadata_['fallback_agent_id']`` (depth 1, no recursion).

Surfaces breaker state in ``external_agents.status``:
``online | offline | busy | error | breaker_open``.

Design notes:
  * Sync entrypoint to match the rest of the adapter and chat path.
  * The shim uses ``db.flush()`` (not ``db.commit()``) so the caller's
    request-scoped session keeps its transaction boundary. Status
    updates are visible inside the request but committed when the
    caller commits at end-of-request.
  * Redis is optional — if unavailable, the breaker degrades to no-op
    (just retry). Mirrors AgentRegistry._get_redis.
  * Breaker race: ``_record_failure`` uses ``set(..., nx=True)`` so a
    concurrent ``_record_success`` that just deleted the key can't be
    raced into reopening it.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.external_agent import ExternalAgent

logger = logging.getLogger(__name__)


# Tunables — match Temporal's RetryPolicy defaults so the vocabulary is
# the same across native (Temporal activity) and external (this shim).
MAX_ATTEMPTS = 3
BACKOFF_INITIAL_S = 1.0
BACKOFF_COEFFICIENT = 2.0

BREAKER_THRESHOLD = 5
BREAKER_OPEN_SECONDS = 60

_BREAKER_KEY_FMT = "agent:breaker:{external_agent_id}"
_FAIL_COUNT_KEY_FMT = "agent:breaker:fails:{external_agent_id}"


class NonRetryableExternalError(RuntimeError):
    """Raised by adapter callers when an external dispatch shouldn't be
    retried — auth failures, schema validation errors, missing tools.

    Mirrors Temporal's ``non_retryable_error_types`` notion. When the
    shim sees this, it skips the remaining retry attempts and goes
    straight to the fallback / breaker path.
    """


# ---------------------------------------------------------------------------
# Redis client (optional)
# ---------------------------------------------------------------------------

_redis_client = None


def _get_redis():
    """Best-effort Redis client. Mirrors AgentRegistry._get_redis so we
    don't fight over connection pools or import order.
    """
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    try:
        import redis as redis_lib
        _redis_client = redis_lib.from_url(settings.REDIS_URL)
        return _redis_client
    except Exception as exc:
        logger.warning("external_agent_reliability: Redis connect failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------

def _breaker_is_open(agent_id) -> bool:
    r = _get_redis()
    if r is None:
        return False
    try:
        return bool(r.exists(_BREAKER_KEY_FMT.format(external_agent_id=str(agent_id))))
    except Exception as exc:
        logger.warning("breaker check failed for %s: %s", agent_id, exc)
        return False


def _record_failure(agent_id) -> int:
    """Increment the consecutive-failure counter; trip the breaker once
    BREAKER_THRESHOLD is reached. Returns the new failure count.
    """
    r = _get_redis()
    if r is None:
        return 0
    key = _FAIL_COUNT_KEY_FMT.format(external_agent_id=str(agent_id))
    try:
        count = int(r.incr(key))
        # Counter expires alongside the breaker so we don't carry old
        # failures forever after a long quiet period.
        r.expire(key, BREAKER_OPEN_SECONDS * 4)
        if count >= BREAKER_THRESHOLD:
            # nx=True: don't reset the TTL of an already-open breaker, and
            # don't race a concurrent _record_success that just deleted the
            # key into reopening it on stale state.
            r.set(
                _BREAKER_KEY_FMT.format(external_agent_id=str(agent_id)),
                "1",
                ex=BREAKER_OPEN_SECONDS,
                nx=True,
            )
        return count
    except Exception as exc:
        logger.warning("breaker record failure for %s: %s", agent_id, exc)
        return 0


def _record_success(agent_id) -> None:
    """A successful call closes the breaker and zeroes the failure counter."""
    r = _get_redis()
    if r is None:
        return
    try:
        r.delete(
            _BREAKER_KEY_FMT.format(external_agent_id=str(agent_id)),
            _FAIL_COUNT_KEY_FMT.format(external_agent_id=str(agent_id)),
        )
    except Exception as exc:
        logger.warning("breaker record success for %s: %s", agent_id, exc)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def external_agent_call(
    agent: ExternalAgent,
    task: str,
    context: dict,
    db: Session,
    *,
    _depth: int = 0,
) -> str:
    """Reliable wrapper around ExternalAgentAdapter.dispatch.

    Honors retry, circuit breaker, and optional fallback. Updates
    ``agent.status`` so the discovery surface reflects current health,
    and writes an ExternalAgentCallLog row at the end of dispatch
    (success or failure) so the AgentPerformanceRollupWorkflow can
    aggregate metrics for external agents.
    """
    # Avoid a circular import — adapter pulls credential vault which
    # pulls Session; this module also uses Session.
    from app.services.external_agent_adapter import adapter
    import time as _time

    if _depth > 1:
        raise RuntimeError("fallback recursion exceeded")

    if _breaker_is_open(agent.id):
        agent.status = "breaker_open"
        db.add(agent)
        db.flush()
        _record_call_log(db, agent, latency_ms=0, status="breaker_open", error="breaker open")
        fallback = _resolve_fallback(agent, db)
        if fallback is not None:
            logger.info(
                "external_agent_call: breaker open for %s, dispatching to fallback %s",
                agent.id, fallback.id,
            )
            return external_agent_call(fallback, task, context, db, _depth=_depth + 1)
        raise RuntimeError(
            f"External agent {agent.name} circuit breaker is open and no fallback is configured."
        )

    last_exc: Optional[Exception] = None
    delay = BACKOFF_INITIAL_S
    started = _time.monotonic()
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            result = adapter.dispatch(agent, task, context, db)
            _mark_online(agent, db)
            _record_success(agent.id)
            _record_call_log(
                db, agent,
                latency_ms=int((_time.monotonic() - started) * 1000),
                status="success",
            )
            return result
        except NonRetryableExternalError as exc:
            # Auth failure / schema validation / missing tool — retrying
            # won't help. Skip the remaining attempts; the failure still
            # counts toward breaker since *something* is wrong with the
            # agent's configuration.
            last_exc = exc
            logger.warning(
                "external_agent_call: %s non-retryable error: %s", agent.id, exc,
            )
            break
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "external_agent_call: %s attempt %d/%d failed: %s",
                agent.id, attempt, MAX_ATTEMPTS, exc,
            )
            if attempt < MAX_ATTEMPTS:
                time.sleep(delay)
                delay *= BACKOFF_COEFFICIENT

    # All attempts failed — record a hard failure and consider fallback.
    _record_failure(agent.id)
    _mark_error(agent, db)
    _record_call_log(
        db, agent,
        latency_ms=int((_time.monotonic() - started) * 1000),
        status="non_retryable" if isinstance(last_exc, NonRetryableExternalError) else "error",
        error=str(last_exc) if last_exc else None,
    )
    fallback = _resolve_fallback(agent, db)
    if fallback is not None:
        logger.info(
            "external_agent_call: %s exhausted retries, dispatching to fallback %s",
            agent.id, fallback.id,
        )
        return external_agent_call(fallback, task, context, db, _depth=_depth + 1)
    if isinstance(last_exc, RuntimeError):
        raise last_exc
    raise RuntimeError(f"external dispatch to {agent.name} failed: {last_exc}") from last_exc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_fallback(agent: ExternalAgent, db: Session) -> Optional[ExternalAgent]:
    fallback_id = (agent.metadata_ or {}).get("fallback_agent_id")
    if not fallback_id:
        return None
    try:
        import uuid
        return (
            db.query(ExternalAgent)
            .filter(ExternalAgent.id == uuid.UUID(str(fallback_id)))
            .first()
        )
    except Exception:
        return None


def _record_call_log(
    db: Session,
    agent: ExternalAgent,
    *,
    latency_ms: int,
    status: str,
    error: Optional[str] = None,
) -> None:
    """Append an ExternalAgentCallLog row.

    Best-effort: any failure here is logged and swallowed — call-log
    writes mustn't break the dispatch contract. Token / cost fields are
    left at zero for v1; PR-D / Hire wizard wires the OpenAI token
    extraction and webhook cost_per_call_usd metadata.
    """
    try:
        from app.models.external_agent_call_log import ExternalAgentCallLog
        row = ExternalAgentCallLog(
            tenant_id=agent.tenant_id,
            external_agent_id=agent.id,
            latency_ms=latency_ms,
            status=status,
            error_message=(error or None),
        )
        db.add(row)
        db.flush()
    except Exception as exc:
        logger.warning("external_agent_reliability: call-log write failed: %s", exc)


def _mark_online(agent: ExternalAgent, db: Session) -> None:
    if agent.status != "online":
        agent.status = "online"
        db.add(agent)
        db.flush()


def _mark_error(agent: ExternalAgent, db: Session) -> None:
    agent.status = "error"
    db.add(agent)
    db.flush()
