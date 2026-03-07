"""KnowledgeRelation model for knowledge graph edges"""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, ForeignKey, JSON, DateTime, Float
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class KnowledgeRelation(Base):
    """Knowledge graph relation - connects two entities."""
    __tablename__ = "knowledge_relations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)

    # Relation endpoints
    from_entity_id = Column(UUID(as_uuid=True), ForeignKey("knowledge_entities.id"), nullable=False)
    to_entity_id = Column(UUID(as_uuid=True), ForeignKey("knowledge_entities.id"), nullable=False)

    # Relation definition
    relation_type = Column(String, nullable=False)  # works_at, purchased, prefers, related_to, knows, owns
    strength = Column(Float, default=1.0)  # How strong is this relationship
    evidence = Column(JSON, nullable=True)  # Supporting evidence/context

    # Provenance
    discovered_by_agent_id = Column(UUID(as_uuid=True), ForeignKey("agents.id"), nullable=True)
    updated_by_agent_id = Column(UUID(as_uuid=True), ForeignKey("agents.id"), nullable=True)
    confidence_source = Column(String(50), default="extraction")  # extraction, manual, inference

    # Temporal validity
    valid_from = Column(DateTime, nullable=True)
    valid_until = Column(DateTime, nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    tenant = relationship("Tenant")
    from_entity = relationship("KnowledgeEntity", foreign_keys=[from_entity_id])
    to_entity = relationship("KnowledgeEntity", foreign_keys=[to_entity_id])
    discovered_by_agent = relationship("Agent", foreign_keys=[discovered_by_agent_id])
    updated_by_agent = relationship("Agent", foreign_keys=[updated_by_agent_id])
