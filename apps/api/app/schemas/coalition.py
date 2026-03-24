"""Schemas for coalition templates and outcomes."""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
import uuid

from pydantic import BaseModel, Field


class CoalitionTemplateCreate(BaseModel):
    name: str
    description: Optional[str] = None
    pattern: str
    role_agent_map: Dict[str, str] = Field(default_factory=dict)
    task_types: List[str] = Field(default_factory=list)


class CoalitionTemplateInDB(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    description: Optional[str] = None
    pattern: str
    role_agent_map: Dict[str, Any] = Field(default_factory=dict)
    task_types: List[Any] = Field(default_factory=list)
    total_uses: int
    success_count: int
    avg_quality_score: float
    avg_rounds_to_consensus: float
    avg_cost_usd: float
    status: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class CoalitionOutcomeCreate(BaseModel):
    template_id: Optional[uuid.UUID] = None
    collaboration_id: Optional[uuid.UUID] = None
    task_type: str
    pattern: str
    role_agent_map: Dict[str, str] = Field(default_factory=dict)
    success: str = "yes"
    quality_score: Optional[float] = None
    rounds_completed: int = 1
    consensus_reached: Optional[str] = None
    cost_usd: float = 0.0
    duration_seconds: Optional[int] = None


class CoalitionOutcomeInDB(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    template_id: Optional[uuid.UUID] = None
    collaboration_id: Optional[uuid.UUID] = None
    task_type: str
    pattern: str
    role_agent_map: Dict[str, Any] = Field(default_factory=dict)
    success: str
    quality_score: Optional[float] = None
    rounds_completed: int
    consensus_reached: Optional[str] = None
    cost_usd: float
    duration_seconds: Optional[int] = None
    created_at: datetime

    class Config:
        from_attributes = True


class CoalitionRecommendation(BaseModel):
    template_id: uuid.UUID
    name: str
    pattern: str
    role_agent_map: Dict[str, str]
    score: float
    reasoning: str
    total_uses: int
    success_rate: float
    avg_quality: float
