"""Pydantic schemas for dynamic workflows."""

from datetime import datetime
from typing import Any, Dict, List, Optional
import uuid

from pydantic import BaseModel, Field, field_validator


class WorkflowStepDef(BaseModel):
    id: str
    type: str  # mcp_tool, agent, condition, for_each, parallel, wait, transform, human_approval, webhook_trigger, workflow
    tool: Optional[str] = None  # MCP tool name (for mcp_tool type)
    agent: Optional[str] = None  # Agent slug (for agent type)
    prompt: Optional[str] = None  # Prompt template (for agent type)
    params: Optional[Dict[str, Any]] = None
    output: Optional[str] = None  # Variable name for step output
    # Condition
    condition: Optional[str] = Field(None, alias="if")
    then_step: Optional[str] = Field(None, alias="then")
    else_step: Optional[str] = Field(None, alias="else")
    # Loop
    collection: Optional[str] = None
    item_as: Optional[str] = Field(None, alias="as")
    steps: Optional[List["WorkflowStepDef"]] = None  # Sub-steps for for_each/parallel
    # Wait
    duration: Optional[str] = None
    # Transform
    operation: Optional[str] = None
    # Overrides
    timeout_seconds: Optional[int] = None
    max_retries: Optional[int] = None

    class Config:
        populate_by_name = True


class WorkflowTriggerDef(BaseModel):
    type: str  # cron, interval, webhook, event, manual, agent
    schedule: Optional[str] = None  # Cron expression
    interval_minutes: Optional[int] = None
    webhook_slug: Optional[str] = None
    event_type: Optional[str] = None  # entity_created, email_received, etc.
    timezone: Optional[str] = "UTC"


class WorkflowDefinition(BaseModel):
    steps: List[WorkflowStepDef]

    @field_validator("steps")
    @classmethod
    def validate_unique_ids(cls, v):
        ids = [s.id for s in v]
        if len(ids) != len(set(ids)):
            raise ValueError("Step IDs must be unique")
        return v


class DynamicWorkflowCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    definition: WorkflowDefinition
    trigger_config: Optional[WorkflowTriggerDef] = None
    tags: List[str] = []


class DynamicWorkflowUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    definition: Optional[WorkflowDefinition] = None
    trigger_config: Optional[WorkflowTriggerDef] = None
    tags: Optional[List[str]] = None
    status: Optional[str] = None


class DynamicWorkflowInDB(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    description: Optional[str]
    definition: Dict[str, Any]
    version: int
    status: str
    trigger_config: Optional[Dict[str, Any]]
    tags: List[str]
    tier: str
    public: bool
    run_count: int
    last_run_at: Optional[datetime]
    avg_duration_ms: Optional[int]
    success_rate: Optional[float]
    created_at: datetime
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True


class WorkflowRunInDB(BaseModel):
    id: uuid.UUID
    workflow_id: uuid.UUID
    trigger_type: Optional[str]
    status: str
    started_at: datetime
    completed_at: Optional[datetime]
    duration_ms: Optional[int]
    current_step: Optional[str]
    error: Optional[str]
    total_tokens: int
    total_cost_usd: float
    step_results: Optional[Dict[str, Any]]

    class Config:
        from_attributes = True


class WorkflowStepLogInDB(BaseModel):
    id: uuid.UUID
    step_id: str
    step_type: str
    step_name: Optional[str]
    status: str
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    duration_ms: Optional[int]
    error: Optional[str]
    tokens_used: int
    cost_usd: float
    platform: Optional[str]
    retry_count: int

    class Config:
        from_attributes = True


class WorkflowRunRequest(BaseModel):
    input_data: Optional[Dict[str, Any]] = None
