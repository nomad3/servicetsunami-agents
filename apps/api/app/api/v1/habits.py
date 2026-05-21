"""Habit observation endpoints (#297 platform-side cut).

Internal endpoint for the Luna Tauri client's vision pipeline. Reads
derived habit signals (posture/hydration/focus) from the client and
persists them as ``agent_memory`` rows so the RL feedback loop and
the conversational surface ("you've been hunched for 45 minutes")
can consume them without a separate dashboard.

Design doc: ``docs/plans/2026-05-19-luna-tauri-habit-tracker-design.md``
§3 ("Memory & RL Integration").

Auth pattern follows ``/api/v1/internal/embed`` /
``/api/v1/internal/affect/agents``: ``X-Internal-Key`` + ``X-Tenant-Id``.
Tenant boundary: the persisted row carries ``tenant_id`` from the
header, NOT from the request body. Cross-tenant writes are
structurally impossible because the body is parsed AFTER the header.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field, validator
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.config import settings
from app.models.agent_memory import AgentMemory

logger = logging.getLogger(__name__)

router = APIRouter()


_ALLOWED_HABITS = frozenset({"posture", "hydration", "focus"})
_ALLOWED_SIGNALS = frozenset({"score", "event", "duration"})

HABIT_MEMORY_TYPE = "habit_observation"


class HabitObservationIn(BaseModel):
    tenant_id: uuid.UUID = Field(..., description="Tenant scope. Must match X-Tenant-Id.")
    habit_name: str = Field(..., description="One of posture/hydration/focus.")
    signal_kind: str = Field(..., description="One of score/event/duration.")
    value: Any = Field(..., description="Signal value — float / str / int / bool.")
    source: str = Field(default="luna_tauri_client")
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)

    @validator("habit_name")
    def _validate_habit(cls, v: str) -> str:
        if v not in _ALLOWED_HABITS:
            raise ValueError(
                f"habit_name must be one of {sorted(_ALLOWED_HABITS)}, got {v!r}"
            )
        return v

    @validator("signal_kind")
    def _validate_signal(cls, v: str) -> str:
        if v not in _ALLOWED_SIGNALS:
            raise ValueError(
                f"signal_kind must be one of {sorted(_ALLOWED_SIGNALS)}, got {v!r}"
            )
        return v


@router.post("/internal/habits/observations")
def log_habit_observation(
    body: HabitObservationIn,
    x_internal_key: Optional[str] = Header(None, alias="X-Internal-Key"),
    x_tenant_id: Optional[uuid.UUID] = Header(None, alias="X-Tenant-Id"),
    db: Session = Depends(get_db),
):
    """Persist a habit observation from the Luna Tauri client.

    Auth: X-Internal-Key (matching API_INTERNAL_KEY or MCP_API_KEY)
    + X-Tenant-Id (the tenant scope is the HEADER value, never the
    body — defense-in-depth against a body that lies about its
    tenant).

    Returns ``{"memory_id": <uuid>}`` on success.
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
    # Body's tenant_id MUST match the header. We refuse the request
    # rather than silently overriding the body — the client should
    # never claim a different tenant in the body than the header.
    if str(body.tenant_id) != str(x_tenant_id):
        raise HTTPException(
            status_code=400,
            detail="body tenant_id does not match X-Tenant-Id",
        )

    # The Luna persona agent is the canonical owner of habit memories
    # — the design doc treats habits as Luna's running observation of
    # the operator. Resolve "the tenant's Luna agent" by looking for
    # an Agent with role='luna' OR name ILIKE '%luna%' as a fallback.
    # If no Luna agent exists for the tenant, write the row anchored
    # to the first agent in the tenant (graceful degradation — the
    # caller never sees "no luna agent" as a hard failure).
    from app.models.agent import Agent

    luna = (
        db.query(Agent)
        .filter(
            Agent.tenant_id == x_tenant_id,
            Agent.name.ilike("%luna%"),
        )
        .first()
    )
    if luna is None:
        luna = (
            db.query(Agent)
            .filter(Agent.tenant_id == x_tenant_id)
            .first()
        )
    if luna is None:
        raise HTTPException(
            status_code=404,
            detail="tenant has no agents — habit observation cannot be anchored",
        )

    ts = datetime.now(timezone.utc).isoformat()
    row = AgentMemory(
        tenant_id=x_tenant_id,
        agent_id=luna.id,
        memory_type=HABIT_MEMORY_TYPE,
        content=(
            f"habit={body.habit_name} signal_kind={body.signal_kind} "
            f"value={body.value!r} source={body.source} ts={ts}"
        ),
        importance=float(body.confidence),
        confidence=float(body.confidence),
        tags=[
            "habit",
            body.habit_name,
            f"signal:{body.signal_kind}",
        ],
    )
    db.add(row)
    db.commit()

    return {"memory_id": str(row.id)}
