from pydantic import BaseModel
from typing import List
import uuid

from app.schemas.agent_skill import AgentSkill as AgentSkillSchema

class AgentBase(BaseModel):
    name: str
    description: str | None = None
    config: dict | None = None
    # Orchestration fields
    role: str | None = None  # "analyst", "manager", "specialist"
    capabilities: list[str] | None = None  # list of capability strings
    personality: dict | None = None  # dict with tone, verbosity settings
    autonomy_level: str = "supervised"  # "full", "supervised", "approval_required"
    max_delegation_depth: int = 2

class AgentCreate(AgentBase):
    pass

class AgentUpdate(AgentBase):
    pass

class Agent(AgentBase):
    id: uuid.UUID
    tenant_id: uuid.UUID
    skills: List[AgentSkillSchema] = []

    class Config:
        from_attributes = True
