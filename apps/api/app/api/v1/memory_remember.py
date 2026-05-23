"""POST /api/v1/memory/remember — tenant-scoped free-form fact ingestion.

Backs the `alpha remember "<fact>"` CLI subcommand from Phase 2 of the
CLI differentiation roadmap (#179). Thin wrapper around
`app/services/knowledge.py::create_observation` so the rich
auto-embedding + memory_activity logging happens automatically.
"""
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_current_active_user
from app.models.knowledge_entity import KnowledgeEntity
from app.models.user import User
from app.services.knowledge import create_observation

logger = logging.getLogger(__name__)

router = APIRouter()


class RememberRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=4096)
    entity_id: Optional[uuid.UUID] = None
    observation_type: str = "fact"


class RememberResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    text: str
    entity_id: Optional[uuid.UUID] = None
    observation_type: str


@router.post("/remember", response_model=RememberResponse, status_code=201)
def remember(
    payload: RememberRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Write a free-form fact to the caller's tenant memory.

    Auto-embeds the text via the embedding service and stores in both
    the knowledge_observations table (for entity-graph queries) and
    the shared vector_store (for cross-content semantic recall —
    `alpha recall` picks it up).
    """
    # Cross-tenant defense: `entity_id` lands on the observation row
    # verbatim. Without an existence + tenant check here, a caller
    # could attach their observation to another tenant's entity UUID.
    # Reviewer BLOCKER B1 on PR #446. Pattern: tenant-scoped lookup +
    # 404 on cross-tenant access.
    if payload.entity_id is not None:
        exists = (
            db.query(KnowledgeEntity.id)
            .filter(
                KnowledgeEntity.id == payload.entity_id,
                KnowledgeEntity.tenant_id == current_user.tenant_id,
            )
            .first()
        )
        if exists is None:
            raise HTTPException(status_code=404, detail="entity not found")

    try:
        obs = create_observation(
            db,
            tenant_id=current_user.tenant_id,
            observation_text=payload.text,
            observation_type=payload.observation_type or "fact",
            source_type="cli",
            source_platform="alpha",
            source_agent=current_user.email,
            entity_id=payload.entity_id,
        )
        # `create_observation` already commits internally; no extra
        # commit here (review NIT N1). The try/except still guards the
        # embedding/activity-log pipeline which can raise after the
        # observation insert.
    except Exception:
        # Generic 500 message: never leak the underlying DB / driver
        # exception text to the client (review IMPORTANT I3). The
        # full stack trace is logged for ops via logger.exception.
        logger.exception("create_observation failed")
        db.rollback()
        raise HTTPException(status_code=500, detail="failed to record observation")
    return RememberResponse(
        id=obs.id,
        text=obs.observation_text,
        entity_id=obs.entity_id,
        observation_type=obs.observation_type,
    )
