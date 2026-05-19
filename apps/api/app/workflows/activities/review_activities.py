"""Activities for ReviewWorkflow.

Two activities:
  * load_review_state — fetches the ReviewCoalition row, builds the
    per-CLI review prompt from `ref + scope`, and returns a dict the
    workflow can fan out.
  * record_review_finding — calls review_service.record_cli_findings,
    which trips the consensus aggregator when the last CLI reports.

Both are sync activities (DB-backed) — keep them short so the
agentprovision-orchestration worker pool stays responsive.
"""

from __future__ import annotations

import logging
import uuid

from temporalio import activity

from app.db.session import SessionLocal
from app.services import review_service

logger = logging.getLogger(__name__)


# ── Review-prompt template ────────────────────────────────────────────
# Sent verbatim to every CLI in the fanout. Kept here (not on the
# `ReviewCoalition` row) so we can iterate on prompt language without
# a migration; tenants who want a custom prompt should subclass the
# coalition_template surface in a follow-up.
_REVIEW_PROMPT = (
    "You are participating in a cross-CLI consensus code review.\n"
    "Multiple AI CLIs are reviewing the same target in parallel and\n"
    "their findings will be aggregated — a finding is **agreed** only\n"
    "when ≥ 2 CLIs flag it.\n\n"
    "Review target (ref):\n"
    "  {ref}\n\n"
    "Scope: {scope}\n\n"
    "Output format — one finding per bullet line, severity-tagged:\n"
    "  - BLOCKER path/to/file.ext:LINE-LINE  one-line description\n"
    "  - IMPORTANT path/to/file.ext:LINE     one-line description\n"
    "  - NIT path/to/file.ext:LINE           one-line description\n\n"
    "Only flag concrete issues you would block a PR on (BLOCKER) or\n"
    "strongly request changes for (IMPORTANT). NIT for style/polish.\n"
    "Do NOT propose new features. Focus on the {scope} dimension.\n"
)


@activity.defn
def load_review_state(tenant_id: str, review_id: str) -> dict:
    db = SessionLocal()
    try:
        review = review_service.get_review(
            db, uuid.UUID(tenant_id), uuid.UUID(review_id),
        )
        if not review:
            raise RuntimeError(f"Review {review_id} not found")
        prompt = _REVIEW_PROMPT.format(
            ref=review.ref,
            scope=review.scope or "bugs+security",
        )
        return {
            "review_id": str(review.id),
            "ref": review.ref,
            "scope": review.scope,
            "clis": list(review.clis or []),
            "prompt": prompt,
            "instruction_md": "",
        }
    finally:
        db.close()


@activity.defn
def record_review_finding(
    tenant_id: str,
    review_id: str,
    cli: str,
    raw_text: str,
) -> dict:
    db = SessionLocal()
    try:
        review = review_service.record_cli_findings(
            db,
            uuid.UUID(tenant_id),
            uuid.UUID(review_id),
            cli=cli,
            raw_text=raw_text or "",
        )
        if not review:
            return {"ok": False, "error": "review not found"}
        return {
            "ok": True,
            "status": review.status,
            "rounds_completed": review.rounds_completed,
            "agreed_count": len(review.agreed_findings or []),
        }
    finally:
        db.close()
