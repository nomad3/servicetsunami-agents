"""EpisodeWorkflow — generates a summary for a conversation window."""
from datetime import timedelta
from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from app.workflows.activities.episode_activities import (
        fetch_window_messages,
        summarize_window,
        embed_and_store_episode,
    )


@workflow.defn
class EpisodeWorkflow:
    @workflow.run
    async def run(
        self,
        tenant_id: str,
        chat_session_id: str,
        window_start_iso: str,
        window_end_iso: str,
        trigger_reason: str,
    ) -> dict:
        """Run activities to fetch, summarize, and store a conversation episode."""
        retry = RetryPolicy(maximum_attempts=3)
        timeout = timedelta(seconds=120)

        # 1. Fetch messages in the window
        msgs = await workflow.execute_activity(
            fetch_window_messages,
            args=[chat_session_id, window_start_iso, window_end_iso],
            start_to_close_timeout=timeout,
            retry_policy=retry,
        )
        if not msgs or len(msgs) < 2:
            return {"created": False, "reason": "too_few_messages", "count": len(msgs) if msgs else 0}

        # 2. Summarize the conversation window using Gemma4
        summary = await workflow.execute_activity(
            summarize_window,
            args=[msgs],
            start_to_close_timeout=timeout,
            retry_policy=retry,
        )

        # 3. Embed the summary and store the episode record
        episode_id = await workflow.execute_activity(
            embed_and_store_episode,
            args=[
                tenant_id,
                chat_session_id,
                window_start_iso,
                window_end_iso,
                trigger_reason,
                summary,
            ],
            start_to_close_timeout=timeout,
            retry_policy=retry,
        )

        return {
            "created": True,
            "episode_id": episode_id,
            "message_count": len(msgs),
            "mood": summary.get("mood"),
        }
