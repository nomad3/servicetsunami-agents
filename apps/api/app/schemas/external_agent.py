import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class ExternalAgentCreate(BaseModel):
    name: str
    description: Optional[str] = None
    avatar_url: Optional[str] = None
    protocol: str
    endpoint_url: str
    auth_type: str = "bearer"
    credential_id: Optional[uuid.UUID] = None
    capabilities: List[str] = []
    health_check_path: str = "/health"
    metadata: Optional[Dict[str, Any]] = None


class ExternalAgentUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    avatar_url: Optional[str] = None
    protocol: Optional[str] = None
    endpoint_url: Optional[str] = None
    auth_type: Optional[str] = None
    credential_id: Optional[uuid.UUID] = None
    capabilities: Optional[List[str]] = None
    health_check_path: Optional[str] = None
    status: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class ExternalAgentInDB(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    description: Optional[str] = None
    avatar_url: Optional[str] = None
    protocol: str
    endpoint_url: str
    auth_type: str
    credential_id: Optional[uuid.UUID] = None
    capabilities: List[Any] = []
    health_check_path: str
    status: str
    last_seen_at: Optional[datetime] = None
    task_count: int
    success_count: int
    error_count: int
    avg_latency_ms: Optional[int] = None
    metadata_: Optional[Dict[str, Any]] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
