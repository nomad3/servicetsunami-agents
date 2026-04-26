"""Per-call metric log for external agents.

Written by ``external_agent_call`` after each dispatch (success or
failure). The cost rollup activity reads this table when computing
``AgentPerformanceSnapshot`` rows for external agents — the native
rollup uses ``AgentAuditLog`` for the same purpose.
"""
import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base


class ExternalAgentCallLog(Base):
    __tablename__ = "external_agent_call_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    external_agent_id = Column(UUID(as_uuid=True), ForeignKey("external_agents.id", ondelete="CASCADE"), nullable=False, index=True)
    started_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    latency_ms = Column(Integer, nullable=True)
    status = Column(String(32), nullable=False)  # success | error | non_retryable | breaker_open
    total_tokens = Column(Integer, nullable=False, default=0)
    cost_usd = Column(Numeric(12, 6), nullable=False, default=0)
    error_message = Column(Text, nullable=True)
