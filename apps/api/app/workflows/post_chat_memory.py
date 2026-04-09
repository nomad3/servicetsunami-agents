"""PostChatMemoryWorkflow — fires async after every chat turn."""
from datetime import timedelta
from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from app.workflows.activities.post_chat_memory_activities import (
        extract_knowledge,
        detect_commitment,
        update_world_state,
        update_behavioral_signals,
        maybe_trigger_episode,
    )


_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=3,
    backoff_coefficient=2.0,
)
_TIMEOUT = timedelta(seconds=60)


@workflow.defn
class PostChatMemoryWorkflow:
    @workflow.run
    async def run(
        self,
        tenant_id: str,
        chat_session_id: str,
        user_message_id: str,
        assistant_message_id: str,
    ) -> dict:
        """Run post-chat memory activities in sequence for tenant isolation."""
        results: dict = {"activities_run": 0, "errors": [], "activity_results": {}}
        args = [tenant_id, chat_session_id, user_message_id, assistant_message_id]

        async def _safe(activity, name):
            try:
                ret = await workflow.execute_activity(
                    activity, args=args,
                    start_to_close_timeout=_TIMEOUT,
                    retry_policy=_RETRY,
                )
                results["activities_run"] += 1
                results["activity_results"][name] = ret
                return ret
            except Exception as e:
                results["errors"].append(f"{name}: {e}")
                return None

        # Activities run sequentially for Phase 1 simplicity
        await _safe(extract_knowledge, "extract_knowledge")
        await _safe(detect_commitment, "detect_commitment")
        await _safe(update_world_state, "update_world_state")
        await _safe(update_behavioral_signals, "update_behavioral_signals")
        ep_signal = await _safe(maybe_trigger_episode, "maybe_trigger_episode")

        # Parent dispatches child workflow if the activity said so.
        if ep_signal and ep_signal.get("should_trigger"):
            window_start = ep_signal["window_start_iso"]
            window_end = ep_signal["window_end_iso"]
            try:
                await workflow.start_child_workflow(
                    "EpisodeWorkflow",
                    args=[tenant_id, chat_session_id, window_start, window_end, "chat_threshold"],
                    id=f"episode-{chat_session_id}-{window_start[:10]}",
                    task_queue="servicetsunami-orchestration",
                    parent_close_policy=workflow.ParentClosePolicy.ABANDON,
                )
                results["episode_triggered"] = True
            except Exception as e:
                results["errors"].append(f"EpisodeWorkflow dispatch: {e}")

        return results
