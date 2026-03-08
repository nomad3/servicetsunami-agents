from typing import List

from sqlalchemy.orm import Session
import uuid

from app.models.integration_config import IntegrationConfig
from app.schemas.integration_config import IntegrationConfigCreate, IntegrationConfigUpdate


def get_integration_config(db: Session, integration_config_id: uuid.UUID) -> IntegrationConfig | None:
    return db.query(IntegrationConfig).filter(IntegrationConfig.id == integration_config_id).first()


def get_integration_configs_by_tenant(
    db: Session, tenant_id: uuid.UUID, skip: int = 0, limit: int = 100
) -> List[IntegrationConfig]:
    return (
        db.query(IntegrationConfig)
        .filter(IntegrationConfig.tenant_id == tenant_id)
        .offset(skip)
        .limit(limit)
        .all()
    )


def create_tenant_integration_config(
    db: Session, *, item_in: IntegrationConfigCreate, tenant_id: uuid.UUID
) -> IntegrationConfig:
    try:
        data = item_in.model_dump(exclude={"instance_id"})
    except AttributeError:
        data = item_in.dict(exclude={"instance_id"})

    db_item = IntegrationConfig(**data, tenant_id=tenant_id)
    db.add(db_item)
    db.commit()
    db.refresh(db_item)
    return db_item


def update_integration_config(
    db: Session, *, db_obj: IntegrationConfig, obj_in: IntegrationConfigUpdate
) -> IntegrationConfig:
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


def delete_integration_config(db: Session, *, integration_config_id: uuid.UUID) -> IntegrationConfig | None:
    integration_config = db.query(IntegrationConfig).filter(IntegrationConfig.id == integration_config_id).first()
    if integration_config:
        db.delete(integration_config)
        db.commit()
    return integration_config
