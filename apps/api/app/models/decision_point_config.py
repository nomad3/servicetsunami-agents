"""Per-decision-point exploration configuration."""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Numeric, ARRAY, Text, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID
from app.db.base_class import Base


class DecisionPointConfig(Base):
    __tablename__ = "decision_point_config"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), nullable=False)
    decision_point = Column(String, nullable=False)
    exploration_rate = Column(Numeric(4, 3), nullable=False, default=0.10)
    exploration_mode = Column(String, nullable=False, default="balanced")
    target_platforms = Column(ARRAY(Text), default=list)
    updated_at = Column(TIMESTAMP, nullable=False, default=datetime.utcnow)
