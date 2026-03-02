from typing import List

from sqlalchemy.orm import Session
import uuid

from app.models.skill_config import SkillConfig
from app.schemas.skill_config import SkillConfigCreate, SkillConfigUpdate


def get_skill_config(db: Session, skill_config_id: uuid.UUID) -> SkillConfig | None:
    return db.query(SkillConfig).filter(SkillConfig.id == skill_config_id).first()


def get_skill_configs_by_tenant(
    db: Session, tenant_id: uuid.UUID, skip: int = 0, limit: int = 100
) -> List[SkillConfig]:
    return (
        db.query(SkillConfig)
        .filter(SkillConfig.tenant_id == tenant_id)
        .offset(skip)
        .limit(limit)
        .all()
    )


def create_tenant_skill_config(
    db: Session, *, item_in: SkillConfigCreate, tenant_id: uuid.UUID
) -> SkillConfig:
    try:
        data = item_in.model_dump(exclude={"instance_id"})
    except AttributeError:
        data = item_in.dict(exclude={"instance_id"})

    db_item = SkillConfig(**data, tenant_id=tenant_id)
    db.add(db_item)
    db.commit()
    db.refresh(db_item)
    return db_item


def update_skill_config(
    db: Session, *, db_obj: SkillConfig, obj_in: SkillConfigUpdate
) -> SkillConfig:
    if isinstance(obj_in, dict):
        update_data = obj_in
    else:
        update_data = obj_in.dict(exclude_unset=True)

    for field in update_data:
        if hasattr(db_obj, field):
            setattr(db_obj, field, update_data[field])

    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj


def delete_skill_config(db: Session, *, skill_config_id: uuid.UUID) -> SkillConfig | None:
    skill_config = db.query(SkillConfig).filter(SkillConfig.id == skill_config_id).first()
    if skill_config:
        db.delete(skill_config)
        db.commit()
    return skill_config
