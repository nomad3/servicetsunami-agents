"""ReviewCoalition — cross-CLI consensus code-review record.

One row per `alpha review <ref>` invocation. Per-CLI findings live as
BlackboardEntry rows on the linked Blackboard (reuses the append-only
substrate from PR #182-#205). The `findings` / `agreed_findings` JSONB
columns here are the cached aggregate snapshot the consensus
aggregator writes after each round so the read path can answer
`GET /reviews/{id}` without re-walking the blackboard.

See migration 139_reviews_coalitions.sql for the column-by-column
schema rationale.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


# ── Status lifecycle ──────────────────────────────────────────────────
# running           → at least one CLI still mid-flight
# awaiting_response → all CLIs returned, operator must call /reply
# done              → consensus reached (no agreed findings) or
#                     max_rounds exhausted
# failed            → dispatch error; see `findings.error` for detail
REVIEW_STATUSES = ("running", "awaiting_response", "done", "failed")


class ReviewCoalition(Base):
    """Cross-CLI code-review coalition record."""

    __tablename__ = "reviews_coalitions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    blackboard_id = Column(
        UUID(as_uuid=True),
        ForeignKey("blackboards.id", ondelete="SET NULL"),
        nullable=True,
    )
    chat_session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("chat_sessions.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Opaque review target (PR number, SHA, file:line range, stdin sha).
    ref = Column(Text, nullable=False)
    scope = Column(String(50), nullable=False, default="bugs+security")

    # JSONB array of {name, agent_slug}. Insertion order preserved.
    clis = Column(JSONB, nullable=False, default=list)

    rounds_completed = Column(Integer, nullable=False, default=0)
    max_rounds = Column(Integer, nullable=False, default=3)

    status = Column(String(30), nullable=False, default="running")

    # Aggregated cache, written by the consensus aggregator after each
    # round. See review_service._aggregate_findings for shape contract.
    findings = Column(JSONB, nullable=False, default=dict)
    agreed_findings = Column(JSONB, nullable=False, default=list)

    last_reply_ref = Column(Text, nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    tenant = relationship("Tenant")
