import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio.client import Client
from croniter import croniter

from app.core.config import settings
from app.db.session import async_session_factory
from app.models.data_pipeline import DataPipeline
from app.models.pipeline_run import PipelineRun
from app.services.dynamic_workflow_launcher import start_dynamic_workflow_by_name

logger = logging.getLogger(__name__)

class SchedulerWorker:
    def __init__(self):
        self.running = False
        self.temporal_client: Optional[Client] = None

    async def start(self):
        """Start the scheduler worker."""
        logger.info("Starting Scheduler Worker...")
        self.running = True

        # Connect to Temporal
        try:
            self.temporal_client = await Client.connect(settings.TEMPORAL_ADDRESS)
            logger.info(f"Connected to Temporal at {settings.TEMPORAL_ADDRESS}")
        except Exception as e:
            logger.error(f"Failed to connect to Temporal: {e}")
            return

        while self.running:
            try:
                await self.check_and_trigger_pipelines()
            except Exception as e:
                logger.error(f"Error in scheduler loop: {e}")

            # Sleep for 60 seconds
            await asyncio.sleep(60)

    async def stop(self):
        """Stop the scheduler worker."""
        self.running = False
        logger.info("Scheduler Worker stopped")

    async def check_and_trigger_pipelines(self):
        """Check for due pipelines and trigger them."""
        async with async_session_factory() as session:
            # Find active pipelines that are due
            # Logic: next_run_at <= now OR (next_run_at is NULL AND is_active=True)
            # For simplicity, we'll fetch all active pipelines and check in python
            # In production, this should be a DB query

            stmt = select(DataPipeline).where(DataPipeline.is_active.is_(True))
            result = await session.execute(stmt)
            pipelines = result.scalars().all()

            now = datetime.utcnow()

            for pipeline in pipelines:
                if self.is_pipeline_due(pipeline, now):
                    await self.trigger_pipeline(session, pipeline)

    def is_pipeline_due(self, pipeline: DataPipeline, now: datetime) -> bool:
        """Check if a pipeline is due for execution."""
        if not pipeline.is_active:
            return False

        # If never run and has schedule, it's due (or we can set next_run_at on creation)
        if not pipeline.next_run_at:
            # Calculate next run immediately if not set
            return True

        return pipeline.next_run_at <= now

    async def trigger_pipeline(self, session: AsyncSession, pipeline: DataPipeline):
        """Trigger a pipeline execution."""
        logger.info(f"Triggering pipeline {pipeline.id} ({pipeline.name})")

        # 1. Calculate next run time
        next_run = self.calculate_next_run(pipeline)

        # 2. Create PipelineRun record
        run_id = uuid.uuid4()
        pipeline_run = PipelineRun(
            id=run_id,
            pipeline_id=pipeline.id,
            status="running",
            started_at=datetime.utcnow()
        )
        session.add(pipeline_run)

        # 3. Update Pipeline next_run_at
        pipeline.next_run_at = next_run
        pipeline.last_run_at = datetime.utcnow()
        pipeline.last_run_status = "running"

        await session.commit()

        # 4. Trigger Dynamic Workflow
        try:
            temporal_wf_id = await start_dynamic_workflow_by_name(
                "Data Source Sync",
                str(pipeline.tenant_id),
                {"data_source_id": str(pipeline.data_source_id)},
            )

            # Update run with workflow ID
            pipeline_run.workflow_id = temporal_wf_id
            await session.commit()

            logger.info(f"Started workflow {temporal_wf_id} for pipeline {pipeline.id}")

        except Exception as e:
            logger.error(f"Failed to start workflow for pipeline {pipeline.id}: {e}")
            pipeline_run.status = "failed"
            pipeline_run.error = str(e)
            pipeline_run.completed_at = datetime.utcnow()

            pipeline.last_run_status = "failed"
            await session.commit()

    def calculate_next_run(self, pipeline: DataPipeline) -> datetime:
        """Calculate the next run time based on schedule."""
        now = datetime.utcnow()

        if pipeline.schedule_type == "cron" and pipeline.cron_expression:
            try:
                cron = croniter(pipeline.cron_expression, now)
                return cron.get_next(datetime)
            except Exception as e:
                logger.error(f"Invalid cron expression for pipeline {pipeline.id}: {e}")
                return now + timedelta(hours=1) # Default fallback

        elif pipeline.schedule_type == "interval" and pipeline.interval_seconds:
            return now + timedelta(seconds=pipeline.interval_seconds)

        # Default or manual
        return now + timedelta(days=1)

if __name__ == "__main__":
    import uuid # Needed for the trigger_pipeline method

    # Run the scheduler
    worker = SchedulerWorker()
    asyncio.run(worker.start())
