"""Feedback, self-diagnosis, and regression-monitoring activities for the nightly learning cycle."""

import logging
import uuid
from datetime import datetime, timedelta

from temporalio import activity

logger = logging.getLogger(__name__)

# Keywords that indicate positive/negative feedback
_POSITIVE_KEYWORDS = ["good call", "approve", "great", "looks good", "sounds right", "go ahead", "try it"]
_NEGATIVE_KEYWORDS = ["bad idea", "don't route", "reject", "no", "stop", "revert", "rollback", "bad", "wrong"]
_DIRECTION_KEYWORDS = ["try", "consider", "maybe", "what about", "how about", "suggest"]
_CORRECTION_KEYWORDS = ["actually", "correction", "wrong", "incorrect", "fix this", "should be"]

# Keywords that indicate the message references a learning report
_REPORT_KEYWORDS = ["learning report", "morning report", "nightly report", "your report", "the report"]


@activity.defn(name="process_human_feedback")
async def process_human_feedback(tenant_id: str) -> dict:
    """Scan recent chat messages for human feedback on learning reports."""
    from app.db.session import SessionLocal
    from app.models.feedback_record import FeedbackRecord
    from sqlalchemy import text

    db = SessionLocal()
    try:
        tenant_uuid = uuid.UUID(tenant_id)
        since = datetime.utcnow() - timedelta(hours=24)

        # Look for recent messages that mention learning reports
        # tenant_id lives on chat_sessions, not chat_messages — join through session
        messages = db.execute(text("""
            SELECT cm.id, cm.content, cm.created_at, cm.session_id
            FROM chat_messages cm
            JOIN chat_sessions cs ON cs.id = cm.session_id
            WHERE cs.tenant_id = CAST(:tid AS uuid)
              AND cm.created_at > :since
              AND cm.role = 'user'
              AND (
                LOWER(cm.content) LIKE '%learning report%'
                OR LOWER(cm.content) LIKE '%morning report%'
                OR LOWER(cm.content) LIKE '%nightly report%'
                OR LOWER(cm.content) LIKE '%your report%'
              )
            ORDER BY cm.created_at DESC
            LIMIT 20
        """), {"tid": tenant_id, "since": since}).fetchall()

        processed = 0
        for msg in messages:
            content_lower = msg.content.lower()

            # Determine feedback type
            feedback_type, parsed_intent = _classify_feedback(content_lower)
            if not feedback_type:
                continue

            # Avoid duplicate processing
            already_exists = db.execute(text("""
                SELECT 1 FROM feedback_records
                WHERE tenant_id = CAST(:tid AS uuid)
                  AND report_id = :rid
                  AND content = :content
                LIMIT 1
            """), {
                "tid": tenant_id,
                "rid": str(msg.session_id),
                "content": msg.content[:500],
            }).fetchone()

            if already_exists:
                continue

            record = FeedbackRecord(
                tenant_id=tenant_uuid,
                report_id=str(msg.session_id),
                feedback_type=feedback_type,
                content=msg.content[:1000],
                parsed_intent=parsed_intent,
                applied=False,
            )
            db.add(record)
            processed += 1

        db.commit()

        logger.info(
            "Processed %d feedback records for tenant %s",
            processed, tenant_id[:8],
        )
        return {"feedback_processed": processed}
    except Exception as e:
        logger.error("process_human_feedback failed for %s: %s", tenant_id[:8], e)
        raise
    finally:
        db.close()


@activity.defn(name="run_self_diagnosis")
async def run_self_diagnosis(tenant_id: str) -> dict:
    """Aggregate simulation failures and compute an overall platform health signal."""
    from app.db.session import SessionLocal
    from sqlalchemy import text

    db = SessionLocal()
    try:
        today = datetime.utcnow().date()

        # Simulation failure summary from the latest cycle
        failure_rows = db.execute(text("""
            SELECT
                sr.failure_type,
                COUNT(*) AS cnt,
                AVG(sr.quality_score) AS avg_score
            FROM simulation_results sr
            JOIN simulation_scenarios ss ON ss.id = sr.scenario_id
            WHERE sr.tenant_id = CAST(:tid AS uuid)
              AND ss.cycle_date = :today
              AND sr.is_simulation = TRUE
              AND sr.failure_type IS NOT NULL
            GROUP BY sr.failure_type
            ORDER BY cnt DESC
        """), {"tid": tenant_id, "today": today}).fetchall()

        top_failures = [
            {
                "failure_type": r.failure_type,
                "count": r.cnt,
                "avg_score": round(float(r.avg_score), 2) if r.avg_score else 0,
            }
            for r in failure_rows
        ]

        # Active skill gaps count
        skill_gaps_active = db.execute(text("""
            SELECT COUNT(*) FROM skill_gaps
            WHERE tenant_id = CAST(:tid AS uuid)
              AND status IN ('detected', 'acknowledged', 'in_progress')
        """), {"tid": tenant_id}).scalar() or 0

        # Total simulations run today
        total_simulations = db.execute(text("""
            SELECT COUNT(*) FROM simulation_results sr
            JOIN simulation_scenarios ss ON ss.id = sr.scenario_id
            WHERE sr.tenant_id = CAST(:tid AS uuid)
              AND ss.cycle_date = :today
              AND sr.is_simulation = TRUE
        """), {"tid": tenant_id, "today": today}).scalar() or 0

        # Failure rate
        total_failures = sum(r["count"] for r in top_failures)
        failure_rate = (total_failures / total_simulations) if total_simulations > 0 else 0.0

        # Overall health classification
        if failure_rate < 0.2 and skill_gaps_active < 3:
            overall_health = "good"
        elif failure_rate < 0.4 and skill_gaps_active < 7:
            overall_health = "needs_attention"
        else:
            overall_health = "critical"

        diagnosis = {
            "top_failures": top_failures[:5],
            "skill_gaps_active": skill_gaps_active,
            "total_simulations": total_simulations,
            "total_failures": total_failures,
            "failure_rate": round(failure_rate, 3),
            "overall_health": overall_health,
        }

        logger.info(
            "Self-diagnosis for tenant %s: health=%s, failure_rate=%.2f, gaps=%d",
            tenant_id[:8], overall_health, failure_rate, skill_gaps_active,
        )
        return diagnosis
    except Exception as e:
        logger.error("run_self_diagnosis failed for %s: %s", tenant_id[:8], e)
        raise
    finally:
        db.close()


@activity.defn(name="monitor_regression")
async def monitor_regression(tenant_id: str) -> dict:
    """Check promoted policy candidates for regression vs their baseline reward."""
    from app.db.session import SessionLocal
    from app.models.learning_experiment import PolicyCandidate
    from app.models.notification import Notification
    from sqlalchemy import text

    db = SessionLocal()
    try:
        tenant_uuid = uuid.UUID(tenant_id)
        now = datetime.utcnow()
        last_24h = now - timedelta(hours=24)

        # Get all promoted candidates with a baseline_reward
        promoted = (
            db.query(PolicyCandidate)
            .filter(
                PolicyCandidate.tenant_id == tenant_uuid,
                PolicyCandidate.status == "promoted",
            )
            .all()
        )

        regressions_detected = 0
        candidates_checked = len(promoted)

        for candidate in promoted:
            # baseline_reward is a direct Float column on PolicyCandidate
            if candidate.baseline_reward is None:
                continue

            try:
                baseline_reward = float(candidate.baseline_reward)
            except (TypeError, ValueError):
                continue

            # Compute rolling 24h avg reward for RL experiences attributed to this candidate's routing
            decision_point = candidate.decision_point
            rolling_avg = db.execute(text("""
                SELECT AVG(reward) AS avg_reward, COUNT(*) AS n
                FROM rl_experiences
                WHERE tenant_id = CAST(:tid AS uuid)
                  AND decision_point = :dp
                  AND action->>'routing_source' = 'rl_policy'
                  AND created_at > :since
                  AND reward IS NOT NULL
                  AND archived_at IS NULL
            """), {
                "tid": tenant_id,
                "dp": decision_point,
                "since": last_24h,
            }).one()

            if not rolling_avg.avg_reward or rolling_avg.n < 5:
                # Not enough data to detect regression
                continue

            current_avg = float(rolling_avg.avg_reward)
            regression_pct = (baseline_reward - current_avg) / baseline_reward * 100 if baseline_reward > 0 else 0

            if regression_pct > 10.0:
                # Regression detected — demote candidate and notify
                logger.warning(
                    "Regression detected for candidate %s (tenant %s): "
                    "baseline=%.3f, current=%.3f, regression=%.1f%%",
                    str(candidate.id)[:8], tenant_id[:8],
                    baseline_reward, current_avg, regression_pct,
                )

                candidate.status = "evaluating"
                candidate.rejection_reason = (
                    f"Regression detected at {now.isoformat()}: "
                    f"{regression_pct:.2f}% drop (current avg: {current_avg:.3f}, "
                    f"baseline: {baseline_reward:.3f})"
                )

                # Create regression notification
                notification = Notification(
                    tenant_id=tenant_uuid,
                    source="autonomous_learning",
                    title=f"Regression Detected — {decision_point} policy reverted",
                    body=(
                        f"Policy candidate for '{decision_point}' showed {regression_pct:.1f}% regression "
                        f"vs baseline (current avg: {current_avg:.3f}, baseline: {baseline_reward:.3f}). "
                        f"Candidate has been reverted to evaluating state."
                    ),
                    priority="high",
                    reference_id=f"regression:{candidate.id}",
                    reference_type="learning_regression",
                )
                db.add(notification)
                regressions_detected += 1

        db.commit()

        logger.info(
            "Regression monitor for tenant %s: checked=%d, regressions=%d",
            tenant_id[:8], candidates_checked, regressions_detected,
        )
        return {
            "regressions_detected": regressions_detected,
            "candidates_checked": candidates_checked,
        }
    except Exception as e:
        logger.error("monitor_regression failed for %s: %s", tenant_id[:8], e)
        raise
    finally:
        db.close()


@activity.defn(name="apply_feedback_to_cycle")
async def apply_feedback_to_cycle(tenant_id: str) -> dict:
    """Apply unapplied human feedback records to policy candidates and exploration config."""
    from app.db.session import SessionLocal
    from app.models.feedback_record import FeedbackRecord
    from app.models.learning_experiment import PolicyCandidate
    from sqlalchemy import text

    db = SessionLocal()
    try:
        tenant_uuid = uuid.UUID(tenant_id)

        unapplied = (
            db.query(FeedbackRecord)
            .filter(
                FeedbackRecord.tenant_id == tenant_uuid,
                FeedbackRecord.applied == False,
            )
            .order_by(FeedbackRecord.created_at.asc())
            .limit(50)
            .all()
        )

        applied_count = 0
        for record in unapplied:
            intent = record.parsed_intent or ""
            content_lower = record.content.lower()
            try:
                if intent == "approve_routing_change":
                    platform = _extract_platform(content_lower)
                    if platform:
                        candidate = (
                            db.query(PolicyCandidate)
                            .filter(
                                PolicyCandidate.tenant_id == tenant_uuid,
                                PolicyCandidate.status == "evaluating",
                                PolicyCandidate.proposed_policy.contains(platform),
                            )
                            .order_by(PolicyCandidate.created_at.desc())
                            .first()
                        )
                        if candidate:
                            # Use the proper promotion gate — requires a successful experiment
                            try:
                                from app.services import learning_experiment_service
                                learning_experiment_service.promote_candidate(
                                    db, tenant_uuid, candidate.id,
                                )
                                logger.info("Human approved + promoted candidate %s", str(candidate.id)[:8])
                            except ValueError as prom_err:
                                # No successful experiment yet — just log the approval intent
                                logger.info(
                                    "Human approved candidate %s but promotion gate not met: %s",
                                    str(candidate.id)[:8], prom_err,
                                )

                elif intent in ("reject_platform", "general_rejection"):
                    platform = _extract_platform(content_lower)
                    q = db.query(PolicyCandidate).filter(
                        PolicyCandidate.tenant_id == tenant_uuid,
                        PolicyCandidate.status == "evaluating",
                    )
                    if platform:
                        q = q.filter(PolicyCandidate.proposed_policy.contains(platform))
                    for c in q.limit(5).all():
                        c.status = "rejected"
                        c.rejected_at = datetime.utcnow()
                        c.rejection_reason = f"Human rejected: {record.content[:200]}"
                        logger.info("Human-rejected candidate %s", str(c.id)[:8])

                elif intent == "request_rollback":
                    db.execute(text("""
                        UPDATE learning_experiments
                        SET status = 'cancelled', conclusion = 'human_rollback_requested'
                        WHERE tenant_id = CAST(:tid AS uuid)
                          AND status = 'running'
                          AND experiment_type = 'split'
                    """), {"tid": tenant_id})
                    logger.info("Human rollback: cancelled running rollouts for %s", tenant_id[:8])

                elif intent == "exploration_direction":
                    dp = _extract_decision_point(content_lower)
                    if dp:
                        db.execute(text("""
                            INSERT INTO decision_point_config
                                (tenant_id, decision_point, exploration_rate, exploration_mode)
                            VALUES (CAST(:tid AS uuid), :dp, 0.20, 'targeted')
                            ON CONFLICT (tenant_id, decision_point) DO UPDATE
                              SET exploration_rate = LEAST(
                                      decision_point_config.exploration_rate + 0.05, 0.30),
                                  exploration_mode = 'targeted',
                                  updated_at = NOW()
                        """), {"tid": tenant_id, "dp": dp})
                        logger.info("Boosted exploration for dp=%s tenant=%s", dp, tenant_id[:8])

                record.applied = True
                applied_count += 1
            except Exception as inner_e:
                logger.warning("Could not apply feedback %s: %s", str(record.id)[:8], inner_e)
                record.applied = True  # prevent infinite reprocessing

        db.commit()
        logger.info("Applied %d feedback records for tenant %s", applied_count, tenant_id[:8])
        return {"feedback_applied": applied_count}
    except Exception as e:
        logger.error("apply_feedback_to_cycle failed for %s: %s", tenant_id[:8], e)
        raise
    finally:
        db.close()


@activity.defn(name="adjust_exploration_rates")
async def adjust_exploration_rates(tenant_id: str, metrics: dict) -> dict:
    """Adjust per-decision-point exploration rates based on stall data and platform performance."""
    from app.db.session import SessionLocal
    from sqlalchemy import text

    db = SessionLocal()
    try:
        # Stalled: decision points with no promotions in the last 30 days
        stalled_rows = db.execute(text("""
            SELECT decision_point
            FROM policy_candidates
            WHERE tenant_id = CAST(:tid AS uuid)
              AND created_at > NOW() - INTERVAL '30 days'
            GROUP BY decision_point
            HAVING COUNT(*) FILTER (WHERE status = 'promoted') = 0
               AND COUNT(*) > 0
        """), {"tid": tenant_id}).fetchall()

        # Well-performing: recently promoted in last 14 days
        performing_rows = db.execute(text("""
            SELECT DISTINCT decision_point
            FROM policy_candidates
            WHERE tenant_id = CAST(:tid AS uuid)
              AND status = 'promoted'
              AND updated_at > NOW() - INTERVAL '14 days'
        """), {"tid": tenant_id}).fetchall()
        performing_dps = {r.decision_point for r in performing_rows}

        stalled_boosted = 0
        for row in stalled_rows:
            dp = row.decision_point
            if dp in performing_dps:
                continue
            db.execute(text("""
                INSERT INTO decision_point_config
                    (tenant_id, decision_point, exploration_rate, exploration_mode)
                VALUES (CAST(:tid AS uuid), :dp, 0.20, 'targeted')
                ON CONFLICT (tenant_id, decision_point) DO UPDATE
                  SET exploration_rate = LEAST(
                          decision_point_config.exploration_rate + 0.05, 0.25),
                      exploration_mode = 'targeted',
                      updated_at = NOW()
            """), {"tid": tenant_id, "dp": dp})
            stalled_boosted += 1

        for dp in performing_dps:
            db.execute(text("""
                INSERT INTO decision_point_config
                    (tenant_id, decision_point, exploration_rate, exploration_mode)
                VALUES (CAST(:tid AS uuid), :dp, 0.05, 'balanced')
                ON CONFLICT (tenant_id, decision_point) DO UPDATE
                  SET exploration_rate = GREATEST(
                          decision_point_config.exploration_rate - 0.02, 0.05),
                      updated_at = NOW()
            """), {"tid": tenant_id, "dp": dp})

        db.commit()
        logger.info(
            "Exploration rates: %d stalled boosted, %d performing reduced (tenant %s)",
            stalled_boosted, len(performing_dps), tenant_id[:8],
        )
        return {"stalled_boosted": stalled_boosted, "performing_reduced": len(performing_dps)}
    except Exception as e:
        logger.error("adjust_exploration_rates failed for %s: %s", tenant_id[:8], e)
        raise
    finally:
        db.close()


# --- Private helpers ---

def _extract_platform(content: str) -> str | None:
    """Extract a platform name from message content."""
    for p in ("claude", "codex", "gemini", "openai", "qwen", "local"):
        if p in content:
            return p
    return None


def _extract_decision_point(content: str) -> str | None:
    """Extract a decision point name from message content."""
    for dp in ("chat_response", "agent_routing", "code_task"):
        if dp.replace("_", " ") in content or dp in content:
            return dp
    return None


def _classify_feedback(content_lower: str) -> tuple:
    """Return (feedback_type, parsed_intent) based on message content."""
    if any(kw in content_lower for kw in _POSITIVE_KEYWORDS):
        if "routing" in content_lower or "platform" in content_lower:
            return ("approval", "approve_routing_change")
        return ("approval", "general_approval")

    if any(kw in content_lower for kw in _NEGATIVE_KEYWORDS):
        if "routing" in content_lower or "platform" in content_lower:
            return ("rejection", "reject_platform")
        if "rollback" in content_lower or "revert" in content_lower:
            return ("rejection", "request_rollback")
        return ("rejection", "general_rejection")

    if any(kw in content_lower for kw in _CORRECTION_KEYWORDS):
        return ("correction", "factual_correction")

    if any(kw in content_lower for kw in _DIRECTION_KEYWORDS):
        return ("direction", "exploration_direction")

    return (None, None)
