import uuid
from datetime import datetime
from sqlalchemy import Column, String, Integer, Float, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from app.db.base import Base


class AutoDreamInsight(Base):
    """Stores patterns and insights extracted during an Auto-dream consolidation cycle.

    Each row captures a discovered pattern for a specific (decision_point, action_key)
    pair — how often it occurred in the last 24h and what average reward it produced.
    High-reward patterns are written back to RLPolicyState weights and optionally
    materialised as AgentMemory entries for context recall.
    """

    __tablename__ = "auto_dream_insights"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    dream_cycle_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    decision_point = Column(String(50), nullable=False, index=True)
    insight_type = Column(String(50), nullable=False)  # "pattern", "anomaly", "opportunity"
    action_key = Column(String(200))
    context_summary = Column(Text)
    avg_reward = Column(Float)
    experience_count = Column(Integer, nullable=False, default=0)
    confidence = Column(Float, nullable=False, default=0.5)
    properties = Column(JSONB, default=dict)
    applied_to_policy = Column(Boolean, nullable=False, default=False)
    synthetic_memory_id = Column(UUID(as_uuid=True), nullable=True)
    generated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
