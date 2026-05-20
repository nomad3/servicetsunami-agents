"""Teamwork Engine schemas — TeamRoleContract + TeamNorm dataclasses.

Phase 1 PR A — schema + read paths, no write paths, no behavior change.
See docs/plans/2026-05-19-teamwork-engine-design.md § "Phase 1 PR A".

Imports are deliberately minimal — these dataclasses are pure value
objects with no DB coupling. The IO layer in
`app.services.team_engine_io` wraps them with reads.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional


# ── Role contract ─────────────────────────────────────────────────────


# Allowed roles. Mirrors the prose role-split memory we've been carrying
# (Claude executes, Luna reviews) in typed form.
ALLOWED_ROLES = frozenset({"driver", "reviewer", "observer", "supervisor"})

# Allowed scopes. The dimension a role applies to.
ALLOWED_SCOPES = frozenset({
    "execution",          # implementation / code changes
    "review",             # code review, design review
    "design",             # design docs, architectural decisions
    "content_generation", # rich-media / marketing / docs
    "research",           # literature surveys, prospecting
})


@dataclass(frozen=True)
class TeamRoleContract:
    """Typed promise: agent A holds role R for scope S, optionally
    until some condition is met.

    Phase 1 PR A: pure dataclass. Persistence in PR B as a materialized
    view over `agent_memory` rows with `memory_type="role_contract"`. A
    dedicated `team_role_contracts` table may land in Phase 2 if usage
    proves out (per the design doc § "Substrate" caveat).
    """
    tenant_id: str
    coalition_id: Optional[str]
    agent_id: str
    role: str
    scope: str
    effective_from: str  # ISO-8601 UTC
    effective_until: Optional[str]
    conditions: dict
    rationale: str
    superseded_by: Optional[str]

    def __post_init__(self) -> None:
        if self.role not in ALLOWED_ROLES:
            raise ValueError(
                f"TeamRoleContract.role must be one of {ALLOWED_ROLES}, "
                f"got {self.role!r}"
            )
        if self.scope not in ALLOWED_SCOPES:
            raise ValueError(
                f"TeamRoleContract.scope must be one of {ALLOWED_SCOPES}, "
                f"got {self.scope!r}"
            )

    def is_active_at(self, when: datetime) -> bool:
        """True iff this contract is in effect at the given moment AND
        has not been superseded by a later amendment."""
        if self.superseded_by:
            return False
        try:
            effective_from = datetime.fromisoformat(self.effective_from)
        except ValueError:
            return False
        if effective_from.tzinfo is None:
            effective_from = effective_from.replace(tzinfo=timezone.utc)
        when_aware = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
        if when_aware < effective_from:
            return False
        if self.effective_until:
            try:
                effective_until = datetime.fromisoformat(self.effective_until)
            except ValueError:
                return True  # malformed end date — fail open
            if effective_until.tzinfo is None:
                effective_until = effective_until.replace(tzinfo=timezone.utc)
            if when_aware >= effective_until:
                return False
        return True

    def to_dict(self) -> dict:
        return asdict(self)


# ── Norm ──────────────────────────────────────────────────────────────


# Allowed norm keys for Phase 1. The set is intentionally finite so we
# don't accumulate ad-hoc string keys before the design has been
# exercised in real usage. Phase 2 may relax to free-form strings with
# a registry.
ALLOWED_NORM_KEYS = frozenset({
    "turn_taking",
    "handoff_etiquette",
    "reciprocity",
    "interrupt_protocol",
    "credit_sharing",
})


@dataclass(frozen=True)
class TeamNorm:
    """A coalition-level invariant. Stored on the Coalition row's
    config JSONB in Phase 1; Phase 2 may introduce a dedicated table.
    """
    tenant_id: str
    coalition_id: Optional[str]
    key: str
    value: object
    rationale: str
    last_confirmed_at: str  # ISO-8601 UTC

    def __post_init__(self) -> None:
        if self.key not in ALLOWED_NORM_KEYS:
            raise ValueError(
                f"TeamNorm.key must be one of {ALLOWED_NORM_KEYS}, "
                f"got {self.key!r}"
            )

    def is_stale(self, now: Optional[datetime] = None, max_age_days: int = 90) -> bool:
        """True iff the norm hasn't been confirmed within max_age_days."""
        try:
            confirmed_at = datetime.fromisoformat(self.last_confirmed_at)
        except ValueError:
            return True  # malformed timestamp -> treat as stale
        if confirmed_at.tzinfo is None:
            confirmed_at = confirmed_at.replace(tzinfo=timezone.utc)
        reference = now or datetime.now(timezone.utc)
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=timezone.utc)
        delta = reference - confirmed_at
        return delta.days >= max_age_days

    def to_dict(self) -> dict:
        return asdict(self)


__all__ = [
    "ALLOWED_ROLES",
    "ALLOWED_SCOPES",
    "ALLOWED_NORM_KEYS",
    "TeamRoleContract",
    "TeamNorm",
]
