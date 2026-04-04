"""Behavioral signals — track Luna suggestions vs user actions (Gap 2: Learning)."""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Text, Boolean, TIMESTAMP, Float, Integer, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB

from app.db.base import Base

try:
    from pgvector.sqlalchemy import Vector
except ImportError:
    Vector = None


class BehavioralSignal(Base):
    """
    Tracks each suggestion Luna makes and whether the user acted on it.

    Lifecycle:
      1. Luna response is processed → signals extracted, stored with status='pending'
      2. User sends next message → matcher runs, acts_on detection
      3. If user acted → acted_on=True, action_timestamp set
      4. After N hours without action → marked acted_on=False (ignored)

    Aggregate acted_on rates per suggestion_type feed back into
    Luna's system prompt to improve suggestion quality over time.
    """
    __tablename__ = "behavioral_signals"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)

    # Source message context
    message_id = Column(UUID(as_uuid=True), nullable=True)   # ChatMessage that contained suggestion
    session_id = Column(UUID(as_uuid=True), nullable=True)   # ChatSession

    # Suggestion details
    suggestion_type = Column(String(50), nullable=False, index=True)
    # e.g. "follow_up", "send_email", "schedule_meeting", "task", "review", "recommendation"

    suggestion_text = Column(Text, nullable=False)    # Verbatim suggestion
    suggestion_tag = Column(String(50), nullable=True)  # Short label for reference in UI

    # Action tracking
    acted_on = Column(Boolean, nullable=True)          # None=pending, True=acted, False=ignored
    action_timestamp = Column(TIMESTAMP, nullable=True)
    action_evidence = Column(Text, nullable=True)      # What the user did that matches

    # Signal quality
    confidence = Column(Float, default=0.0)            # How confident we are they acted on it (0-1)
    match_score = Column(Float, nullable=True)         # Semantic similarity score if matched

    # Hours until we expire pending signals
    expires_after_hours = Column(Integer, default=24)

    # Extra context for future analysis
    context = Column(JSONB, default=lambda: {})        # e.g. {"entity": "John", "type": "lead"}

    # Embedding for semantic matching when detecting "acted_on"
    embedding = Column(Vector(768), nullable=True) if Vector else Column(JSONB, nullable=True)

    created_at = Column(TIMESTAMP, nullable=False, default=datetime.utcnow)
    updated_at = Column(TIMESTAMP, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
