"""LearnFromMediaWorkflow — orchestrates the Luna Learn pipeline (spec §1.10)."""
from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from app.workflows.activities import learn_from_media_activities as A  # noqa: F401


@workflow.defn(name="LearnFromMediaWorkflow")
class LearnFromMediaWorkflow:
    @workflow.run
    async def run(self, intent_dict: dict) -> dict:
        # T3.2 implements the actual orchestration body.
        raise NotImplementedError("T3.2")
