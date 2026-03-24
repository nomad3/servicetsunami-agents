"""Schemas for world state assertions and snapshots."""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
import uuid

from pydantic import BaseModel, Field


class AssertionStatus(str, Enum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    DISPUTED = "disputed"
    EXPIRED = "expired"


class AssertionSourceType(str, Enum):
    OBSERVATION = "observation"
    WORKFLOW = "workflow"
    AGENT = "agent"
    USER = "user"
    SYSTEM = "system"


class WorldStateAssertionCreate(BaseModel):
    subject_entity_id: Optional[uuid.UUID] = None
    subject_slug: str
    attribute_path: str
    value_json: Any
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    source_observation_id: Optional[uuid.UUID] = None
    source_type: AssertionSourceType = AssertionSourceType.OBSERVATION
    freshness_ttl_hours: int = Field(default=168, ge=1)


class WorldStateAssertionInDB(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    subject_entity_id: Optional[uuid.UUID] = None
    subject_slug: str
    attribute_path: str
    value_json: Any
    previous_value_json: Any = None
    confidence: float
    source_observation_id: Optional[uuid.UUID] = None
    source_type: str
    corroboration_count: int
    status: str
    superseded_by_id: Optional[uuid.UUID] = None
    valid_from: datetime
    valid_to: Optional[datetime] = None
    freshness_ttl_hours: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class WorldStateSnapshotInDB(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    subject_entity_id: Optional[uuid.UUID] = None
    subject_slug: str
    projected_state: Dict[str, Any] = Field(default_factory=dict)
    assertion_count: int
    min_confidence: float
    avg_confidence: float
    unstable_attributes: List[str] = Field(default_factory=list)
    last_projected_at: datetime
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
