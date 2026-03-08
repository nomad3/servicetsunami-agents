"""Pydantic schemas for SkillExecution."""
from pydantic import BaseModel
from typing import Optional, Dict, Any
from datetime import datetime
import uuid


class SkillExecutionCreate(BaseModel):
    skill_id: uuid.UUID
    entity_id: Optional[uuid.UUID] = None
    agent_id: Optional[uuid.UUID] = None
    workflow_run_id: Optional[uuid.UUID] = None
    input: Optional[Dict[str, Any]] = None
    status: str = "success"


class SkillExecutionInDB(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    skill_id: uuid.UUID
    entity_id: Optional[uuid.UUID] = None
    agent_id: Optional[uuid.UUID] = None
    workflow_run_id: Optional[uuid.UUID] = None
    input: Optional[Dict[str, Any]] = None
    output: Optional[Dict[str, Any]] = None
    status: str
    duration_ms: Optional[int] = None
    created_at: datetime

    class Config:
        from_attributes = True


class SkillExecuteRequest(BaseModel):
    entity_id: uuid.UUID
    params: Optional[Dict[str, Any]] = None
