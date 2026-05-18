-- 136_skill_evals_tables.sql
--
-- Phase 1 of the skill-creator framework port (see
-- docs/plans/2026-05-18-skill-creator-framework-port.md). Adds the three
-- tables that back the authoring loop's data model:
--
--   skill_evals         — one row per eval *definition*  (prompt + expectations)
--   skill_eval_runs     — one row per *execution* of an eval
--                          (with_skill or baseline leg, per iteration)
--   skill_eval_grading  — one row per graded run, owned 1:1 by a run
--
-- Phase 1 only writes to `skill_eval_grading` via the grader endpoint.
-- The other two tables are seeded by Phase 2 (eval runner) but their
-- shape is fixed now so Phase 2 can land as a pure additive change
-- without a schema-change PR.
--
-- Schema decisions worth pinning:
--
--   * `expectations` lives on `skill_evals` as JSONB (not a separate
--     child table) — expectations are co-edited with the prompt and
--     never queried independently. JSONB keeps a single transactional
--     update boundary; a child table would force two-step writes that
--     can interleave with running graders.
--
--   * `skill_eval_runs.outputs` is JSONB (not a path string) — file
--     artifacts on disk live in the workspaces volume under
--     `<workspaces_root>/<tenant>/skills/<slug>-workspace/iteration-<N>/eval-<id>/`
--     (Phase 2). The DB column carries the *manifest* of which files
--     were written, sized, and mime-typed, NOT the file bodies. This
--     lets the eval-viewer (Phase 4) decide what to fetch without
--     scanning the volume.
--
--   * `skill_eval_grading.run_id` is BOTH the primary key AND a foreign
--     key — every grading row belongs to exactly one run, and a run
--     has at most one grading. If we ever need to re-grade (Phase 3
--     analyzer optionally re-grades with a stronger model), we'll
--     archive the prior row into `library_revisions` before overwriting
--     so the audit trail is preserved without doubling the row count.
--
--   * `score` is NUMERIC(5,4) — fraction in [0, 1] with four decimals
--     of precision. Enough to distinguish 0.6666 from 0.6667 (the
--     2-of-3 vs 2-of-3-rounded edge); NUMERIC instead of FLOAT so the
--     analyzer's aggregate arithmetic is bit-stable across replicas.
--
-- Wrapped in BEGIN/COMMIT (same pattern as migration 133) so a failure
-- on any index/comment after a successful CREATE TABLE doesn't leave a
-- half-applied state when run via `docker exec psql` per
-- ~/.claude/.../migration_apply_pattern.md.

BEGIN;

CREATE TABLE IF NOT EXISTS skill_evals (
    id            UUID PRIMARY KEY,
    tenant_id     UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    skill_id      UUID NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
    prompt        TEXT NOT NULL,
    expectations  JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_skill_evals_tenant_skill
    ON skill_evals (tenant_id, skill_id);

COMMENT ON COLUMN skill_evals.expectations IS
    'JSONB array of Expectation objects: [{"id": str, "description": str, "kind": "assertion"|"structured"}]. See docs/skill-creator/schemas.md.';


CREATE TABLE IF NOT EXISTS skill_eval_runs (
    id            UUID PRIMARY KEY,
    eval_id       UUID NOT NULL REFERENCES skill_evals(id) ON DELETE CASCADE,
    iteration     INTEGER NOT NULL,
    with_skill    BOOLEAN NOT NULL,
    transcript    TEXT,
    outputs       JSONB,
    metrics       JSONB,
    timing_ms     INTEGER,
    model         VARCHAR(120),
    token_usage   JSONB,
    status        VARCHAR(40),
    error         TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_skill_eval_runs_eval_iter_leg
    ON skill_eval_runs (eval_id, iteration, with_skill);

COMMENT ON COLUMN skill_eval_runs.with_skill IS
    'TRUE for the skill-loaded leg of a paired run, FALSE for the no-skill baseline. The pair shares (eval_id, iteration).';
COMMENT ON COLUMN skill_eval_runs.outputs IS
    'Manifest of files written by the runner: {"path": {"size_bytes": int, "mime": str}}. File bodies live on the workspaces volume.';
COMMENT ON COLUMN skill_eval_runs.token_usage IS
    'JSONB object {"input": int, "output": int, "total": int}. Mirrors eval_metadata.json.';
COMMENT ON COLUMN skill_eval_runs.status IS
    'One of: ok | error | timeout. NULL while a run is in flight.';


CREATE TABLE IF NOT EXISTS skill_eval_grading (
    run_id        UUID PRIMARY KEY REFERENCES skill_eval_runs(id) ON DELETE CASCADE,
    grading       JSONB NOT NULL,
    score         NUMERIC(5,4),
    grader_model  VARCHAR(120),
    graded_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON COLUMN skill_eval_grading.grading IS
    'Full grading.json payload (see docs/skill-creator/schemas.md). The score column is the same value as grading.score, denormalized for cheap aggregate queries.';


INSERT INTO _migrations(filename) VALUES ('136_skill_evals_tables.sql')
ON CONFLICT DO NOTHING;

COMMIT;
