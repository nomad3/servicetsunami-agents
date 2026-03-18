"""KnowledgeEntity model for knowledge graph nodes"""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Text, ForeignKey, JSON, DateTime, Float, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class KnowledgeEntity(Base):
    """Knowledge graph entity - represents a thing, concept, or person."""
    __tablename__ = "knowledge_entities"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)

    # Entity definition
    entity_type = Column(String, nullable=False)  # customer, product, concept, person, organization, location
    category = Column(String(50), nullable=True)  # lead, contact, investor, accelerator, signal, organization, person
    name = Column(String, nullable=False, index=True)
    description = Column(Text, nullable=True)  # Entity description for semantic search
    attributes = Column(JSON, nullable=True)  # Flexible attribute storage
    properties = Column(JSON, nullable=True)  # Structured properties
    aliases = Column(JSON, default=list)  # Alternative names for the entity

    # Confidence and provenance
    confidence = Column(Float, default=1.0)  # How confident are we in this entity
    source_agent_id = Column(UUID(as_uuid=True), ForeignKey("agents.id"), nullable=True)
    updated_by_agent_id = Column(UUID(as_uuid=True), ForeignKey("agents.id"), nullable=True)
    extraction_model = Column(String(100), nullable=True)  # LLM model that extracted this
    data_quality_score = Column(Float, nullable=True)  # 0.0-1.0 reliability score
    tags = Column(JSON, default=list)  # Categorization tags

    # Entity lifecycle
    status = Column(String(20), default="draft")  # draft, verified, enriched, actioned, archived
    collection_task_id = Column(UUID(as_uuid=True), ForeignKey("agent_tasks.id"), nullable=True)
    source_url = Column(String, nullable=True)
    enrichment_data = Column(JSON, nullable=True)

    # Lead scoring
    score = Column(Integer, nullable=True)  # Composite lead score 0-100
    scored_at = Column(DateTime, nullable=True)  # When last scored
    scoring_rubric_id = Column(String, nullable=True)  # Which rubric was used: ai_lead, hca_deal, marketing_signal

    # Soft delete
    deleted_at = Column(DateTime, nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    tenant = relationship("Tenant")
    source_agent = relationship("Agent", foreign_keys=[source_agent_id])
    updated_by = relationship("Agent", foreign_keys=[updated_by_agent_id])
    collection_task = relationship("AgentTask", foreign_keys=[collection_task_id])
