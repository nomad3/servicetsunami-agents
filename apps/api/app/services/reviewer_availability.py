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


# Bundled slug → canonical Agent.name lookup. Mirrors the helper
# in review_circularity._resolve_escalation; kept in sync there.
_SLUG_TO_NAME = {
    "code-reviewer": "Code Reviewer",
    "substrate-sentinel": "Substrate Sentinel",
    "luna": "Luna",
}


_DISABLED_STATUSES = {"draft", "deprecated"}


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
        name = _SLUG_TO_NAME.get(slug)
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
        return UnavailabilityReason(
            agent_slug=slug,
            code="agent_missing",
            detail=f"no Agent row found for slug '{slug}' in this tenant",
        )
    status = (agent.status or "").lower()
    if status in _DISABLED_STATUSES:
        return UnavailabilityReason(
            agent_slug=slug,
            code="agent_disabled",
            detail=f"agent status='{status}' — not eligible to gate merges",
        )
    if getattr(agent, "tool_groups_review_required", False):
        return UnavailabilityReason(
            agent_slug=slug,
            code="review_required_unresolved",
            detail=(
                "agent is itself awaiting operator review of its "
                "tool_groups (tool_groups_review_required=TRUE) — "
                "cannot act as a merge gate until cleared"
            ),
        )
    return None
