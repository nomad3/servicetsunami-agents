"""IdleEpisodeScanWorkflow — periodic sweep for idle chat sessions."""
from datetime import timedelta
from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from app.workflows.activities.episode_activities import find_idle_sessions


@workflow.defn
class IdleEpisodeScanWorkflow:
    @workflow.run
    async def run(self, tenant_id: str) -> None:
        """Scan for idle sessions and trigger EpisodeWorkflow."""
        idle_sessions = await workflow.execute_activity(
            find_idle_sessions,
            args=[tenant_id, 10],  # 10 minute idle threshold
            start_to_close_timeout=timedelta(seconds=60),
        )
        
        for session in idle_sessions:
            # Trigger child workflow for each idle session
            await workflow.start_child_workflow(
                "EpisodeWorkflow",
                args=[
                    tenant_id,
                    session["id"],
                    session["window_start"],
                    session["window_end"],
                    "idle_timeout",
                ],
                id=f"episode-{session['id']}-{session['window_start'][:10]}",
                task_queue="servicetsunami-orchestration",
                parent_close_policy=workflow.ParentClosePolicy.ABANDON,
            )
            
        # Sleep for one hour before next sweep
        await workflow.sleep(timedelta(hours=1))
        
        # Continue as new to keep workflow history bounded
        workflow.continue_as_new(args=[tenant_id])
