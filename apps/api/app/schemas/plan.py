"""Schemas for plans, steps, assumptions, and events."""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
import uuid

from pydantic import BaseModel, Field


class PlanStatus(str, Enum):
    DRAFT = "draft"
    APPROVED = "approved"
    EXECUTING = "executing"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class AssumptionStatus(str, Enum):
    UNVERIFIED = "unverified"
    VALID = "valid"
    INVALIDATED = "invalidated"


class PlanEventType(str, Enum):
    CREATED = "created"
    APPROVED = "approved"
    STEP_STARTED = "step_started"
    STEP_COMPLETED = "step_completed"
    STEP_FAILED = "step_failed"
    REPLANNED = "replanned"
    PAUSED = "paused"
    RESUMED = "resumed"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    ASSUMPTION_INVALIDATED = "assumption_invalidated"
    BUDGET_WARNING = "budget_warning"


# --- Plan ---

class PlanCreate(BaseModel):
    goal_id: Optional[uuid.UUID] = None
    owner_agent_slug: str
    title: str
    description: Optional[str] = None
    budget_max_actions: Optional[int] = None
    budget_max_cost_usd: Optional[float] = None
    budget_max_runtime_hours: Optional[float] = None
    steps: List["PlanStepCreate"] = Field(default_factory=list)
    assumptions: List["PlanAssumptionCreate"] = Field(default_factory=list)


class PlanUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[PlanStatus] = None
    budget_max_actions: Optional[int] = None
    budget_max_cost_usd: Optional[float] = None
    budget_max_runtime_hours: Optional[float] = None


class PlanInDB(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    goal_id: Optional[uuid.UUID] = None
    owner_agent_slug: str
    title: str
    description: Optional[str] = None
    plan_version: int
    status: str
    current_step_index: int
    replan_count: int
    budget_max_actions: Optional[int] = None
    budget_max_cost_usd: Optional[float] = None
    budget_max_runtime_hours: Optional[float] = None
    budget_actions_used: int
    budget_cost_used: float
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# --- Steps ---

class PlanStepCreate(BaseModel):
    title: str
    description: Optional[str] = None
    owner_agent_slug: Optional[str] = None
    step_type: str = "action"
    expected_inputs: List[str] = Field(default_factory=list)
    expected_outputs: List[str] = Field(default_factory=list)
    required_tools: List[str] = Field(default_factory=list)
    side_effect_level: str = "none"
    retry_policy: Dict[str, Any] = Field(default_factory=dict)
    fallback_step_index: Optional[int] = None


class PlanStepInDB(BaseModel):
    id: uuid.UUID
    plan_id: uuid.UUID
    step_index: int
    title: str
    description: Optional[str] = None
    owner_agent_slug: Optional[str] = None
    step_type: str
    status: str
    expected_inputs: List[Any] = Field(default_factory=list)
    expected_outputs: List[Any] = Field(default_factory=list)
    required_tools: List[Any] = Field(default_factory=list)
    side_effect_level: str
    fallback_step_index: Optional[int] = None
    output: Any = None
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# --- Assumptions ---

class PlanAssumptionCreate(BaseModel):
    description: str
    assertion_id: Optional[uuid.UUID] = None


class PlanAssumptionInDB(BaseModel):
    id: uuid.UUID
    plan_id: uuid.UUID
    description: str
    status: str
    assertion_id: Optional[uuid.UUID] = None
    invalidated_reason: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# --- Events ---

class PlanEventInDB(BaseModel):
    id: uuid.UUID
    plan_id: uuid.UUID
    step_id: Optional[uuid.UUID] = None
    event_type: str
    previous_status: Optional[str] = None
    new_status: Optional[str] = None
    reason: Optional[str] = None
    metadata_json: Dict[str, Any] = Field(default_factory=dict)
    agent_slug: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


# --- Composite ---

class PlanDetailInDB(PlanInDB):
    steps: List[PlanStepInDB] = Field(default_factory=list)
    assumptions: List[PlanAssumptionInDB] = Field(default_factory=list)
    recent_events: List[PlanEventInDB] = Field(default_factory=list)
