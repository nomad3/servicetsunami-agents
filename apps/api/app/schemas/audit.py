from datetime import datetime
from typing import Any, Dict, List, Optional
import uuid

from pydantic import BaseModel


class AuditLogEntry(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    agent_id: Optional[uuid.UUID] = None
    external_agent_id: Optional[uuid.UUID] = None
    invoked_by_user_id: Optional[uuid.UUID] = None
    invoked_by_agent_id: Optional[uuid.UUID] = None
    session_id: Optional[uuid.UUID] = None
    invocation_type: str
    input_summary: Optional[str] = None
    output_summary: Optional[str] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cost_usd: Optional[float] = None
    latency_ms: Optional[int] = None
    status: str
    error_message: Optional[str] = None
    policy_violations: Optional[Dict[str, Any]] = None
    quality_score: Optional[float] = None
    created_at: datetime

    class Config:
        from_attributes = True
