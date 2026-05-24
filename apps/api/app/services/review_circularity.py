"""Introduction-PR review circularity detection.

A PR that modifies an agent's own bundled config (skill.md, tool
groups, persona) cannot reliably be reviewed by that same agent —
the reviewer is operating under the OLD config while the diff
*defines* its new config. The reviewer has no way to assess whether
the new config still satisfies the invariants it was instantiated
under.

This module detects that case and returns the affected reviewers
plus their escalation target (Agent.escalation_agent_id resolved to
its bundled slug). The caller — typically `review_service.start_review`
or an operator-facing CLI — uses the filtered list and surfaces the
findings as a DECISION on the blackboard / a structured error.

Design: docs/plans/2026-05-24-review-gate-medium-followups-design.md
Motivation: PR #705 self-modification case (concern observation
b0533a44, gap #4 of the 2026-05-24 blameless RL experiment).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.agent import Agent
from app.services.bundled_agents import (
    BUNDLED_AGENTS_ROOT,
    name_to_slug,
    slug_to_name,
)


@dataclass(frozen=True)
class CircularityFinding:
    """One reviewer was filtered for self-modification.

    `escalation_slug` is the bundled slug of the supervisor the
    operator should route to instead, resolved via
    `Agent.escalation_agent_id`. None when the agent has no
    escalation target or the supervisor itself was the modified one
    (callers must then fall back to operator attestation).
    """

    agent_slug: str
    bundled_path: str
    escalation_slug: Optional[str]


def _bundled_paths_for_slug(slug: str) -> List[str]:
    """Return path prefixes that count as 'owned by' this agent.

    Today: the skill.md file plus the agent's _bundled/<slug>/ dir.
    Future migrations (per-agent prompt fragments, tool-group
    overlays) just add to this set.
    """
    base = f"{BUNDLED_AGENTS_ROOT}/{slug}"
    return [
        f"{base}/skill.md",
        f"{base}/",
    ]


def _slug_from_agent(agent: Agent) -> Optional[str]:
    """Best-effort slug lookup for an Agent row.

    Agent doesn't carry the bundled slug directly. We resolve it
    against the auto-discovered name→slug map maintained by
    bundled_agents. Returns None when no match found
    (operator-curated custom agents, etc.).
    """
    if not agent.name:
        return None
    return name_to_slug(agent.name)


def detect_self_modification(
    db: Session,
    tenant_id: uuid.UUID,
    changed_files: List[str],
    candidate_reviewer_slugs: List[str],
) -> Tuple[List[str], List[CircularityFinding]]:
    """Return ``(filtered_reviewers, findings)`` for a PR.

    For each candidate reviewer slug, check whether ``changed_files``
    includes the reviewer's bundled skill.md or any file under its
    `_bundled/<slug>/` directory. If yes, drop the slug from the
    returned list and record a finding with the escalation target
    (resolved via ``Agent.escalation_agent_id``) when available.

    Slugs that don't correspond to a bundled agent in this tenant
    (the CLI-platform slugs ``claude``/``codex``/``gemini``, custom
    operator agents, anything not present under ``_bundled/``) are
    passed through unchanged. The function is a no-op for them.
    """
    # Normalize paths so callers can pass absolute or relative
    # entries (gh pr diff vs. local path). Sorted so that the
    # `bundled_path` field on each finding is deterministic across
    # processes (otherwise the set's hash-randomized iteration order
    # made dry-run /check-circularity calls flap between two runs of
    # the same PR — see PR #706 review I3).
    normalized = sorted({_strip_repo_prefix(p) for p in changed_files})

    filtered: List[str] = []
    findings: List[CircularityFinding] = []

    for slug in candidate_reviewer_slugs:
        prefixes = _bundled_paths_for_slug(slug)
        # Match if any changed file exactly matches the skill.md OR
        # starts with the _bundled/<slug>/ directory prefix. First
        # match wins; iteration order is deterministic because we
        # sorted above.
        match = next(
            (
                p
                for p in normalized
                if p == prefixes[0] or p.startswith(prefixes[1])
            ),
            None,
        )
        if match is None:
            filtered.append(slug)
            continue

        escalation_slug = _resolve_escalation(db, tenant_id, slug)
        findings.append(
            CircularityFinding(
                agent_slug=slug,
                bundled_path=match,
                escalation_slug=escalation_slug,
            )
        )

    return filtered, findings


def _strip_repo_prefix(path: str) -> str:
    """Normalize an absolute or `./`-prefixed path to a repo-relative one.

    Uses explicit prefix-strip (`removeprefix("./")`) instead of
    `lstrip("./")` — `str.lstrip` is a character-class operation,
    so the old form silently swallowed any leading run of `.` and
    `/` characters (e.g. `'../something/...'` would collapse to
    `'something/...'`). The explicit form is the documented intent.
    """
    p = Path(path).as_posix()
    p = p.removeprefix("./")
    # If the caller passed an absolute path that contains BUNDLED_AGENTS_ROOT,
    # keep only from that root forward — matches how `gh pr diff --name-only`
    # emits paths.
    marker = BUNDLED_AGENTS_ROOT
    if marker in p and not p.startswith(marker):
        p = p[p.index(marker):]
    return p


def _resolve_escalation(
    db: Session,
    tenant_id: uuid.UUID,
    slug: str,
) -> Optional[str]:
    """Resolve the bundled slug of the agent's escalation target.

    Returns None when:
      - the slug doesn't correspond to a known bundled agent row
      - the agent has no ``escalation_agent_id`` set
      - the escalation target itself has no derivable bundled slug
        (custom operator agent, etc.)
    """
    name = slug_to_name(slug)
    if name is None:
        return None
    agent = (
        db.query(Agent)
        .filter(
            Agent.tenant_id == tenant_id,
            Agent.name == name,
        )
        .one_or_none()
    )
    if agent is None or agent.escalation_agent_id is None:
        return None
    target = (
        db.query(Agent)
        .filter(Agent.id == agent.escalation_agent_id)
        .one_or_none()
    )
    if target is None:
        return None
    return _slug_from_agent(target)
