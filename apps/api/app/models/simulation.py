"""Simulation engine models: personas, scenarios, results, skill gaps."""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Boolean, Integer, Numeric, Date, Text, ARRAY, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID, JSONB
from app.db.base_class import Base


class SimulationPersona(Base):
    __tablename__ = "simulation_personas"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), nullable=False)
    name = Column(String, nullable=False)
    industry = Column(String, nullable=False)
    role = Column(String, nullable=False)
    typical_actions = Column(ARRAY(Text), nullable=False, default=list)
    persona_config = Column(JSONB, nullable=False, default=dict)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(TIMESTAMP, nullable=False, default=datetime.utcnow)


class SimulationScenario(Base):
    __tablename__ = "simulation_scenarios"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), nullable=False)
    persona_id = Column(UUID(as_uuid=True), nullable=False)
    cycle_date = Column(Date, nullable=False, default=datetime.utcnow)
    scenario_type = Column(String, nullable=False)
    message = Column(Text, nullable=False)
    expected_criteria = Column(JSONB, nullable=False, default=dict)
    status = Column(String, nullable=False, default="pending")
    created_at = Column(TIMESTAMP, nullable=False, default=datetime.utcnow)


class SimulationResult(Base):
    __tablename__ = "simulation_results"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), nullable=False)
    scenario_id = Column(UUID(as_uuid=True), nullable=False)
    response_text = Column(Text)
    quality_score = Column(Numeric(5, 2))
    dimension_scores = Column(JSONB, default=dict)
    failure_type = Column(String)
    failure_detail = Column(Text)
    is_simulation = Column(Boolean, nullable=False, default=True)
    executed_at = Column(TIMESTAMP, nullable=False, default=datetime.utcnow)


class SkillGap(Base):
    __tablename__ = "skill_gaps"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), nullable=False)
    gap_type = Column(String, nullable=False)
    description = Column(Text, nullable=False)
    industry = Column(String)
    frequency = Column(Integer, nullable=False, default=1)
    severity = Column(String, nullable=False, default="medium")
    proposed_fix = Column(Text)
    status = Column(String, nullable=False, default="detected")
    detected_at = Column(TIMESTAMP, nullable=False, default=datetime.utcnow)
    resolved_at = Column(TIMESTAMP)
