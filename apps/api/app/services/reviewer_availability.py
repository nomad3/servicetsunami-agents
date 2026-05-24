"""Reviewer-availability gate for the formal review fanout.

When a PR is dispatched for review (`review_service.start_review`),
this layer asserts that every required bundled-agent reviewer is
actually reachable + trusted to act as a gate. If any required
reviewer fails availability, the dispatch is aborted with a
structured error — no silent fail-open.

Design: docs/plans/2026-05-24-review-gate-medium-followups-design.md
Motivation: gap #3 of the 2026-05-24 blameless RL experiment
(concern observation b0533a44). Companion to the circularity gate
in review_circularity.py.

What this DOES check (Phase 1):
  1. Agent exists in this tenant (else `agent_missing`).
  2. Agent.status is not `draft` or `deprecated` (else
     `agent_disabled` — drafts haven't been operator-approved;
     deprecated agents shouldn't be reviewing anything new).
  3. Agent.tool_groups_review_required is False (else
     `review_required_unresolved` — a reviewer that's itself
     awaiting operator review of its tool_groups cannot be a
     trusted merge gate). This is the bridge to PR #705's flag.

What this DOES NOT check (deferred):
  4. "Last successful dispatch within N minutes" — needs a
     `last_dispatch_at` column on Agent that doesn't exist yet.
     Adding it is a separate migration + dispatch-path integration.

Chicken-and-egg: Code Reviewer (migration 151) and Substrate
Sentinel (migration 152) both shipped with
`tool_groups_review_required=TRUE` (retroactively set in migration
153). Per the design they cannot act as reviewers until an
operator explicitly clears the flag. This is intentional — the
gate is doing its job. The operator-review-queue UI is a known
follow-up (column COMMENT in migration 153 calls this out).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import List, Literal, Optional

from sqlalchemy.orm import Session

from app.models.agent import Agent
from app.services.bundled_agents import slug_to_name


UnavailabilityCode = Literal[
    "agent_missing",
    "agent_disabled",
    "review_required_unresolved",
]


@dataclass(frozen=True)
class UnavailabilityReason:
    """One required reviewer is not in a state to act as a gate."""

    agent_slug: str
    code: UnavailabilityCode
    detail: str
    next_steps: str


# Operator-facing "how do I unblock this?" hint per code. Lives
# alongside the reasons rather than being a separate runbook because
# operators staring at a 409 detail blob need the path-forward inline
# — especially during the chicken-and-egg window where Code Reviewer
# + Substrate Sentinel are both review_required=TRUE on first ship.
_NEXT_STEPS_BY_CODE: dict[str, str] = {
    "agent_missing": (
        "Confirm the bundled agent is seeded for this tenant. If the "
        "agent should exist, re-run its seed migration."
    ),
    "agent_disabled": (
        "Promote the agent to status='production' via the agent "
        "lifecycle CLI/UI after operator review."
    ),
    "review_required_unresolved": (
        "Operator must clear tool_groups_review_required for this "
        "agent after confirming its tool_groups match advertised "
        "capability. No UI yet — clear via SQL: "
        "UPDATE agents SET tool_groups_review_required=FALSE "
        "WHERE name='<Name>'. See migration 153."
    ),
}


class ReviewerUnavailableError(Exception):
    """Raised when one or more required reviewers fail availability.

    Carries the list of `UnavailabilityReason` so the API layer can
    return them as structured 409 detail rather than a flat message.
    """

    def __init__(self, reasons: List[UnavailabilityReason]) -> None:
        self.reasons = reasons
        super().__init__(
            "reviewer-availability gate refused dispatch: "
            + ", ".join(f"{r.agent_slug}={r.code}" for r in reasons)
        )


# Bundled slug → canonical Agent.name lookup comes from
# app.services.bundled_agents (auto-discovered from
# _bundled/<slug>/skill.md frontmatter at import time). Both this
# module and review_circularity import from there — single source of
# truth so adding a new bundled agent is zero-touch.


# Statuses that disqualify an agent from acting as a merge gate.
# - draft: hasn't been operator-approved at all yet
# - staging: promoted but not blessed for production traffic; same
#   posture as a draft for the purpose of *gating a merge* — we want
#   operator sign-off via the production-promotion flow first
# - deprecated: explicitly retired
# Valid Agent.status values come from app/services/_agent_ordering.py
# (production / staging / draft / deprecated). Only `production` is
# eligible to act as a merge gate.
_DISABLED_STATUSES = {"draft", "staging", "deprecated"}


def check_required_reviewers(
    db: Session,
    tenant_id: uuid.UUID,
    required_slugs: List[str],
) -> List[UnavailabilityReason]:
    """Return the unavailable subset of ``required_slugs``.

    Empty list = all required reviewers are reachable. Caller is
    expected to abort dispatch + surface the reasons to the operator
    when this returns non-empty.

    Slugs that aren't bundled agents (CLI-platform slugs like
    `claude`/`codex`/`gemini`, custom operator agents) are skipped:
    they're considered "out of scope" for this gate, since the
    gate's invariants only apply to the new bundled-agent reviewer
    surface (Code Reviewer, Substrate Sentinel, ...).
    """
    reasons: List[UnavailabilityReason] = []

    for slug in required_slugs:
        name = slug_to_name(slug)
        if name is None:
            # Unknown slug — out of scope (CLI platform or custom).
            continue

        agent = (
            db.query(Agent)
            .filter(
                Agent.tenant_id == tenant_id,
                Agent.name == name,
            )
            .one_or_none()
        )
        reason = _evaluate(slug, agent)
        if reason is not None:
            reasons.append(reason)

    return reasons


def _evaluate(slug: str, agent: Optional[Agent]) -> Optional[UnavailabilityReason]:
    if agent is None:
        return _reason(
            slug,
            "agent_missing",
            f"no Agent row found for slug '{slug}' in this tenant",
        )
    status = (agent.status or "").lower()
    if status in _DISABLED_STATUSES:
        return _reason(
            slug,
            "agent_disabled",
            f"agent status='{status}' — not eligible to gate merges",
        )
    # Direct attribute access — the column is non-nullable and
    # defaulted on the model (see app/models/agent.py:48). Using
    # getattr with a default would fail-open silently on a future
    # rename, which is the wrong direction for a security gate.
    if agent.tool_groups_review_required:
        return _reason(
            slug,
            "review_required_unresolved",
            (
                "agent is itself awaiting operator review of its "
                "tool_groups (tool_groups_review_required=TRUE) — "
                "cannot act as a merge gate until cleared"
            ),
        )
    return None


def _reason(
    slug: str, code: UnavailabilityCode, detail: str
) -> UnavailabilityReason:
    return UnavailabilityReason(
        agent_slug=slug,
        code=code,
        detail=detail,
        next_steps=_NEXT_STEPS_BY_CODE[code],
    )
