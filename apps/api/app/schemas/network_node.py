"""Pydantic schemas for STP network nodes."""
from datetime import datetime
from typing import Any, Dict, Optional
from uuid import UUID

from pydantic import BaseModel, field_validator


class NetworkNodeRegister(BaseModel):
    node_id: Optional[UUID] = None
    name: str
    tailscale_ip: Optional[str] = None
    capabilities: Optional[Dict[str, Any]] = None
    max_concurrent_tasks: int = 3
    current_load: float = 0.0
    pricing_tier: str = "standard"
    status: str = "online"

    @field_validator("status")
    @classmethod
    def validate_status(cls, v):
        if v not in ("online", "suspect", "offline"):
            raise ValueError("status must be 'online', 'suspect', or 'offline'")
        return v


class NetworkNodeHeartbeat(BaseModel):
    current_load: Optional[float] = None
    status: Optional[str] = None
    capabilities: Optional[Dict[str, Any]] = None
    avg_execution_time_ms: Optional[float] = None
    total_tasks_completed: Optional[int] = None
    reputation_score: Optional[float] = None

    @field_validator("status")
    @classmethod
    def validate_status(cls, v):
        if v is not None and v not in ("online", "suspect", "offline"):
            raise ValueError("status must be 'online', 'suspect', or 'offline'")
        return v


class NetworkNodeInDB(BaseModel):
    id: UUID
    tenant_id: UUID
    name: str
    tailscale_ip: Optional[str] = None
    status: str
    last_heartbeat: datetime
    capabilities: Optional[Dict[str, Any]] = None
    max_concurrent_tasks: int
    current_load: float
    pricing_tier: str
    total_tasks_completed: int
    avg_execution_time_ms: Optional[float] = None
    reputation_score: float
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True
