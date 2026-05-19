"""Affect observability endpoint — Phase 1 PR B.

Read-only endpoint that returns the PAD trajectory for a session.
Tenant-isolated: 404 on foreign-tenant access (the design doc's safety
pattern — see § Risks § "Emotion-state pollution across tenants").

Wired into the API router under /api/v1/sessions/{session_id}/affect-trace.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.user import User
from app.services.emotion_engine_io import (
    get_affect_trace,
    session_belongs_to_tenant,
)


router = APIRouter()


@router.get("/sessions/{session_id}/affect-trace")
def get_session_affect_trace(
    session_id: uuid.UUID,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return the PAD vector trajectory for a chat session.

    Foreign-tenant access returns 404 (not 403) — same convention as
    memories.py to avoid revealing whether a session exists.

    Response shape:
        {
            "session_id": "<uuid>",
            "trace": [
                {
                    "episode_id": "<uuid>",
                    "created_at": "<iso8601>",
                    "affect_vector": {"pleasure": .., "arousal": .., "dominance": .., "label": .., "updated_at": ..} | null,
                    "mood": "<string>" | null
                },
                ...
            ]
        }
    """
    if not session_belongs_to_tenant(
        db,
        session_id=session_id,
        tenant_id=current_user.tenant_id,
    ):
        raise HTTPException(status_code=404, detail="session not found")

    trace = get_affect_trace(
        db,
        session_id=session_id,
        tenant_id=current_user.tenant_id,
        limit=limit,
    )

    return {
        "session_id": str(session_id),
        "trace": trace,
    }
