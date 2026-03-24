"""Schemas for learning experiments and policy candidates."""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
import uuid

from pydantic import BaseModel, Field


class PolicyType(str, Enum):
    ROUTING = "routing"
    PROMPTING = "prompting"
    TOOL_SELECTION = "tool_selection"
    RISK_THRESHOLD = "risk_threshold"
    MEMORY_RECALL = "memory_recall"
    REPLANNING = "replanning"


class CandidateStatus(str, Enum):
    PROPOSED = "proposed"
    EVALUATING = "evaluating"
    PROMOTED = "promoted"
    REJECTED = "rejected"


class ExperimentType(str, Enum):
    SHADOW = "shadow"
    SPLIT = "split"
    OFFLINE = "offline"


class ExperimentStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    ABORTED = "aborted"


class PolicyCandidateCreate(BaseModel):
    policy_type: PolicyType
    decision_point: str
    description: str
    current_policy: Dict[str, Any] = Field(default_factory=dict)
    proposed_policy: Dict[str, Any] = Field(default_factory=dict)
    rationale: str
    source_experience_count: int = 0
    source_query: Dict[str, Any] = Field(default_factory=dict)
    baseline_reward: Optional[float] = None
    expected_improvement: Optional[float] = None


class PolicyCandidateInDB(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    policy_type: str
    decision_point: str
    description: str
    current_policy: Dict[str, Any] = Field(default_factory=dict)
    proposed_policy: Dict[str, Any] = Field(default_factory=dict)
    rationale: str
    source_experience_count: int
    source_query: Dict[str, Any] = Field(default_factory=dict)
    baseline_reward: Optional[float] = None
    expected_improvement: Optional[float] = None
    status: str
    promoted_at: Optional[datetime] = None
    rejected_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class LearningExperimentCreate(BaseModel):
    candidate_id: uuid.UUID
    experiment_type: ExperimentType = ExperimentType.OFFLINE
    rollout_pct: float = Field(default=0.0, ge=0.0, le=1.0)
    min_sample_size: int = Field(default=20, ge=5)
    max_duration_hours: int = Field(default=168, ge=1)


class LearningExperimentInDB(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    candidate_id: uuid.UUID
    decision_point: Optional[str] = None
    experiment_type: str
    rollout_pct: float
    min_sample_size: int
    max_duration_hours: int
    status: str
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    control_sample_size: int
    treatment_sample_size: int
    control_avg_reward: Optional[float] = None
    treatment_avg_reward: Optional[float] = None
    improvement_pct: Optional[float] = None
    is_significant: Optional[str] = None
    conclusion: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
