"""Activities for SkillEvalIterationWorkflow (Phase 3 scaffold).

Phase 3 ships these as stubs. Phase 3a fills the bodies:

  - ``persist_run_artifacts`` — write the leg's chat outputs to disk
    under ``workspaces/<tenant>/skill-evals/<run_id>/`` and flip the
    ``skill_eval_runs`` row to the terminal status. Mirrors the write
    phase of the legacy ``eval_runner._run_one``.
  - ``aggregate_iteration`` — roll up per-leg results into the
    analyzer's per-iteration tables once all children have resolved.

The scaffold returns a minimal "noop" result so the parent workflow
can run end-to-end without touching disk or DB yet. Default
``SKILL_EVAL_DISPATCH_MODE=thread`` keeps production on the existing
daemon-thread path (eval_runner._spawn_worker_thread) until the
operator flips the env var.
"""
from __future__ import annotations

import logging

from temporalio import activity

log = logging.getLogger(__name__)


@activity.defn(name="skill_eval.persist_run_artifacts")
async def persist_run_artifacts(
    iteration_run_id: str,
    eval_id: str,
    with_skill: bool,
) -> dict:
    """Phase 3 stub. Phase 3a body will:

      1. Dispatch ChatCliWorkflow child (or accept its result if
         dispatched by the parent directly).
      2. Write returned artifacts to disk under the tenant workspace.
      3. Update the ``skill_eval_runs`` row to the terminal status.

    Returns a noop dict today — the parent workflow ignores the
    body and only counts the activity completion.
    """
    log.info(
        "persist_run_artifacts STUB run=%s eval=%s with_skill=%s",
        iteration_run_id, eval_id, with_skill,
    )
    return {
        "iteration_run_id": iteration_run_id,
        "eval_id": eval_id,
        "with_skill": with_skill,
        "status": "noop_stub",
    }


@activity.defn(name="skill_eval.aggregate_iteration")
async def aggregate_iteration(
    iteration_run_id: str,
    skill_id: str,
    iteration: int,
) -> dict:
    """Phase 3 stub. Phase 3a body will roll up per-leg results into
    the analyzer's tables (mean reward, with-vs-without delta,
    confidence interval) so the eval-viewer Phase 4 surface has a
    single-row read.
    """
    log.info(
        "aggregate_iteration STUB run=%s skill=%s iteration=%s",
        iteration_run_id, skill_id, iteration,
    )
    return {
        "iteration_run_id": iteration_run_id,
        "skill_id": skill_id,
        "iteration": iteration,
        "status": "noop_stub",
    }


__all__ = [
    "persist_run_artifacts",
    "aggregate_iteration",
]
