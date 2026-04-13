"""Schemas for collaboration sessions."""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
import uuid

from pydantic import BaseModel, Field


class CollaborationPattern(str, Enum):
    PROPOSE_CRITIQUE_REVISE = "propose_critique_revise"
    PLAN_VERIFY = "plan_verify"
    RESEARCH_SYNTHESIZE = "research_synthesize"
    DEBATE_RESOLVE = "debate_resolve"
    INCIDENT_INVESTIGATION = "incident_investigation"


class CollaborationPhase(str, Enum):
    PROPOSE = "propose"
    CRITIQUE = "critique"
    REVISE = "revise"
    VERIFY = "verify"
    SYNTHESIZE = "synthesize"
    RESEARCH = "research"
    DEBATE = "debate"
    RESOLVE = "resolve"
    COMPLETE = "complete"
    TRIAGE = "triage"
    INVESTIGATE = "investigate"
    ANALYZE = "analyze"
    COMMAND = "command"


PATTERN_PHASES = {
    "propose_critique_revise": ["propose", "critique", "revise", "verify"],
    "plan_verify": ["propose", "verify"],
    "research_synthesize": ["research", "synthesize", "verify"],
    "debate_resolve": ["propose", "debate", "resolve"],
    "incident_investigation": ["triage", "investigate", "analyze", "command"],
}

PHASE_REQUIRED_ROLES = {
    "propose": ["planner"],
    "critique": ["critic"],
    "revise": ["planner"],
    "verify": ["verifier"],
    "synthesize": ["synthesizer"],
    "research": ["researcher"],
    "debate": ["critic", "planner"],
    "resolve": ["synthesizer"],
    "triage": ["triage_agent"],
    "investigate": ["investigator"],
    "analyze": ["analyst"],
    "command": ["commander"],
}


class CollaborationSessionCreate(BaseModel):
    blackboard_id: uuid.UUID
    pattern: CollaborationPattern
    role_assignments: Dict[str, str] = Field(default_factory=dict)
    max_rounds: int = Field(default=3, ge=1, le=10)
    pattern_config: Dict[str, Any] = Field(default_factory=dict)


class CollaborationSessionInDB(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    blackboard_id: uuid.UUID
    pattern: str
    status: str
    current_phase: str
    phase_index: int
    role_assignments: Dict[str, Any] = Field(default_factory=dict)
    pattern_config: Dict[str, Any] = Field(default_factory=dict)
    outcome: Optional[str] = None
    consensus_reached: Optional[str] = None
    rounds_completed: int
    max_rounds: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class AdvancePhaseRequest(BaseModel):
    agent_slug: str
    contribution: str
    evidence: List[Dict[str, Any]] = Field(default_factory=list)
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    agrees_with_previous: Optional[bool] = None
