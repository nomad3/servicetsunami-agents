"""Resolver chain metrics — Op-1 of the visibility roadmap.

Tenant-wide rollup of how the CLI resolver served chat turns over a
recent window. Complements the per-message ``routing_summary`` footer
(PR #256) with an operator-level view: how often did fallback fire,
which CLIs are doing the actual work, what are the dominant fallback
reasons, are chains getting exhausted.

The curated-don't-dump rule applies (see PR #248 / #256 / #263 / #265
/ #267 / #268). We never return individual message IDs or the raw
``cli_chain_attempted`` list — both would re-leak the internal routing
decisions that PR #245 review explicitly flagged. Only the aggregate
shape lands in the response.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api import deps
from app.models.chat import ChatMessage, ChatSession
from app.models.user import User


router = APIRouter()


# ── Schemas — curated, no message IDs / no raw chain ───────────────────


class ServedByEntry(BaseModel):
    platform: str = Field(..., description="snake_case CLI identifier (claude_code, copilot_cli, ...)")
    label: str = Field(..., description="Friendly display label")
    count: int
    pct: float = Field(..., description="Share of the window's turns")


class FallbackReasonEntry(BaseModel):
    reason: str = Field(..., description="quota / auth / missing_credential / exception / internal_error")
    count: int
    pct: float


class ResolverMetricsResponse(BaseModel):
    """Top-level rollup. The forbidden-keys test pins this contract:
    no ``messages`` array, no per-turn IDs, no raw ``cli_chain_attempted``.
    """

    window_hours: int
    total_turns: int = Field(..., description="Assistant turns with a routing_summary in the window")
    served_by: List[ServedByEntry] = Field(default_factory=list)
    fallback_rate: float = Field(..., description="0..1 share of turns where served != requested")
    fallback_reasons: List[FallbackReasonEntry] = Field(default_factory=list)
    chain_exhausted_count: int = Field(..., description="Turns where every CLI in the chain errored")
    chain_length_p50: int
    chain_length_p95: int


# ── Helpers ────────────────────────────────────────────────────────────


def _percentile(sorted_values: List[int], pct: float) -> int:
    """Nearest-rank percentile. Returns 0 on empty input so callers
    don't have to special-case empty windows."""
    if not sorted_values:
        return 0
    if pct <= 0:
        return sorted_values[0]
    if pct >= 1:
        return sorted_values[-1]
    # nearest-rank: ceil(pct * N) - 1, floored at 0
    idx = max(0, min(len(sorted_values) - 1, int(pct * len(sorted_values))))
    return sorted_values[idx]


def _label_for(platform: Optional[str]) -> str:
    """Mirror agent_router._CLI_DISPLAY_LABELS but without importing
    private state — keep this endpoint independent of the resolver
    internals so adding a CLI doesn't quietly change the metrics shape."""
    if not platform:
        return "—"
    return {
        "claude_code": "Claude Code",
        "copilot_cli": "GitHub Copilot CLI",
        "codex": "Codex CLI",
        "gemini_cli": "Gemini CLI",
        "opencode": "OpenCode (local)",
        "local_gemma": "Local Gemma",
        "template": "Template",
    }.get(platform, platform)


# ── Endpoint ───────────────────────────────────────────────────────────


@router.get("/resolver-metrics", response_model=ResolverMetricsResponse)
def get_resolver_metrics(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    lookback_hours: int = Query(24, ge=1, le=168, description="Window in hours, max 7 days"),
):
    """Aggregate resolver chain stats for the caller's tenant over the
    last ``lookback_hours``.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)

    # JSON access cross-dialect: pull the column and filter in Python.
    # ChatMessage.context is JSON (not JSONB); volume per tenant per
    # 24h is bounded by chat usage so a Python aggregation is fine.
    rows = (
        db.query(ChatMessage.context)
        .join(ChatSession, ChatMessage.session_id == ChatSession.id)
        .filter(
            ChatSession.tenant_id == current_user.tenant_id,
            ChatMessage.role == "assistant",
            ChatMessage.created_at >= cutoff,
            ChatMessage.context.isnot(None),
        )
        .all()
    )

    served_counter: Counter = Counter()
    fallback_reason_counter: Counter = Counter()
    chain_lengths: List[int] = []
    fallback_count = 0
    exhausted_count = 0
    total = 0

    for (ctx,) in rows:
        if not isinstance(ctx, dict):
            continue
        rs = ctx.get("routing_summary")
        if not isinstance(rs, dict):
            continue
        total += 1

        platform = rs.get("served_by_platform")
        served_counter[platform or "—"] += 1

        chain_len = rs.get("chain_length")
        if isinstance(chain_len, int) and chain_len > 0:
            chain_lengths.append(chain_len)

        if rs.get("error_state") == "exhausted":
            exhausted_count += 1

        # Fallback fired when fallback_reason exists OR when served
        # platform differs from requested platform (case-insensitive,
        # mirroring the M9 fix in routing_summary).
        served_p = (rs.get("served_by_platform") or "").lower()
        requested_p = (rs.get("requested_platform") or "").lower()
        reason = rs.get("fallback_reason")
        if reason or (requested_p and served_p and served_p != requested_p):
            fallback_count += 1
            if reason:
                fallback_reason_counter[reason] += 1

    chain_lengths.sort()

    served_by = [
        ServedByEntry(
            platform=p,
            label=_label_for(p if p != "—" else None),
            count=c,
            pct=round(c / total, 4) if total else 0.0,
        )
        for p, c in served_counter.most_common()
    ]
    fallback_reasons = [
        FallbackReasonEntry(
            reason=r,
            count=c,
            pct=round(c / total, 4) if total else 0.0,
        )
        for r, c in fallback_reason_counter.most_common()
    ]

    return ResolverMetricsResponse(
        window_hours=lookback_hours,
        total_turns=total,
        served_by=served_by,
        fallback_rate=round(fallback_count / total, 4) if total else 0.0,
        fallback_reasons=fallback_reasons,
        chain_exhausted_count=exhausted_count,
        chain_length_p50=_percentile(chain_lengths, 0.5),
        chain_length_p95=_percentile(chain_lengths, 0.95),
    )
