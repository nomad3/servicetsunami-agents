"""Schemas for governed action taxonomy and tenant safety policies."""

from datetime import datetime
from enum import Enum
from typing import Dict, Optional
import uuid

from pydantic import BaseModel, Field


class ActionType(str, Enum):
    MCP_TOOL = "mcp_tool"
    WORKFLOW_ACTION = "workflow_action"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RiskClass(str, Enum):
    READ_ONLY = "read_only"
    INTERNAL_MUTATION = "internal_mutation"
    EXTERNAL_WRITE = "external_write"
    EXECUTION_CONTROL = "execution_control"
    ORCHESTRATION_CONTROL = "orchestration_control"


class SideEffectLevel(str, Enum):
    NONE = "none"
    INTERNAL_STATE = "internal_state"
    EXTERNAL_WRITE = "external_write"
    CODE_EXECUTION = "code_execution"


class Reversibility(str, Enum):
    REVERSIBLE = "reversible"
    PARTIAL = "partial"
    IRREVERSIBLE = "irreversible"
    UNKNOWN = "unknown"


class PolicyDecision(str, Enum):
    ALLOW = "allow"
    ALLOW_WITH_LOGGING = "allow_with_logging"
    REQUIRE_CONFIRMATION = "require_confirmation"
    REQUIRE_REVIEW = "require_review"
    BLOCK = "block"


class TenantActionPolicyBase(BaseModel):
    action_type: ActionType
    action_name: str
    channel: str = Field(default="*", description="Specific channel or '*' for all channels")
    decision: PolicyDecision
    rationale: Optional[str] = None
    enabled: bool = True


class TenantActionPolicyUpsert(TenantActionPolicyBase):
    pass


class TenantActionPolicy(TenantActionPolicyBase):
    id: uuid.UUID
    tenant_id: uuid.UUID
    created_by: Optional[uuid.UUID] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class SafetyActionEvaluationRequest(BaseModel):
    action_type: ActionType
    action_name: str
    channel: str = "web"


class SafetyActionEvaluation(BaseModel):
    action_key: str
    action_type: ActionType
    action_name: str
    category: str
    channel: str
    risk_class: RiskClass
    risk_level: RiskLevel
    side_effect_level: SideEffectLevel
    reversibility: Reversibility
    default_decision: PolicyDecision
    decision: PolicyDecision
    decision_source: str
    rationale: str
    policy_override_id: Optional[uuid.UUID] = None


class SafetyActionCatalogEntry(BaseModel):
    action_key: str
    action_type: ActionType
    action_name: str
    category: str
    risk_class: RiskClass
    risk_level: RiskLevel
    side_effect_level: SideEffectLevel
    reversibility: Reversibility
    default_channel_policies: Dict[str, PolicyDecision]
    effective_decision: PolicyDecision
    decision_source: str
    rationale: str
    policy_override_id: Optional[uuid.UUID] = None
