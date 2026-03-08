from pydantic import BaseModel
from typing import Optional, Literal
from datetime import datetime
from decimal import Decimal
import uuid

StepType = Literal[
    "dispatched", "memory_recall", "executing", "skill_call",
    "delegated", "approval_requested", "approval_granted",
    "entity_persist", "evaluation",
    "completed", "failed"
]


class ExecutionTraceCreate(BaseModel):
    task_id: uuid.UUID
    step_type: StepType
    step_order: int
    agent_id: Optional[uuid.UUID] = None
    skill_id: Optional[uuid.UUID] = None
    details: Optional[dict] = None
    duration_ms: Optional[int] = None
    error_message: Optional[str] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cost_usd: Optional[Decimal] = None
    parent_step_id: Optional[uuid.UUID] = None
    retry_count: Optional[int] = 0


class ExecutionTrace(ExecutionTraceCreate):
    id: uuid.UUID
    tenant_id: uuid.UUID
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True
