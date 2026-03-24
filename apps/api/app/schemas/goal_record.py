"""Schemas for goal records."""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
import uuid

from pydantic import BaseModel, Field


class GoalObjectiveType(str, Enum):
    OPERATIONAL = "operational"
    STRATEGIC = "strategic"
    LEARNING = "learning"
    MAINTENANCE = "maintenance"


class GoalPriority(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


class GoalState(str, Enum):
    PROPOSED = "proposed"
    ACTIVE = "active"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


class GoalRecordCreate(BaseModel):
    owner_agent_slug: str
    title: str
    description: Optional[str] = None
    objective_type: GoalObjectiveType = GoalObjectiveType.OPERATIONAL
    priority: GoalPriority = GoalPriority.NORMAL
    success_criteria: List[Dict[str, Any]] = Field(default_factory=list)
    deadline: Optional[datetime] = None
    related_entity_ids: List[str] = Field(default_factory=list)
    parent_goal_id: Optional[uuid.UUID] = None


class GoalRecordUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    objective_type: Optional[GoalObjectiveType] = None
    priority: Optional[GoalPriority] = None
    state: Optional[GoalState] = None
    success_criteria: Optional[List[Dict[str, Any]]] = None
    deadline: Optional[datetime] = None
    related_entity_ids: Optional[List[str]] = None
    progress_summary: Optional[str] = None
    progress_pct: Optional[int] = None
    abandoned_reason: Optional[str] = None


class GoalRecordInDB(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    owner_agent_slug: str
    created_by: Optional[uuid.UUID] = None
    title: str
    description: Optional[str] = None
    objective_type: str
    priority: str
    state: str
    success_criteria: List[Dict[str, Any]] = Field(default_factory=list)
    deadline: Optional[datetime] = None
    related_entity_ids: List[Any] = Field(default_factory=list)
    parent_goal_id: Optional[uuid.UUID] = None
    progress_summary: Optional[str] = None
    progress_pct: int = 0
    last_reviewed_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    abandoned_at: Optional[datetime] = None
    abandoned_reason: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
