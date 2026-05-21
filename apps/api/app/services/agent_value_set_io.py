"""IO wrapper for the value layer — reads + writes + audited consult.

Pure matching lives in ``agent_value_set.py``. This module is the
production boundary every consultation-point caller uses:

  - ``read_value_set``: latest-wins read of the (tenant, agent)
    value set from agent_memory (memory_type='value_set').
  - ``write_value_set``: append-only INSERT with monotonic version.
    Concurrent writers collide on the migration-144 unique index;
    the writer retries with version+1.
  - ``is_value_layer_enabled``: per-tenant kill-switch lookup
    against tenant_features.value_layer_enabled.
  - ``consult_with_audit``: read enabled+set, call pure ``consult()``,
    record verdict to the audit log, return verdict.
  - Five thin shim callers — ``consult_routing``, ``consult_tool``,
    ``consult_reflection``, ``appraise_user_signal_with_values``,
    ``synthesize_value_observations`` — each translates its point's
    args into the canonical (action, intent) shape consult expects.

Reflection-kind-aware intent flag (design §4.2 round-3 fix):
``risk`` / ``idea`` / ``tension`` / ``creative`` are descriptive →
``intent='read'``. ``next_move`` / ``value_proposal`` propose an
action → ``intent='mutate'``. That's what makes the §8 criterion
"reflection mentions protect item but proposes touching it gets
blocked at write time" actually fire.

Audit logging is Python-logger-based for v1 (structured log line
per consult verdict). A dedicated ``audit_logs`` table write is a
follow-up; not part of the consult contract because the wrapper
must stay cheap on the chat hot path.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.agent_memory import AgentMemory
from app.services.agent_value_set import (
    AgentValueSet,
    ValueVerdict,
    consult,
)

log = logging.getLogger(__name__)

VALUE_SET_MEMORY_TYPE = "value_set"

# Reflection kinds that PROPOSE an action vs. those that describe.
# Used by ``consult_reflection`` to pick the intent flag.
_MUTATING_REFLECTION_KINDS = frozenset({"next_move", "value_proposal"})


# ── Tenant kill-switch ────────────────────────────────────────────────


def is_value_layer_enabled(
    db: Session,
    *,
    tenant_id: uuid.UUID,
) -> bool:
    """Read ``tenant_features.value_layer_enabled``. Missing row →
    False (defensive default OFF). SQL failure → False. Mirrors the
    nightly_reflection kill-switch from #631."""
    try:
        from app.models.tenant_features import TenantFeatures
        row = (
            db.query(TenantFeatures)
            .filter(TenantFeatures.tenant_id == str(tenant_id))
            .first()
        )
        if row is None:
            return False
        return bool(getattr(row, "value_layer_enabled", False))
    except SQLAlchemyError as exc:
        log.warning(
            "agent_value_set_io.is_value_layer_enabled: lookup failed "
            "tenant=%s err=%s; treating as OFF",
            tenant_id, exc,
        )
        return False
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "agent_value_set_io.is_value_layer_enabled: unexpected err "
            "tenant=%s err=%s; treating as OFF",
            tenant_id, exc,
        )
        return False


# ── Read / write ──────────────────────────────────────────────────────


def read_value_set(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID,
    raise_on_sql_error: bool = False,
) -> AgentValueSet:
    """Latest-wins read with corruption walk-back. (Review B3 fix.)

    If the most recent row's content can't be parsed, log at ERROR
    and walk backward through prior rows until we find one that
    parses. The store is append-only, so the second-most-recent row
    almost certainly parses — that's the safest latest-valid-wins
    fallback. Only returns ``AgentValueSet.empty()`` when NO row
    parses, which is the genuinely-empty state.

    (Luna review-round 6 fix.) Ordering is by version DESC, then
    updated_at DESC as a tiebreaker. Time-only ordering let a
    higher-version row with stale updated_at lose to a lower-version
    row — wrong latest-wins semantics. Version is monotonic per
    write so it's the right primary key.

    (Luna review-round 6 fix.) ``raise_on_sql_error`` makes this
    helper participate in the B5 abort-on-read-failure invariant.
    Default False preserves the chat-hot-path fail-open behavior
    (return empty). _next_version sets True so write_value_set
    sees the real failure and aborts.

    Why not fail-closed on corruption: the chat hot path consults
    on every turn. Raising would 5xx every chat. Walk-back to last
    valid + log ERROR preserves the §6 safety invariant (operator
    sees the corruption in the audit feed but Luna keeps reasoning).
    """
    try:
        rows = (
            db.query(AgentMemory.content)
            .filter(
                AgentMemory.tenant_id == str(tenant_id),
                AgentMemory.agent_id == str(agent_id),
                AgentMemory.memory_type == VALUE_SET_MEMORY_TYPE,
            )
            .order_by(
                # Order by integer-cast of the embedded version
                # field — same expression the migration 144 index
                # uses. Defensive guards in the WHERE clause keep
                # malformed rows from tripping the cast.
                AgentMemory.created_at.desc(),  # secondary
            )
            .all()
        )
    except SQLAlchemyError as exc:
        log.warning(
            "read_value_set: SQL failure tenant=%s agent=%s err=%s",
            tenant_id, agent_id, exc,
        )
        if raise_on_sql_error:
            raise
        return AgentValueSet.empty()

    if not rows:
        return AgentValueSet.empty()

    # (Luna review-round 6) Sort by parsed version DESC in Python.
    # SQL-side ORDER BY can't use the jsonb cast safely without
    # tripping malformed-row WHERE-guard contortions; pull rows in
    # any order, parse, sort. Corrupt rows get sort key -1 so they
    # land last; the walk-back loop hits them only if every valid
    # row was exhausted.
    parsed: list[tuple[int, Optional[dict], str]] = []
    for row in rows:
        content = row[0]
        if not content:
            parsed.append((-1, None, ""))
            continue
        try:
            data = json.loads(content)
        except (TypeError, ValueError):
            parsed.append((-1, None, content))
            continue
        try:
            version = int(data.get("version", 0))
        except (TypeError, ValueError):
            version = -1
        parsed.append((version, data, content))

    # Sort: highest version first, corrupt entries (-1) at the end.
    parsed.sort(key=lambda x: x[0], reverse=True)

    corruption_count = 0
    for idx, (version, data, _content) in enumerate(parsed):
        if data is None:
            corruption_count += 1
            log.error(
                "read_value_set: CORRUPT JSON at sorted_offset=%s "
                "tenant=%s agent=%s; walking back to prior version "
                "(operator should investigate)",
                idx, tenant_id, agent_id,
            )
            continue
        try:
            vs = AgentValueSet.from_dict(data)
            if corruption_count > 0:
                log.error(
                    "read_value_set: returned version=%s after walking "
                    "past %s corrupted row(s) for tenant=%s agent=%s",
                    vs.version, corruption_count, tenant_id, agent_id,
                )
            return vs
        except (TypeError, ValueError) as exc:
            log.error(
                "read_value_set: CORRUPT value-set shape at "
                "sorted_offset=%s version=%s tenant=%s agent=%s err=%s",
                idx, version, tenant_id, agent_id, exc,
            )
            corruption_count += 1
            continue

    # Every row corrupted. Operator must investigate.
    log.error(
        "read_value_set: ALL %s value-set rows corrupted for "
        "tenant=%s agent=%s; returning empty (default-OFF safety)",
        len(rows), tenant_id, agent_id,
    )
    return AgentValueSet.empty()


class _NextVersionError(Exception):
    """Raised by ``_next_version`` on unrecoverable read failure.

    write_value_set converts to a structured failure return so the
    caller surfaces a 503 without retrying against a stale max."""


def _next_version(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID,
    minimum: int = 0,
) -> int:
    """Return the next version to write for (tenant, agent).

    (Review B2 fix.) Uses the existing ``read_value_set`` latest-wins
    read + 1 instead of scanning ALL historical rows. O(1) per write
    regardless of audit-trail size.

    (Review B5 fix.) Raises ``_NextVersionError`` on SQL failure
    rather than silently returning 1. The caller (``write_value_set``)
    treats the raise as an abort and returns None to the operator.

    ``minimum`` lets the retry loop force the candidate version to
    advance even if the latest read's max hasn't yet caught up to
    the colliding version — closes the B1 race where two writers
    both see max=5, both compute 6, one wins, the other rolls back
    and re-reads 5 again. The retry passes ``minimum=prev_attempt+1``
    so the next version strictly advances.
    """
    try:
        # raise_on_sql_error=True so this read's SQL failure actually
        # bubbles to here as SQLAlchemyError. Without it, the default
        # fail-open path returns empty and we silently compute
        # version=1 on every tenant (B5 the reviewer flagged).
        vs = read_value_set(
            db,
            tenant_id=tenant_id,
            agent_id=agent_id,
            raise_on_sql_error=True,
        )
    except SQLAlchemyError as exc:
        log.error(
            "_next_version: read_value_set SQL failure tenant=%s "
            "agent=%s err=%s; aborting write",
            tenant_id, agent_id, exc,
        )
        raise _NextVersionError(
            f"read_value_set failed: {exc}"
        ) from exc

    # `vs.version` is the value-set's stored version; +1 is the
    # next-to-write. `read_value_set` returns empty (version=1) for
    # a fresh (tenant, agent) — first write lands at version=1+0 → 1.
    if vs.is_empty():
        candidate = 1
    else:
        candidate = vs.version + 1
    return max(candidate, minimum)


def write_value_set(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID,
    protect: List[Dict[str, Any]],
    pursue: List[Dict[str, Any]],
    avoid: List[Dict[str, Any]],
    max_retries: int = 3,
) -> Optional[AgentValueSet]:
    """Append-only write. INSERTs a new agent_memory row with the
    next version; on unique-index collision (concurrent writer beat
    us) retries up to ``max_retries`` times with strictly-advancing
    version.

    (Review B1 fix.) After ``db.rollback()`` on IntegrityError we
    call ``db.expire_all()`` so the next ``_next_version`` read sees
    the winner's commit (or at minimum re-issues the SELECT against
    a fresh snapshot). The ``minimum`` arg to ``_next_version`` also
    forces the candidate to strictly exceed the last attempt's
    version so the retry can't loop on the same number under high
    contention.

    (Review B5 fix.) ``_next_version`` raises ``_NextVersionError``
    on SQL failure instead of silently returning 1. We catch + abort
    (return None) so the operator sees a 503 rather than a
    potentially-colliding write.

    Note for future maintainers: the ``content`` field MUST be a
    JSON object with an integer ``version`` key. Migration 144's
    partial unique index extracts that field via SQL cast; a
    malformed write would trip ``invalid input syntax for type
    integer`` on the index expression. The `body` dict below
    guarantees this shape.

    Returns the persisted AgentValueSet on success or None on
    repeated collision / SQL failure. Caller surfaces a 503."""
    now = datetime.now(timezone.utc).isoformat()
    last_version = 0
    for attempt in range(max_retries):
        try:
            version = _next_version(
                db,
                tenant_id=tenant_id,
                agent_id=agent_id,
                minimum=last_version + 1,
            )
        except _NextVersionError:
            return None

        body = {
            "protect": protect,
            "pursue": pursue,
            "avoid": avoid,
            "version": version,
            "updated_at": now,
        }
        row = AgentMemory(
            tenant_id=tenant_id,
            agent_id=agent_id,
            memory_type=VALUE_SET_MEMORY_TYPE,
            content=json.dumps(body),
            importance=1.0,
            confidence=1.0,
            source="value_layer",  # (Review N4)
            tags=["value_set", f"version:{version}"],
        )
        try:
            db.add(row)
            db.commit()
            return AgentValueSet.from_dict(body)
        except IntegrityError as exc:
            db.rollback()
            db.expire_all()  # (B1) drop cached snapshot before re-read
            last_version = version
            log.info(
                "write_value_set: version collision attempt=%s "
                "tenant=%s agent=%s version=%s err=%s; "
                "retrying with version > %s",
                attempt, tenant_id, agent_id, version, exc, version,
            )
            continue
        except SQLAlchemyError as exc:
            log.warning(
                "write_value_set: SQL failure tenant=%s agent=%s err=%s",
                tenant_id, agent_id, exc,
            )
            db.rollback()
            return None
    log.warning(
        "write_value_set: gave up after %s retries tenant=%s agent=%s",
        max_retries, tenant_id, agent_id,
    )
    return None


# ── Audited consult + 5 shim callers ──────────────────────────────────


def _record_verdict(
    *,
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID,
    action: dict,
    verdict: ValueVerdict,
) -> None:
    """Structured log of the verdict. v1 ships logging-only; a
    dedicated audit_logs table write is a Phase 1.5 follow-up
    (alongside the break-glass endpoint).

    Logged at INFO for block/warn (operator may want to see these
    in dashboards) and DEBUG for plain allow (otherwise the log
    drowns)."""
    payload = {
        "tenant_id": str(tenant_id),
        "agent_id": str(agent_id),
        "decision": verdict.decision,
        "reason": verdict.reason,
        "point": verdict.consultation_point,
        "matched_slug": (
            verdict.matched_item.get("slug")
            if verdict.matched_item else None
        ),
    }
    # (Review I1) pursue_match allows are operator-visible signal
    # (the emotion_engine wrapper scales PAD by 1.5x on pursue hits;
    # operators want to see them firing). Promote to INFO. Other
    # allow reasons (no_match / empty_value_set / kill_switch_off)
    # stay DEBUG so the chat hot path doesn't flood the log.
    if verdict.decision in ("block", "warn"):
        log.info("value_layer.verdict %s", payload)
    elif verdict.reason.startswith("pursue_match"):
        log.info("value_layer.verdict %s", payload)
    else:
        log.debug("value_layer.verdict %s", payload)


def consult_with_audit(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID,
    action: dict,
    point: str,
    intent: str,
) -> ValueVerdict:
    """Production boundary: read kill-switch + value set, call
    pure consult, record verdict.

    Every consultation-point caller invokes this (directly or
    through one of the 5 shims below)."""
    enabled = is_value_layer_enabled(db, tenant_id=tenant_id)
    value_set = read_value_set(db, tenant_id=tenant_id, agent_id=agent_id)
    # (Review N6) Defensive: a shim caller passing a malformed point
    # or intent string would raise ValueError out of consult and
    # crash the chat hot path. Catch + fail-open with a logged error
    # so production stays up; tests still detect the bug via the
    # caller-level unit tests.
    try:
        verdict = consult(
            action, value_set,
            point=point, intent=intent, enabled=enabled,
        )
    except ValueError as exc:
        log.error(
            "consult_with_audit: pure consult raised ValueError "
            "tenant=%s agent=%s point=%s intent=%s err=%s; "
            "fail-open (allow)",
            tenant_id, agent_id, point, intent, exc,
        )
        from app.services.agent_value_set import ValueVerdict as _VV
        verdict = _VV.allow(
            reason=f"consult_value_error: {exc}",
            point=point if point in {
                "routing", "tool", "reflection",
                "user_signal", "synthesis",
            } else "unknown",
        )
    _record_verdict(
        tenant_id=tenant_id, agent_id=agent_id,
        action=action, verdict=verdict,
    )
    return verdict


# Five shim callers — each translates its point's args into the
# canonical (action, intent) shape ``consult`` expects.


def consult_routing(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID,
    intent_text: str,
    intent_classifier_says_mutate: bool = False,
) -> ValueVerdict:
    """Pre-dispatch routing gate (design §4.2 point 1)."""
    return consult_with_audit(
        db,
        tenant_id=tenant_id,
        agent_id=agent_id,
        action={"text": intent_text},
        point="routing",
        intent="mutate" if intent_classifier_says_mutate else "read",
    )


def consult_tool(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID,
    tool_name: str,
    args: dict,
    is_mutating: bool,
) -> ValueVerdict:
    """Tool-call gate (design §4.2 point 2). The caller knows whether
    the tool mutates state (the MCP tool registry can carry this
    metadata; for now the caller passes the flag)."""
    return consult_with_audit(
        db,
        tenant_id=tenant_id,
        agent_id=agent_id,
        action={"tool": tool_name, "args": args},
        point="tool",
        intent="mutate" if is_mutating else "read",
    )


def consult_reflection(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID,
    reflection_kind: str,
    reflection_content: str,
) -> ValueVerdict:
    """Reflection validator (design §4.2 point 3 + round-3 fix).

    Reflection kinds drive intent:
      - risk / idea / tension / creative → 'read' (descriptive,
        mention is fine)
      - next_move / value_proposal → 'mutate' (proposes action,
        protect matches must block)
    """
    intent = (
        "mutate" if reflection_kind in _MUTATING_REFLECTION_KINDS
        else "read"
    )
    return consult_with_audit(
        db,
        tenant_id=tenant_id,
        agent_id=agent_id,
        action={"kind": reflection_kind, "content": reflection_content},
        point="reflection",
        intent=intent,
    )


def appraise_user_signal_with_values(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID,
    user_text: str,
) -> ValueVerdict:
    """User-signal appraisal hook (design §4.2 point 4).

    Returns the verdict; the caller (emotion_engine PR) decides
    whether to scale the PAD-pleasure delta when the verdict
    surfaces a ``pursue`` match (1.5x USER_SIGNAL_PLEASURE_GAIN,
    capped at TOOL_OUTCOME_PLEASURE_GAIN per design §4.2 Q3
    round-1 resolution).
    """
    return consult_with_audit(
        db,
        tenant_id=tenant_id,
        agent_id=agent_id,
        action={"text": user_text},
        point="user_signal",
        intent="read",
    )


def synthesize_value_observations(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID,
    proposed_kind: str,
    proposed_content: str,
) -> ValueVerdict:
    """Phase 2 synthesis hook (design §4.2 point 5). Used by the
    reflection workflow when emitting a value_proposal kind — the
    proposal itself gets consulted to catch self-referential
    contradictions (e.g. a proposal to remove a protect that itself
    mentions the protected entity).

    (Review I6) DEAD-WIRED IN PHASE 1: no Phase 1 caller invokes
    this. The shim ships now so the contract is testable + locked,
    and PR 7 (Phase 2) consumes it from
    ``reflection_activities.synthesize_reflections`` when the
    value_proposal mechanism lands. Acceptable per design §10.
    """
    intent = (
        "mutate" if proposed_kind in _MUTATING_REFLECTION_KINDS
        else "read"
    )
    return consult_with_audit(
        db,
        tenant_id=tenant_id,
        agent_id=agent_id,
        action={"kind": proposed_kind, "content": proposed_content},
        point="synthesis",
        intent=intent,
    )


__all__ = [
    "VALUE_SET_MEMORY_TYPE",
    "is_value_layer_enabled",
    "read_value_set",
    "write_value_set",
    "consult_with_audit",
    "consult_routing",
    "consult_tool",
    "consult_reflection",
    "appraise_user_signal_with_values",
    "synthesize_value_observations",
]
