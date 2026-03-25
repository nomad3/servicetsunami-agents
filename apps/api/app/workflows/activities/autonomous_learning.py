"""Activities for the AutonomousLearningWorkflow — the nightly heartbeat."""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from temporalio import activity

logger = logging.getLogger(__name__)


@activity.defn(name="collect_learning_metrics")
async def collect_learning_metrics(tenant_id: str) -> dict:
    """Collect current platform health and learning state for a tenant."""
    from app.db.session import SessionLocal
    from sqlalchemy import text

    db = SessionLocal()
    try:
        now = datetime.utcnow()
        last_24h = now - timedelta(hours=24)
        last_7d = now - timedelta(days=7)

        # RL experience stats (last 24h)
        rl_stats = db.execute(text("""
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE reward IS NOT NULL) AS rated,
                AVG(reward) FILTER (WHERE reward IS NOT NULL) AS avg_reward,
                COUNT(DISTINCT action->>'platform') AS platforms_used
            FROM rl_experiences
            WHERE tenant_id = CAST(:tid AS uuid)
              AND created_at > :since
              AND archived_at IS NULL
        """), {"tid": tenant_id, "since": last_24h}).one()

        # Per-platform breakdown (last 7 days for enough data)
        platform_rows = db.execute(text("""
            SELECT
                action->>'platform' AS platform,
                COUNT(*) AS total,
                AVG(reward) FILTER (WHERE reward IS NOT NULL) AS avg_reward,
                COUNT(*) FILTER (WHERE reward IS NOT NULL) AS rated
            FROM rl_experiences
            WHERE tenant_id = CAST(:tid AS uuid)
              AND created_at > :since
              AND archived_at IS NULL
              AND action->>'platform' IS NOT NULL
            GROUP BY action->>'platform'
            ORDER BY avg_reward DESC NULLS LAST
        """), {"tid": tenant_id, "since": last_7d}).fetchall()

        platforms = [
            {
                "platform": r.platform,
                "total": r.total,
                "avg_reward": round(float(r.avg_reward), 3) if r.avg_reward else None,
                "rated": r.rated,
            }
            for r in platform_rows
        ]

        # Trust profile summary
        trust_rows = db.execute(text("""
            SELECT agent_slug, trust_score, confidence, autonomy_tier
            FROM agent_trust_profiles
            WHERE tenant_id = CAST(:tid AS uuid)
            ORDER BY trust_score DESC
        """), {"tid": tenant_id}).fetchall()

        trust_profiles = [
            {
                "agent": r.agent_slug,
                "trust": round(float(r.trust_score), 3),
                "confidence": round(float(r.confidence), 3),
                "tier": r.autonomy_tier,
            }
            for r in trust_rows
        ]

        # Goal health
        goal_stats = db.execute(text("""
            SELECT
                COUNT(*) FILTER (WHERE state IN ('proposed', 'active', 'blocked')) AS active,
                COUNT(*) FILTER (WHERE state = 'completed') AS completed,
                COUNT(*) FILTER (WHERE state = 'blocked') AS blocked,
                COUNT(*) FILTER (WHERE state = 'abandoned') AS abandoned
            FROM goal_records
            WHERE tenant_id = CAST(:tid AS uuid)
        """), {"tid": tenant_id}).one()

        # Commitment health
        commitment_stats = db.execute(text("""
            SELECT
                COUNT(*) FILTER (WHERE state IN ('open', 'in_progress')) AS open,
                COUNT(*) FILTER (WHERE state = 'fulfilled') AS fulfilled,
                COUNT(*) FILTER (WHERE state = 'broken') AS broken,
                COUNT(*) FILTER (WHERE due_at < NOW() AND state IN ('open', 'in_progress')) AS overdue
            FROM commitment_records
            WHERE tenant_id = CAST(:tid AS uuid)
        """), {"tid": tenant_id}).one()

        # World state health
        ws_stats = db.execute(text("""
            SELECT
                COUNT(*) FILTER (WHERE status = 'active') AS active_assertions,
                COUNT(*) FILTER (WHERE status = 'disputed') AS disputed,
                COUNT(*) FILTER (WHERE status = 'expired') AS expired
            FROM world_state_assertions
            WHERE tenant_id = CAST(:tid AS uuid)
        """), {"tid": tenant_id}).one()

        # Policy candidate summary
        candidate_stats = db.execute(text("""
            SELECT
                COUNT(*) FILTER (WHERE status = 'proposed') AS proposed,
                COUNT(*) FILTER (WHERE status = 'evaluating') AS evaluating,
                COUNT(*) FILTER (WHERE status = 'promoted') AS promoted,
                COUNT(*) FILTER (WHERE status = 'rejected') AS rejected
            FROM policy_candidates
            WHERE tenant_id = CAST(:tid AS uuid)
        """), {"tid": tenant_id}).one()

        return {
            "collected_at": now.isoformat(),
            "rl": {
                "last_24h_total": rl_stats.total,
                "last_24h_rated": rl_stats.rated,
                "last_24h_avg_reward": round(float(rl_stats.avg_reward), 3) if rl_stats.avg_reward else None,
                "platforms_used": rl_stats.platforms_used,
            },
            "platforms": platforms,
            "trust_profiles": trust_profiles,
            "goals": {
                "active": goal_stats.active,
                "completed": goal_stats.completed,
                "blocked": goal_stats.blocked,
                "abandoned": goal_stats.abandoned,
            },
            "commitments": {
                "open": commitment_stats.open,
                "fulfilled": commitment_stats.fulfilled,
                "broken": commitment_stats.broken,
                "overdue": commitment_stats.overdue,
            },
            "world_state": {
                "active_assertions": ws_stats.active_assertions,
                "disputed": ws_stats.disputed,
                "expired": ws_stats.expired,
            },
            "candidates": {
                "proposed": candidate_stats.proposed,
                "evaluating": candidate_stats.evaluating,
                "promoted": candidate_stats.promoted,
                "rejected": candidate_stats.rejected,
            },
        }
    except Exception as e:
        logger.error("collect_learning_metrics failed for %s: %s", tenant_id[:8], e)
        raise
    finally:
        db.close()


@activity.defn(name="generate_and_evaluate_candidates")
async def generate_and_evaluate_candidates(
    tenant_id: str,
    metrics: dict,
) -> dict:
    """Generate policy candidates from RL patterns and evaluate them offline."""
    from app.db.session import SessionLocal
    from app.services import learning_experiment_service
    from app.schemas.learning_experiment import LearningExperimentCreate, ExperimentType
    import uuid

    db = SessionLocal()
    try:
        tenant_uuid = uuid.UUID(tenant_id)

        # Step 1: Auto-generate routing candidates
        generated = learning_experiment_service.generate_routing_candidates(db, tenant_uuid)
        logger.info(
            "Generated %d routing candidates for tenant %s",
            len(generated), tenant_id[:8],
        )

        # Step 2: Evaluate candidates that need evaluation
        # Include both 'proposed' (never evaluated) and 'evaluating' with only
        # insufficient_data results (re-evaluate with potentially more data)
        proposed = learning_experiment_service.list_candidates(
            db, tenant_uuid, status="proposed"
        )
        MAX_INSUFFICIENT_DATA_CYCLES = 3

        evaluating_stuck = []
        for c in learning_experiment_service.list_candidates(db, tenant_uuid, status="evaluating"):
            offline_experiments = [
                e for e in learning_experiment_service.list_experiments(db, tenant_uuid, candidate_id=c.id)
                if e.experiment_type == "offline" and e.status == "completed"
            ]
            if not offline_experiments:
                continue
            all_insufficient = all(e.is_significant == "insufficient_data" for e in offline_experiments)
            if not all_insufficient:
                continue

            # Auto-reject after MAX_INSUFFICIENT_DATA_CYCLES attempts
            if len(offline_experiments) >= MAX_INSUFFICIENT_DATA_CYCLES:
                try:
                    learning_experiment_service.reject_candidate(
                        db, tenant_uuid, c.id,
                        reason=f"Auto-rejected: {len(offline_experiments)} consecutive cycles with insufficient data",
                    )
                    logger.info("Auto-rejected candidate %s after %d insufficient-data cycles",
                                str(c.id)[:8], len(offline_experiments))
                except Exception:
                    pass
                continue

            evaluating_stuck.append(c)

        candidates_to_evaluate = list(proposed) + evaluating_stuck

        evaluated = 0
        for candidate in candidates_to_evaluate:
            # Skip if already has a non-insufficient-data offline experiment
            existing_experiments = learning_experiment_service.list_experiments(
                db, tenant_uuid, candidate_id=candidate.id
            )
            has_conclusive = any(
                e.experiment_type == "offline" and e.status == "completed"
                and e.is_significant in ("yes", "no")
                for e in existing_experiments
            )
            if has_conclusive:
                continue

            # Create and run offline evaluation
            try:
                experiment = learning_experiment_service.create_experiment(
                    db, tenant_uuid,
                    LearningExperimentCreate(
                        candidate_id=candidate.id,
                        experiment_type=ExperimentType.OFFLINE,
                        min_sample_size=20,
                    ),
                )
                result = learning_experiment_service.run_offline_evaluation(
                    db, tenant_uuid, experiment.id,
                )
                evaluated += 1

                if result and result.get("is_significant") == "yes":
                    logger.info(
                        "Candidate %s passed evaluation: %s",
                        str(candidate.id)[:8], result.get("conclusion", ""),
                    )
                elif result and result.get("is_significant") == "no":
                    # Conclusive but not significant — auto-reject
                    learning_experiment_service.reject_candidate(
                        db, tenant_uuid, candidate.id,
                        reason=f"Offline evaluation not significant: {result.get('conclusion', '')}",
                    )
                    logger.info(
                        "Auto-rejected candidate %s: not significant",
                        str(candidate.id)[:8],
                    )
                elif result and result.get("improvement_pct") is not None:
                    if result["improvement_pct"] < -5.0:
                        # Auto-reject regressions
                        learning_experiment_service.reject_candidate(
                            db, tenant_uuid, candidate.id,
                            reason=f"Offline evaluation shows regression: {result['conclusion']}",
                        )
                        logger.info(
                            "Auto-rejected candidate %s: regression",
                            str(candidate.id)[:8],
                        )
            except Exception as e:
                logger.warning(
                    "Failed to evaluate candidate %s: %s",
                    str(candidate.id)[:8], e,
                )

        return {
            "generated": len(generated),
            "evaluated": evaluated,
        }
    except Exception as e:
        logger.error("generate_and_evaluate failed for %s: %s", tenant_id[:8], e)
        raise
    finally:
        db.close()


@activity.defn(name="manage_active_rollouts")
async def manage_active_rollouts(tenant_id: str) -> dict:
    """Check active rollouts and start new ones for passing candidates."""
    from app.db.session import SessionLocal
    from app.services import learning_experiment_service, policy_rollout_service
    from app.models.learning_experiment import LearningExperiment, PolicyCandidate
    import uuid

    db = SessionLocal()
    try:
        tenant_uuid = uuid.UUID(tenant_id)
        managed = 0

        # Check if any evaluating candidates passed and can start rollouts
        evaluating = learning_experiment_service.list_candidates(
            db, tenant_uuid, status="evaluating"
        )

        for candidate in evaluating:
            # Check if it has a successful offline evaluation
            experiments = learning_experiment_service.list_experiments(
                db, tenant_uuid, candidate_id=candidate.id
            )
            has_significant_offline = any(
                e.status == "completed" and e.is_significant == "yes"
                and e.experiment_type == "offline"
                for e in experiments
            )
            has_running_rollout = any(
                e.status == "running" and e.experiment_type == "split"
                for e in experiments
            )
            has_completed_rollout = any(
                e.status in ("completed", "aborted") and e.experiment_type == "split"
                for e in experiments
            )

            # Only start a rollout if: passed offline eval, no running rollout,
            # and never had a completed/aborted rollout (one shot per candidate)
            if has_significant_offline and not has_running_rollout and not has_completed_rollout:
                # Try to start a rollout
                try:
                    policy_rollout_service.start_rollout(
                        db, tenant_uuid, candidate.id,
                        rollout_pct=0.10,
                        experiment_type="split",
                        min_sample_size=30,
                        max_duration_hours=168,
                    )
                    managed += 1
                    logger.info(
                        "Started rollout for candidate %s",
                        str(candidate.id)[:8],
                    )
                except ValueError as e:
                    logger.debug("Could not start rollout for %s: %s", str(candidate.id)[:8], e)

        # Check completed rollouts for auto-promotion
        completed_rollouts = (
            db.query(LearningExperiment)
            .filter(
                LearningExperiment.tenant_id == tenant_uuid,
                LearningExperiment.status == "completed",
                LearningExperiment.experiment_type == "split",
                LearningExperiment.is_significant == "yes",
            )
            .all()
        )

        for rollout in completed_rollouts:
            candidate = (
                db.query(PolicyCandidate)
                .filter(PolicyCandidate.id == rollout.candidate_id)
                .first()
            )
            if candidate and candidate.status == "evaluating":
                try:
                    learning_experiment_service.promote_candidate(
                        db, tenant_uuid, candidate.id,
                    )
                    managed += 1
                    logger.info(
                        "Auto-promoted candidate %s after successful rollout",
                        str(candidate.id)[:8],
                    )
                except ValueError as e:
                    logger.debug("Could not auto-promote %s: %s", str(candidate.id)[:8], e)

        return {"managed": managed}
    except Exception as e:
        logger.error("manage_active_rollouts failed for %s: %s", tenant_id[:8], e)
        raise
    finally:
        db.close()


@activity.defn(name="generate_morning_report")
async def generate_morning_report(
    tenant_id: str,
    cycle_result: dict,
) -> dict:
    """Generate and persist a morning learning report as a notification."""
    from app.db.session import SessionLocal
    from app.models.notification import Notification
    from app.services import learning_dashboard_service
    from sqlalchemy import text
    import uuid

    db = SessionLocal()
    try:
        tenant_uuid = uuid.UUID(tenant_id)

        # Gather dashboard data
        improvements = learning_dashboard_service.get_policy_improvement_summary(db, tenant_uuid)
        stalls = learning_dashboard_service.get_learning_stalls(db, tenant_uuid)
        explore = learning_dashboard_service.get_explore_exploit_balance(db, tenant_uuid, days=7)
        rollouts = learning_dashboard_service.get_rollout_status(db, tenant_uuid)

        # Build structured report
        lines = [f"Morning Learning Report — {datetime.utcnow().strftime('%Y-%m-%d')}"]
        lines.append("")

        # What improved
        if improvements.get("improvements"):
            lines.append("What Improved:")
            for imp in improvements["improvements"][:3]:
                pct = imp.get("actual_improvement_pct")
                pct_str = f" ({pct:+.1f}%)" if pct is not None else ""
                lines.append(f"  {imp['description']}{pct_str}")
            lines.append("")

        # Active experiments
        if rollouts.get("running_rollouts"):
            lines.append("Active Experiments:")
            for r in rollouts["running_rollouts"]:
                lines.append(
                    f"  {r.get('decision_point', '?')}: "
                    f"day {((datetime.utcnow() - datetime.fromisoformat(r['started_at'])).days if r.get('started_at') else 0)}/7, "
                    f"n={r['treatment']['n']}+{r['control']['n']}"
                )
            lines.append("")

        # Candidates generated this cycle
        gen = cycle_result.get("candidates_generated", 0)
        evl = cycle_result.get("candidates_evaluated", 0)
        if gen or evl:
            lines.append(f"This Cycle: {gen} candidates generated, {evl} evaluated")
            lines.append("")

        # Stalled areas
        stalled = stalls.get("stalled_decision_points", [])
        if stalled:
            lines.append("Stalled:")
            for s in stalled[:3]:
                lines.append(f"  {s['decision_point']}: {s['total_candidates']} candidates, no recent promotion")
            lines.append("")

        # Health summary
        metrics = cycle_result.get("metrics", {})
        rl = metrics.get("rl", {})
        goals = metrics.get("goals", {})
        ws = metrics.get("world_state", {})
        commitments = metrics.get("commitments", {})

        lines.append("Health:")
        if rl.get("last_24h_avg_reward") is not None:
            lines.append(f"  RL: {rl['last_24h_rated']} rated, avg={rl['last_24h_avg_reward']:.3f}")
        if goals:
            lines.append(f"  Goals: {goals.get('active', 0)} active, {goals.get('blocked', 0)} blocked")
        if commitments.get("overdue", 0) > 0:
            lines.append(f"  Commitments: {commitments['overdue']} overdue")
        if ws.get("disputed", 0) > 0:
            lines.append(f"  World state: {ws['disputed']} disputed assertions")

        # Simulation results section
        sim_executed = cycle_result.get("simulation_executed", 0)
        sim_avg = cycle_result.get("simulation_avg_score")
        gaps_detected = cycle_result.get("skill_gaps_detected", 0)
        if sim_executed:
            avg_str = f", avg_score={sim_avg:.2f}" if sim_avg is not None else ""
            lines.append(f"Simulation: {sim_executed} scenarios run{avg_str}")
            lines.append("")

        # Skill gaps section
        if gaps_detected:
            lines.append(f"Skill Gaps Detected: {gaps_detected} new gap(s)")
            try:
                today = datetime.utcnow().date()
                gap_rows = db.execute(text("""
                    SELECT industry, description, severity
                    FROM skill_gaps
                    WHERE tenant_id = CAST(:tid AS uuid)
                      AND DATE(detected_at) = :today
                    ORDER BY
                        CASE severity WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
                        detected_at DESC
                    LIMIT 3
                """), {"tid": tenant_id, "today": today}).fetchall()
                for gap in gap_rows:
                    ind = f"[{gap.industry}] " if gap.industry else ""
                    lines.append(f"  {ind}{gap.description[:80]} ({gap.severity})")
            except Exception:
                pass
            lines.append("")

        # Proactive actions
        proactive = cycle_result.get("proactive_actions", 0)
        if proactive:
            lines.append(f"Proactive Actions Queued: {proactive} nudge(s)/briefing(s)")
            lines.append("")

        # Regression alerts
        regressions = cycle_result.get("regressions_detected", 0)
        if regressions:
            lines.append(f"REGRESSION ALERTS: {regressions} promoted policy candidate(s) reverted")
            lines.append("")

        # Diagnosis summary
        diagnosis = cycle_result.get("diagnosis", {})
        if diagnosis:
            health = diagnosis.get("overall_health", "unknown")
            failure_rate = diagnosis.get("failure_rate", 0)
            lines.append(f"Platform Health: {health} (simulation failure rate: {failure_rate:.0%})")
            lines.append("")

        # Errors
        errors = cycle_result.get("errors", [])
        if errors:
            lines.append("")
            lines.append(f"Errors ({len(errors)}):")
            for err in errors[:3]:
                lines.append(f"  {err}")

        report_text = "\n".join(lines)

        # Persist as notification
        notification = Notification(
            tenant_id=tenant_id,
            source="autonomous_learning",
            title=f"Morning Learning Report — {datetime.utcnow().strftime('%Y-%m-%d')}",
            body=report_text,
            priority="medium",
            reference_id=f"learning_report:{datetime.utcnow().strftime('%Y-%m-%d')}",
            reference_type="learning_report",
        )
        db.add(notification)
        db.commit()

        logger.info("Morning report generated for tenant %s (%d chars)", tenant_id[:8], len(report_text))

        # Attempt WhatsApp delivery — condensed version for mobile readability
        whatsapp_sent = False
        try:
            wa_number = db.execute(text("""
                SELECT phone_number FROM channel_accounts
                WHERE tenant_id = CAST(:tid AS uuid)
                  AND channel_type = 'whatsapp'
                  AND status = 'connected'
                  AND phone_number IS NOT NULL
                ORDER BY updated_at DESC
                LIMIT 1
            """), {"tid": tenant_id}).scalar()

            if wa_number:
                from app.services.whatsapp_service import whatsapp_service
                condensed = _condense_report_for_whatsapp(report_text)
                result = await whatsapp_service.send_message(
                    tenant_id=tenant_id,
                    to=wa_number,
                    message=condensed,
                )
                whatsapp_sent = result.get("status") == "sent"
                if not whatsapp_sent:
                    logger.warning(
                        "WhatsApp delivery failed for tenant %s: %s",
                        tenant_id[:8], result.get("error"),
                    )
                else:
                    logger.info(
                        "Morning report sent via WhatsApp to ***%s (tenant %s)",
                        wa_number[-4:], tenant_id[:8],
                    )
        except Exception as wa_err:
            logger.warning("WhatsApp morning report error for %s: %s", tenant_id[:8], wa_err)

        return {
            "sent": True,
            "whatsapp_sent": whatsapp_sent,
            "report_length": len(report_text),
            "report_preview": report_text[:500],
        }
    except Exception as e:
        logger.error("generate_morning_report failed for %s: %s", tenant_id[:8], e)
        return {"sent": False, "error": str(e)}
    finally:
        db.close()


def _condense_report_for_whatsapp(report_text: str) -> str:
    """Return a condensed ≤900 char version of the morning report for WhatsApp."""
    lines = [ln for ln in report_text.splitlines() if ln.strip()]
    # Keep the header + key sections, truncate body
    condensed_lines = []
    for ln in lines:
        condensed_lines.append(ln)
        total = sum(len(l) + 1 for l in condensed_lines)
        if total >= 800:
            condensed_lines.append("… (full report in your notifications)")
            break
    return "\n".join(condensed_lines)
