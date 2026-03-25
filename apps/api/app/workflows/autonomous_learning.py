"""Autonomous Learning Workflow — the nightly heartbeat.

Long-running workflow (one per tenant) that runs the self-improvement
pipeline: collect metrics → generate candidates → evaluate offline →
manage rollouts → morning report. Uses continue_as_new every cycle.

Queue: servicetsunami-orchestration
Workflow ID: autonomous-learning-{tenant_id}
"""
from temporalio import workflow
from temporalio.common import RetryPolicy
from datetime import timedelta
from typing import Optional


@workflow.defn(sandboxed=False)
class AutonomousLearningWorkflow:
    """Nightly self-improvement cycle. One instance per tenant.

    Default cycle: every 24h at ~02:00 UTC.
    Activities:
      1. collect_learning_metrics
      2. generate_and_evaluate_candidates
      3. manage_active_rollouts
      4. generate_morning_report
    """

    @workflow.run
    async def run(
        self,
        tenant_id: str,
        cycle_interval_seconds: int = 86400,  # 24h default
        last_cycle_summary: Optional[str] = None,
    ) -> dict:
        retry_policy = RetryPolicy(
            maximum_attempts=3,
            initial_interval=timedelta(seconds=30),
            backoff_coefficient=2.0,
            maximum_interval=timedelta(seconds=120),
        )
        activity_timeout = timedelta(minutes=5)

        workflow.logger.info(
            f"Autonomous learning cycle starting for tenant {tenant_id[:8]}"
        )

        cycle_result = {
            "tenant_id": tenant_id,
            "metrics": {},
            "candidates_generated": 0,
            "candidates_evaluated": 0,
            "rollouts_managed": 0,
            # Self-simulation fields
            "personas_selected": 0,
            "scenarios_generated": 0,
            "simulation_executed": 0,
            "simulation_avg_score": None,
            "skill_gaps_detected": 0,
            # Proactive agent fields
            "proactive_actions": 0,
            # Feedback + diagnosis fields
            "feedback_processed": 0,
            "feedback_applied": 0,
            "diagnosis": {},
            "regressions_detected": 0,
            # Phase 6 fields
            "skill_stubs_created": 0,
            # Cost tracking
            "cost_usd": 0.0,
            "budget_exceeded": False,
            "report_sent": False,
            "errors": [],
        }

        # Step 1: Collect learning metrics
        try:
            metrics = await workflow.execute_activity(
                "collect_learning_metrics",
                args=[tenant_id],
                start_to_close_timeout=activity_timeout,
                retry_policy=retry_policy,
            )
            cycle_result["metrics"] = metrics
        except Exception as e:
            workflow.logger.error(f"Step 1 (collect_learning_metrics) failed: {e}")
            cycle_result["errors"].append(f"collect_metrics: {e}")

        # Step 2: Generate and evaluate candidates
        try:
            eval_result = await workflow.execute_activity(
                "generate_and_evaluate_candidates",
                args=[tenant_id, cycle_result.get("metrics", {})],
                start_to_close_timeout=timedelta(minutes=10),
                retry_policy=retry_policy,
            )
            cycle_result["candidates_generated"] = eval_result.get("generated", 0)
            cycle_result["candidates_evaluated"] = eval_result.get("evaluated", 0)
        except Exception as e:
            workflow.logger.error(f"Step 2 (generate_and_evaluate) failed: {e}")
            cycle_result["errors"].append(f"generate_evaluate: {e}")

        # Step 3: Manage active rollouts
        try:
            rollout_result = await workflow.execute_activity(
                "manage_active_rollouts",
                args=[tenant_id],
                start_to_close_timeout=activity_timeout,
                retry_policy=retry_policy,
            )
            cycle_result["rollouts_managed"] = rollout_result.get("managed", 0)
        except Exception as e:
            workflow.logger.error(f"Step 3 (manage_rollouts) failed: {e}")
            cycle_result["errors"].append(f"manage_rollouts: {e}")

        # Step 3b: Self-simulation
        try:
            persona_result = await workflow.execute_activity(
                "select_personas_for_cycle",
                args=[tenant_id],
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=retry_policy,
            )
            cycle_result["personas_selected"] = persona_result.get("selected", 0)

            if persona_result.get("persona_ids"):
                scenario_result = await workflow.execute_activity(
                    "generate_simulation_scenarios",
                    args=[tenant_id, persona_result["persona_ids"]],
                    start_to_close_timeout=timedelta(minutes=5),
                    retry_policy=retry_policy,
                )
                cycle_result["scenarios_generated"] = scenario_result.get("scenarios_created", 0)

            exec_result = await workflow.execute_activity(
                "execute_simulation_scenarios",
                args=[tenant_id],
                start_to_close_timeout=timedelta(minutes=30),
                retry_policy=RetryPolicy(maximum_attempts=1),
            )
            cycle_result["simulation_avg_score"] = exec_result.get("avg_score")
            cycle_result["simulation_executed"] = exec_result.get("executed", 0)

            failure_data = await workflow.execute_activity(
                "classify_simulation_failures",
                args=[tenant_id],
                start_to_close_timeout=timedelta(minutes=3),
                retry_policy=retry_policy,
            )
            gap_result = await workflow.execute_activity(
                "detect_skill_gaps",
                args=[tenant_id, failure_data],
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=retry_policy,
            )
            cycle_result["skill_gaps_detected"] = gap_result.get("gaps_detected", 0)
        except Exception as e:
            workflow.logger.error(f"Step 3b (self-simulation) failed: {e}")
            cycle_result["errors"].append(f"self_simulation: {e}")

        # Step 3c: Proactive actions
        try:
            proactive_result = await workflow.execute_activity(
                "scan_for_proactive_actions",
                args=[tenant_id],
                start_to_close_timeout=timedelta(minutes=3),
                retry_policy=retry_policy,
            )
            await workflow.execute_activity(
                "send_proactive_notifications",
                args=[tenant_id],
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=retry_policy,
            )
            cycle_result["proactive_actions"] = proactive_result.get("actions_queued", 0)
        except Exception as e:
            workflow.logger.error(f"Step 3c (proactive actions) failed: {e}")
            cycle_result["errors"].append(f"proactive: {e}")

        # Step 3d: Feedback + diagnosis
        try:
            feedback_result = await workflow.execute_activity(
                "process_human_feedback",
                args=[tenant_id],
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=retry_policy,
            )
            cycle_result["feedback_processed"] = feedback_result.get("feedback_processed", 0)

            # Apply collected feedback to candidates and exploration config
            apply_result = await workflow.execute_activity(
                "apply_feedback_to_cycle",
                args=[tenant_id],
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=retry_policy,
            )
            cycle_result["feedback_applied"] = apply_result.get("feedback_applied", 0)

            diagnosis = await workflow.execute_activity(
                "run_self_diagnosis",
                args=[tenant_id],
                start_to_close_timeout=timedelta(minutes=3),
                retry_policy=retry_policy,
            )
            cycle_result["diagnosis"] = diagnosis

            regression_result = await workflow.execute_activity(
                "monitor_regression",
                args=[tenant_id],
                start_to_close_timeout=timedelta(minutes=3),
                retry_policy=retry_policy,
            )
            cycle_result["regressions_detected"] = regression_result.get("regressions_detected", 0)

            # Tune per-decision-point exploration rates based on stall/performance data
            await workflow.execute_activity(
                "adjust_exploration_rates",
                args=[tenant_id, cycle_result.get("metrics", {})],
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=retry_policy,
            )
        except Exception as e:
            workflow.logger.error(f"Step 3d (feedback/diagnosis) failed: {e}")
            cycle_result["errors"].append(f"feedback_diagnosis: {e}")

        # Step 3e: Skill auto-creation from gaps (Phase 6)
        try:
            stub_result = await workflow.execute_activity(
                "auto_create_skill_stubs",
                args=[tenant_id],
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=retry_policy,
            )
            cycle_result["skill_stubs_created"] = stub_result.get("stubs_created", 0)
        except Exception as e:
            workflow.logger.error(f"Step 3e (skill stubs) failed: {e}")
            cycle_result["errors"].append(f"skill_stubs: {e}")

        # Step 3f: Track cycle cost against budget
        try:
            cost_result = await workflow.execute_activity(
                "track_cycle_cost",
                args=[tenant_id, cycle_result],
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=retry_policy,
            )
            cycle_result["cost_usd"] = cost_result.get("cost_usd", 0.0)
            cycle_result["budget_exceeded"] = cost_result.get("budget_exceeded", False)
        except Exception as e:
            workflow.logger.error(f"Step 3f (cost tracking) failed: {e}")
            cycle_result["errors"].append(f"cost_tracking: {e}")

        # Step 4: Generate and send morning report
        try:
            report_result = await workflow.execute_activity(
                "generate_morning_report",
                args=[tenant_id, cycle_result],
                start_to_close_timeout=activity_timeout,
                retry_policy=retry_policy,
            )
            cycle_result["report_sent"] = report_result.get("sent", False)
        except Exception as e:
            workflow.logger.error(f"Step 4 (morning_report) failed: {e}")
            cycle_result["errors"].append(f"morning_report: {e}")

        summary = (
            f"cycle complete: {cycle_result['candidates_generated']} generated, "
            f"{cycle_result['candidates_evaluated']} evaluated, "
            f"{cycle_result['rollouts_managed']} rollouts managed, "
            f"simulations={cycle_result['simulation_executed']} "
            f"(avg={cycle_result['simulation_avg_score']}), "
            f"gaps={cycle_result['skill_gaps_detected']}, "
            f"proactive={cycle_result['proactive_actions']}, "
            f"regressions={cycle_result['regressions_detected']}, "
            f"stubs={cycle_result['skill_stubs_created']}, "
            f"cost=${cycle_result['cost_usd']:.4f}, "
            f"report={'sent' if cycle_result['report_sent'] else 'failed'}, "
            f"errors={len(cycle_result['errors'])}"
        )
        workflow.logger.info(f"Autonomous learning: {summary}")

        # Sleep until next cycle
        await workflow.sleep(timedelta(seconds=cycle_interval_seconds))
        workflow.continue_as_new(args=[
            tenant_id,
            cycle_interval_seconds,
            summary,
        ])
