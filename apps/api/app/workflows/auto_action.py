"""
Temporal workflow for memory-triggered automated actions.

Routes through Luna (root supervisor) to delegate to the appropriate
sub-agent team: personal assistant, sales, marketing, data, dev.
"""
from temporalio import workflow
from datetime import timedelta
from dataclasses import dataclass


@dataclass
class AutoActionInput:
    tenant_id: str
    action_type: str       # "reply_email", "send_whatsapp", "research", "analyze", "create_task"
    entity_id: str          # Target entity ID or name
    context: str            # What to act on / instruction
    target_agent: str = ""  # "personal_assistant", "sales_agent", "web_researcher", etc.
    delay_hours: int = 0    # Optional delay before execution


@workflow.defn(sandboxed=False)
class AutoActionWorkflow:
    """Execute automated actions by routing through Luna's sub-agent teams.

    Memory triggers (reminders, auto-replies, research tasks) are dispatched
    here and executed via the agent system.
    """

    @workflow.run
    async def run(self, input: AutoActionInput) -> dict:
        workflow.logger.info(
            f"AutoAction: {input.action_type} for entity {input.entity_id} "
            f"(target: {input.target_agent or 'auto'})"
        )

        try:
            # Optional delay
            if input.delay_hours > 0:
                await workflow.sleep(timedelta(hours=input.delay_hours))

            # Execute the action via activity
            result = await workflow.execute_activity(
                "execute_auto_action",
                args=[input],
                start_to_close_timeout=timedelta(minutes=10),
                schedule_to_close_timeout=timedelta(minutes=20),
                retry_policy=workflow.RetryPolicy(
                    maximum_attempts=3,
                    initial_interval=timedelta(seconds=30),
                    maximum_interval=timedelta(seconds=60),
                ),
            )

            return result
        except Exception as e:
            workflow.logger.error(
                f"AutoActionWorkflow failed: {e} "
                f"[entity_id={input.entity_id}, tenant_id={input.tenant_id}, "
                f"action_type={input.action_type}, target_agent={input.target_agent or 'auto'}]"
            )
            return {
                "status": "failed",
                "error": str(e),
                "entity_id": input.entity_id,
                "tenant_id": input.tenant_id,
                "action_type": input.action_type,
                "target_agent": input.target_agent or "auto",
            }
