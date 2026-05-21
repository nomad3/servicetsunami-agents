"""Activities for SkillEvalIterationWorkflow (Phase 3a — real bodies).

Phase 3a fills in the bodies that shipped as stubs in #643:

  - ``persist_run_artifacts`` — rehydrate run context from the DB,
    then call the existing ``eval_runner._run_one`` to dispatch the
    ChatCliWorkflow leg, write artifacts to disk, and flip the
    ``skill_eval_runs`` row to its terminal status. Reuses the
    battle-tested ``_run_one`` instead of duplicating its logic —
    Phase 3b can refactor ``_run_one`` into activity-friendly
    pieces if needed.

  - ``aggregate_iteration`` — SELECT all skill_eval_runs rows for
    the iteration_run_id, compute the with-vs-baseline reward
    delta + per-status counts, return the summary. No rollup table
    yet (Phase 3b); for now the workflow result carries the
    aggregate so the eval-viewer SSE feed in Phase 4 can read it
    from Temporal history.

Production safety: defaults to the legacy daemon-thread dispatch
(eval_runner._spawn_worker_thread) until ``SKILL_EVAL_DISPATCH_MODE
=workflow`` is set at runtime — the env-gate wiring lands in a
Phase 3b PR that flips eval_runner.dispatch_iteration.

These activities are **sync** rather than async because ``_run_one``
is sync (subprocess.run + sync DB calls). The orchestration_worker
already provides a ThreadPoolExecutor for sync activities, so this
is the cheapest reuse path.
"""
from __future__ import annotations

import logging
import uuid
from typing import Optional

from temporalio import activity

log = logging.getLogger(__name__)


@activity.defn(name="skill_eval.persist_run_artifacts")
def persist_run_artifacts(
    iteration_run_id: str,
    eval_id: str,
    with_skill: bool,
) -> dict:
    """Dispatch the ChatCliWorkflow for one eval leg, write
    artifacts, and update the skill_eval_runs row.

    Rehydrates the full run context (run_id, eval_prompt, iteration,
    tenant_id, skill_slug, skill_body, platform, model) from the
    skill_eval_runs row, then delegates to the existing
    ``eval_runner._run_one`` which already handles disk writes +
    row state transitions correctly.

    Returns a dict with the per-leg outcome that the parent
    workflow's success/failure counter consumes:

        {
            "iteration_run_id": str,
            "eval_id": str,
            "with_skill": bool,
            "run_id": Optional[str],
            "status": "ok" | "error" | "timeout" | "missing_row",
        }

    A missing run row (e.g. concurrent retry that already wrote the
    terminal status, or row was never inserted) returns status=
    "missing_row" — the parent counts it as a failure but doesn't
    raise.
    """
    from sqlalchemy import text as sql_text

    from app.db.session import SessionLocal
    from app.services.skill_creator import eval_runner

    db = SessionLocal()
    try:
        row = db.execute(
            sql_text(
                """
                SELECT
                    r.id AS run_id,
                    r.iteration,
                    r.status,
                    e.skill_id,
                    e.prompt,
                    s.tenant_id,
                    s.name AS skill_name
                FROM skill_eval_runs r
                JOIN skill_evals e ON r.eval_id = e.id
                JOIN skills s ON e.skill_id = s.id
                WHERE r.iteration_run_id = :run_id
                  AND r.eval_id = :eval_id
                  AND r.with_skill = :ws
                LIMIT 1
                """
            ),
            {
                "run_id": iteration_run_id,
                "eval_id": eval_id,
                "ws": with_skill,
            },
        ).first()
    finally:
        db.close()

    if row is None:
        log.warning(
            "persist_run_artifacts: no skill_eval_runs row for "
            "iteration_run_id=%s eval_id=%s with_skill=%s",
            iteration_run_id, eval_id, with_skill,
        )
        return {
            "iteration_run_id": iteration_run_id,
            "eval_id": eval_id,
            "with_skill": with_skill,
            "run_id": None,
            "status": "missing_row",
        }

    # Already terminal — likely a concurrent retry. Don't re-dispatch
    # (would create a duplicate Temporal child workflow under the
    # deterministic skill-eval-<run_id> id and burn quota).
    if row.status in eval_runner.TERMINAL_STATUSES:
        log.info(
            "persist_run_artifacts: run %s already terminal status=%s, "
            "skipping re-dispatch", row.run_id, row.status,
        )
        return {
            "iteration_run_id": iteration_run_id,
            "eval_id": eval_id,
            "with_skill": with_skill,
            "run_id": str(row.run_id),
            "status": row.status,
        }

    # Resolve the skill slug + body from the existing helpers.
    skill_slug = eval_runner._derive_slug(row.skill_name)
    skill_body = eval_runner._load_skill_body(
        skill_slug=skill_slug,
        tenant_id=row.tenant_id,
    )

    try:
        eval_runner._run_one(
            run_id=row.run_id,
            eval_id=str(eval_id),
            eval_prompt=row.prompt,
            iteration=row.iteration,
            with_skill=with_skill,
            tenant_id=row.tenant_id,
            skill_id=row.skill_id,
            skill_slug=skill_slug,
            skill_body=skill_body,
            platform="claude_code",  # Phase 3b will resolve per-tenant default
            model="",
            iteration_run_id=uuid.UUID(iteration_run_id),
        )
    except Exception as exc:  # noqa: BLE001
        # _run_one is supposed to swallow its own errors and persist
        # status=error. If it raises here something deeper broke;
        # log + report the failure to the workflow so the iteration
        # rollup can see the count.
        log.exception(
            "persist_run_artifacts: _run_one raised for run %s: %s",
            row.run_id, exc,
        )
        return {
            "iteration_run_id": iteration_run_id,
            "eval_id": eval_id,
            "with_skill": with_skill,
            "run_id": str(row.run_id),
            "status": "error",
        }

    # Re-read terminal status so the workflow result reflects the
    # actual landing state.
    db2 = SessionLocal()
    try:
        final = db2.execute(
            sql_text("SELECT status FROM skill_eval_runs WHERE id = :rid"),
            {"rid": row.run_id},
        ).first()
        terminal_status = final.status if final else "missing_row"
    finally:
        db2.close()

    return {
        "iteration_run_id": iteration_run_id,
        "eval_id": eval_id,
        "with_skill": with_skill,
        "run_id": str(row.run_id),
        "status": terminal_status,
    }


@activity.defn(name="skill_eval.aggregate_iteration")
def aggregate_iteration(
    iteration_run_id: str,
    skill_id: str,
    iteration: int,
) -> dict:
    """Roll up per-leg results for one iteration into a single
    summary. Phase 3a returns the aggregate inline; Phase 3b may
    persist it to a dedicated rollup table.

    Returns::

        {
            "iteration_run_id": str,
            "skill_id": str,
            "iteration": int,
            "total_runs": int,
            "with_skill_ok": int,
            "with_skill_failed": int,
            "baseline_ok": int,
            "baseline_failed": int,
            "with_skill_mean_timing_ms": Optional[float],
            "baseline_mean_timing_ms": Optional[float],
        }
    """
    from sqlalchemy import text as sql_text

    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        rows = db.execute(
            sql_text(
                """
                SELECT with_skill, status, timing_ms
                FROM skill_eval_runs
                WHERE iteration_run_id = :run_id
                """
            ),
            {"run_id": iteration_run_id},
        ).all()
    finally:
        db.close()

    if not rows:
        log.warning(
            "aggregate_iteration: no rows for iteration_run_id=%s",
            iteration_run_id,
        )
        return {
            "iteration_run_id": iteration_run_id,
            "skill_id": skill_id,
            "iteration": iteration,
            "total_runs": 0,
            "with_skill_ok": 0,
            "with_skill_failed": 0,
            "baseline_ok": 0,
            "baseline_failed": 0,
            "with_skill_mean_timing_ms": None,
            "baseline_mean_timing_ms": None,
        }

    with_skill_ok = 0
    with_skill_failed = 0
    baseline_ok = 0
    baseline_failed = 0
    with_skill_timings: list[int] = []
    baseline_timings: list[int] = []

    for r in rows:
        ok = r.status == "ok"
        if r.with_skill:
            if ok:
                with_skill_ok += 1
                if r.timing_ms is not None:
                    with_skill_timings.append(r.timing_ms)
            else:
                with_skill_failed += 1
        else:
            if ok:
                baseline_ok += 1
                if r.timing_ms is not None:
                    baseline_timings.append(r.timing_ms)
            else:
                baseline_failed += 1

    def _mean(values: list[int]) -> Optional[float]:
        return sum(values) / len(values) if values else None

    summary = {
        "iteration_run_id": iteration_run_id,
        "skill_id": skill_id,
        "iteration": iteration,
        "total_runs": len(rows),
        "with_skill_ok": with_skill_ok,
        "with_skill_failed": with_skill_failed,
        "baseline_ok": baseline_ok,
        "baseline_failed": baseline_failed,
        "with_skill_mean_timing_ms": _mean(with_skill_timings),
        "baseline_mean_timing_ms": _mean(baseline_timings),
    }
    log.info("aggregate_iteration done: %s", summary)
    return summary


__all__ = [
    "persist_run_artifacts",
    "aggregate_iteration",
]
