"""Teamwork Engine — Social Protocol primitive (Phase 1 PR A).

Pure functions for evaluating role contracts and selecting norms.
Mirrors the structure of `app.services.emotion_engine` (pure-functional
appraisal + decay; DB layer separate in `team_engine_io`). See
docs/plans/2026-05-19-teamwork-engine-design.md § "Phase 1 PR A".

Storage substrate: `agent_memory` rows with discriminator
`memory_type` values:
  - "team_role_contract" — TeamRoleContract JSON in `content`
  - "team_norm"          — TeamNorm JSON in `content`

No new tables. No new migration. Per the operator's standing rule
"reuse components". Phase 2 may introduce a dedicated
`team_role_contracts` table if the agent_memory approach proves
load-bearing.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Iterable, Optional

from app.schemas.team import (
    ALLOWED_NORM_KEYS,
    ALLOWED_ROLES,
    ALLOWED_SCOPES,
    TeamNorm,
    TeamRoleContract,
)


# ── Discriminator values for agent_memory.memory_type ─────────────────

ROLE_CONTRACT_MEMORY_TYPE = "team_role_contract"
NORM_MEMORY_TYPE = "team_norm"


# ── Serialisation between dataclass and agent_memory.content ──────────


def serialize_role_contract(contract: TeamRoleContract) -> str:
    """Serialise a TeamRoleContract for storage in
    agent_memory.content. JSON string keeps it portable + readable in
    the DB."""
    return json.dumps(contract.to_dict(), default=str, sort_keys=True)


def deserialize_role_contract(blob: str) -> Optional[TeamRoleContract]:
    """Hydrate a TeamRoleContract from agent_memory.content. Returns
    None for malformed payloads (don't crash the read path on a single
    bad row)."""
    try:
        data = json.loads(blob)
    except (TypeError, ValueError):
        return None
    try:
        return TeamRoleContract(
            tenant_id=data["tenant_id"],
            coalition_id=data.get("coalition_id"),
            agent_id=data["agent_id"],
            role=data["role"],
            scope=data["scope"],
            effective_from=data["effective_from"],
            effective_until=data.get("effective_until"),
            conditions=data.get("conditions") or {},
            rationale=data.get("rationale", ""),
            superseded_by=data.get("superseded_by"),
        )
    except (KeyError, ValueError):
        return None


def serialize_norm(norm: TeamNorm) -> str:
    """Serialise a TeamNorm for storage in agent_memory.content."""
    return json.dumps(norm.to_dict(), default=str, sort_keys=True)


def deserialize_norm(blob: str) -> Optional[TeamNorm]:
    """Hydrate a TeamNorm from agent_memory.content. Returns None for
    malformed payloads."""
    try:
        data = json.loads(blob)
    except (TypeError, ValueError):
        return None
    try:
        return TeamNorm(
            tenant_id=data["tenant_id"],
            coalition_id=data.get("coalition_id"),
            key=data["key"],
            value=data.get("value"),
            rationale=data.get("rationale", ""),
            last_confirmed_at=data["last_confirmed_at"],
        )
    except (KeyError, ValueError):
        return None


# ── Pure selection logic ──────────────────────────────────────────────


def evaluate_role_contract(
    contracts: Iterable[TeamRoleContract],
    *,
    agent_id: str,
    scope: str,
    now: Optional[datetime] = None,
) -> Optional[TeamRoleContract]:
    """Given an iterable of TeamRoleContract candidates, return the one
    that's currently in effect for `(agent_id, scope)`, or None.

    Tie-breaking when multiple contracts match: prefer the one with the
    latest `effective_from` — most recently amended wins. This lets the
    operator add a new contract that supersedes the older one without
    having to explicitly mark `superseded_by` on the old row.

    Pure function. The IO layer fetches the candidates from
    agent_memory; this function picks the active one.
    """
    if scope not in ALLOWED_SCOPES:
        raise ValueError(
            f"evaluate_role_contract: scope must be one of {ALLOWED_SCOPES}, "
            f"got {scope!r}"
        )
    when = now or datetime.now(timezone.utc)
    candidates = [
        c for c in contracts
        if c.agent_id == agent_id
        and c.scope == scope
        and c.is_active_at(when)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda c: c.effective_from)


def select_norm(
    norms: Iterable[TeamNorm],
    *,
    key: str,
    coalition_id: Optional[str] = None,
) -> Optional[TeamNorm]:
    """Given an iterable of TeamNorm candidates, return the most-
    specific one matching `key` for the given coalition. Resolution
    order:
      1. Coalition-specific (norm.coalition_id == coalition_id, non-None)
      2. Tenant-wide default (norm.coalition_id is None)
      3. None if neither exists.

    When a coalition has both a specific override and a tenant-wide
    default for the same key, the override wins.
    """
    if key not in ALLOWED_NORM_KEYS:
        raise ValueError(
            f"select_norm: key must be one of {ALLOWED_NORM_KEYS}, "
            f"got {key!r}"
        )
    coalition_specific: Optional[TeamNorm] = None
    tenant_wide: Optional[TeamNorm] = None
    for norm in norms:
        if norm.key != key:
            continue
        if coalition_id is not None and norm.coalition_id == coalition_id:
            coalition_specific = norm  # last one wins (tiebreak by order)
        elif norm.coalition_id is None:
            tenant_wide = norm
    return coalition_specific or tenant_wide


# ── Role-name aliases (mirror the prose role-split in typed form) ─────


def describe_role_split(contract: TeamRoleContract) -> str:
    """Render a TeamRoleContract as a human-readable sentence for
    display in the UI or in audit logs. The Phase 1 'role split' from
    2026-05-19 is the canonical example:
        'Claude holds driver for execution until codex_subscription_tier=team. Rationale: ...'
    """
    tail = ""
    if contract.effective_until:
        tail = f" until {contract.effective_until}"
    elif contract.conditions:
        cond_str = ", ".join(f"{k}={v}" for k, v in contract.conditions.items())
        tail = f" until {cond_str}"
    return (
        f"{contract.agent_id} holds {contract.role} for {contract.scope}{tail}. "
        f"Rationale: {contract.rationale or '(none)'}"
    )


__all__ = [
    "ROLE_CONTRACT_MEMORY_TYPE",
    "NORM_MEMORY_TYPE",
    "serialize_role_contract",
    "deserialize_role_contract",
    "serialize_norm",
    "deserialize_norm",
    "evaluate_role_contract",
    "select_norm",
    "describe_role_split",
]
