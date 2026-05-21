"""SkillEvalIterationWorkflow — Phase 3 parent dispatch pattern.

Per ADR docs/plans/2026-05-19-skill-eval-temporal-parent-pattern-adr.md.
Phase 3 scaffold: workflow class + activity stubs + env-gated dispatch.
Production path defaults to ``SKILL_EVAL_DISPATCH_MODE=thread`` so the
existing daemon-thread model in ``eval_runner._spawn_worker_thread``
keeps running unchanged. Operators flip to ``workflow`` per-tenant
once Phase 3 synthesis activities have real bodies (mirrors the O2
scaffold pattern from #631).

Workflow shape:

    SkillEvalIterationWorkflow  (parent)
    ├── ChatCliWorkflow  (child, eval-1 with_skill=True)
    ├── ChatCliWorkflow  (child, eval-1 with_skill=False)
    ├── ChatCliWorkflow  (child, eval-2 with_skill=True)
    └── ChatCliWorkflow  (child, eval-2 with_skill=False)

After each child completes, ``persist_run_artifacts`` activity writes
the leg's outputs to disk + flips the `skill_eval_runs` row to the
terminal status. Once all children resolve, ``aggregate_iteration``
rolls up into the analyzer's tables.

Children use ``parent_close_policy=ABANDON`` so a parent restart from
history doesn't murder in-flight children — matching the
EpisodeWorkflow pattern shipped earlier.
"""
from datetime import timedelta
from typing import List, Tuple

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from app.workflows.activities.skill_eval_activities import (
        aggregate_iteration,
        persist_run_artifacts,
    )


# Activity timeouts. Conservative — synthesis is variable-cost and
# the parent restarts free thanks to Temporal history.
_PERSIST_TIMEOUT = timedelta(minutes=3)
_AGGREGATE_TIMEOUT = timedelta(minutes=5)
# Each child ChatCliWorkflow gets its own timeout via its workflow
# definition; the parent just observes completion.


@workflow.defn
class SkillEvalIterationWorkflow:
    @workflow.run
    async def run(
        self,
        iteration_run_id: str,
        skill_id: str,
        iteration: int,
        legs: List[Tuple[str, bool]],
    ) -> dict:
        """Fan out to N ChatCliWorkflow children, then aggregate.

        Args:
            iteration_run_id: UUID identifying this iteration's roll-up.
            skill_id: Skill UUID.
            iteration: 1-indexed iteration number.
            legs: list of (eval_id, with_skill) pairs to dispatch.

        Returns:
            ``{
                "iteration_run_id": str,
                "legs_total": int,
                "legs_succeeded": int,
                "legs_failed": int,
                "aggregated": bool,
            }``
        """
        # Phase 3 scaffold: stub the per-leg dispatch as a single
        # persist_run_artifacts call per leg. Phase 3a fills in the
        # real start_child_workflow(ChatCliWorkflow, ...) pattern.
        succeeded = 0
        failed = 0
        for (eval_id, with_skill) in legs:
            try:
                await workflow.execute_activity(
                    persist_run_artifacts,
                    args=[iteration_run_id, eval_id, with_skill],
                    start_to_close_timeout=_PERSIST_TIMEOUT,
                )
                succeeded += 1
            except Exception:
                failed += 1

        aggregated = False
        try:
            await workflow.execute_activity(
                aggregate_iteration,
                args=[iteration_run_id, skill_id, iteration],
                start_to_close_timeout=_AGGREGATE_TIMEOUT,
            )
            aggregated = True
        except Exception:
            aggregated = False

        return {
            "iteration_run_id": iteration_run_id,
            "legs_total": len(legs),
            "legs_succeeded": succeeded,
            "legs_failed": failed,
            "aggregated": aggregated,
        }
