"""Type contracts for the memory package.

These dataclasses mirror the gRPC IDL at
docs/plans/2026-04-07-memory-first-grpc-idl.proto. Phase 2 generates
equivalent Python bindings from the .proto and replaces these — but
the field names and defaults must match exactly.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Optional
from uuid import UUID


@dataclass
class RecallRequest:
    tenant_id: UUID
    agent_slug: str
    query: str
    chat_session_id: Optional[UUID] = None
    top_k_per_type: int = 5
    total_token_budget: int = 8000
    source_filter: Optional[list[str]] = None


@dataclass
class EntitySummary:
    id: UUID
    name: str
    category: Optional[str]
    description: Optional[str]
    confidence: float
    similarity: float
    source_type: Optional[str] = None


@dataclass
class ObservationSummary:
    id: UUID
    entity_id: UUID
    content: str
    confidence: float
    similarity: float
    created_at: datetime


@dataclass
class RelationSummary:
    id: UUID
    from_entity: str
    to_entity: str
    relation_type: str
    confidence: float


@dataclass
class CommitmentSummary:
    id: UUID
    title: str
    state: str
    due_at: Optional[datetime]
    priority: str
    similarity: float


@dataclass
class GoalSummary:
    id: UUID
    title: str
    state: str
    progress_pct: int
    priority: str
    similarity: float


@dataclass
class ConversationSummary:
    id: UUID
    role: str
    content: str
    created_at: datetime
    similarity: float


@dataclass
class EpisodeSummary:
    """Summary of a `conversation_episodes` row (the existing table)."""
    id: UUID
    session_id: Optional[UUID]
    summary: str
    key_topics: list[str]
    key_entities: list[str]
    created_at: datetime
    similarity: float


@dataclass
class ContradictionSummary:
    assertion_id: UUID
    subject: str
    predicate: str
    winning_value: str
    losing_value: str
    losing_source: str


@dataclass
class RecallMetadata:
    elapsed_ms: float
    used_keyword_fallback: bool = False
    degraded: bool = False
    truncated_for_budget: bool = False


@dataclass
class RecallResponse:
    entities: list[EntitySummary] = field(default_factory=list)
    observations: list[ObservationSummary] = field(default_factory=list)
    relations: list[RelationSummary] = field(default_factory=list)
    commitments: list[CommitmentSummary] = field(default_factory=list)
    goals: list[GoalSummary] = field(default_factory=list)
    past_conversations: list[ConversationSummary] = field(default_factory=list)
    episodes: list[EpisodeSummary] = field(default_factory=list)
    contradictions: list[ContradictionSummary] = field(default_factory=list)
    total_tokens_estimate: int = 0
    metadata: Optional[RecallMetadata] = None


@dataclass
class MemoryEvent:
    tenant_id: UUID
    source_type: str  # registry-validated, NOT a Literal — see adapters/registry.py
    source_id: str
    occurred_at: datetime
    ingested_at: datetime
    kind: Literal["text", "structured", "media"]
    actor_slug: Optional[str] = None
    source_metadata: dict[str, Any] = field(default_factory=dict)
    text: Optional[str] = None
    structured: Optional[dict[str, Any]] = None
    media_ref: Optional[str] = None
    proposed_entities: list[dict[str, Any]] = field(default_factory=list)
    proposed_observations: list[dict[str, Any]] = field(default_factory=list)
    proposed_relations: list[dict[str, Any]] = field(default_factory=list)
    proposed_commitments: list[dict[str, Any]] = field(default_factory=list)
    confidence: float = 1.0
    visibility: str = "tenant_wide"
    visible_to: list[str] = field(default_factory=list)  # agent slugs when visibility=agent_group
