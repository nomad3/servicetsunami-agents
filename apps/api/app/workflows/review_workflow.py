"""ReviewWorkflow — parallel cross-CLI consensus code review.

Companion to CoalitionWorkflow (PR #440). Differs in two ways:

  1. **Parallel fanout, not sequential phases.** All CLIs in
     `review.clis` execute the same review prompt at once via
     `asyncio.gather` over child ChatCliWorkflow handles. CoalitionWorkflow
     iterates phases (planner → critic → verifier). Reviews don't have
     phase ordering — they're a flat consensus vote.

  2. **Aggregation happens server-side in the service layer**, not in
     a finalize activity. Each child workflow's output is POSTed back
     via `record_review_finding` activity, which calls
     review_service.record_cli_findings. The aggregator runs as a
     side-effect of the last CLI reporting.

Stop condition: the workflow finishes when every child has reported.
Round-2/3 dispatch is driven by the operator via POST
/api/v1/reviews/{id}/reply, which re-fires this workflow with the new
ref.

Dependency on #287:
  The child ChatCliWorkflow invocations require a working CLI
  dispatch chain (claude/codex/gemini). Until #287 lands, the
  test suite drives the loop directly via the /record endpoint;
  this workflow is the production plumbing.
"""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from app.workflows.activities.review_activities import (
        load_review_state,
        record_review_finding,
    )


@workflow.defn
class ReviewWorkflow:
    @workflow.run
    async def run(self, payload: dict) -> dict:
        # Accept dict-form (cross-pod dispatch) only — no positional
        # form. Keeps the call sites in review_dispatch.py simple.
        tenant_id = payload.get("tenant_id")
        review_id = payload.get("review_id")
        if not tenant_id or not review_id:
            raise ValueError("ReviewWorkflow requires tenant_id and review_id")

        retry = RetryPolicy(maximum_attempts=3)
        activity_timeout = timedelta(seconds=60)
        cli_timeout = timedelta(minutes=15)

        # 1. Load coalition state — clis list, ref, scope, prompt.
        state = await workflow.execute_activity(
            load_review_state,
            args=[tenant_id, review_id],
            start_to_close_timeout=activity_timeout,
            retry_policy=retry,
        )

        clis = state.get("clis") or []
        prompt = state.get("prompt") or ""
        if not clis:
            return {"status": "no_clis", "review_id": review_id}

        # 2. Fan out: one child ChatCliWorkflow per CLI, in parallel.
        async def _dispatch_one(cli: dict) -> dict:
            try:
                result = await workflow.execute_child_workflow(
                    "ChatCliWorkflow",
                    args=[{
                        "platform": cli.get("name"),
                        "message": prompt,
                        "tenant_id": tenant_id,
                        "instruction_md_content": state.get("instruction_md", ""),
                    }],
                    id=f"review-{review_id}-{cli.get('name')}",
                    task_queue="agentprovision-code",
                    execution_timeout=cli_timeout,
                )
                if not isinstance(result, dict):
                    result = {"response_text": str(result), "success": True}
                text = (
                    result.get("response_text", "")
                    if result.get("success")
                    else f"[CLI error: {result.get('error', 'unknown')}]"
                )
            except Exception as e:
                # Per-CLI failures must not poison the whole review —
                # record an empty finding for that CLI so the consensus
                # aggregator still trips when others finish.
                text = f"[CLI dispatch error: {e}]"

            # 3. Record the output. The activity calls
            # review_service.record_cli_findings which runs the
            # consensus aggregator when this is the last CLI to report.
            await workflow.execute_activity(
                record_review_finding,
                args=[tenant_id, review_id, cli.get("name"), text],
                start_to_close_timeout=activity_timeout,
                retry_policy=retry,
            )
            return {"cli": cli.get("name"), "ok": True}

        # asyncio.gather inside a workflow is deterministic when each
        # task is a child-workflow await — Temporal records them as
        # parallel branches.
        import asyncio
        results = await asyncio.gather(
            *(_dispatch_one(c) for c in clis),
            return_exceptions=True,
        )

        return {
            "status": "completed",
            "review_id": review_id,
            "cli_count": len(clis),
            "results": [r if isinstance(r, dict) else {"error": str(r)} for r in results],
        }
