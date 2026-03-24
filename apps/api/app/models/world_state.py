"""World state models: assertions and snapshots for grounded state layer."""

import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class WorldStateAssertion(Base):
    """Normalized claim derived from one or more observations."""

    __tablename__ = "world_state_assertions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    subject_entity_id = Column(UUID(as_uuid=True), ForeignKey("knowledge_entities.id"), nullable=True)
    subject_slug = Column(String(200), nullable=False)

    attribute_path = Column(String(300), nullable=False)
    value_json = Column(JSONB, nullable=False)
    previous_value_json = Column(JSONB, nullable=True)

    confidence = Column(Float, nullable=False, default=0.7)
    source_observation_id = Column(UUID(as_uuid=True), ForeignKey("knowledge_observations.id"), nullable=True)
    source_type = Column(String(50), nullable=False, default="observation")
    corroboration_count = Column(Integer, nullable=False, default=1)

    status = Column(String(30), nullable=False, default="active")
    superseded_by_id = Column(UUID(as_uuid=True), ForeignKey("world_state_assertions.id"), nullable=True)

    valid_from = Column(DateTime, nullable=False, default=datetime.utcnow)
    valid_to = Column(DateTime, nullable=True)
    freshness_ttl_hours = Column(Integer, nullable=False, default=168)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    tenant = relationship("Tenant")
    subject_entity = relationship("KnowledgeEntity")
    source_observation = relationship("KnowledgeObservation")
    superseded_by = relationship("WorldStateAssertion", remote_side=[id])


class WorldStateSnapshot(Base):
    """Point-in-time projection of the current best-known state for an entity."""

    __tablename__ = "world_state_snapshots"
    __table_args__ = (
        UniqueConstraint("tenant_id", "subject_slug", name="uq_world_state_snapshot"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    subject_entity_id = Column(UUID(as_uuid=True), ForeignKey("knowledge_entities.id"), nullable=True)
    subject_slug = Column(String(200), nullable=False)

    projected_state = Column(JSONB, nullable=False, default=dict)
    assertion_count = Column(Integer, nullable=False, default=0)
    min_confidence = Column(Float, nullable=False, default=1.0)
    avg_confidence = Column(Float, nullable=False, default=1.0)
    unstable_attributes = Column(JSONB, nullable=False, default=list)

    last_projected_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    tenant = relationship("Tenant")
    subject_entity = relationship("KnowledgeEntity")
