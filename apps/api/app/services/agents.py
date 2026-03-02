from typing import List

from sqlalchemy import text
from sqlalchemy.orm import Session
import uuid

from app.models.agent import Agent
from app.schemas.agent import AgentCreate, AgentBase

def get_agent(db: Session, agent_id: uuid.UUID) -> Agent | None:
    return db.query(Agent).filter(Agent.id == agent_id).first()

def get_agents_by_tenant(db: Session, tenant_id: uuid.UUID, skip: int = 0, limit: int = 100) -> List[Agent]:
    return db.query(Agent).filter(Agent.tenant_id == tenant_id).offset(skip).limit(limit).all()

def create_tenant_agent(db: Session, *, item_in: AgentCreate, tenant_id: uuid.UUID) -> Agent:
    db_item = Agent(**item_in.dict(), tenant_id=tenant_id)
    db.add(db_item)
    db.commit()
    db.refresh(db_item)

    # Auto-create AgentKit for Chat compatibility
    try:
        from app.services import agent_kits as agent_kit_service
        from app.schemas.agent_kit import AgentKitCreate, AgentKitConfig

        kit_config = AgentKitConfig(
            primary_objective=(item_in.config.get("system_prompt") if item_in.config else None) or f"Act as {db_item.name}",
            triggers=[],
            metrics=[],
            constraints=[],
            tool_bindings=[],
            vector_bindings=[],
            playbook=[],
            handoff_channels=[]
        )
        kit_create = AgentKitCreate(
            name=db_item.name,
            description=db_item.description,
            version="1.0",
            config=kit_config
        )
        agent_kit_service.create_tenant_agent_kit(db, item_in=kit_create, tenant_id=tenant_id)
    except Exception as e:
        print(f"Failed to auto-create AgentKit: {e}")

    return db_item

def update_agent(db: Session, *, db_obj: Agent, obj_in: AgentBase) -> Agent:
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

def delete_agent(db: Session, *, agent_id: uuid.UUID) -> Agent | None:
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if not agent:
        return None

    aid = str(agent_id)

    # Nullify nullable FK references
    db.execute(text("UPDATE execution_traces SET agent_id = NULL WHERE agent_id = :aid"), {"aid": aid})
    db.execute(text("UPDATE conversations SET agent_id = NULL WHERE agent_id = :aid"), {"aid": aid})
    db.execute(text("UPDATE knowledge_entities SET source_agent_id = NULL WHERE source_agent_id = :aid"), {"aid": aid})
    db.execute(text("UPDATE knowledge_relations SET discovered_by_agent_id = NULL WHERE discovered_by_agent_id = :aid"), {"aid": aid})

    # Delete owned records
    for tbl, col in [
        ("agent_skills", "agent_id"),
        ("deployments", "agent_id"),
        ("agent_tasks", "assigned_agent_id"),
        ("agent_tasks", "created_by_agent_id"),
        ("agent_relationships", "from_agent_id"),
        ("agent_relationships", "to_agent_id"),
        ("agent_messages", "from_agent_id"),
        ("agent_messages", "to_agent_id"),
        ("agent_memory", "agent_id"),
    ]:
        try:
            db.execute(text(f"DELETE FROM {tbl} WHERE {col} = :aid"), {"aid": aid})
        except Exception:
            pass  # Table may not exist yet

    db.delete(agent)
    db.commit()
    return agent
