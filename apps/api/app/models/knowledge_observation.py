"""KnowledgeObservation model for storing facts and insights about entities."""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Text, Float, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from pgvector.sqlalchemy import Vector

from app.db.base import Base


class KnowledgeObservation(Base):
    """Stores observations, facts, and insights linked to knowledge entities."""
    __tablename__ = "knowledge_observations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    entity_id = Column(UUID(as_uuid=True), ForeignKey("knowledge_entities.id", ondelete="SET NULL"), nullable=True, index=True)

    observation_text = Column(Text, nullable=False)
    observation_type = Column(String(50), default="fact", index=True)  # fact, opinion, git_commit, git_pr, file_hotspot, decision_insight
    source_type = Column(String(50), default="conversation")  # conversation, git_history, git_pr, rl_experience, email, dataset
    source_platform = Column(String(50), nullable=True)  # claude_code, gemini_cli, git, etc.
    source_agent = Column(String(100), nullable=True)
    source_channel = Column(String(50), nullable=True)  # chat, whatsapp, gmail, calendar, system
    source_ref = Column(String(500), nullable=True)  # e.g. "WhatsApp Mar 27" or "email from john@..."

    confidence = Column(Float, default=1.0)
    embedding = Column(Vector(768), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    tenant = relationship("Tenant")
    entity = relationship("KnowledgeEntity")

    def __repr__(self):
        return f"<KnowledgeObservation {self.id} type={self.observation_type}>"
