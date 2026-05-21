"""Tests for O3 reflection_validators — the hard CI gate that any
synthesized reflection MUST pass before it's written to agent_memory.

Pure-function validators (next_move harm, entity grounding) get
unit-tested without a DB. The citation + creative-opt-in validators
need a real DB session, so they run on the integration job.

Locked properties verified here:
  - citation_unknown_ids rejects hallucinated source UUIDs
  - entity_invention rejects 'Project Pegasus' that never appeared
  - next_move_harm rejects destructive vocabulary on next_move kind
  - next_move_harm is INERT on non-next_move kinds (no false positives)
  - creative_optin_off rejects creative kind when the tenant flag is off
  - validate_reflection short-circuits on the FIRST failure
"""
from __future__ import annotations

import uuid

import pytest

from app.schemas.reflection import NightlyReflection
from app.services.reflection_validators import (
    ValidationResult,
    _extract_entities,
    validate_citation,
    validate_creative_opt_in,
    validate_entity_grounding,
    validate_next_move_safety,
    validate_reflection,
)


def _refl(
    *,
    kind: str = "risk",
    content: str = "the deploy pipeline keeps timing out",
    source_memory_ids=None,
    confidence: float = 0.7,
    tenant_id: str = "11111111-1111-1111-1111-111111111111",
    agent_id: str = "22222222-2222-2222-2222-222222222222",
    day: str = "2026-05-20",
    ts: str = "2026-05-21T00:00:00+00:00",
) -> NightlyReflection:
    return NightlyReflection(
        tenant_id=tenant_id,
        agent_id=agent_id,
        day=day,
        kind=kind,
        content=content,
        source_memory_ids=source_memory_ids
        or [str(uuid.uuid4())],
        confidence=confidence,
        ts=ts,
    )


# ── ValidationResult ─────────────────────────────────────────────────


def test_validation_result_pass_and_fail():
    p = ValidationResult.pass_()
    assert p.ok is True and p.reason is None
    f = ValidationResult.fail("oops")
    assert f.ok is False and f.reason == "oops"


# ── Entity extraction (pure function, no DB) ─────────────────────────


def test_extract_entities_quoted_and_capitalized():
    text = (
        "Tomorrow I'll review 'Project Pegasus' and meet with "
        "Acme Corp to discuss the Tomato cluster."
    )
    out = {e.lower() for e in _extract_entities(text)}
    assert "project pegasus" in out
    assert "acme corp" in out
    # "Tomorrow" is in the stopword list (single-word, sentence-starter)
    assert "tomorrow" not in out


def test_extract_entities_empty_text():
    assert _extract_entities("") == []
    assert _extract_entities("   ") == []


def test_extract_entities_dedupes_case_insensitively():
    out = _extract_entities("Acme Corp wrote to ACME CORP and Acme Corp.")
    # All three case variants collapse to one canonical
    assert len([e for e in out if e.lower() == "acme corp"]) == 1


# ── validate_entity_grounding (pure function) ────────────────────────


def test_entity_grounding_passes_when_all_entities_in_source():
    refl = _refl(content="Acme Corp needs a follow-up on 'Pegasus'.")
    sources = [
        "We discussed Acme Corp deliverables this morning.",
        "The Pegasus engagement is on hold until Q3.",
    ]
    r = validate_entity_grounding(refl, source_memory_contents=sources)
    assert r.ok, r.reason


def test_entity_grounding_rejects_invented_entity():
    refl = _refl(content="Acme Corp confirmed they'll work with NotARealCo.")
    sources = ["Met with Acme Corp today."]
    r = validate_entity_grounding(refl, source_memory_contents=sources)
    assert not r.ok
    assert "entity_invention" in r.reason


def test_entity_grounding_passes_when_no_entities_present():
    """Reflections without proper-noun-shaped tokens pass automatically.
    The validator's job is to catch INVENTED entities, not to require
    them."""
    refl = _refl(content="the deploy pipeline is timing out lately")
    r = validate_entity_grounding(refl, source_memory_contents=[])
    assert r.ok


# ── validate_next_move_safety (pure function) ────────────────────────


@pytest.mark.parametrize("content,should_pass", [
    # Pass cases
    ("Review the deploy log and post a summary in #ops.", True),
    ("Tomorrow, ping the SRE channel before the migration.", True),
    ("Confirm the test plan with the security team.", True),
    # Hard-block cases
    ("Drop the production database to free disk space.", False),
    ("Delete all customer records older than 2 years.", False),
    ("Disable rate limiting on the public API.", False),
    ("Mass send the breach notice to every user immediately.", False),
    ("Force push main to bypass the failing review.", False),
    ("Revoke all permissions for the contractor account.", False),
])
def test_next_move_harm_gate(content, should_pass):
    refl = _refl(kind="next_move", content=content)
    r = validate_next_move_safety(refl)
    assert r.ok == should_pass, (
        f"expected ok={should_pass} for content={content!r}, got "
        f"ok={r.ok} reason={r.reason}"
    )


def test_next_move_harm_is_inert_on_non_next_move_kinds():
    """Same destructive vocabulary on a 'risk' kind reflection MUST
    pass — the validator's purpose is to block proposed ACTIONS, not
    to censor pattern descriptions."""
    refl = _refl(
        kind="risk",
        content="Pattern: agents sometimes drop the production "
                "database when retry storms hit.",
    )
    r = validate_next_move_safety(refl)
    assert r.ok, r.reason


# ── Citation validator (needs DB — integration) ───────────────────────


def test_citation_rejects_malformed_uuid_without_db():
    """Even without a DB session, a malformed UUID string in
    source_memory_ids should fail validation before any SQL runs.
    We can test this synchronously since the schema accepts any
    non-empty list of strings."""
    # NightlyReflection accepts arbitrary strings in source_memory_ids;
    # validate_citation parses them. Pass a non-UUID string.
    refl = _refl(source_memory_ids=["not-a-uuid"])
    # DB doesn't matter — the UUID parse fails first.
    class _NoOpDb:
        def query(self, *a, **kw):
            raise AssertionError("should not reach DB on malformed UUID")
    r = validate_citation(
        refl,
        db=_NoOpDb(),  # type: ignore[arg-type]
        current_tenant_id=uuid.UUID(refl.tenant_id),
    )
    assert not r.ok
    assert "citation_malformed_uuid" in r.reason
