"""Service for managing skills (reusable capabilities for agents and workflows)."""
from sqlalchemy.orm import Session
from typing import List, Optional, Dict, Any
import uuid
import time

from app.models.skill import Skill
from app.models.skill_execution import SkillExecution
from app.schemas.skill import SkillCreate, SkillUpdate


def get_skills(db: Session, tenant_id: uuid.UUID, skill_type: str = None, skip: int = 0, limit: int = 100) -> List[Skill]:
    query = db.query(Skill).filter(Skill.tenant_id == tenant_id)
    if skill_type:
        query = query.filter(Skill.skill_type == skill_type)
    return query.order_by(Skill.created_at.desc()).offset(skip).limit(limit).all()


def get_skill(db: Session, skill_id: uuid.UUID, tenant_id: uuid.UUID) -> Optional[Skill]:
    return db.query(Skill).filter(Skill.id == skill_id, Skill.tenant_id == tenant_id).first()


def get_skill_by_name(db: Session, name: str, tenant_id: uuid.UUID) -> Optional[Skill]:
    return db.query(Skill).filter(Skill.name == name, Skill.tenant_id == tenant_id).first()


def create_skill(db: Session, skill_in: SkillCreate, tenant_id: uuid.UUID) -> Skill:
    skill = Skill(
        tenant_id=tenant_id,
        name=skill_in.name,
        description=skill_in.description,
        skill_type=skill_in.skill_type,
        config=skill_in.config,
        enabled=skill_in.enabled,
    )
    db.add(skill)
    db.commit()
    db.refresh(skill)
    return skill


def update_skill(db: Session, skill_id: uuid.UUID, tenant_id: uuid.UUID, skill_in: SkillUpdate) -> Optional[Skill]:
    skill = get_skill(db, skill_id, tenant_id)
    if not skill:
        return None
    update_data = skill_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(skill, field, value)
    db.commit()
    db.refresh(skill)
    return skill


def delete_skill(db: Session, skill_id: uuid.UUID, tenant_id: uuid.UUID) -> bool:
    skill = get_skill(db, skill_id, tenant_id)
    if not skill or skill.is_system:
        return False
    db.delete(skill)
    db.commit()
    return True


def clone_skill(db: Session, skill_id: uuid.UUID, tenant_id: uuid.UUID) -> Optional[Skill]:
    original = get_skill(db, skill_id, tenant_id)
    if not original:
        return None
    clone = Skill(
        tenant_id=tenant_id,
        name=f"{original.name} (Custom)",
        description=original.description,
        skill_type=original.skill_type,
        config=original.config,
        is_system=False,
        enabled=True,
    )
    db.add(clone)
    db.commit()
    db.refresh(clone)
    return clone


def execute_skill(
    db: Session, skill_id: uuid.UUID, tenant_id: uuid.UUID,
    entity_id: uuid.UUID, params: Optional[Dict[str, Any]] = None,
    agent_id: Optional[uuid.UUID] = None,
) -> Optional[Dict[str, Any]]:
    skill = get_skill(db, skill_id, tenant_id)
    if not skill or not skill.enabled:
        return None

    start = time.time()
    output = None
    status = "success"

    try:
        if skill.skill_type == "scoring":
            from app.services.tool_executor import LeadScoringTool
            rubric_id = skill.name.lower().replace(" ", "_")
            tool = LeadScoringTool(db, tenant_id, rubric_id=rubric_id)
            result = tool.execute(entity_id=str(entity_id))
            if result.success:
                output = result.data
            else:
                status = "error"
                output = {"error": "Scoring failed"}
        else:
            output = {"message": f"Skill type '{skill.skill_type}' execution not yet implemented"}
    except Exception as e:
        status = "error"
        output = {"error": str(e)}

    duration_ms = int((time.time() - start) * 1000)

    execution = SkillExecution(
        tenant_id=tenant_id,
        skill_id=skill_id,
        entity_id=entity_id,
        agent_id=agent_id,
        input=params or {},
        output=output,
        status=status,
        duration_ms=duration_ms,
    )
    db.add(execution)
    db.commit()

    # Log activity
    try:
        from app.services.memory_activity import log_activity
        log_activity(
            db, tenant_id, "skill_executed",
            f'Executed "{skill.name}" on entity {entity_id}',
            source="skill",
            entity_id=entity_id,
            event_metadata={"skill_id": str(skill_id), "status": status, "duration_ms": duration_ms},
        )
    except Exception:
        pass

    return {"execution_id": str(execution.id), "status": status, "output": output, "duration_ms": duration_ms}


def get_skill_executions(db: Session, skill_id: uuid.UUID, tenant_id: uuid.UUID, skip: int = 0, limit: int = 50) -> List[SkillExecution]:
    return db.query(SkillExecution).filter(
        SkillExecution.skill_id == skill_id,
        SkillExecution.tenant_id == tenant_id,
    ).order_by(SkillExecution.created_at.desc()).offset(skip).limit(limit).all()
