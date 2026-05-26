"""Deterministic Code Reviewer stub (T6.1).

The Luna Learn pipeline (`docs/superpowers/plans/2026-05-25-luna-learn-from-media-plan.md`
spec §0.3) dispatches a draft SKILL.md to a Code Reviewer agent before
install. In production this is a tenant-scoped Claude agent; in tests
we need a hermetic stand-in that returns deterministic verdicts so the
workflow's revise / reject / approve branches are exercisable without
LLM calls.

The stub inspects the draft body for marker patterns and returns the
matching verdict:

  * any line containing ``TODO`` (case-sensitive) → ``revise`` with a
    finding explaining the marker. Mirrors a real reviewer flagging
    placeholder content for the synth-revise loop (T3.2c).
  * any occurrence of ``rm -rf`` OR ``subprocess`` → ``rejected``
    with a finding citing the dangerous shellout. Mirrors the spec
    §0.3 "no shellouts in skills" review rule.
  * otherwise → ``approved`` with empty findings — the happy path
    used by smoke tests that just want install to succeed.

The function is sync (not async) because tests can call it directly,
and async wrappers can ``return reviewer_stub(...)`` inside an
``async def`` shim if they need an awaitable form. Keeping it sync
also makes it trivial to use as a ``side_effect`` for an
``AsyncMock``: wrap it with ``lambda *a, **kw: reviewer_stub(...)``.

Returns the same shape as the real ``dispatch_skill_review`` MCP tool:

    {"verdict": "approved"|"revise"|"rejected", "findings": [str, ...],
     "reviewer_agent_id": str}
"""
from __future__ import annotations

# A fixed UUID so tests can assert provenance.reviewer_agent_id without
# coupling to the live tenant-seeded reviewer id. Picked deterministically.
STUB_REVIEWER_AGENT_ID = "00000000-0000-0000-0000-00000000beef"


def reviewer_stub(
    skill_md: str,
    transcript: str | None = None,
    source_url: str | None = None,
    synthetic_test_input: dict | None = None,
    synthetic_test_expected: dict | None = None,
) -> dict:
    """Return a deterministic review verdict for ``skill_md``.

    Pattern matching is case-sensitive for the TODO marker (matches the
    convention real reviewers flag) and case-insensitive for the
    shellout markers (defense-in-depth against accidental case-flips).
    """
    body = skill_md or ""

    # Reject takes precedence over revise — a draft with both a TODO and a
    # subprocess call should be rejected outright, not bounced for revision.
    lowered = body.lower()
    if "rm -rf" in lowered or "subprocess" in lowered:
        findings = []
        if "rm -rf" in lowered:
            findings.append("dangerous shellout: rm -rf detected in skill body")
        if "subprocess" in lowered:
            findings.append("dangerous shellout: subprocess call detected in skill body")
        return {
            "verdict": "rejected",
            "findings": findings,
            "reviewer_agent_id": STUB_REVIEWER_AGENT_ID,
        }

    if "TODO" in body:
        return {
            "verdict": "revise",
            "findings": ["draft contains TODO placeholder — replace before install"],
            "reviewer_agent_id": STUB_REVIEWER_AGENT_ID,
        }

    return {
        "verdict": "approved",
        "findings": [],
        "reviewer_agent_id": STUB_REVIEWER_AGENT_ID,
    }
