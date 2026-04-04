"""Session Journal — stores synthesized summaries of user activity over time periods."""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Text, Integer, DateTime, ForeignKey, Date
from sqlalchemy.dialects.postgresql import UUID, JSONB

from app.db.base import Base

try:
    from pgvector.sqlalchemy import Vector
except ImportError:
    Vector = None


class SessionJournal(Base):
    __tablename__ = "session_journals"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)

    # Period covered by this journal entry
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)
    period_type = Column(String(50), default="week")  # week, month, day, custom

    # Synthesized narrative
    summary = Column(Text, nullable=False)  # "Your last week you accomplished..."

    # Extracted context
    key_themes = Column(JSONB, default=list)  # [theme1, theme2, ...]
    key_accomplishments = Column(JSONB, default=list)  # [achievement1, ...]
    key_challenges = Column(JSONB, default=list)  # [challenge1, ...]
    mentioned_people = Column(JSONB, default=list)  # [person1, person2, ...]
    mentioned_projects = Column(JSONB, default=list)  # [project1, ...]

    # Metadata
    episode_count = Column(Integer, default=0)  # How many episodes were synthesized
    message_count = Column(Integer, default=0)  # Total messages in period
    activity_score = Column(Integer, default=0)  # 0-100 activity level

    # Embedding for semantic search
    embedding = Column(Vector(768), nullable=True) if Vector else Column(JSONB, nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
