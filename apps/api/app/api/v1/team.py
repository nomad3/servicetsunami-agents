"""Teamwork Engine — observability + operator write endpoints
(Phase 1 PR B + PR C).

HTTP surface for the Social Protocol primitive. Mirrors emotion.py
(thin FastAPI router, tenant-scoped via get_current_user, JSON shapes
consumable by dashboard + alpha CLI).

Read endpoints (PR B):
- GET /team/roles            list role contracts (optionally by agent)
- GET /team/roles/active     active contract for (agent, scope) or 404
- GET /team/norms            list norms (coalition-scoped or tenant-wide)

Write endpoints (PR C):
- POST /team/roles           create a role contract
- POST /team/norms           create a norm
- POST /team/roles/{id}/amend  write a superseding contract (operator
                                can override conditions / rationale /
                                effective_from; old contract stays as
                                audit trail; evaluate_role_contract
                                picks new one via most-recent-
                                effective_from tie-break)
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.user import User
from app.schemas.team import (
    ALLOWED_NORM_KEYS,
    ALLOWED_ROLES,
    ALLOWED_SCOPES,
    TeamNorm,
    TeamRoleContract,
)
from app.services.team_engine_io import (
    get_active_role,
    list_norms,
    list_role_contracts,
    write_norm,
    write_role_contract,
)

router = APIRouter()


# ── Roles ─────────────────────────────────────────────────────────────


@router.get("/team/roles")
def list_team_roles(
    agent_id: Optional[uuid.UUID] = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return all role contracts for the current tenant, optionally
    filtered to a single agent.

    Response shape:
        {
            "tenant_id": "<uuid>",
            "agent_id_filter": "<uuid>" | null,
            "contracts": [
                {role contract JSON, including the derive-on-read
                 `is_active_now` flag},
                ...
            ]
        }

    No foreign-tenant access path — the service-layer query filters by
    `tenant_id` from the JWT.
    """
    from datetime import datetime, timezone

    contracts = list_role_contracts(
        db,
        tenant_id=current_user.tenant_id,
        agent_id=agent_id,
    )
    now = datetime.now(timezone.utc)
    out = []
    for c in contracts:
        d = c.to_dict()
        d["is_active_now"] = c.is_active_at(now)
        out.append(d)
    return {
        "tenant_id": str(current_user.tenant_id),
        "agent_id_filter": str(agent_id) if agent_id else None,
        "contracts": out,
    }


@router.get("/team/roles/active")
def get_team_active_role(
    agent_id: uuid.UUID,
    scope: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return the single currently-in-effect role contract for the
    given (agent, scope), or 404 if no contract applies.

    Used by the agent_router (eventually) to gate dispatch decisions
    against the typed role split.
    """
    contract = get_active_role(
        db,
        tenant_id=current_user.tenant_id,
        agent_id=agent_id,
        scope=scope,
    )
    if contract is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No active role contract for agent_id={agent_id} "
                f"scope={scope!r} in tenant {current_user.tenant_id}"
            ),
        )
    return contract.to_dict()


# ── Norms ─────────────────────────────────────────────────────────────


@router.get("/team/norms")
def list_team_norms(
    coalition_id: Optional[uuid.UUID] = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return all norms relevant to the current tenant, optionally
    scoped to a coalition. Includes BOTH the coalition-specific norms
    AND the tenant-wide defaults.

    Response shape:
        {
            "tenant_id": "<uuid>",
            "coalition_id_filter": "<uuid>" | null,
            "norms": [
                {norm JSON with derive-on-read `is_stale` flag},
                ...
            ]
        }
    """
    norms = list_norms(
        db,
        tenant_id=current_user.tenant_id,
        coalition_id=coalition_id,
    )
    out = []
    for n in norms:
        d = n.to_dict()
        d["is_stale"] = n.is_stale()
        out.append(d)
    return {
        "tenant_id": str(current_user.tenant_id),
        "coalition_id_filter": str(coalition_id) if coalition_id else None,
        "norms": out,
    }


# ── PR C — operator write endpoints ───────────────────────────────────


class CreateRoleContractRequest(BaseModel):
    """Body for POST /team/roles. The tenant_id comes from the JWT —
    never from the body — same pattern as tasks_fanout."""

    agent_id: uuid.UUID
    role: str
    scope: str
    coalition_id: Optional[uuid.UUID] = None
    effective_from: Optional[str] = None
    effective_until: Optional[str] = None
    conditions: dict = Field(default_factory=dict)
    rationale: str = ""

    @field_validator("role")
    @classmethod
    def _validate_role(cls, v: str) -> str:
        if v not in ALLOWED_ROLES:
            raise ValueError(f"role must be one of {sorted(ALLOWED_ROLES)}")
        return v

    @field_validator("scope")
    @classmethod
    def _validate_scope(cls, v: str) -> str:
        if v not in ALLOWED_SCOPES:
            raise ValueError(f"scope must be one of {sorted(ALLOWED_SCOPES)}")
        return v


class CreateNormRequest(BaseModel):
    """Body for POST /team/norms."""

    key: str
    value: Any
    coalition_id: Optional[uuid.UUID] = None
    rationale: str = ""

    @field_validator("key")
    @classmethod
    def _validate_key(cls, v: str) -> str:
        if v not in ALLOWED_NORM_KEYS:
            raise ValueError(f"key must be one of {sorted(ALLOWED_NORM_KEYS)}")
        return v


class AmendRoleContractRequest(BaseModel):
    """Body for POST /team/roles/{id}/amend. Writes a NEW contract
    that supersedes the existing one. Unset fields carry over from
    the original."""

    effective_from: Optional[str] = None
    effective_until: Optional[str] = None
    conditions: Optional[dict] = None
    rationale: Optional[str] = None


@router.post("/team/roles", status_code=201)
def create_team_role(
    body: CreateRoleContractRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a role contract for the current tenant.

    tenant_id is taken from the JWT, never the body (round-1 spoofing
    discipline matching tasks_fanout).
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    contract = TeamRoleContract(
        tenant_id=str(current_user.tenant_id),
        coalition_id=str(body.coalition_id) if body.coalition_id else None,
        agent_id=str(body.agent_id),
        role=body.role,
        scope=body.scope,
        effective_from=body.effective_from or now_iso,
        effective_until=body.effective_until,
        conditions=body.conditions,
        rationale=body.rationale,
        superseded_by=None,
    )
    row_id = write_role_contract(
        db,
        contract=contract,
        current_tenant_id=current_user.tenant_id,
    )
    if row_id is None:
        raise HTTPException(
            status_code=500,
            detail="Failed to persist role contract (see api logs)",
        )
    return {"id": str(row_id), "contract": contract.to_dict()}


@router.post("/team/norms", status_code=201)
def create_team_norm(
    body: CreateNormRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a norm for the current tenant. Coalition-specific
    (with coalition_id) OR tenant-wide default (without)."""
    norm = TeamNorm(
        tenant_id=str(current_user.tenant_id),
        coalition_id=str(body.coalition_id) if body.coalition_id else None,
        key=body.key,
        value=body.value,
        rationale=body.rationale,
        last_confirmed_at=datetime.now(timezone.utc).isoformat(),
    )
    row_id = write_norm(
        db,
        norm=norm,
        current_tenant_id=current_user.tenant_id,
    )
    if row_id is None:
        raise HTTPException(
            status_code=500,
            detail=(
                "Failed to persist norm — tenant may have no agents "
                "(norms anchor on a real agent_id), or write failed; "
                "see api logs"
            ),
        )
    return {"id": str(row_id), "norm": norm.to_dict()}


@router.post("/team/roles/{contract_row_id}/amend", status_code=201)
def amend_team_role(
    contract_row_id: uuid.UUID,
    body: AmendRoleContractRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Amend an existing role contract by writing a NEW contract that
    inherits the original's identity (agent_id, role, scope,
    coalition_id) but overrides conditions / effective range /
    rationale.

    evaluate_role_contract picks the most-recent-effective_from
    contract as the active one, so writing a newer contract is the
    natural amendment path. The original stays in-place as audit.

    Returns 404 if `contract_row_id` doesn't exist or belongs to
    another tenant. Returns 201 + the new contract on success.
    """
    from app.models.agent_memory import AgentMemory
    from app.services.team_engine import (
        ROLE_CONTRACT_MEMORY_TYPE,
        deserialize_role_contract,
    )

    row = (
        db.query(AgentMemory)
        .filter(
            AgentMemory.id == contract_row_id,
            AgentMemory.tenant_id == current_user.tenant_id,
            AgentMemory.memory_type == ROLE_CONTRACT_MEMORY_TYPE,
        )
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="role contract not found")

    original = deserialize_role_contract(row.content)
    if original is None:
        raise HTTPException(
            status_code=500,
            detail="role contract row exists but content is malformed",
        )

    now_iso = datetime.now(timezone.utc).isoformat()
    amended_effective_from = body.effective_from or now_iso

    # Luna 2026-05-19 review: enforce supersession invariant. If the
    # caller supplies an effective_from that is not strictly after the
    # original's effective_from, evaluate_role_contract would still
    # pick the older row (it's the most-recent tie-break) and the
    # "amend" would silently leave the old contract active. Reject
    # rather than silently keep the old contract in force.
    if amended_effective_from <= original.effective_from:
        raise HTTPException(
            status_code=422,
            detail=(
                f"effective_from ({amended_effective_from}) must be "
                f"strictly after original's effective_from "
                f"({original.effective_from}) — supersession requires a "
                "later timestamp"
            ),
        )

    amended = TeamRoleContract(
        # Luna 2026-05-19 review: use current_user.tenant_id rather
        # than original.tenant_id from JSON content. Even though the
        # row was tenant-scope-filtered on lookup, corrupted content
        # could carry a wrong tenant_id that we'd faithfully copy.
        tenant_id=str(current_user.tenant_id),
        coalition_id=original.coalition_id,
        agent_id=original.agent_id,
        role=original.role,
        scope=original.scope,
        effective_from=amended_effective_from,
        effective_until=(
            body.effective_until
            if body.effective_until is not None
            else original.effective_until
        ),
        conditions=(
            body.conditions if body.conditions is not None else original.conditions
        ),
        rationale=(
            body.rationale if body.rationale is not None else original.rationale
        ),
        superseded_by=None,
    )
    new_row_id = write_role_contract(
        db,
        contract=amended,
        current_tenant_id=current_user.tenant_id,
    )
    if new_row_id is None:
        raise HTTPException(
            status_code=500,
            detail="Failed to persist amended role contract",
        )

    # Stamp the original row with superseded_by pointer so the audit
    # trail is bidirectional (forward via effective_from ordering,
    # backward via superseded_by). Best-effort — if this fails the new
    # contract still wins by evaluate_role_contract's tie-break, but
    # we'd lose the explicit pointer.
    try:
        from app.services.team_engine import serialize_role_contract

        stamped = TeamRoleContract(
            tenant_id=original.tenant_id,
            coalition_id=original.coalition_id,
            agent_id=original.agent_id,
            role=original.role,
            scope=original.scope,
            effective_from=original.effective_from,
            effective_until=original.effective_until,
            conditions=original.conditions,
            rationale=original.rationale,
            superseded_by=str(new_row_id),
        )
        row.content = serialize_role_contract(stamped)
        db.add(row)
        db.commit()
    except Exception as exc:
        # Don't surface this failure — the amend itself succeeded.
        # Log for visibility.
        import logging
        logging.getLogger(__name__).warning(
            "amend_team_role: failed to stamp superseded_by on original "
            "row id=%s — new contract is still in force. err=%s",
            contract_row_id, exc,
        )
        db.rollback()

    return {
        "id": str(new_row_id),
        "superseded_row_id": str(contract_row_id),
        "contract": amended.to_dict(),
    }
