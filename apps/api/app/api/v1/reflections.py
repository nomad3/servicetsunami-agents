"""O4 — read-side surface for NightlyReflection rows.

Canonical design §5 / O4. Operator-facing GET endpoint that the Den
UI's "Yesterday's Reflections" page consumes; conversational chat
expansion uses the same path to surface source memories.

Tenant-scoped: every query filters on ``current_user.tenant_id`` —
foreign-tenant reflections are simply absent from the result (not
404'd, since the absence IS the correct shape: this tenant has no
reflections matching).

The reflection rows themselves live in ``agent_memories`` with
``memory_type='nightly_reflection'`` — the read goes through
``reflection_io.list_reflections`` so the SQLite-shim/Postgres
dialect quirks stay in one place.

Wired at ``/api/v1/luna/reflections`` so the path mirrors the
"Luna's morning notes" mental model. Filters:

  - day:      YYYY-MM-DD UTC — which day the reflections are about
  - kind:     one of REFLECTION_KINDS
  - agent_id: scope to one agent (defaults to all in tenant)
  - limit:    cap result count (default 100, max 500)
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.user import User
from app.schemas.reflection import REFLECTION_KINDS
from app.services import reflection_io


router = APIRouter()


@router.get("/luna/reflections")
def list_reflections(
    day: Optional[str] = Query(
        default=None,
        description="YYYY-MM-DD UTC day the reflection is about. "
        "Omit to return all days the tenant has on file.",
    ),
    kind: Optional[str] = Query(
        default=None,
        description="One of risk / idea / tension / next_move / creative.",
    ),
    agent_id: Optional[uuid.UUID] = Query(
        default=None,
        description="Scope to a single agent. Omit for all agents in tenant.",
    ),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return reflections for the caller's tenant, optionally filtered.

    Tenant isolation: ``current_user.tenant_id`` is the only tenant
    every row passes through — no opt-in cross-tenant view exists.

    Validates ``kind`` against REFLECTION_KINDS BEFORE touching the
    DB so a misspelled query (which would otherwise silently filter
    every row out) surfaces as a 400.

    Response::

        {
            "tenant_id": "<uuid>",
            "count": int,
            "reflections": [ <NightlyReflection.to_dict()>, ... ]
        }

    Ordered by ``created_at DESC`` so the morning-review surface
    sees the freshest reflection first.
    """
    if kind is not None and kind not in REFLECTION_KINDS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"kind must be one of {sorted(REFLECTION_KINDS)}, "
                f"got {kind!r}"
            ),
        )

    rows = reflection_io.list_reflections(
        db,
        tenant_id=current_user.tenant_id,
        day=day,
        kind=kind,
        agent_id=agent_id,
    )
    rows = rows[:limit]

    return {
        "tenant_id": str(current_user.tenant_id),
        "count": len(rows),
        "reflections": [r.to_dict() for r in rows],
    }


@router.get("/luna/reflections/count")
def get_reflections_count(
    day: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Cheap count probe for the morning-dashboard badge.

    Returns ``{count: int}`` — no row contents. Same tenant-isolation
    contract as the list endpoint.
    """
    n = reflection_io.get_reflection_count(
        db,
        tenant_id=current_user.tenant_id,
        day=day,
    )
    return {"count": n}
