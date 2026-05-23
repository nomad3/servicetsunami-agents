"""SQLAlchemy model for the `tool_audit_drops` breadcrumb table.

Migration: apps/api/migrations/147_tool_audit_drops.sql
Design: docs/plans/2026-05-23-p0c-audit-log-fail-loud.md §6.
Writer: apps/mcp-server/src/audit_breadcrumb.py (mcp-server uses raw
SQL via a separate connection pool; this model is for api-side reads
— operator dashboard, alert queries, etc.).

Schema invariants enforced at the migration level (see the .sql file):
  - NO tenant_id column. The whole point is we couldn't resolve one.
  - args_keys carries TOP-LEVEL keys only, never values. PII safety net.
  - error_message capped at 600 chars at the writer boundary.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, UUID

from app.db.base import Base


class ToolAuditDrop(Base):
    __tablename__ = "tool_audit_drops"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tool_name = Column(Text, nullable=False)
    # 'no_tenant_id' | 'sql_insert_failed' | 'scheduling_failed'
    drop_reason = Column(Text, nullable=False)
    # 'agent_token' | 'tenant_header' | 'internal_key' | 'anonymous' | None
    tier = Column(String(32), nullable=True)
    # Top-level keys only; NEVER values.
    args_keys = Column(ARRAY(Text), nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
