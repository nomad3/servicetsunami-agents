-- 140_skill_eval_iteration_runs.sql
--
-- Phase 2 of the skill-creator framework port (see
-- docs/plans/2026-05-18-skill-creator-framework-port.md). Adds the
-- bookkeeping columns the Phase-2 eval runner needs that aren't covered
-- by the Phase-1 tables in migration 136:
--
--   skill_eval_runs.iteration_run_id  — UUID that groups every
--                                       skill_eval_runs row dispatched
--                                       by a single POST /evals/run
--                                       call. Returned from the endpoint
--                                       as `{job_id}` so the caller can
--                                       poll the full set without
--                                       re-deriving (eval_id, iteration)
--                                       tuples for every leg.
--   skill_eval_runs.workspace_path    — POSIX path under the workspaces
--                                       volume where the runner wrote the
--                                       on-disk artifacts (transcript.md,
--                                       outputs/, metrics.json,
--                                       timing.json). Mirror of the
--                                       Claude Code reference layout. NULL
--                                       while the run is in flight.
--   skill_eval_runs.started_at        — Pinned at dispatch time, separate
--                                       from `created_at` (DB-DEFAULT NOW())
--                                       so the eval-viewer can show
--                                       queue-wait vs run-duration without
--                                       inferring from event timestamps.
--   skill_eval_runs.completed_at      — Pinned when the runner persists
--                                       the run's terminal status.
--
-- New status values: the existing schema constrains nothing (VARCHAR(40)
-- with a NULL-while-in-flight convention). Phase 2 introduces:
--
--   queued     — row inserted, no worker has picked it up
--   running    — worker dispatched, ChatCliWorkflow in flight
--   ok         — workflow returned success, artifacts persisted
--   error      — workflow returned an error OR a persist step failed
--   timeout    — workflow exceeded its execution_timeout
--
-- No CHECK constraint is added — the analyzer (Phase 3) needs to add
-- `partial` for retry-with-stale-output recovery and we don't want a
-- schema-change PR every time. The Python-side `_VALID_STATUSES` tuple
-- in eval_runner.py is the gate.
--
-- Indexes:
--
--   * idx_skill_eval_runs_iteration_run_id — primary read path for
--     `GET /evals/runs?job_id=...` (Phase 2 endpoint).
--   * The existing (eval_id, iteration, with_skill) index from mig 136
--     still serves the per-eval listings.
--
-- Wrapped in BEGIN/COMMIT same pattern as migration 136/137.

BEGIN;

ALTER TABLE skill_eval_runs
    ADD COLUMN IF NOT EXISTS iteration_run_id UUID,
    ADD COLUMN IF NOT EXISTS workspace_path TEXT,
    ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_skill_eval_runs_iteration_run_id
    ON skill_eval_runs (iteration_run_id);

COMMENT ON COLUMN skill_eval_runs.iteration_run_id IS
    'UUID grouping every row dispatched by one POST /skills/{id}/evals/run call. Returned to the caller as job_id so they can poll the full iteration set without joining on (eval_id, iteration). NULL on legacy rows inserted before Phase 2.';
COMMENT ON COLUMN skill_eval_runs.workspace_path IS
    'POSIX path under the workspaces volume holding transcript.md / outputs/ / metrics.json / timing.json for the run. NULL while the run is in flight or if the worker failed before any artifact was written.';
COMMENT ON COLUMN skill_eval_runs.started_at IS
    'Pinned by the worker at dispatch time. Separate from created_at so the eval-viewer can distinguish queue-wait from run-duration.';
COMMENT ON COLUMN skill_eval_runs.completed_at IS
    'Pinned by the worker when the run reaches a terminal status (ok / error / timeout). Used by Phase 3 aggregator to compute timing_ms.';

INSERT INTO _migrations(filename) VALUES ('140_skill_eval_iteration_runs.sql')
ON CONFLICT DO NOTHING;

COMMIT;
