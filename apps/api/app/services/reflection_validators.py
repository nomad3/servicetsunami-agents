"""O3 — reflection safety + grounding validators.

Canonical design §5 / O3 (locked 2026-05-20). Pure functions that
gate any candidate ``NightlyReflection`` before it lands in
``agent_memories``. The validator chain is enforced by
``reflection_activities.write_reflections`` — synthesis activities
produce payloads, validators reject the bad ones, only survivors
reach ``reflection_io.write_reflection``.

Four validators in design-doc order:

  1. ``validate_citation`` — every reflection MUST cite ≥ 1
     source memory (canonical §3.6 citation discipline). The
     dataclass ``__post_init__`` enforces non-empty list, but this
     check goes further: each cited UUID must EXIST in
     agent_memories for the same tenant. Catches hallucinated IDs.

  2. ``validate_entity_grounding`` — fact-invention guard. Extract
     entity-shaped tokens (quoted strings + Capitalized phrases)
     from the reflection content; reject when any extracted entity
     fails to appear in ANY cited source memory's content. Stops a
     synthesis pass from inventing 'project Pegasus' that never
     existed in the day's history.

  3. ``validate_next_move_safety`` — harm classifier (Phase 1
     heuristic). On ``kind == 'next_move'`` only, reject reflections
     whose action vocabulary matches a hard-block pattern (drop
     production tables, delete user data, mass-send messages, etc).
     Phase 2 may swap in a real classifier; the regex gate ships
     today so a runaway LLM can't quietly write a destructive
     "tomorrow's plan" into agent memory.

  4. ``validate_creative_opt_in`` — locked decision #1: ``creative``
     reflections are per-tenant opt-in via
     ``tenant_features.creative_reflections_enabled``. Reject
     creative payloads for tenants that haven't flipped the flag.

The chain is total: a payload that passes all four is safe to write.
A payload that fails any of them gets dropped with a structured
log line + counted in the write-activity's rejected-counter.

NO partial-write: a single bad payload doesn't taint others. Each
reflection in a synthesis batch is validated independently.
"""
from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.schemas.reflection import NightlyReflection

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ValidationResult:
    """Outcome of a single validator. ``ok`` True = pass; False =
    reject (caller drops the reflection + logs ``reason``). Pure
    dataclass — easy to compose into a chain."""

    ok: bool
    reason: Optional[str] = None

    @classmethod
    def pass_(cls) -> "ValidationResult":
        return cls(ok=True)

    @classmethod
    def fail(cls, reason: str) -> "ValidationResult":
        return cls(ok=False, reason=reason)


# ── 1. Citation validator ─────────────────────────────────────────────


def validate_citation(
    reflection: NightlyReflection,
    *,
    db: Session,
    current_tenant_id: uuid.UUID,
) -> ValidationResult:
    """Every cited source_memory_id MUST exist in agent_memories for
    this tenant. Schema-level ``__post_init__`` already requires the
    list to be non-empty; here we go further and verify EACH UUID is
    real, in this tenant, and not a hallucinated string.

    Returns fail() on:
      - Any UUID that doesn't parse.
      - Any UUID that doesn't exist in agent_memories for tenant_id.
      - SQL failure (defensive: a flaky DB MUST NOT silently let
        unvalidated reflections through; we treat as fail).
    """
    if not reflection.source_memory_ids:
        return ValidationResult.fail(
            "citation_empty: source_memory_ids list is empty",
        )

    try:
        parsed = [uuid.UUID(s) for s in reflection.source_memory_ids]
    except (ValueError, AttributeError) as exc:
        return ValidationResult.fail(
            f"citation_malformed_uuid: {exc}",
        )

    from app.models.agent_memory import AgentMemory

    try:
        rows = (
            db.query(AgentMemory.id)
            .filter(
                AgentMemory.tenant_id == str(current_tenant_id),
                AgentMemory.id.in_([str(p) for p in parsed]),
            )
            .all()
        )
    except SQLAlchemyError as exc:
        log.warning(
            "validate_citation: SQL failure tenant=%s err=%s; "
            "rejecting reflection to preserve the safety gate",
            current_tenant_id, exc,
        )
        return ValidationResult.fail(f"citation_sql_failure: {exc}")

    found = {str(r[0]) for r in rows}
    missing = [str(p) for p in parsed if str(p) not in found]
    if missing:
        return ValidationResult.fail(
            f"citation_unknown_ids: {missing[:3]}"
            + (f" (+{len(missing) - 3} more)" if len(missing) > 3 else "")
        )

    return ValidationResult.pass_()


# ── 2. Entity grounding validator ─────────────────────────────────────

# Extract entity-shaped tokens from reflection content:
#   - Quoted strings: 'foo bar', "Baz Quux"
#   - Multi-word Capitalized phrases: ProperNoun, Proper Noun
#     (heuristic; misses non-Latin scripts — acceptable for Phase 1)
_QUOTED_RE = re.compile(r"['\"]([^'\"]{2,80})['\"]")
_CAPITALIZED_PHRASE_RE = re.compile(
    r"\b[A-Z][a-zA-Z0-9]+(?:\s+[A-Z][a-zA-Z0-9]+){0,3}\b"
)

# Words that look Capitalized but aren't entity names — strip from the
# extracted set so we don't reject reflections that mention common
# sentence-starter words.
_ENTITY_STOPWORDS = frozenset({
    "The", "A", "An", "I", "We", "You", "They", "It",
    "Tomorrow", "Today", "Yesterday", "Monday", "Tuesday",
    "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
    "Morning", "Afternoon", "Evening", "Night",
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
})


def _extract_entities(text: str) -> List[str]:
    """Heuristic entity extraction. Returns lowercased candidate
    entity strings. Designed to over-extract — the validator just
    needs ONE source-memory match per entity, so false positives
    don't cause rejections."""
    if not text:
        return []
    entities: List[str] = []
    for m in _QUOTED_RE.finditer(text):
        entities.append(m.group(1).strip())
    for m in _CAPITALIZED_PHRASE_RE.finditer(text):
        candidate = m.group(0)
        # Drop single-word stopwords (sentence starters)
        if " " not in candidate and candidate in _ENTITY_STOPWORDS:
            continue
        entities.append(candidate)
    # Dedupe lowercased; preserve original case for the log
    seen: set[str] = set()
    out: List[str] = []
    for e in entities:
        k = e.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(e)
    return out


def validate_entity_grounding(
    reflection: NightlyReflection,
    *,
    source_memory_contents: Sequence[str],
) -> ValidationResult:
    """Reject reflections that mention entities never appearing in
    the cited source memories. Caller fetches the source-memory
    content strings (already filtered to tenant in
    ``validate_citation``) and passes them in.

    Heuristic: 'entities' = quoted strings + multi-word Capitalized
    phrases. Each extracted entity must substring-match (case-
    insensitive) at least one source-memory content. Missing
    entities → reject with the offending names in the reason.

    Phase 1 over-extracts to err on the side of rejection. A
    sentence-starter like 'The' gets stripped via stopword list;
    a fabricated 'Project Pegasus' would not match any source.
    """
    entities = _extract_entities(reflection.content)
    if not entities:
        return ValidationResult.pass_()  # no entities to verify

    combined = "\n".join(s.lower() for s in source_memory_contents)
    missing = [e for e in entities if e.lower() not in combined]
    if missing:
        return ValidationResult.fail(
            f"entity_invention: not found in source memories — "
            f"{missing[:3]}"
            + (f" (+{len(missing) - 3} more)" if len(missing) > 3 else "")
        )
    return ValidationResult.pass_()


# ── 3. next_move harm validator ───────────────────────────────────────

# Hard-block vocabulary. A real Phase-2 harm classifier will replace
# this with an LLM check, but the regex gate ships today so the
# scaffold can't accidentally land a destructive "tomorrow's plan"
# into agent memory. Patterns are conservative — false-positive cost
# is "operator manually approves a benign next_move", much smaller
# than the alternative.
_HARM_PATTERNS = (
    re.compile(r"\b(drop|delete|truncate|wipe|purge)\b.*\b("
               r"production|prod|database|table|user[s]?|tenant[s]?"
               r"|customer[s]?|account[s]?)\b", re.IGNORECASE),
    re.compile(r"\bdisable\b.*\b(auth|authentication|security|"
               r"rate[\s_-]?limit|backup)\b", re.IGNORECASE),
    re.compile(r"\b(mass|bulk)\s+(send|email|message|delete|notify)\b",
               re.IGNORECASE),
    re.compile(r"\b(force|hard)[\s_-]?push\b.*\b(main|master|release)\b",
               re.IGNORECASE),
    re.compile(r"\b(revoke|remove)\b.*\b(all|every)\b.*\b("
               r"permission[s]?|access|key[s]?|credential[s]?)\b",
               re.IGNORECASE),
    re.compile(r"\bexfiltrate\b|\bdoxx?\b|\bphish\b", re.IGNORECASE),
)


def validate_next_move_safety(
    reflection: NightlyReflection,
) -> ValidationResult:
    """Heuristic harm gate on ``kind == 'next_move'`` reflections only.
    Other kinds pass through.

    Phase 1 regex blacklist. A future Phase 2 should run a real
    harm classifier here; the gate is a hard-deny on the most
    obvious destructive patterns we'd never want appearing as
    'tomorrow's plan'.
    """
    if reflection.kind != "next_move":
        return ValidationResult.pass_()

    content = reflection.content or ""
    for pat in _HARM_PATTERNS:
        m = pat.search(content)
        if m:
            return ValidationResult.fail(
                f"next_move_harm: matched harm pattern — {m.group(0)!r}"
            )
    return ValidationResult.pass_()


# ── 4. Creative opt-in validator ──────────────────────────────────────


def validate_creative_opt_in(
    reflection: NightlyReflection,
    *,
    db: Session,
    current_tenant_id: uuid.UUID,
) -> ValidationResult:
    """Locked decision #1: ``creative`` reflections are per-tenant
    opt-in via ``tenant_features.creative_reflections_enabled``.
    Non-creative kinds pass through. Missing tenant_features row →
    treated as opt-out (defensive default OFF, same shape as the
    nightly_reflection kill-switch)."""
    if reflection.kind != "creative":
        return ValidationResult.pass_()

    try:
        from app.models.tenant_features import TenantFeatures
        row = (
            db.query(TenantFeatures)
            .filter(TenantFeatures.tenant_id == str(current_tenant_id))
            .first()
        )
    except SQLAlchemyError as exc:
        log.warning(
            "validate_creative_opt_in: SQL failure tenant=%s err=%s; "
            "rejecting (default OFF on flake)",
            current_tenant_id, exc,
        )
        return ValidationResult.fail(
            f"creative_optin_sql_failure: {exc}"
        )

    if row is None or not getattr(row, "creative_reflections_enabled", False):
        return ValidationResult.fail(
            "creative_optin_off: tenant has not enabled creative reflections"
        )
    return ValidationResult.pass_()


# ── Chain ─────────────────────────────────────────────────────────────


def validate_reflection(
    reflection: NightlyReflection,
    *,
    db: Session,
    current_tenant_id: uuid.UUID,
    source_memory_contents: Optional[Sequence[str]] = None,
) -> ValidationResult:
    """Run the full validator chain. Returns the FIRST failure or
    pass(). Callers (``write_reflections``) drop on fail and log
    the reason; passing reflections go to ``reflection_io.write_reflection``.

    ``source_memory_contents`` is the lookup list for the entity
    grounding validator. When None or empty, that validator is
    SKIPPED (we can't verify what we can't see). The citation
    validator runs first regardless, which establishes that the
    cited UUIDs really exist — so the entity check is bounded to
    real source-memory bodies the activity fetched.
    """
    r = validate_citation(
        reflection,
        db=db,
        current_tenant_id=current_tenant_id,
    )
    if not r.ok:
        return r

    if source_memory_contents:
        r = validate_entity_grounding(
            reflection,
            source_memory_contents=source_memory_contents,
        )
        if not r.ok:
            return r

    r = validate_next_move_safety(reflection)
    if not r.ok:
        return r

    r = validate_creative_opt_in(
        reflection,
        db=db,
        current_tenant_id=current_tenant_id,
    )
    if not r.ok:
        return r

    return ValidationResult.pass_()


__all__ = [
    "ValidationResult",
    "validate_citation",
    "validate_entity_grounding",
    "validate_next_move_safety",
    "validate_creative_opt_in",
    "validate_reflection",
]
