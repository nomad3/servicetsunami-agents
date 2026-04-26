import logging
from datetime import datetime, timedelta
from typing import List

from temporalio import activity

logger = logging.getLogger(__name__)


def _percentile(sorted_values: List[int], pct: float) -> int:
    if not sorted_values:
        return 0
    idx = int(len(sorted_values) * pct / 100)
    idx = min(idx, len(sorted_values) - 1)
    return sorted_values[idx]


@activity.defn(name="compute_agent_performance_snapshot")
async def compute_agent_performance_snapshot() -> dict:
    from app.db.session import SessionLocal
    from app.models.agent import Agent
    from app.models.agent_audit_log import AgentAuditLog
    from app.models.agent_performance_snapshot import AgentPerformanceSnapshot
    from app.models.external_agent import ExternalAgent
    from app.models.external_agent_call_log import ExternalAgentCallLog

    db = SessionLocal()
    try:
        since = datetime.utcnow() - timedelta(hours=24)
        agents = db.query(Agent).filter(Agent.status == "production").all()
        created = 0

        # ── Native agents — same shape the rollup has always used.
        for agent in agents:
            try:
                logs = (
                    db.query(AgentAuditLog)
                    .filter(
                        AgentAuditLog.agent_id == agent.id,
                        AgentAuditLog.created_at >= since,
                    )
                    .all()
                )

                invocation_count = len(logs)
                success_count = sum(1 for l in logs if l.status == "success")
                error_count = sum(1 for l in logs if l.status == "error")
                timeout_count = sum(1 for l in logs if l.status == "timeout")

                latency_vals = sorted([l.latency_ms for l in logs if l.latency_ms is not None])
                latency_p50 = _percentile(latency_vals, 50) if latency_vals else None
                latency_p95 = _percentile(latency_vals, 95) if latency_vals else None
                latency_p99 = _percentile(latency_vals, 99) if latency_vals else None

                qs_vals = [l.quality_score for l in logs if l.quality_score is not None]
                avg_quality_score = (sum(qs_vals) / len(qs_vals)) if qs_vals else None

                total_tokens = sum(
                    (l.input_tokens or 0) + (l.output_tokens or 0) for l in logs
                )
                total_cost_usd = sum(l.cost_usd or 0.0 for l in logs)
                cost_per_quality_point = None
                if avg_quality_score and avg_quality_score > 0 and total_cost_usd > 0:
                    cost_per_quality_point = total_cost_usd / avg_quality_score

                snapshot = AgentPerformanceSnapshot(
                    agent_id=agent.id,
                    tenant_id=agent.tenant_id,
                    window_start=since,
                    window_hours=24,
                    invocation_count=invocation_count,
                    success_count=success_count,
                    error_count=error_count,
                    timeout_count=timeout_count,
                    latency_p50_ms=latency_p50,
                    latency_p95_ms=latency_p95,
                    latency_p99_ms=latency_p99,
                    avg_quality_score=avg_quality_score,
                    total_tokens=total_tokens,
                    total_cost_usd=total_cost_usd,
                    cost_per_quality_point=cost_per_quality_point,
                )
                db.add(snapshot)
                db.commit()
                created += 1
            except Exception as e:
                logger.warning("Failed to compute snapshot for agent %s: %s", agent.id, e)
                db.rollback()

        # ── External agents — same percentile + cost shape, sourced
        # from external_agent_call_logs.
        external_created = 0
        for ext in db.query(ExternalAgent).all():
            try:
                logs = (
                    db.query(ExternalAgentCallLog)
                    .filter(
                        ExternalAgentCallLog.tenant_id == ext.tenant_id,
                        ExternalAgentCallLog.external_agent_id == ext.id,
                        ExternalAgentCallLog.started_at >= since,
                    )
                    .all()
                )
                if not logs:
                    continue

                invocation_count = len(logs)
                success_count = sum(1 for row in logs if row.status == "success")
                # Map non-retryable to error_count for rollup symmetry.
                error_count = sum(1 for row in logs if row.status in ("error", "non_retryable", "breaker_open"))
                timeout_count = 0  # external dispatch surfaces timeouts as error today

                latency_vals = sorted([row.latency_ms for row in logs if row.latency_ms is not None])
                latency_p50 = _percentile(latency_vals, 50) if latency_vals else None
                latency_p95 = _percentile(latency_vals, 95) if latency_vals else None
                latency_p99 = _percentile(latency_vals, 99) if latency_vals else None

                total_tokens = sum(row.total_tokens or 0 for row in logs)
                # cost_usd is Numeric(12,6); coerce to float for the snapshot column.
                total_cost_usd = float(sum((row.cost_usd or 0) for row in logs))

                snapshot = AgentPerformanceSnapshot(
                    external_agent_id=ext.id,
                    tenant_id=ext.tenant_id,
                    window_start=since,
                    window_hours=24,
                    invocation_count=invocation_count,
                    success_count=success_count,
                    error_count=error_count,
                    timeout_count=timeout_count,
                    latency_p50_ms=latency_p50,
                    latency_p95_ms=latency_p95,
                    latency_p99_ms=latency_p99,
                    avg_quality_score=None,  # external agents don't run through the auto-scorer
                    total_tokens=total_tokens,
                    total_cost_usd=total_cost_usd,
                    cost_per_quality_point=None,
                )
                db.add(snapshot)
                db.commit()
                external_created += 1
            except Exception as e:
                logger.warning("Failed to compute snapshot for external agent %s: %s", ext.id, e)
                db.rollback()

        logger.info(
            "compute_agent_performance_snapshot: created %d native + %d external snapshots",
            created, external_created,
        )
        return {"snapshots_created": created, "external_snapshots_created": external_created}
    finally:
        db.close()
