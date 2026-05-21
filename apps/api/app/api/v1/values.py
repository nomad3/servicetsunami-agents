"""Operator endpoints for the Luna value set — PR 2 of #647.

Per docs/plans/2026-05-21-luna-value-layer-design.md §4.3 + §10.
Three routes:

  GET  /api/v1/luna/values
       Read the current (most-recent valid) value set for the
       caller's tenant + Luna persona agent. Returns the parsed
       AgentValueSet body + version + updated_at.

  GET  /api/v1/luna/values/agents/{agent_id}
       Same shape but explicitly scoped to a specific agent — for
       operators with multiple agents per tenant.

  PUT  /api/v1/luna/values
       Operator-driven full replace. Body is the three named lists
       (protect / pursue / avoid). Writes an append-only new row
       via reflection_io.write_value_set; version bumps from the
       prior max + 1.

Auth: standard user JWT (tenant scope from JWT, NOT from body).
Cross-tenant writes are structurally impossible: the body's
tenant is overwritten by the JWT's tenant before write.

The PUT path validates each item dict — slug + description required;
empty slug rejected; max 50 items per list (operator hygiene cap).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field, validator
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.core.config import settings
from app.models.agent import Agent
from app.models.user import User
from app.services import agent_value_set_io
from app.services.agent_value_set import AgentValueSet


router = APIRouter()


# ── Request / response schemas ────────────────────────────────────────


# Operator hygiene caps. A value set with 1000 items would be a
# misuse — the slug-substring matcher is O(items × text). Phase 2
# may bump these once the embedding-based match lands.
_MAX_ITEMS_PER_LIST = 50
_MAX_SLUG_LEN = 80
_MAX_DESCRIPTION_LEN = 400


class ValueItemIn(BaseModel):
    """One item written by the operator. The IO layer normalizes
    slug to lowercase + strips whitespace; we still validate
    minimum-length here so the operator sees a 400 immediately."""

    slug: str = Field(..., min_length=1, max_length=_MAX_SLUG_LEN)
    description: str = Field("", max_length=_MAX_DESCRIPTION_LEN)
    evidence_memory_ids: List[uuid.UUID] = Field(default_factory=list)

    @validator("slug")
    def _slug_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("slug must not be blank")
        return v


class ValueSetPutBody(BaseModel):
    """Full-replace body. Operator submits the entire value set;
    the IO layer writes a new row with version+1 (append-only,
    audit-trail via prior rows)."""

    protect: List[ValueItemIn] = Field(default_factory=list)
    pursue: List[ValueItemIn] = Field(default_factory=list)
    avoid: List[ValueItemIn] = Field(default_factory=list)

    @validator("protect", "pursue", "avoid")
    def _cap_list_length(cls, v: List[ValueItemIn]) -> List[ValueItemIn]:
        if len(v) > _MAX_ITEMS_PER_LIST:
            raise ValueError(
                f"each list may have at most {_MAX_ITEMS_PER_LIST} items"
            )
        return v


class ValueItemOut(BaseModel):
    slug: str
    description: str
    added_at: str
    added_by: str
    evidence_memory_ids: List[str]


class ValueSetOut(BaseModel):
    tenant_id: str
    agent_id: str
    protect: List[ValueItemOut]
    pursue: List[ValueItemOut]
    avoid: List[ValueItemOut]
    version: int
    updated_at: str


# ── Helpers ───────────────────────────────────────────────────────────


def _resolve_default_agent(
    db: Session,
    tenant_id: uuid.UUID,
) -> Agent:
    """Pick the tenant's Luna persona agent (or first agent if
    none named luna). Mirrors the resolution in
    reflection_activities.synthesize_reflections. Used by the
    GET / PUT routes that don't take an explicit agent_id."""
    agent = (
        db.query(Agent)
        .filter(
            Agent.tenant_id == tenant_id,
            Agent.name.ilike("%luna%"),
        )
        .first()
    )
    if agent is None:
        agent = (
            db.query(Agent)
            .filter(Agent.tenant_id == tenant_id)
            .first()
        )
    if agent is None:
        raise HTTPException(
            status_code=404,
            detail="tenant has no agents — create one before writing values",
        )
    return agent


def _vs_to_out(
    vs: AgentValueSet,
    *,
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID,
) -> ValueSetOut:
    return ValueSetOut(
        tenant_id=str(tenant_id),
        agent_id=str(agent_id),
        protect=[ValueItemOut(**i.to_dict()) for i in vs.protect],
        pursue=[ValueItemOut(**i.to_dict()) for i in vs.pursue],
        avoid=[ValueItemOut(**i.to_dict()) for i in vs.avoid],
        version=vs.version,
        updated_at=vs.updated_at,
    )


def _items_in_to_list(items: List[ValueItemIn], *, added_by: str) -> list[dict]:
    """Pydantic-validated items → the dict shape write_value_set
    expects. Sets added_by + added_at server-side (operator can't
    forge those fields)."""
    now = datetime.now(timezone.utc).isoformat()
    return [
        {
            "slug": item.slug,
            "description": item.description,
            "added_at": now,
            "added_by": added_by,
            "evidence_memory_ids": [str(u) for u in item.evidence_memory_ids],
        }
        for item in items
    ]


# ── Routes ────────────────────────────────────────────────────────────


@router.get("/luna/values", response_model=ValueSetOut)
def get_default_values(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Read the value set for the caller's tenant + Luna persona
    agent (or first agent if no Luna).

    Tenant scope: derived from ``current_user.tenant_id``. Cross-
    tenant reads are structurally impossible — the agent_id we
    resolve via _resolve_default_agent is filtered by the JWT
    tenant first.
    """
    agent = _resolve_default_agent(db, current_user.tenant_id)
    vs = agent_value_set_io.read_value_set(
        db,
        tenant_id=current_user.tenant_id,
        agent_id=agent.id,
    )
    return _vs_to_out(vs, tenant_id=current_user.tenant_id, agent_id=agent.id)


@router.get(
    "/luna/values/agents/{agent_id}",
    response_model=ValueSetOut,
)
def get_values_for_agent(
    agent_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Per-agent value set — for operators with multiple agents.

    Cross-tenant read protection: agent lookup is filtered by
    ``current_user.tenant_id``. Foreign agent_id → 404."""
    agent = (
        db.query(Agent)
        .filter(
            Agent.id == agent_id,
            Agent.tenant_id == current_user.tenant_id,
        )
        .first()
    )
    if agent is None:
        raise HTTPException(status_code=404, detail="agent not found")
    vs = agent_value_set_io.read_value_set(
        db,
        tenant_id=current_user.tenant_id,
        agent_id=agent.id,
    )
    return _vs_to_out(vs, tenant_id=current_user.tenant_id, agent_id=agent.id)


@router.put("/luna/values", response_model=ValueSetOut)
def put_default_values(
    body: ValueSetPutBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Full-replace the value set for the caller's tenant + Luna
    persona agent. Writes a new agent_memory row (append-only);
    prior versions remain as audit trail.

    The operator's submission is the ENTIRE state — items not
    included get implicitly removed. To remove a single item, GET
    the current set, drop the item, PUT the rest. Phase 2 may add
    PATCH semantics if operator UX demands it.

    Returns the persisted ValueSetOut (so the operator UI can
    confirm version bumped). Returns 503 on persistent write
    failure (concurrent collision exhausted retries, or DB error)."""
    agent = _resolve_default_agent(db, current_user.tenant_id)
    result = agent_value_set_io.write_value_set(
        db,
        tenant_id=current_user.tenant_id,
        agent_id=agent.id,
        protect=_items_in_to_list(body.protect, added_by="operator"),
        pursue=_items_in_to_list(body.pursue, added_by="operator"),
        avoid=_items_in_to_list(body.avoid, added_by="operator"),
    )
    if result is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "value set write failed (concurrent retry exhausted or "
                "db unavailable); please retry"
            ),
        )
    return _vs_to_out(
        result,
        tenant_id=current_user.tenant_id,
        agent_id=agent.id,
    )


@router.get(
    "/internal/values/agents/{agent_id}",
    response_model=ValueSetOut,
)
def get_values_internal(
    agent_id: uuid.UUID,
    x_internal_key: Optional[str] = Header(None, alias="X-Internal-Key"),
    x_tenant_id: Optional[uuid.UUID] = Header(None, alias="X-Tenant-Id"),
    db: Session = Depends(get_db),
):
    """Internal variant of the per-agent GET for MCP tools.

    Same auth pattern as /api/v1/internal/affect/agents/{id}:
    X-Internal-Key (matching settings.API_INTERNAL_KEY or
    MCP_API_KEY) + required X-Tenant-Id header. Tenant isolation
    via the agent-lookup guard: foreign-tenant agent_id → 404.
    Closes the same gap #640 closed for affect_baseline so Luna
    can read her own value set without a JWT.
    """
    if x_internal_key not in (
        settings.API_INTERNAL_KEY,
        settings.MCP_API_KEY,
    ):
        raise HTTPException(status_code=401, detail="Invalid internal key")
    if x_tenant_id is None:
        raise HTTPException(
            status_code=400,
            detail="X-Tenant-Id required with X-Internal-Key",
        )

    agent = (
        db.query(Agent)
        .filter(
            Agent.id == agent_id,
            Agent.tenant_id == x_tenant_id,
        )
        .first()
    )
    if agent is None:
        raise HTTPException(status_code=404, detail="agent not found")

    vs = agent_value_set_io.read_value_set(
        db, tenant_id=x_tenant_id, agent_id=agent.id,
    )
    return _vs_to_out(vs, tenant_id=x_tenant_id, agent_id=agent.id)


@router.put(
    "/luna/values/agents/{agent_id}",
    response_model=ValueSetOut,
)
def put_values_for_agent(
    agent_id: uuid.UUID,
    body: ValueSetPutBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Per-agent write companion to the default PUT. Cross-tenant
    write protection same as the GET."""
    agent = (
        db.query(Agent)
        .filter(
            Agent.id == agent_id,
            Agent.tenant_id == current_user.tenant_id,
        )
        .first()
    )
    if agent is None:
        raise HTTPException(status_code=404, detail="agent not found")
    result = agent_value_set_io.write_value_set(
        db,
        tenant_id=current_user.tenant_id,
        agent_id=agent.id,
        protect=_items_in_to_list(body.protect, added_by="operator"),
        pursue=_items_in_to_list(body.pursue, added_by="operator"),
        avoid=_items_in_to_list(body.avoid, added_by="operator"),
    )
    if result is None:
        raise HTTPException(
            status_code=503,
            detail="value set write failed; please retry",
        )
    return _vs_to_out(
        result,
        tenant_id=current_user.tenant_id,
        agent_id=agent.id,
    )
