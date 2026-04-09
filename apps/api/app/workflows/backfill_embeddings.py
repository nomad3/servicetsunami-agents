"""BackfillEmbeddingsWorkflow — embeds historical chat_messages."""
from datetime import timedelta
from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from app.workflows.activities.backfill_activities import (
        find_unembedded_chat_messages,
        embed_message_batch,
    )


@workflow.defn
class BackfillEmbeddingsWorkflow:
    @workflow.run
    async def run(self, tenant_id: str, batch_size: int = 50, max_batches: int = 200) -> dict:
        """Run backfill activities in a loop."""
        total_embedded = 0
        last_batch_size = 0
        
        for i in range(max_batches):
            # 1. Find a batch of unembedded messages
            batch = await workflow.execute_activity(
                find_unembedded_chat_messages,
                args=[tenant_id, batch_size],
                start_to_close_timeout=timedelta(seconds=30),
            )
            if not batch:
                break
                
            last_batch_size = len(batch)
            
            # 2. Embed and store the batch
            embedded_count = await workflow.execute_activity(
                embed_message_batch,
                args=[batch],
                start_to_close_timeout=timedelta(seconds=120),
            )
            total_embedded += embedded_count
            workflow.logger.info(f"Backfilled {total_embedded} embeddings so far for tenant {tenant_id}")

        # If we might have more messages, continue_as_new to keep history small
        if last_batch_size == batch_size:
            workflow.continue_as_new(args=[tenant_id, batch_size, max_batches])
            
        return {"embedded": total_embedded}
