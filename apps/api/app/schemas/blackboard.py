"""Schemas for blackboard collaboration."""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
import uuid

from pydantic import BaseModel, Field


class EntryType(str, Enum):
    GOAL = "goal"
    SUBPROBLEM = "subproblem"
    HYPOTHESIS = "hypothesis"
    EVIDENCE = "evidence"
    CRITIQUE = "critique"
    PROPOSAL = "proposal"
    SYNTHESIS = "synthesis"
    DISAGREEMENT = "disagreement"
    RESOLUTION = "resolution"


class EntryStatus(str, Enum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    DISPUTED = "disputed"
    RESOLVED = "resolved"
    SUPERSEDED = "superseded"


class AuthorRole(str, Enum):
    PLANNER = "planner"
    RESEARCHER = "researcher"
    EXECUTOR = "executor"
    CRITIC = "critic"
    VERIFIER = "verifier"
    SYNTHESIZER = "synthesizer"
    AUDITOR = "auditor"
    CONTRIBUTOR = "contributor"


class BlackboardCreate(BaseModel):
    title: str
    plan_id: Optional[uuid.UUID] = None
    goal_id: Optional[uuid.UUID] = None


class BlackboardInDB(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    plan_id: Optional[uuid.UUID] = None
    goal_id: Optional[uuid.UUID] = None
    title: str
    status: str
    version: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class BlackboardEntryCreate(BaseModel):
    entry_type: EntryType
    content: str
    evidence: List[Dict[str, Any]] = Field(default_factory=list)
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    author_agent_slug: str
    author_role: AuthorRole = AuthorRole.CONTRIBUTOR
    parent_entry_id: Optional[uuid.UUID] = None
    supersedes_entry_id: Optional[uuid.UUID] = None


class BlackboardEntryInDB(BaseModel):
    id: uuid.UUID
    blackboard_id: uuid.UUID
    board_version: int
    entry_type: str
    content: str
    evidence: List[Any] = Field(default_factory=list)
    confidence: float
    author_agent_slug: str
    author_role: str
    parent_entry_id: Optional[uuid.UUID] = None
    supersedes_entry_id: Optional[uuid.UUID] = None
    status: str
    resolved_by_agent: Optional[str] = None
    resolution_reason: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class BlackboardDetailInDB(BlackboardInDB):
    entries: List[BlackboardEntryInDB] = Field(default_factory=list)


class ResolveEntryRequest(BaseModel):
    resolution_status: EntryStatus
    resolved_by_agent: str
    resolved_by_role: AuthorRole = AuthorRole.CONTRIBUTOR
    resolution_reason: Optional[str] = None
