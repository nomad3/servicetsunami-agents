"""Pydantic schemas for the `alpha review` cross-CLI consensus API.

Wire contract for:
  POST /api/v1/reviews/start
  GET  /api/v1/reviews/{id}
  POST /api/v1/reviews/{id}/reply
  GET  /api/v1/reviews/{id}/events
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


# ── Allowed scopes ────────────────────────────────────────────────────
# Soft-validated (we don't reject unknown values yet — different CLIs
# may want bespoke scopes like "perf" or "a11y"). The list here is the
# documented surface for the CLI's --scope flag.
ALLOWED_SCOPES = {"bugs+security", "perf", "style", "all"}

# ── Severity buckets ──────────────────────────────────────────────────
# Mirrors the superpowers:code-reviewer rubric the user already runs on
# every PR (see MEMORY.md: feedback_pr_superpowers_review.md).
SEVERITIES = ("BLOCKER", "IMPORTANT", "NIT")


class ReviewStartRequest(BaseModel):
    """POST /api/v1/reviews/start body."""

    ref: str = Field(
        ...,
        min_length=1,
        max_length=4000,
        description=(
            "Opaque review target: PR number (#123), commit SHA, "
            "file:line range (path/to/file.py:50-100), or "
            "stdin://<sha256> for piped content."
        ),
    )
    clis: Optional[List[str]] = Field(
        default=None,
        description=(
            "Explicit list of leaf CLIs to fan out to (e.g. "
            "['claude', 'codex', 'gemini']). When None, the server "
            "uses the tenant's active CLI set."
        ),
    )
    scope: str = Field(default="bugs+security", max_length=50)
    max_rounds: int = Field(default=3, ge=1, le=10)
    chat_session_id: Optional[uuid.UUID] = None

    @field_validator("clis")
    @classmethod
    def _clis_nonempty(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        if v is None:
            return v
        # Strip + dedupe while preserving order.
        seen: set = set()
        out: List[str] = []
        for cli in v:
            cli = cli.strip().lower()
            if not cli:
                continue
            if cli in seen:
                continue
            seen.add(cli)
            out.append(cli)
        if not out:
            raise ValueError("clis cannot be empty after normalization")
        return out


class ReviewStartResponse(BaseModel):
    review_id: uuid.UUID
    status: str
    clis: List[Dict[str, str]]
    blackboard_id: Optional[uuid.UUID]
    chat_session_id: Optional[uuid.UUID]
    message: str


class ReviewReplyRequest(BaseModel):
    """POST /api/v1/reviews/{id}/reply body.

    The operator (Claude Code) applied the agreed_findings and is
    submitting the updated diff/ref for another consensus round.
    """

    updated_ref: str = Field(..., min_length=1, max_length=4000)


class ReviewFindingsByCli(BaseModel):
    """Per-CLI findings list shape inside ReviewState.findings.per_cli."""

    cli: str
    findings: List[Dict] = Field(default_factory=list)
    raw_text: Optional[str] = None


class AgreedFinding(BaseModel):
    """A consensus finding flagged by 2+ CLIs."""

    severity: str
    file: Optional[str] = None
    line_range: Optional[str] = None
    descriptions: List[str]
    cli_set: List[str]


class ReviewState(BaseModel):
    """GET /api/v1/reviews/{id} response."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    ref: str
    scope: str
    clis: List[Dict[str, str]]
    blackboard_id: Optional[uuid.UUID]
    chat_session_id: Optional[uuid.UUID]
    rounds_completed: int
    max_rounds: int
    status: str
    findings: Dict
    agreed_findings: List[Dict]
    last_reply_ref: Optional[str]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
