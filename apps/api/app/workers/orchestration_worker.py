"""
Temporal worker for orchestration engine task execution workflows.

All static workflow classes have been removed. The DynamicWorkflowExecutor
interprets JSON workflow definitions at runtime, replacing all per-workflow
Python classes. Activity registrations are kept so the executor can dispatch them.
"""

import asyncio
from temporalio.client import Client
from temporalio.worker import Worker

from app.core.config import settings
from app.workflows.dynamic_executor import DynamicWorkflowExecutor
from app.workflows.gap1_journal_synthesis import Gap1JournalSynthesis
from app.workflows.post_chat_memory import PostChatMemoryWorkflow
from app.workflows.episode_workflow import EpisodeWorkflow
from app.workflows.idle_episode_scan import IdleEpisodeScanWorkflow
from app.workflows.teams_monitor import TeamsMonitorWorkflow
from app.workflows.backfill_embeddings import BackfillEmbeddingsWorkflow
from app.workflows.coalition_workflow import CoalitionWorkflow
from app.workflows.activities.dynamic_step import execute_dynamic_step, finalize_workflow_run
from app.workflows.activities.task_execution import (
    dispatch_task,
    recall_memory,
    execute_task,
    persist_entities,
    evaluate_task,
)
from app.workflows.activities.channel_health import (
    check_channel_health,
    reconnect_channel,
    update_channel_health_status,
)
from app.workflows.activities.follow_up import execute_followup_action
from app.workflows.activities.monthly_billing import (
    aggregate_billing_visits,
    generate_billing_invoices,
    send_billing_invoices,
    schedule_billing_followups,
)
from app.workflows.activities.remedia import (
    create_remedia_order,
    send_remedia_notification,
    monitor_remedia_payment,
    track_remedia_delivery,
)
# HCA Deal Intelligence activities removed 2026-04-26 — investment banking
# use case is no longer being pursued. The hca_activities module is kept on
# disk under app/workflows/activities/ for one release cycle in case any
# in-flight Temporal workflows still reference these activity names by string.
from app.workflows.activities.inbox_monitor import (
    fetch_new_emails,
    fetch_upcoming_events,
    triage_items,
    create_notifications,
    extract_from_emails,
    check_proactive_triggers,
    log_monitor_cycle,
)
from app.workflows.activities.teams_monitor import teams_monitor_tick
from app.workflows.activities.memory_consolidation import (
    find_duplicate_entities,
    auto_merge_duplicates,
    apply_memory_decay,
    promote_entities,
    sync_memories_and_entities,
    log_consolidation_results,
)
from app.workflows.activities.aremko_monitor import (
    fetch_aremko_snapshot,
    detect_aremko_changes,
    create_aremko_notifications,
)
from app.workflows.activities.competitor_monitor import (
    fetch_competitors,
    scrape_competitor_activity,
    check_ad_libraries,
    analyze_competitor_changes,
    store_competitor_observations,
    create_competitor_notifications,
)
from app.workflows.activities.prospecting import (
    prospect_research,
    prospect_score,
    prospect_qualify,
    prospect_outreach,
    prospect_notify,
)
from app.workflows.activities.goal_review import (
    review_goals,
    review_commitments,
    create_review_notifications,
)
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
from app.workflows.activities.self_improvement import dispatch_self_improvement_task
from app.workflows.activities.cost_tracking_activities import track_cycle_cost
from app.workflows.activities.skill_activities import execute_skill
from app.workflows.activities.auto_dream_activities import (
    scan_unconsolidated_experiences,
    extract_decision_patterns,
    generate_dream_insights,
    consolidate_dream_policies,
    log_dream_results,
    prune_stale_knowledge,
    learn_user_preferences,
)
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
from app.workflows.activities.morning_briefing import (
    synthesize_morning_briefing,
    create_daily_journal_entry,
    create_weekly_journal_summary,
)
from app.workflows.activities.coalition_activities import (
    select_coalition_template,
    initialize_collaboration,
    prepare_collaboration_step,
    record_collaboration_step,
    finalize_collaboration,
)
from app.workflows.activities.post_chat_memory_activities import (
    detect_commitment,
    update_world_state,
    update_behavioral_signals,
    maybe_trigger_episode,
)
from app.workflows.activities.episode_activities import (
    fetch_window_messages,
    summarize_window,
    embed_and_store_episode,
    find_idle_sessions,
)
from app.workflows.activities.backfill_activities import (
    find_unembedded_chat_messages,
    embed_message_batch,
)
from app.workflows.activities.journal_synthesis import (

    synthesize_daily_journal,
    synthesize_weekly_journal,
)
from app.workflows.activities.inbound_lead_capture import (
    classify_email_as_lead,
    classify_whatsapp_as_lead,
)
from app.workflows.activities.agent_performance import compute_agent_performance_snapshot
from app.workflows.agent_performance_rollup import AgentPerformanceRollupWorkflow
from app.utils.logger import get_logger

logger = get_logger(__name__)

TASK_QUEUE = "agentprovision-orchestration"


async def run_orchestration_worker():
    """
    Start Temporal worker for orchestration engine workflows.

    This worker processes:
    - DynamicWorkflowExecutor (JSON-defined workflows interpreted at runtime)
    - All activities previously registered by static workflow classes

    Task queue: agentprovision-orchestration
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
            DynamicWorkflowExecutor,
            Gap1JournalSynthesis,
            PostChatMemoryWorkflow,
            EpisodeWorkflow,
            IdleEpisodeScanWorkflow,
            BackfillEmbeddingsWorkflow,
            CoalitionWorkflow,
            AgentPerformanceRollupWorkflow,
            TeamsMonitorWorkflow,
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
            fetch_new_emails,
            fetch_upcoming_events,
            triage_items,
            create_notifications,
            extract_from_emails,
            check_proactive_triggers,
            log_monitor_cycle,
            teams_monitor_tick,
            fetch_competitors,
            scrape_competitor_activity,
            fetch_aremko_snapshot,
            detect_aremko_changes,
            create_aremko_notifications,
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
            # Phase 6: skill auto-creation + self-improvement + cost tracking
            auto_create_skill_stubs,
            dispatch_self_improvement_task,
            track_cycle_cost,
            # Goal review activities
            review_goals,
            review_commitments,
            create_review_notifications,
            # Auto-dream RL consolidation activities
            scan_unconsolidated_experiences,
            extract_decision_patterns,
            generate_dream_insights,
            consolidate_dream_policies,
            log_dream_results,
            # Active forgetting + user preference activities
            prune_stale_knowledge,
            learn_user_preferences,
            # Dynamic workflow step executor
            execute_dynamic_step,
            finalize_workflow_run,
            # Morning briefing / Session Journal activities (Gap 1: Continuity)
            synthesize_morning_briefing,
            create_daily_journal_entry,
            create_weekly_journal_summary,
            # Auto-journal synthesis from conversations (Gap 1: auto-population)
            synthesize_daily_journal,
            synthesize_weekly_journal,
            # Inbound lead capture activities (Sales Phase 2: Module 5)
            classify_email_as_lead,
            classify_whatsapp_as_lead,
            # Memory activities (Memory-First Phase 1)
            detect_commitment,
            update_world_state,
            update_behavioral_signals,
            maybe_trigger_episode,
            fetch_window_messages,
            summarize_window,
            embed_and_store_episode,
            find_idle_sessions,
            find_unembedded_chat_messages,
            embed_message_batch,
            # Coalition activities
            select_coalition_template,
            initialize_collaboration,
            prepare_collaboration_step,
            record_collaboration_step,
            finalize_collaboration,
            # Agent lifecycle: performance snapshots
            compute_agent_performance_snapshot,
        ],
    )

    # Start the hourly performance rollup loop (idempotent — no-op if already running)
    try:
        from temporalio.service import RPCError
        await client.start_workflow(
            AgentPerformanceRollupWorkflow.run,
            id="agent-performance-rollup-singleton",
            task_queue=TASK_QUEUE,
        )
        logger.info("AgentPerformanceRollupWorkflow started")
    except Exception as e:
        # Already running or other transient error — not fatal
        logger.debug("AgentPerformanceRollupWorkflow start skipped: %s", e)

    logger.info("Orchestration worker started successfully")
    await worker.run()


if __name__ == "__main__":
    """Run worker as standalone process"""
    asyncio.run(run_orchestration_worker())
