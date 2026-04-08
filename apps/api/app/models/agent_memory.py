"""AgentMemory model for storing agent memories with semantic search support"""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Text, Float, Integer, DateTime, ForeignKey, JSON
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import relationship
from pgvector.sqlalchemy import Vector

from app.db.base import Base


class AgentMemory(Base):
    """
    Stores agent memories including facts, experiences, skills, and preferences.
    Supports vector embeddings for semantic search.

    Memory Types:
    - fact: Factual information learned
    - experience: Past interaction outcomes
    - skill: Learned capabilities
    - preference: User/agent preferences
    - relationship: Knowledge about entities
    - procedure: How-to knowledge
    """
    __tablename__ = "agent_memories"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id = Column(UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)

    # Memory content
    memory_type = Column(String(50), nullable=False, index=True)
    content = Column(Text, nullable=False)
    embedding = Column(JSON, nullable=True)  # Legacy JSON embedding
    content_embedding = Column(Vector(768), nullable=True)  # pgvector for semantic search

    # Importance and access tracking
    importance = Column(Float, default=0.5)
    confidence = Column(Float, default=1.0)  # How reliable is this memory
    access_count = Column(Integer, default=0)
    decay_rate = Column(Float, default=1.0)  # Memory decay multiplier

    # Source tracking
    source = Column(String(100), nullable=True)
    source_task_id = Column(UUID(as_uuid=True), ForeignKey("agent_tasks.id", ondelete="SET NULL"), nullable=True)

    # Categorization and linking
    tags = Column(JSON, default=list)  # e.g. ["project:luna", "priority:high"]
    related_entity_ids = Column(JSON, default=list)  # Links to knowledge_entities

    # Multi-agent visibility scoping (migration 087, design doc §7)
    visibility = Column(String(20), nullable=False, default="tenant_wide")
    visible_to = Column(ARRAY(String), nullable=True)
    owner_agent_slug = Column(String(100), nullable=True)

    # Lifecycle
    expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_accessed_at = Column(DateTime, nullable=True)

    # Relationships
    agent = relationship("Agent", back_populates="memories")
    tenant = relationship("Tenant")
    source_task = relationship("AgentTask")

    def __repr__(self):
        return f"<AgentMemory {self.id} type={self.memory_type}>"
