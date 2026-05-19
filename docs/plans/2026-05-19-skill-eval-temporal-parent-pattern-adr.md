# ADR: Replace skill-eval daemon-thread dispatch with a Temporal parent workflow

- **Status:** Proposed (deferred from PR #579 review)
- **Date:** 2026-05-19
- **Author:** skill-creator Phase 2 review
- **Phase:** 3 (analyzer) — implementation lands alongside the eval-aggregator
- **Supersedes:** the fire-and-forget daemon-thread model in
  `apps/api/app/services/skill_creator/eval_runner.py` (`_spawn_worker_thread`).

## Context

PR #579 ships the Phase-2 eval runner with a daemon-thread-per-leg
dispatch model. For each (eval, with_skill ∈ {True, False}) pair the API
process:

1. Inserts a `skill_eval_runs` row in `queued` state.
2. Spawns `threading.Thread(target=_run_one, daemon=True)` from the
   request handler.
3. The thread opens its own `SessionLocal`, dispatches a Temporal
   `ChatCliWorkflow` synchronously via `asyncio.run`, writes artifacts
   to disk, and flips the row to a terminal status.

This mirrors the PR-#574 `dispatch_review_workflow` pattern. It works
for the Phase-2 happy path but has two structural problems the
reviewer flagged:

1. **Orphan-running rows on API restart.** If the api pod recycles
   while a thread is mid-flight, the underlying Temporal child KEEPS
   running (at-least-once delivery), but the worker thread that writes
   the result back to `skill_eval_runs` is gone. The row stays
   `running` forever. Phase 2 accepts this; we'll add a janitor in
   Phase 3 that sweeps `running` rows older than `execution_timeout`.
2. **No native fan-in / cancellation.** The runner has no first-class
   way to wait for "all legs of this iteration finished" or cancel a
   whole iteration. Today's `get_iteration_status` polls; Phase-4
   eval-viewer wants SSE. Both are workable but add bespoke
   bookkeeping.

The reference Claude Code skill-creator uses the OS process tree to
solve both — the parent CLI process is the supervisor, child
subprocess deaths cascade. We don't have that on the server. The
ergonomic equivalent is a Temporal **parent workflow** that spawns
N child workflows and observes their completion through Temporal's
native primitives.

## Decision

In Phase 3 (alongside the analyzer), replace the
daemon-thread-per-leg model with:

```
SkillEvalIterationWorkflow  (parent)
├── ChatCliWorkflow  (child, eval-1 with_skill=True)
├── ChatCliWorkflow  (child, eval-1 with_skill=False)
├── ChatCliWorkflow  (child, eval-2 with_skill=True)
└── ChatCliWorkflow  (child, eval-2 with_skill=False)
```

`SkillEvalIterationWorkflow`:

- Takes `(iteration_run_id, skill_id, iteration, [(eval_id, with_skill), …])`.
- For each pair, starts `ChatCliWorkflow` as a child workflow with
  `parent_close_policy=ABANDON` (so a parent restart from history
  doesn't murder in-flight children) and a deterministic
  `workflow_id=skill-eval-{run_id}`.
- Uses `asyncio.gather(*children)` to fan in.
- On each child completion, invokes a `persist_run_artifacts` activity
  (signature mirrors `_run_one`'s write phase) so the on-disk and DB
  writes are durable / retried by the activity layer.
- Emits a final `aggregate_iteration` activity that updates the
  Phase-3 analyzer's roll-up tables.

API surface:

- `POST /skills/{id}/evals/run` returns `iteration_run_id` (unchanged)
  and starts `SkillEvalIterationWorkflow` instead of spawning threads.
- `GET /skills/{id}/evals/jobs/{job_id}` keeps the same JSON shape.
  Reads still come from `skill_eval_runs`; the analyzer activity
  writes them.

## Rationale

| Concern | Daemon thread (Phase 2) | Temporal parent (Phase 3) |
| --- | --- | --- |
| API restart mid-run | Row stuck `running`; need janitor | Workflow resumes from history; row state still correct |
| Cancel iteration | Not supported | `client.cancel_workflow(parent_id)` cascades |
| Fan-in semantics | Manual polling + status reduce | `asyncio.gather(*children)` |
| Retry per leg | Re-dispatch row manually | Child retry policy + idempotent activity |
| Cost per iteration | One thread + one workflow per leg | One parent workflow + one child per leg (parent is cheap) |
| SSE for Phase 4 | Bespoke event log | Workflow signals + Temporal event history |
| Test fixtures | `_runner` hook substitutes the thread | Temporal `WorkflowEnvironment` test harness — already used by PR #570's chat-job tests |

## Open questions

1. **Concurrency cap.** Phase 5's "parallel CLI fanout" wants N
   children running in parallel. The parent workflow needs a
   `Semaphore`-like concurrency cap (Temporal's `start_child_workflow`
   doesn't queue). Option A: gate at the API side (run no more than M
   iterations per tenant). Option B: gate inside the parent via a
   sliding window over `asyncio.wait(FIRST_COMPLETED)`.
2. **Workspace-quota precheck.** Today the quota check (I3) lives in
   `dispatch_iteration` synchronously. With a parent workflow, we
   want the check to run BEFORE `start_workflow` returns (i.e. still
   in the request handler), not inside the workflow — otherwise a 413
   becomes a workflow that finishes with `terminated: quota`. Keep
   the precheck where it is; the parent assumes its inputs are valid.
3. **Migration path.** Feature-flag for safety: env-var
   `SKILL_EVAL_DISPATCH_MODE=thread|workflow` so Phase 3 can ship
   both paths and a-b test under load before retiring the thread
   path. Roll forward by switching the default; backward by toggling
   the env var.
4. **Test environment.** PR #570 added `pytest-temporal` fixtures.
   Reuse those for the parent-workflow tests. The `_runner` hook in
   `dispatch_iteration` goes away; tests register the parent workflow
   with the same in-memory Temporal worker.

## Consequences

- `eval_runner._spawn_worker_thread`, `_run_one`, and the daemon-thread
  apparatus get deleted (or moved to a deprecated module gated behind
  the env var).
- `persist_run_artifacts` becomes a Temporal activity, gaining retry
  semantics for free.
- The Phase-2 "orphan-row janitor" planned for Phase 3 is no longer
  needed for the workflow path (but stays as cleanup for legacy
  rows).
- Tests gain a Temporal dependency for the parent-workflow path.
- Operational visibility improves: Temporal Web UI shows iteration
  progress without bespoke endpoints.

## Out of scope for this ADR

- The aggregator / analyzer activities themselves (separate Phase-3
  doc).
- The Phase-4 eval-viewer SSE channel (separate doc).
- Phase-5 parallel CLI fanout — this ADR only addresses dispatch
  topology, not how many CLIs run in parallel.

## References

- Reviewer comment on PR #579 — "promote Temporal parent workflow to
  Phase 3 ADR".
- `docs/plans/2026-05-18-skill-creator-framework-port.md` — original
  plan; the Phase-3 section will reference this ADR.
- `docs/plans/2026-05-17-async-chat-result-pattern-design.md` — adjacent
  workflow-vs-thread tradeoff for chat sessions; same reasoning applies.
- PR #570 — chat-jobs (`chat_sessions`-bound) async result pattern.
- PR #574 — `dispatch_review_workflow` (the other live daemon-thread
  call-site; will migrate in lockstep).
