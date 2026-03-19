import uuid
from datetime import datetime
from sqlalchemy import Column, String, Integer, Float, Boolean, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from pgvector.sqlalchemy import Vector
from app.db.base import Base


class RLExperience(Base):
    __tablename__ = "rl_experiences"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    trajectory_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    step_index = Column(Integer, nullable=False, default=0)
    decision_point = Column(String(50), nullable=False, index=True)
    state = Column(JSONB, nullable=False, default=dict)
    state_embedding = Column(Vector(768))
    action = Column(JSONB, nullable=False, default=dict)
    alternatives = Column(JSONB, default=list)
    reward = Column(Float, nullable=True)
    reward_components = Column(JSONB)
    reward_source = Column(String(50))
    explanation = Column(JSONB)
    policy_version = Column(String(50))
    exploration = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    rewarded_at = Column(DateTime, nullable=True)
    archived_at = Column(DateTime, nullable=True)
    span_id = Column(UUID(as_uuid=True), nullable=True)  # Observability correlation
