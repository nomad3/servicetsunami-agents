"""Proactive activities — Luna-initiated nudges and briefings for the nightly cycle."""

import logging
import uuid
from datetime import datetime, timedelta

from temporalio import activity

logger = logging.getLogger(__name__)

_DAILY_ACTION_LIMIT = 5


@activity.defn(name="scan_for_proactive_actions")
async def scan_for_proactive_actions(tenant_id: str) -> dict:
    """Scan for stalled goals, overdue commitments, and expiring assertions; queue proactive actions."""
    from app.db.session import SessionLocal
    from app.models.proactive_action import ProactiveAction
    from sqlalchemy import text

    db = SessionLocal()
    try:
        tenant_uuid = uuid.UUID(tenant_id)
        now = datetime.utcnow()
        actions_queued = 0

        # Check daily cap
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        existing_today = db.execute(text("""
            SELECT COUNT(*) AS cnt
            FROM proactive_actions
            WHERE tenant_id = CAST(:tid AS uuid)
              AND created_at >= :since
        """), {"tid": tenant_id, "since": today_start}).scalar() or 0

        remaining_capacity = _DAILY_ACTION_LIMIT - existing_today
        if remaining_capacity <= 0:
            logger.info("Daily proactive action limit reached for tenant %s", tenant_id[:8])
            return {"actions_queued": 0}

        # 1. Stalled goals (blocked for > 3 days)
        stalled_cutoff = now - timedelta(days=3)
        stalled_goals = db.execute(text("""
            SELECT id, title, state
            FROM goal_records
            WHERE tenant_id = CAST(:tid AS uuid)
              AND state = 'blocked'
              AND updated_at < :cutoff
            ORDER BY updated_at ASC
            LIMIT 5
        """), {"tid": tenant_id, "cutoff": stalled_cutoff}).fetchall()

        for goal in stalled_goals:
            if actions_queued >= remaining_capacity:
                break
            target_ref = f"goal:{goal.id}"
            # Skip if already queued today for this target
            already_queued = _check_already_queued(db, tenant_id, target_ref, today_start)
            if already_queued:
                continue

            action = ProactiveAction(
                tenant_id=tenant_uuid,
                agent_slug="luna",
                action_type="nudge",
                trigger_type="stalled_goal",
                target_ref=target_ref,
                priority="high",
                content=(
                    f"Goal '{goal.title}' has been blocked for more than 3 days. "
                    f"Would you like me to help identify what's holding it back?"
                ),
                channel="notification",
                status="pending",
            )
            db.add(action)
            actions_queued += 1

        # 2. Overdue commitments
        overdue_commitments = db.execute(text("""
            SELECT id, description, due_at, state
            FROM commitment_records
            WHERE tenant_id = CAST(:tid AS uuid)
              AND state IN ('open', 'in_progress')
              AND due_at < :now
            ORDER BY due_at ASC
            LIMIT 5
        """), {"tid": tenant_id, "now": now}).fetchall()

        for commitment in overdue_commitments:
            if actions_queued >= remaining_capacity:
                break
            target_ref = f"commitment:{commitment.id}"
            already_queued = _check_already_queued(db, tenant_id, target_ref, today_start)
            if already_queued:
                continue

            days_overdue = (now - commitment.due_at).days if commitment.due_at else 0
            action = ProactiveAction(
                tenant_id=tenant_uuid,
                agent_slug="luna",
                action_type="followup",
                trigger_type="overdue_commitment",
                target_ref=target_ref,
                priority="high" if days_overdue > 2 else "medium",
                content=(
                    f"Commitment '{commitment.description[:80]}' is overdue by {days_overdue} day(s). "
                    f"Should I help update its status or draft a follow-up?"
                ),
                channel="notification",
                status="pending",
            )
            db.add(action)
            actions_queued += 1

        # 3. Expiring world state assertions (within 48h)
        expiry_window = now + timedelta(hours=48)
        expiring_assertions = db.execute(text("""
            SELECT id, subject, predicate, object_value
            FROM world_state_assertions
            WHERE tenant_id = CAST(:tid AS uuid)
              AND status = 'active'
              AND expires_at IS NOT NULL
              AND expires_at <= :window
              AND expires_at > :now
            ORDER BY expires_at ASC
            LIMIT 5
        """), {"tid": tenant_id, "window": expiry_window, "now": now}).fetchall()

        for assertion in expiring_assertions:
            if actions_queued >= remaining_capacity:
                break
            target_ref = f"assertion:{assertion.id}"
            already_queued = _check_already_queued(db, tenant_id, target_ref, today_start)
            if already_queued:
                continue

            action = ProactiveAction(
                tenant_id=tenant_uuid,
                agent_slug="luna",
                action_type="alert",
                trigger_type="expiring_assertion",
                target_ref=target_ref,
                priority="medium",
                content=(
                    f"World state fact expiring soon: '{assertion.subject} {assertion.predicate} "
                    f"{assertion.object_value}'. Should I verify and refresh this information?"
                ),
                channel="notification",
                status="pending",
            )
            db.add(action)
            actions_queued += 1

        db.commit()

        logger.info(
            "Queued %d proactive actions for tenant %s",
            actions_queued, tenant_id[:8],
        )
        return {"actions_queued": actions_queued}
    except Exception as e:
        logger.error("scan_for_proactive_actions failed for %s: %s", tenant_id[:8], e)
        raise
    finally:
        db.close()


@activity.defn(name="send_proactive_notifications")
async def send_proactive_notifications(tenant_id: str) -> dict:
    """Convert pending proactive actions into Notification records."""
    from app.db.session import SessionLocal
    from app.models.proactive_action import ProactiveAction
    from app.models.notification import Notification
    from sqlalchemy import text

    db = SessionLocal()
    try:
        tenant_uuid = uuid.UUID(tenant_id)
        now = datetime.utcnow()

        pending = (
            db.query(ProactiveAction)
            .filter(
                ProactiveAction.tenant_id == tenant_uuid,
                ProactiveAction.status == "pending",
            )
            .filter(
                (ProactiveAction.scheduled_at == None) |  # noqa: E711
                (ProactiveAction.scheduled_at <= now)
            )
            .all()
        )

        sent = 0
        for action in pending:
            # Create a notification
            notification = Notification(
                tenant_id=tenant_id,
                source="proactive_agent",
                title=_action_title(action.action_type, action.trigger_type),
                body=action.content,
                priority=action.priority,
                reference_id=f"proactive:{action.id}",
                reference_type="proactive_action",
            )
            db.add(notification)

            action.status = "sent"
            action.sent_at = now
            sent += 1

        db.commit()

        logger.info(
            "Sent %d proactive notifications for tenant %s",
            sent, tenant_id[:8],
        )
        return {"sent": sent}
    except Exception as e:
        logger.error("send_proactive_notifications failed for %s: %s", tenant_id[:8], e)
        raise
    finally:
        db.close()


# --- Private helpers ---

def _check_already_queued(db, tenant_id: str, target_ref: str, since: datetime) -> bool:
    """Return True if a proactive action for this target was already created today."""
    from sqlalchemy import text
    result = db.execute(text("""
        SELECT 1 FROM proactive_actions
        WHERE tenant_id = CAST(:tid AS uuid)
          AND target_ref = :ref
          AND created_at >= :since
        LIMIT 1
    """), {"tid": tenant_id, "ref": target_ref, "since": since}).fetchone()
    return result is not None


def _action_title(action_type: str, trigger_type: str) -> str:
    """Build a concise notification title."""
    titles = {
        ("nudge", "stalled_goal"): "Stalled Goal Needs Attention",
        ("followup", "overdue_commitment"): "Overdue Commitment Follow-up",
        ("alert", "expiring_assertion"): "World State Fact Expiring Soon",
        ("briefing", "calendar_prep"): "Upcoming Meeting Briefing",
        ("analysis", "cold_lead"): "Cold Lead Opportunity",
    }
    return titles.get((action_type, trigger_type), f"Luna: {action_type.capitalize()} — {trigger_type}")
