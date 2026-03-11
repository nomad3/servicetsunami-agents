"""API routes for skills management."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
import uuid

from app.api.deps import get_db, get_current_user
from app.models.user import User
from app.schemas.skill import SkillInDB, SkillCreate, SkillUpdate
from app.schemas.skill_execution import SkillExecutionInDB, SkillExecuteRequest
from app.schemas.file_skill import FileSkill
from app.services import skills as service
from app.services.skill_manager import skill_manager

router = APIRouter()


@router.get("/library", response_model=List[FileSkill])
def list_file_skills(
    current_user: User = Depends(get_current_user),
):
    """List all file-based skills loaded from the skills directory."""
    return skill_manager.list_skills()


@router.get("/", response_model=List[SkillInDB])
def list_skills(
    skill_type: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return service.get_skills(db, current_user.tenant_id, skill_type, skip, limit)


@router.post("/", response_model=SkillInDB, status_code=201)
def create_skill(
    skill_in: SkillCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return service.create_skill(db, skill_in, current_user.tenant_id)


@router.get("/{skill_id}", response_model=SkillInDB)
def get_skill(
    skill_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    skill = service.get_skill(db, skill_id, current_user.tenant_id)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    return skill


@router.put("/{skill_id}", response_model=SkillInDB)
def update_skill(
    skill_id: uuid.UUID,
    skill_in: SkillUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    skill = service.update_skill(db, skill_id, current_user.tenant_id, skill_in)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    return skill


@router.delete("/{skill_id}", status_code=204)
def delete_skill(
    skill_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not service.delete_skill(db, skill_id, current_user.tenant_id):
        raise HTTPException(status_code=400, detail="Cannot delete system skill or skill not found")


@router.post("/{skill_id}/execute")
def execute_skill(
    skill_id: uuid.UUID,
    request: SkillExecuteRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = service.execute_skill(db, skill_id, current_user.tenant_id, request.entity_id, request.params)
    if not result:
        raise HTTPException(status_code=404, detail="Skill not found or disabled")
    return result


@router.get("/{skill_id}/executions", response_model=List[SkillExecutionInDB])
def list_skill_executions(
    skill_id: uuid.UUID,
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return service.get_skill_executions(db, skill_id, current_user.tenant_id, skip, limit)


@router.post("/{skill_id}/clone", response_model=SkillInDB, status_code=201)
def clone_skill(
    skill_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    skill = service.clone_skill(db, skill_id, current_user.tenant_id)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    return skill
