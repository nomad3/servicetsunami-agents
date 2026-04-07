"""Conversation Episode — stores summaries of conversation segments for episodic recall."""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Text, Integer, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB

from app.db.base import Base

try:
    from pgvector.sqlalchemy import Vector
except ImportError:
    Vector = None


class ConversationEpisode(Base):
    __tablename__ = "conversation_episodes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    session_id = Column(UUID(as_uuid=True), ForeignKey("chat_sessions.id", ondelete="SET NULL"), nullable=True)
    summary = Column(Text, nullable=False)
    key_topics = Column(JSONB, default=list)
    key_entities = Column(JSONB, default=list)
    mood = Column(String(30), nullable=True)
    outcome = Column(String(100), nullable=True)
    message_count = Column(Integer, default=0)
    source_channel = Column(String(50), nullable=True)
    embedding = Column(Vector(768), nullable=True) if Vector else Column(JSONB, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    window_start = Column(DateTime(timezone=True), nullable=True)
    window_end = Column(DateTime(timezone=True), nullable=True)
    trigger_reason = Column(String(30), nullable=True)
    agent_slug = Column(String(100), nullable=True)
    generated_by = Column(String(50), nullable=True)
