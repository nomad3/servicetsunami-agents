from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
import uuid

from app.api import deps
from app.models.user import User
from app.services.orchestration.skill_router import SkillRouter

router = APIRouter()


class SkillExecuteRequest(BaseModel):
    skill_name: Optional[str] = None  # deprecated, use integration_name
    integration_name: Optional[str] = None
    payload: dict
    task_id: Optional[uuid.UUID] = None
    agent_id: Optional[uuid.UUID] = None


@router.post("/execute")
def execute_skill(
    request: SkillExecuteRequest,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Execute a skill through the tenant's skill router."""
    import logging
    logger = logging.getLogger(__name__)
    name = request.integration_name or request.skill_name or ""
    skill_router = SkillRouter(db=db, tenant_id=current_user.tenant_id)
    result = skill_router.execute_skill(
        integration_name=name,
        payload=request.payload,
        task_id=request.task_id,
        agent_id=request.agent_id,
    )
    if result.get("status") == "error":
        error_detail = result.get("error", "Unknown error")
        logger.error("Skill execution failed for '%s': %s", name, error_detail)
        raise HTTPException(status_code=502, detail=error_detail)
    return result


@router.get("/health")
def skill_health(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """Check skill execution health."""
    skill_router = SkillRouter(db=db, tenant_id=current_user.tenant_id)
    return skill_router.health_check()
