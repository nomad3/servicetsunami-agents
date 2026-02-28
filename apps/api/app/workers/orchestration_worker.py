"""
Temporal worker for orchestration engine task execution workflows
"""

import asyncio
from temporalio.client import Client
from temporalio.worker import Worker

from app.core.config import settings
from app.workflows.task_execution import TaskExecutionWorkflow
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

    Task queue: servicetsunami-orchestration
    """
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
        ],
    )

    logger.info("Orchestration worker started successfully")
    await worker.run()


if __name__ == "__main__":
    """Run worker as standalone process"""
    asyncio.run(run_orchestration_worker())
