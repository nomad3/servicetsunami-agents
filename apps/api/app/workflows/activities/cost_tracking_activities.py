"""Cost tracking activity for the autonomous learning cycle.

Tracks per-cycle spend against tenant budgets and creates alerts
when approaching or exceeding limits.
"""

import logging
import uuid
from datetime import datetime, timedelta

from temporalio import activity

logger = logging.getLogger(__name__)

# Approximate cost per 1K tokens by platform (USD)
_COST_PER_1K_TOKENS = {
    "claude": 0.003,    # claude-sonnet midpoint
    "codex": 0.002,     # codex estimate
    "gemini": 0.001,
    "gemma": 0.0,       # local — free
    "local": 0.0,
}

# Estimated token usage per activity
_ACTIVITY_TOKEN_ESTIMATES = {
    "execute_simulation_scenarios": 5000,
    "generate_and_evaluate_candidates": 2000,
    "generate_morning_report": 1000,
    "process_human_feedback": 500,
    "collect_learning_metrics": 200,
    "manage_active_rollouts": 300,
    "auto_create_skill_stubs": 800,
    "run_self_diagnosis": 300,
}


@activity.defn(name="track_cycle_cost")
async def track_cycle_cost(tenant_id: str, cycle_result: dict) -> dict:
    """Log estimated cycle cost and check against tenant budgets. Alert if over threshold."""
    from app.db.session import SessionLocal
    from app.models.notification import Notification
    from sqlalchemy import text

    db = SessionLocal()
    try:
        tenant_uuid = uuid.UUID(tenant_id)
        today = datetime.utcnow().date()

        # Estimate cost from RL experiences created today (have actual token/cost data)
        rl_cost_row = db.execute(text("""
            SELECT
                COALESCE(SUM((reward_components->>'tokens_used')::numeric), 0) AS total_tokens,
                COALESCE(SUM((reward_components->>'cost_usd')::numeric), 0) AS total_cost
            FROM rl_experiences
            WHERE tenant_id = CAST(:tid AS uuid)
              AND created_at::date = :today
              AND reward_components ? 'cost_usd'
              AND archived_at IS NULL
        """), {"tid": tenant_id, "today": today}).one()

        # Supplement with simulation execution estimate (local model, near-zero cost)
        sim_tokens = _ACTIVITY_TOKEN_ESTIMATES["execute_simulation_scenarios"]
        sim_cost = 0.0  # always local Gemma 4

        total_tokens = int(rl_cost_row.total_tokens or 0) + sim_tokens
        total_cost_usd = float(rl_cost_row.total_cost or 0.0) + sim_cost

        # Log to cost_tracking_log (upsert: accumulate tokens/cost if cycle reruns today)
        db.execute(text("""
            INSERT INTO cost_tracking_log
                (tenant_id, cycle_date, activity_name, tokens_used, cost_usd, platform)
            VALUES
                (CAST(:tid AS uuid), :today, 'learning_cycle', :tokens, :cost, 'mixed')
            ON CONFLICT (tenant_id, cycle_date, activity_name) DO UPDATE
                SET tokens_used = cost_tracking_log.tokens_used + EXCLUDED.tokens_used,
                    cost_usd    = cost_tracking_log.cost_usd + EXCLUDED.cost_usd
        """), {
            "tid": tenant_id,
            "today": today,
            "tokens": total_tokens,
            "cost": total_cost_usd,
        })

        # Check against daily budget
        budget_row = db.execute(text("""
            SELECT amount_usd, current_spend_usd, alert_threshold
            FROM cost_budgets
            WHERE tenant_id = CAST(:tid AS uuid)
              AND budget_type = 'daily'
        """), {"tid": tenant_id}).fetchone()

        alert_sent = False
        budget_exceeded = False

        if budget_row:
            # Reset daily spend if the period has rolled over
            db.execute(text("""
                UPDATE cost_budgets
                SET current_spend_usd = 0.0,
                    current_period_start = DATE_TRUNC('day', NOW()),
                    updated_at = NOW()
                WHERE tenant_id = CAST(:tid AS uuid)
                  AND budget_type = 'daily'
                  AND current_period_start < DATE_TRUNC('day', NOW())
            """), {"tid": tenant_id})

            # Add today's cost
            db.execute(text("""
                UPDATE cost_budgets
                SET current_spend_usd = current_spend_usd + :cost,
                    updated_at = NOW()
                WHERE tenant_id = CAST(:tid AS uuid)
                  AND budget_type = 'daily'
            """), {"tid": tenant_id, "cost": total_cost_usd})

            new_spend = float(budget_row.current_spend_usd) + total_cost_usd
            budget = float(budget_row.amount_usd)
            threshold = float(budget_row.alert_threshold)

            if new_spend >= budget:
                budget_exceeded = True
                notification = Notification(
                    tenant_id=tenant_uuid,
                    source="autonomous_learning",
                    title="Daily Learning Budget Exceeded",
                    body=(
                        f"Learning cycle spend today: ${new_spend:.4f} "
                        f"(budget: ${budget:.2f}). "
                        f"Next cycle will be skipped until tomorrow."
                    ),
                    priority="high",
                    reference_id=f"cost_exceeded:{today}",
                    reference_type="cost_alert",
                )
                db.add(notification)
                alert_sent = True
                logger.warning(
                    "Daily budget exceeded for tenant %s: $%.4f / $%.2f",
                    tenant_id[:8], new_spend, budget,
                )
            elif new_spend >= budget * threshold:
                notification = Notification(
                    tenant_id=tenant_uuid,
                    source="autonomous_learning",
                    title=f"Learning Budget at {threshold:.0%}",
                    body=(
                        f"Today's learning spend: ${new_spend:.4f} "
                        f"(budget: ${budget:.2f}, {new_spend / budget:.0%} used)."
                    ),
                    priority="medium",
                    reference_id=f"cost_threshold:{today}",
                    reference_type="cost_alert",
                )
                db.add(notification)
                alert_sent = True
                logger.info(
                    "Budget threshold reached for tenant %s: $%.4f / $%.2f",
                    tenant_id[:8], new_spend, budget,
                )

        db.commit()
        logger.info(
            "Cost tracked for tenant %s: tokens=%d, cost=$%.4f, alert=%s",
            tenant_id[:8], total_tokens, total_cost_usd, alert_sent,
        )
        return {
            "tokens_used": total_tokens,
            "cost_usd": round(total_cost_usd, 6),
            "alert_sent": alert_sent,
            "budget_exceeded": budget_exceeded,
        }
    except Exception as e:
        logger.error("track_cycle_cost failed for %s: %s", tenant_id[:8], e)
        raise
    finally:
        db.close()
