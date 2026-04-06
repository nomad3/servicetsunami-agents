"""Learning experiment models for self-improvement pipeline."""

import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class PolicyCandidate(Base):
    """A proposed policy change derived from RL experience analysis."""

    __tablename__ = "policy_candidates"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)

    # What this policy changes
    policy_type = Column(String(50), nullable=False)
    decision_point = Column(String(50), nullable=False)
    description = Column(Text, nullable=False)

    # Before/after
    current_policy = Column(JSONB, nullable=False, default=dict)
    proposed_policy = Column(JSONB, nullable=False, default=dict)
    rationale = Column(Text, nullable=False)

    # Evidence
    source_experience_count = Column(Integer, nullable=False, default=0)
    source_query = Column(JSONB, nullable=False, default=dict)
    baseline_reward = Column(Float, nullable=True)
    expected_improvement = Column(Float, nullable=True)

    # Lifecycle
    status = Column(String(30), nullable=False, default="proposed")
    promoted_at = Column(DateTime, nullable=True)
    rejected_at = Column(DateTime, nullable=True)
    rejection_reason = Column(Text, nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    tenant = relationship("Tenant")


class LearningExperiment(Base):
    """A controlled experiment evaluating a policy candidate."""

    __tablename__ = "learning_experiments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    candidate_id = Column(UUID(as_uuid=True), ForeignKey("policy_candidates.id", ondelete="CASCADE"), nullable=False)

    decision_point = Column(String(50), nullable=True)

    # Experiment config
    experiment_type = Column(String(30), nullable=False, default="split")
    rollout_pct = Column(Float, nullable=False, default=0.0)
    min_sample_size = Column(Integer, nullable=False, default=20)
    max_duration_hours = Column(Integer, nullable=False, default=168)

    # Status
    status = Column(String(30), nullable=False, default="pending")
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    # Results
    control_sample_size = Column(Integer, nullable=False, default=0)
    treatment_sample_size = Column(Integer, nullable=False, default=0)
    control_avg_reward = Column(Float, nullable=True)
    treatment_avg_reward = Column(Float, nullable=True)
    improvement_pct = Column(Float, nullable=True)
    is_significant = Column(String(20), nullable=True)
    conclusion = Column(Text, nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    tenant = relationship("Tenant")
    candidate = relationship("PolicyCandidate")
