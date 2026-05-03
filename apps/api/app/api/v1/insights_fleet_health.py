"""Fleet health endpoint — Tier 3 of the visibility roadmap.

`GET /api/v1/agents/fleet-health` returns one row per agent with:
  - Identity: id, name, source (from `config.metadata.source`)
  - Ownership: owner email, team_id, status
  - Activity: last_invoked_at, invocations_24h, invocations_7d
  - Cost: tokens_used_7d, cost_usd_7d
  - Health: latest_error (truncated)
  - Drift: placeholder "unknown" — real drift detection needs the
    MicrosoftAgentSyncWorkflow which isn't built yet.

Cursor pagination on `(last_invoked_at DESC NULLS LAST, id)` so a
tenant with 1000 agents at offset N doesn't pay the offset-scan tax.

Curate-don't-dump per PR #256/#260 + PR #248's UserBrief pattern:
  - No `agent.config.metadata.original` (the raw source-platform JSON
    importer kept — can be huge + leaks source-system internals)
  - Owner is `email` only, not nested User object
  - Aggregates only — no raw audit_log rows in response
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from app.api import deps
from app.models.agent import Agent
from app.models.agent_audit_log import AgentAuditLog
from app.models.user import User as UserModel

router = APIRouter()


# Zombie threshold — an agent with no activity in this many days is
# "candidate for cleanup". 14 days is the same threshold the plan
# calls out and matches the inbox-monitor "stale" window.
_ZOMBIE_DAYS = 14


# Source values the importer writes into `config.metadata.source`.
# Closed Literal so frontend filter chips can rely on the enum.
_SourceFilter = Literal[
    "copilot_studio", "ai_foundry", "crewai", "langchain", "autogen", "native"
]


class FleetHealthRow(BaseModel):
    """Slim per-agent row in the fleet-health response.

    Deliberately excludes:
      - The full Agent ORM relationships (tenant, llm_config, etc.)
      - `config.metadata.original` (raw importer source payload)
      - Owner's full User object — only email + display name
      - Per-call audit data — only aggregates
    """

    id: uuid.UUID
    name: str
    source: str  # copilot_studio | ai_foundry | crewai | langchain | autogen | native
    status: str
    owner_email: Optional[str] = None
    team_id: Optional[uuid.UUID] = None
    last_invoked_at: Optional[datetime] = None
    invocations_24h: int = 0
    invocations_7d: int = 0
    tokens_used_7d: int = 0
    cost_usd_7d: float = 0.0
    latest_error: Optional[str] = None
    drift_state: str = "unknown"

    class Config:
        from_attributes = False


class FleetHealthResponse(BaseModel):
    rows: List[FleetHealthRow]
    next_cursor: Optional[str] = None
    has_more: bool = False


def _agent_source(agent: Agent) -> str:
    """Pick the import source string from `config.metadata.source`,
    defaulting to "native" for agents created in-platform (no importer
    metadata). Mirrors the values written by `agent_importer.py`."""
    cfg = agent.config or {}
    md = cfg.get("metadata") if isinstance(cfg, dict) else None
    if isinstance(md, dict):
        src = md.get("source")
        if src:
            return str(src)
    return "native"


def _decode_cursor(cursor: Optional[str]) -> Optional[tuple[Optional[datetime], uuid.UUID]]:
    """Decode an opaque pagination cursor of the form
    ``"<iso8601_or_null>|<uuid>"``. Returns None for missing/invalid
    cursors so the caller starts from the first page."""
    if not cursor:
        return None
    try:
        ts_str, id_str = cursor.split("|", 1)
        ts = None if ts_str == "null" else datetime.fromisoformat(ts_str)
        return ts, uuid.UUID(id_str)
    except (ValueError, AttributeError):
        return None


def _encode_cursor(last_ts: Optional[datetime], last_id: uuid.UUID) -> str:
    return f"{last_ts.isoformat() if last_ts else 'null'}|{last_id}"


@router.get("/fleet-health", response_model=FleetHealthResponse)
def get_fleet_health(
    *,
    db: Session = Depends(deps.get_db),
    current_user: UserModel = Depends(deps.get_current_active_user),
    source: Optional[str] = Query(None, description="Filter by import source"),
    status_: Optional[str] = Query(None, alias="status", description="Filter by lifecycle status"),
    owner_user_id: Optional[uuid.UUID] = None,
    team_id: Optional[uuid.UUID] = None,
    zombies: bool = Query(False, description="Only agents idle >14 days"),
    limit: int = Query(50, ge=1, le=200),
    cursor: Optional[str] = None,
):
    """Tenant-scoped fleet-health rollup.

    Cursor pagination on (last_invoked_at DESC NULLS LAST, id ASC).
    """
    tenant_id = current_user.tenant_id
    now = datetime.now(timezone.utc)
    since_24h = now - timedelta(hours=24)
    since_7d = now - timedelta(days=7)
    zombie_cutoff = now - timedelta(days=_ZOMBIE_DAYS)

    # Per-agent last_invoked_at + 7d/24h aggregates from audit log.
    # Tenant-scoped at the audit_log level so the GROUP BY only sees
    # this tenant's rows. Uses CASE expressions for the windowed sums
    # because that's the dialect-portable way to do conditional
    # aggregation (works on PG + SQLite test runner).
    from sqlalchemy import case
    agg_q = (
        db.query(
            AgentAuditLog.agent_id.label("agent_id"),
            func.max(AgentAuditLog.created_at).label("last_invoked_at"),
            func.sum(
                case((AgentAuditLog.created_at >= since_24h, 1), else_=0)
            ).label("invocations_24h"),
            func.sum(
                case((AgentAuditLog.created_at >= since_7d, 1), else_=0)
            ).label("invocations_7d"),
            func.coalesce(
                func.sum(
                    case(
                        (
                            AgentAuditLog.created_at >= since_7d,
                            func.coalesce(AgentAuditLog.input_tokens, 0)
                            + func.coalesce(AgentAuditLog.output_tokens, 0),
                        ),
                        else_=0,
                    )
                ),
                0,
            ).label("tokens_used_7d"),
            func.coalesce(
                func.sum(
                    case(
                        (
                            AgentAuditLog.created_at >= since_7d,
                            func.coalesce(AgentAuditLog.cost_usd, 0.0),
                        ),
                        else_=0.0,
                    )
                ),
                0.0,
            ).label("cost_usd_7d"),
        )
        .filter(AgentAuditLog.tenant_id == tenant_id)
        .filter(AgentAuditLog.agent_id.isnot(None))
        .group_by(AgentAuditLog.agent_id)
        .subquery()
    )

    # Latest error per agent — separate subquery to keep the main agg
    # focused. Pulls the most-recent failed audit row per agent in the
    # past 7d (or returns null if no failures).
    err_subq = (
        db.query(
            AgentAuditLog.agent_id.label("agent_id"),
            func.max(AgentAuditLog.created_at).label("err_at"),
        )
        .filter(AgentAuditLog.tenant_id == tenant_id)
        .filter(AgentAuditLog.agent_id.isnot(None))
        .filter(AgentAuditLog.created_at >= since_7d)
        .filter(AgentAuditLog.status != "success")
        .group_by(AgentAuditLog.agent_id)
        .subquery()
    )

    # Outer join agents → aggregates → latest error.
    main = (
        db.query(
            Agent,
            agg_q.c.last_invoked_at,
            agg_q.c.invocations_24h,
            agg_q.c.invocations_7d,
            agg_q.c.tokens_used_7d,
            agg_q.c.cost_usd_7d,
            err_subq.c.err_at,
            UserModel.email.label("owner_email"),
        )
        .outerjoin(agg_q, agg_q.c.agent_id == Agent.id)
        .outerjoin(err_subq, err_subq.c.agent_id == Agent.id)
        .outerjoin(UserModel, UserModel.id == Agent.owner_user_id)
        .filter(Agent.tenant_id == tenant_id)
    )

    if status_:
        main = main.filter(Agent.status == status_)
    if owner_user_id:
        main = main.filter(Agent.owner_user_id == owner_user_id)
    if team_id:
        main = main.filter(Agent.team_id == team_id)
    if zombies:
        main = main.filter(
            (agg_q.c.last_invoked_at.is_(None))
            | (agg_q.c.last_invoked_at < zombie_cutoff)
        )

    # Cursor: paginate on (last_invoked_at DESC NULLS LAST, id ASC).
    decoded = _decode_cursor(cursor)
    if decoded:
        cur_ts, cur_id = decoded
        if cur_ts is None:
            # Cursor was at the NULL section — only continue with rows
            # also at NULL with id > cur_id.
            main = main.filter(
                and_(agg_q.c.last_invoked_at.is_(None), Agent.id > cur_id)
            )
        else:
            # Standard "less than the cursor's timestamp, OR equal but
            # with greater id" — handles equal-timestamp ties.
            main = main.filter(
                (agg_q.c.last_invoked_at < cur_ts)
                | and_(agg_q.c.last_invoked_at == cur_ts, Agent.id > cur_id)
                | (agg_q.c.last_invoked_at.is_(None))  # NULLs come last
            )

    main = main.order_by(
        agg_q.c.last_invoked_at.desc().nullslast(),
        Agent.id.asc(),
    ).limit(limit + 1)  # +1 to detect has_more

    rows = main.all()
    has_more = len(rows) > limit
    rows = rows[:limit]

    out: List[FleetHealthRow] = []
    for (
        agent, last_invoked_at, inv24h, inv7d, tokens7d, cost7d, err_at, owner_email,
    ) in rows:
        # Filter by source AFTER the SQL result so the source filter doesn't
        # require a JSONB index on `config->'metadata'->>'source'`. Cheap
        # for typical tenant sizes; for Levi-scale (1000+ agents) we'd
        # promote this to a stored column.
        agent_src = _agent_source(agent)
        if source and agent_src != source:
            continue

        latest_err = None
        if err_at is not None:
            err_row = (
                db.query(AgentAuditLog.error_message)
                .filter(AgentAuditLog.tenant_id == tenant_id)
                .filter(AgentAuditLog.agent_id == agent.id)
                .filter(AgentAuditLog.created_at == err_at)
                .first()
            )
            if err_row and err_row[0]:
                latest_err = err_row[0][:200]

        out.append(
            FleetHealthRow(
                id=agent.id,
                name=agent.name,
                source=agent_src,
                status=agent.status,
                owner_email=owner_email,
                team_id=agent.team_id,
                last_invoked_at=last_invoked_at,
                invocations_24h=int(inv24h or 0),
                invocations_7d=int(inv7d or 0),
                tokens_used_7d=int(tokens7d or 0),
                cost_usd_7d=float(cost7d or 0.0),
                latest_error=latest_err,
                drift_state="unknown",
            )
        )

    next_cursor = None
    if has_more and out:
        last = rows[-1]
        next_cursor = _encode_cursor(last[1], last[0].id)

    return FleetHealthResponse(rows=out, next_cursor=next_cursor, has_more=has_more)
