"""Pydantic schemas for KnowledgeEntity"""
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
from datetime import datetime
import uuid


class KnowledgeEntityBase(BaseModel):
    entity_type: str  # customer, product, concept, person, organization, prospect
    category: Optional[str] = None  # lead, contact, investor, accelerator, signal, organization, person
    name: str
    description: Optional[str] = None
    attributes: Optional[Dict[str, Any]] = None
    confidence: Optional[float] = 1.0
    status: Optional[str] = "draft"
    source_url: Optional[str] = None


class KnowledgeEntityCreate(KnowledgeEntityBase):
    source_agent_id: Optional[uuid.UUID] = None
    collection_task_id: Optional[uuid.UUID] = None
    enrichment_data: Optional[Dict[str, Any]] = None
    extraction_model: Optional[str] = None
    tags: Optional[List[str]] = None


class KnowledgeEntityUpdate(BaseModel):
    name: Optional[str] = None
    entity_type: Optional[str] = None
    category: Optional[str] = None
    description: Optional[str] = None
    attributes: Optional[Dict[str, Any]] = None
    confidence: Optional[float] = None
    status: Optional[str] = None
    source_url: Optional[str] = None
    enrichment_data: Optional[Dict[str, Any]] = None
    tags: Optional[List[str]] = None
    data_quality_score: Optional[float] = None


class KnowledgeEntity(KnowledgeEntityBase):
    id: uuid.UUID
    tenant_id: uuid.UUID
    source_agent_id: Optional[uuid.UUID] = None
    updated_by_agent_id: Optional[uuid.UUID] = None
    collection_task_id: Optional[uuid.UUID] = None
    enrichment_data: Optional[Dict[str, Any]] = None
    properties: Optional[Dict[str, Any]] = None
    description: Optional[str] = None
    extraction_model: Optional[str] = None
    data_quality_score: Optional[float] = None
    tags: Optional[List[str]] = None
    score: Optional[int] = None
    scored_at: Optional[datetime] = None
    scoring_rubric_id: Optional[str] = None
    deleted_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class KnowledgeEntityBulkCreate(BaseModel):
    """Bulk create request."""
    entities: List[KnowledgeEntityCreate]


class KnowledgeEntityBulkResponse(BaseModel):
    """Bulk create response."""
    created: int
    updated: int
    duplicates_skipped: int
    entities: List[KnowledgeEntity]


class CollectionSummary(BaseModel):
    """Summary of entities collected by a task."""
    task_id: uuid.UUID
    total_entities: int
    by_status: Dict[str, int]
    by_type: Dict[str, int]
    by_category: Dict[str, int]
    sources: List[str]
