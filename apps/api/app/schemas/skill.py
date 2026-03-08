"""Pydantic schemas for Skill."""
from pydantic import BaseModel
from typing import Optional, Dict, Any
from datetime import datetime
import uuid


class SkillBase(BaseModel):
    name: str
    description: Optional[str] = None
    skill_type: str
    config: Optional[Dict[str, Any]] = None
    enabled: bool = True


class SkillCreate(SkillBase):
    pass


class SkillUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    skill_type: Optional[str] = None
    config: Optional[Dict[str, Any]] = None
    enabled: Optional[bool] = None


class SkillInDB(SkillBase):
    id: uuid.UUID
    tenant_id: uuid.UUID
    is_system: bool = False
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True
