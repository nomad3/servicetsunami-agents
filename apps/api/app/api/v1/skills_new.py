"""API routes for skills management."""
import re
from fastapi import APIRouter, Body, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Dict, List, Optional
import uuid

from app.api.deps import get_db, get_current_user
from app.core.config import settings
from app.models.user import User
from app.schemas.skill import SkillInDB, SkillCreate, SkillUpdate
from app.schemas.skill_execution import SkillExecutionInDB, SkillExecuteRequest
from app.schemas.file_skill import FileSkill
from app.services import skills as service
from app.services.skill_manager import skill_manager
from app.services.memory_activity import log_activity

router = APIRouter()


def _verify_internal_key(
    x_internal_key: Optional[str] = Header(None, alias="X-Internal-Key"),
):
    if x_internal_key not in (getattr(settings, 'API_INTERNAL_KEY', ''), getattr(settings, 'MCP_API_KEY', '')):
        raise HTTPException(status_code=401, detail="Invalid internal key")


@router.get("/library", response_model=List[FileSkill])
def list_file_skills(
    current_user: User = Depends(get_current_user),
):
    """List all file-based skills loaded from the skills directory."""
    return skill_manager.list_skills()


@router.get("/library/internal", response_model=List[FileSkill])
def list_file_skills_internal(
    _auth: None = Depends(_verify_internal_key),
):
    """List file-based skills (internal — for ADK server)."""
    return skill_manager.list_skills()


@router.post("/library/internal/execute")
def execute_file_skill_internal(
    skill_name: str = Body(...),
    inputs: Dict = Body(default={}),
    _auth: None = Depends(_verify_internal_key),
):
    """Execute a file-based skill by name (internal — for ADK server)."""
    result = skill_manager.execute_skill(skill_name, inputs)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


class FileSkillCreateInput(BaseModel):
    name: str
    type: str = "string"
    description: str = ""
    required: bool = False


class FileSkillCreateRequest(BaseModel):
    name: str
    description: str = ""
    engine: str = "python"
    script: str = 'def execute(inputs):\n    return {"result": "done"}'
    inputs: List[FileSkillCreateInput] = []


@router.post("/library/create", response_model=FileSkill, status_code=201)
def create_file_skill(
    payload: FileSkillCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new file-based skill from the UI."""
    result = skill_manager.create_skill(
        name=payload.name,
        description=payload.description,
        engine=payload.engine,
        script=payload.script,
        inputs=[inp.dict() for inp in payload.inputs],
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])

    log_activity(
        db,
        tenant_id=current_user.tenant_id,
        event_type="action_triggered",
        description=f"Skill created: {payload.name} ({payload.engine})",
        source="skills",
        event_metadata={"skill_name": payload.name, "engine": payload.engine, "action": "skill_created"},
    )
    return result["skill"]


class GitHubImportRequest(BaseModel):
    repo_url: str


@router.post("/library/import-github")
def import_from_github(
    payload: GitHubImportRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Import skill(s) from a GitHub repository."""
    # Try to get user's GitHub OAuth token
    from app.models.integration_config import IntegrationConfig
    from app.models.integration_credential import IntegrationCredential
    from app.services.orchestration.credential_vault import retrieve_credentials_for_skill

    github_token = None
    try:
        config = db.query(IntegrationConfig).filter(
            IntegrationConfig.tenant_id == current_user.tenant_id,
            IntegrationConfig.integration_name == "github",
        ).first()
        if config:
            creds = retrieve_credentials_for_skill(db, config.id, current_user.tenant_id)
            github_token = creds.get("access_token")
    except Exception:
        pass  # Proceed without token (public repos still work)

    result = skill_manager.import_from_github(payload.repo_url, github_token=github_token)

    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])

    # Log to memory
    imported = result.get("imported", [])
    skill_obj = result.get("skill")
    if skill_obj:
        imported = [skill_obj.name]

    log_activity(
        db,
        tenant_id=current_user.tenant_id,
        event_type="action_triggered",
        description=f"Skills imported from GitHub: {', '.join(imported)}",
        source="skills",
        event_metadata={
            "action": "skill_imported",
            "repo_url": payload.repo_url,
            "imported": imported,
        },
    )
    return result


@router.post("/library/execute")
def execute_file_skill(
    skill_name: str = Body(...),
    inputs: Dict = Body(default={}),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Execute a file-based skill by name (user-facing)."""
    result = skill_manager.execute_skill(skill_name, inputs)

    if "error" in result:
        log_activity(
            db,
            tenant_id=current_user.tenant_id,
            event_type="action_failed",
            description=f"Skill execution failed: {skill_name}",
            source="skills",
            event_metadata={"skill_name": skill_name, "inputs": inputs, "error": result["error"], "action": "skill_executed"},
        )
        raise HTTPException(status_code=400, detail=result["error"])

    log_activity(
        db,
        tenant_id=current_user.tenant_id,
        event_type="action_completed",
        description=f"Skill executed: {skill_name}",
        source="skills",
        event_metadata={"skill_name": skill_name, "inputs": inputs, "action": "skill_executed"},
    )
    return result


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
