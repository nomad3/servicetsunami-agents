"""
Temporal worker for Databricks synchronization workflows
"""

import asyncio
from temporalio.client import Client
from temporalio.worker import Worker

from app.core.config import settings
from app.workflows.dataset_sync import DatasetSyncWorkflow
from app.workflows.knowledge_extraction import KnowledgeExtractionWorkflow
from app.workflows.data_source_sync import DataSourceSyncWorkflow, ScheduledSyncWorkflow
from app.workflows.activities.databricks_sync import (
    sync_to_bronze,
    transform_to_silver,
    update_dataset_metadata
)
from app.workflows.activities.knowledge_extraction import extract_knowledge_from_session
from app.workflows.activities.connectors.extract import (
    extract_from_connector,
    load_to_bronze,
    load_to_silver,
    update_sync_metadata
)
from app.workflows.embedding_backfill_workflow import EmbeddingBackfillWorkflow
from app.workflows.activities.embedding_backfill import (
    backfill_entity_embeddings,
    backfill_memory_embeddings,
    backfill_observation_embeddings,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)


async def run_databricks_worker():
    """
    Start Temporal worker for Databricks workflows

    This worker processes:
    - DatasetSyncWorkflow
    - KnowledgeExtractionWorkflow
    - DataSourceSyncWorkflow (generic connector sync)
    - ScheduledSyncWorkflow
    - Related activities

    Task queue: servicetsunami-databricks
    """
    # Connect to Temporal server
    client = await Client.connect(settings.TEMPORAL_ADDRESS)

    logger.info("Starting Databricks Temporal worker...")
    logger.info(f"Temporal address: {settings.TEMPORAL_ADDRESS}")
    logger.info("Task queue: servicetsunami-databricks")

    # Create and run worker
    worker = Worker(
        client,
        task_queue="servicetsunami-databricks",
        workflows=[
            DatasetSyncWorkflow,
            KnowledgeExtractionWorkflow,
            DataSourceSyncWorkflow,
            ScheduledSyncWorkflow,
            EmbeddingBackfillWorkflow,
        ],
        activities=[
            # Existing activities
            sync_to_bronze,
            transform_to_silver,
            update_dataset_metadata,
            extract_knowledge_from_session,
            # Connector sync activities
            extract_from_connector,
            load_to_bronze,
            load_to_silver,
            update_sync_metadata,
            # Embedding backfill activities
            backfill_entity_embeddings,
            backfill_memory_embeddings,
            backfill_observation_embeddings,
        ]
    )

    logger.info("Databricks worker started successfully")
    await worker.run()


if __name__ == "__main__":
    """Run worker as standalone process"""
    asyncio.run(run_databricks_worker())
