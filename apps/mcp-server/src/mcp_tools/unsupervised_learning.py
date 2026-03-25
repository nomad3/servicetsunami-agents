"""Unsupervised learning MCP tools — skill gaps, simulation summaries, proactive actions, feedback."""

import logging
import os
from datetime import date, datetime
from typing import Optional

import asyncpg

from src.mcp_app import mcp
from src.mcp_auth import resolve_tenant_id
from mcp.server.fastmcp import Context

logger = logging.getLogger(__name__)

_pool: Optional[asyncpg.Pool] = None


def _get_db_url() -> str:
    from src.config import settings
    url = settings.DATABASE_URL or os.environ.get("DATABASE_URL", "")
    return (
        url.replace("postgresql+asyncpg://", "postgresql://")
        .replace("postgresql+psycopg2://", "postgresql://")
    )


async def _get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(_get_db_url(), min_size=1, max_size=5)
    return _pool


@mcp.tool()
async def get_skill_gaps(
    ctx: Context,
    status: Optional[str] = None,
    severity: Optional[str] = None,
    industry: Optional[str] = None,
    limit: int = 20,
) -> dict:
    """List skill gaps detected from simulation failures for the current tenant.

    Args:
        status: Filter by status (detected, acknowledged, in_progress, resolved)
        severity: Filter by severity (low, medium, high)
        industry: Filter by industry (e.g. 'startups', 'sales', 'marketing')
        limit: Max results to return (default 20)
    """
    tenant_id = await resolve_tenant_id(ctx)
    pool = await _get_pool()

    conditions = ["tenant_id = $1"]
    params = [tenant_id]
    idx = 2

    if status:
        conditions.append(f"status = ${idx}")
        params.append(status)
        idx += 1
    if severity:
        conditions.append(f"severity = ${idx}")
        params.append(severity)
        idx += 1
    if industry:
        conditions.append(f"industry = ${idx}")
        params.append(industry)
        idx += 1

    where_clause = " AND ".join(conditions)

    rows = await pool.fetch(
        f"""
        SELECT id, gap_type, description, industry, frequency, severity,
               proposed_fix, status, detected_at, resolved_at
        FROM skill_gaps
        WHERE {where_clause}
        ORDER BY
            CASE severity WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
            frequency DESC,
            detected_at DESC
        LIMIT ${idx}
        """,
        *params,
        limit,
    )

    gaps = [
        {
            "id": str(r["id"]),
            "gap_type": r["gap_type"],
            "description": r["description"],
            "industry": r["industry"],
            "frequency": r["frequency"],
            "severity": r["severity"],
            "proposed_fix": r["proposed_fix"],
            "status": r["status"],
            "detected_at": r["detected_at"].isoformat() if r["detected_at"] else None,
            "resolved_at": r["resolved_at"].isoformat() if r["resolved_at"] else None,
        }
        for r in rows
    ]

    return {
        "skill_gaps": gaps,
        "total": len(gaps),
        "filters": {"status": status, "severity": severity, "industry": industry},
    }


@mcp.tool()
async def get_simulation_summary(
    ctx: Context,
    cycle_date: Optional[str] = None,
) -> dict:
    """Get simulation results summary for a specific cycle date (defaults to today).

    Args:
        cycle_date: ISO date string (YYYY-MM-DD), defaults to today
    """
    tenant_id = await resolve_tenant_id(ctx)
    pool = await _get_pool()

    target_date = cycle_date or date.today().isoformat()

    # Overall stats
    stats = await pool.fetchrow(
        """
        SELECT
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE sr.failure_type IS NOT NULL) AS failed,
            AVG(sr.quality_score) AS avg_score,
            COUNT(DISTINCT ss.persona_id) AS personas_tested
        FROM simulation_results sr
        JOIN simulation_scenarios ss ON ss.id = sr.scenario_id
        WHERE sr.tenant_id = $1
          AND ss.cycle_date = $2::date
          AND sr.is_simulation = TRUE
        """,
        tenant_id,
        target_date,
    )

    # Failure breakdown
    failure_rows = await pool.fetch(
        """
        SELECT sr.failure_type, COUNT(*) AS cnt
        FROM simulation_results sr
        JOIN simulation_scenarios ss ON ss.id = sr.scenario_id
        WHERE sr.tenant_id = $1
          AND ss.cycle_date = $2::date
          AND sr.is_simulation = TRUE
          AND sr.failure_type IS NOT NULL
        GROUP BY sr.failure_type
        ORDER BY cnt DESC
        """,
        tenant_id,
        target_date,
    )

    # Sample low-scoring results
    sample_failures = await pool.fetch(
        """
        SELECT
            sr.id, ss.scenario_type, ss.message,
            CAST(sr.quality_score AS FLOAT) AS quality_score,
            sr.failure_type, sr.failure_detail,
            sp.industry
        FROM simulation_results sr
        JOIN simulation_scenarios ss ON ss.id = sr.scenario_id
        JOIN simulation_personas sp ON sp.id = ss.persona_id
        WHERE sr.tenant_id = $1
          AND ss.cycle_date = $2::date
          AND sr.is_simulation = TRUE
          AND sr.quality_score < 60
        ORDER BY sr.quality_score ASC
        LIMIT 5
        """,
        tenant_id,
        target_date,
    )

    return {
        "cycle_date": target_date,
        "summary": {
            "total_scenarios": stats["total"] if stats else 0,
            "failed": stats["failed"] if stats else 0,
            "avg_score": round(float(stats["avg_score"]), 2) if stats and stats["avg_score"] else None,
            "personas_tested": stats["personas_tested"] if stats else 0,
        },
        "failure_breakdown": [
            {"failure_type": r["failure_type"], "count": r["cnt"]}
            for r in failure_rows
        ],
        "sample_failures": [
            {
                "id": str(r["id"]),
                "scenario_type": r["scenario_type"],
                "message": r["message"][:80],
                "quality_score": r["quality_score"],
                "failure_type": r["failure_type"],
                "industry": r["industry"],
            }
            for r in sample_failures
        ],
    }


@mcp.tool()
async def get_proactive_actions(
    ctx: Context,
    status: str = "pending",
    limit: int = 20,
) -> dict:
    """List proactive actions queued by Luna for the current tenant.

    Args:
        status: Filter by status (pending, sent, acknowledged, dismissed). Default: pending
        limit: Max results to return (default 20)
    """
    tenant_id = await resolve_tenant_id(ctx)
    pool = await _get_pool()

    rows = await pool.fetch(
        """
        SELECT id, agent_slug, action_type, trigger_type, target_ref,
               priority, content, channel, status, scheduled_at, sent_at, created_at
        FROM proactive_actions
        WHERE tenant_id = $1
          AND status = $2
        ORDER BY
            CASE priority WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
            created_at DESC
        LIMIT $3
        """,
        tenant_id,
        status,
        limit,
    )

    return {
        "proactive_actions": [
            {
                "id": str(r["id"]),
                "agent_slug": r["agent_slug"],
                "action_type": r["action_type"],
                "trigger_type": r["trigger_type"],
                "target_ref": r["target_ref"],
                "priority": r["priority"],
                "content": r["content"],
                "channel": r["channel"],
                "status": r["status"],
                "scheduled_at": r["scheduled_at"].isoformat() if r["scheduled_at"] else None,
                "sent_at": r["sent_at"].isoformat() if r["sent_at"] else None,
                "created_at": r["created_at"].isoformat(),
            }
            for r in rows
        ],
        "total": len(rows),
        "status_filter": status,
    }


@mcp.tool()
async def dismiss_proactive_action(
    ctx: Context,
    action_id: str,
) -> dict:
    """Dismiss a proactive action so it no longer appears in the pending queue.

    Args:
        action_id: UUID of the proactive action to dismiss
    """
    tenant_id = await resolve_tenant_id(ctx)
    pool = await _get_pool()

    result = await pool.fetchrow(
        """
        UPDATE proactive_actions
        SET status = 'dismissed'
        WHERE id = $1::uuid
          AND tenant_id = $2
          AND status NOT IN ('dismissed')
        RETURNING id, action_type, trigger_type, status
        """,
        action_id,
        tenant_id,
    )

    if not result:
        return {"success": False, "error": "Action not found or already dismissed"}

    return {
        "success": True,
        "action_id": str(result["id"]),
        "action_type": result["action_type"],
        "trigger_type": result["trigger_type"],
        "new_status": result["status"],
    }


@mcp.tool()
async def submit_learning_feedback(
    ctx: Context,
    content: str,
    feedback_type: str = "direction",
    report_id: Optional[str] = None,
) -> dict:
    """Submit human feedback on the learning system's decisions or morning reports.

    Args:
        content: The feedback message (e.g. 'Good call on routing to Claude', 'Don't use Codex for SQL')
        feedback_type: One of: approval, rejection, direction, correction (default: direction)
        report_id: Optional reference to a specific morning report notification
    """
    tenant_id = await resolve_tenant_id(ctx)
    pool = await _get_pool()

    valid_types = {"approval", "rejection", "direction", "correction"}
    if feedback_type not in valid_types:
        return {"success": False, "error": f"Invalid feedback_type. Must be one of: {valid_types}"}

    # Infer parsed intent
    parsed_intent = _infer_intent_from_content(content, feedback_type)

    row = await pool.fetchrow(
        """
        INSERT INTO feedback_records
            (tenant_id, report_id, feedback_type, content, parsed_intent, applied)
        VALUES ($1, $2, $3, $4, $5, FALSE)
        RETURNING id, created_at
        """,
        tenant_id,
        report_id,
        feedback_type,
        content[:1000],
        parsed_intent,
    )

    return {
        "success": True,
        "feedback_id": str(row["id"]),
        "feedback_type": feedback_type,
        "parsed_intent": parsed_intent,
        "created_at": row["created_at"].isoformat(),
    }


def _infer_intent_from_content(content: str, feedback_type: str) -> str:
    content_lower = content.lower()
    if feedback_type == "approval":
        if "routing" in content_lower or "platform" in content_lower:
            return "approve_routing_change"
        return "general_approval"
    if feedback_type == "rejection":
        if "platform" in content_lower:
            return "reject_platform"
        if "rollback" in content_lower or "revert" in content_lower:
            return "request_rollback"
        return "general_rejection"
    if feedback_type == "correction":
        return "factual_correction"
    return "exploration_direction"
