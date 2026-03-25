"""
Temporal worker for orchestration engine task execution workflows
"""

import asyncio
from temporalio.client import Client
from temporalio.worker import Worker

from app.core.config import settings
from app.workflows.task_execution import TaskExecutionWorkflow
from app.workflows.dynamic_executor import DynamicWorkflowExecutor
from app.workflows.activities.dynamic_step import execute_dynamic_step, finalize_workflow_run
from app.workflows.activities.task_execution import (
    dispatch_task,
    recall_memory,
    execute_task,
    persist_entities,
    evaluate_task,
)
from app.workflows.channel_health import ChannelHealthMonitorWorkflow
from app.workflows.activities.channel_health import (
    check_channel_health,
    reconnect_channel,
    update_channel_health_status,
)
from app.workflows.follow_up import FollowUpWorkflow
from app.workflows.activities.follow_up import execute_followup_action
from app.workflows.monthly_billing import MonthlyBillingWorkflow
from app.workflows.activities.monthly_billing import (
    aggregate_billing_visits,
    generate_billing_invoices,
    send_billing_invoices,
    schedule_billing_followups,
)
from app.workflows.remedia_order import RemediaOrderWorkflow
from app.workflows.activities.remedia import (
    create_remedia_order,
    send_remedia_notification,
    monitor_remedia_payment,
    track_remedia_delivery,
)
from app.workflows.auto_action import AutoActionWorkflow
from app.workflows.deal_pipeline import DealPipelineWorkflow
from app.workflows.activities.hca_activities import (
    hca_discover_prospects,
    hca_score_prospects,
    hca_generate_research,
    hca_generate_outreach,
    hca_advance_pipeline,
    hca_sync_knowledge_graph,
)
from app.workflows.inbox_monitor import InboxMonitorWorkflow
from app.workflows.activities.inbox_monitor import (
    fetch_new_emails,
    fetch_upcoming_events,
    triage_items,
    create_notifications,
    extract_from_emails,
    check_proactive_triggers,
    log_monitor_cycle,
)
from app.workflows.memory_consolidation_workflow import MemoryConsolidationWorkflow
from app.workflows.activities.memory_consolidation import (
    find_duplicate_entities,
    auto_merge_duplicates,
    apply_memory_decay,
    promote_entities,
    sync_memories_and_entities,
    log_consolidation_results,
)
from app.workflows.competitor_monitor import CompetitorMonitorWorkflow
from app.workflows.activities.competitor_monitor import (
    fetch_competitors,
    scrape_competitor_activity,
    check_ad_libraries,
    analyze_competitor_changes,
    store_competitor_observations,
    create_competitor_notifications,
)
from app.workflows.prospecting_pipeline import (
    ProspectingPipelineWorkflow,
    prospect_research,
    prospect_score,
    prospect_qualify,
    prospect_outreach,
    prospect_notify,
)
from app.workflows.goal_review import GoalReviewWorkflow
from app.workflows.activities.goal_review import (
    review_goals,
    review_commitments,
    create_review_notifications,
)
from app.workflows.autonomous_learning import AutonomousLearningWorkflow
from app.workflows.activities.autonomous_learning import (
    collect_learning_metrics,
    generate_and_evaluate_candidates,
    manage_active_rollouts,
    generate_morning_report,
)
from app.workflows.activities.simulation_activities import (
    select_personas_for_cycle,
    generate_simulation_scenarios,
    execute_simulation_scenarios,
    classify_simulation_failures,
    detect_skill_gaps,
)
from app.workflows.activities.proactive_activities import (
    scan_for_proactive_actions,
    send_proactive_notifications,
)
from app.workflows.activities.feedback_activities import (
    process_human_feedback,
    run_self_diagnosis,
    monitor_regression,
    apply_feedback_to_cycle,
    adjust_exploration_rates,
)
from app.workflows.activities.skill_gap_activities import auto_create_skill_stubs
from app.workflows.activities.cost_tracking_activities import track_cycle_cost
from app.workflows.activities.skill_activities import execute_skill
from app.workflows.activities.rl_policy_update import (
    collect_tenant_experiences,
    update_tenant_policy,
    anonymize_and_aggregate_global,
    archive_old_experiences,
)
from app.workflows.activities.git_history import (
    extract_git_history,
    poll_pr_outcomes,
)
from app.workflows.rl_policy_update_workflow import RLPolicyUpdateWorkflow
from app.utils.logger import get_logger

logger = get_logger(__name__)

TASK_QUEUE = "servicetsunami-orchestration"


async def run_orchestration_worker():
    """
    Start Temporal worker for orchestration engine workflows

    This worker processes:
    - TaskExecutionWorkflow (dispatch, recall, execute, persist_entities, evaluate)
    - ChannelHealthMonitorWorkflow (WhatsApp connection health monitoring)
    - FollowUpWorkflow (scheduled sales follow-up actions)
    - AutoActionWorkflow (memory-triggered automated actions via Luna)

    Task queue: servicetsunami-orchestration
    """
    # Scan skills so agent activities can find them
    try:
        from app.services.skill_manager import skill_manager
        skill_manager.scan()
        logger.info("Skill manager: %d skills loaded", len(skill_manager.list_skills()))
    except Exception as e:
        logger.warning("Skill manager scan failed: %s", e)

    # Connect to Temporal server
    client = await Client.connect(settings.TEMPORAL_ADDRESS)

    logger.info("Starting Orchestration Temporal worker...")
    logger.info(f"Temporal address: {settings.TEMPORAL_ADDRESS}")
    logger.info(f"Task queue: {TASK_QUEUE}")

    # Create and run worker
    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[
            TaskExecutionWorkflow,
            ChannelHealthMonitorWorkflow,
            FollowUpWorkflow,
            MonthlyBillingWorkflow,
            RemediaOrderWorkflow,
            DealPipelineWorkflow,
            AutoActionWorkflow,
            InboxMonitorWorkflow,
            CompetitorMonitorWorkflow,
            ProspectingPipelineWorkflow,
            RLPolicyUpdateWorkflow,
            MemoryConsolidationWorkflow,
            DynamicWorkflowExecutor,
            AutonomousLearningWorkflow,
            GoalReviewWorkflow,
        ],
        activities=[
            dispatch_task,
            recall_memory,
            execute_task,
            persist_entities,
            evaluate_task,
            check_channel_health,
            reconnect_channel,
            update_channel_health_status,
            execute_followup_action,
            aggregate_billing_visits,
            generate_billing_invoices,
            send_billing_invoices,
            schedule_billing_followups,
            create_remedia_order,
            send_remedia_notification,
            monitor_remedia_payment,
            track_remedia_delivery,
            hca_discover_prospects,
            hca_score_prospects,
            hca_generate_research,
            hca_generate_outreach,
            hca_advance_pipeline,
            hca_sync_knowledge_graph,
            fetch_new_emails,
            fetch_upcoming_events,
            triage_items,
            create_notifications,
            extract_from_emails,
            check_proactive_triggers,
            log_monitor_cycle,
            fetch_competitors,
            scrape_competitor_activity,
            check_ad_libraries,
            analyze_competitor_changes,
            store_competitor_observations,
            create_competitor_notifications,
            prospect_research,
            prospect_score,
            prospect_qualify,
            prospect_outreach,
            prospect_notify,
            execute_skill,
            collect_tenant_experiences,
            update_tenant_policy,
            anonymize_and_aggregate_global,
            archive_old_experiences,
            extract_git_history,
            poll_pr_outcomes,
            # Memory consolidation activities
            find_duplicate_entities,
            auto_merge_duplicates,
            apply_memory_decay,
            promote_entities,
            sync_memories_and_entities,
            log_consolidation_results,
            # Autonomous learning activities
            collect_learning_metrics,
            generate_and_evaluate_candidates,
            manage_active_rollouts,
            generate_morning_report,
            # Self-simulation activities
            select_personas_for_cycle,
            generate_simulation_scenarios,
            execute_simulation_scenarios,
            classify_simulation_failures,
            detect_skill_gaps,
            # Proactive agent activities
            scan_for_proactive_actions,
            send_proactive_notifications,
            # Feedback + diagnosis activities
            process_human_feedback,
            run_self_diagnosis,
            monitor_regression,
            apply_feedback_to_cycle,
            adjust_exploration_rates,
            # Phase 6: skill auto-creation + cost tracking
            auto_create_skill_stubs,
            track_cycle_cost,
            # Goal review activities
            review_goals,
            review_commitments,
            create_review_notifications,
            # Dynamic workflow step executor
            execute_dynamic_step,
            finalize_workflow_run,
        ],
    )

    logger.info("Orchestration worker started successfully")
    await worker.run()


if __name__ == "__main__":
    """Run worker as standalone process"""
    asyncio.run(run_orchestration_worker())
