"""Temporal worker for code tasks -- runs Claude Code CLI."""

import asyncio
import logging
import os

from temporalio.client import Client
from temporalio.worker import Worker
from temporalio.worker.workflow_sandbox import (
    SandboxedWorkflowRunner,
    SandboxRestrictions,
)

from workflows import CodeTaskWorkflow, execute_code_task, ChatCliWorkflow, execute_chat_cli
from session_manager import get_session_manager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TEMPORAL_ADDRESS = os.environ.get("TEMPORAL_ADDRESS", "temporal:7233")
TASK_QUEUE = "servicetsunami-code"


async def main():
    # Start the persistent session manager
    manager = get_session_manager()
    await manager.start()

    logger.info("Connecting to Temporal at %s", TEMPORAL_ADDRESS)
    client = await Client.connect(TEMPORAL_ADDRESS)

    logger.info("Starting code worker on queue '%s'", TASK_QUEUE)
    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[CodeTaskWorkflow, ChatCliWorkflow],
        activities=[execute_code_task, execute_chat_cli],
        workflow_runner=SandboxedWorkflowRunner(
            restrictions=SandboxRestrictions.default.with_passthrough_modules(
                "httpx", "subprocess",
            )
        ),
    )
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
