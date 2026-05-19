-- 137_chat_jobs.sql
--
-- Async chat-result pattern — task #161.
-- Design: docs/plans/2026-05-17-async-chat-result-pattern-design.md
--
-- Two append-only tables that let chat turns survive client disconnect
-- and Cloudflare's 524 idle ceiling:
--
--   chat_jobs        — one row per turn. Single source of truth for
--                      whether a generation is alive (queued / running /
--                      done / failed / cancelled). The CLI worker owns
--                      the row; clients only read it.
--
--   chat_job_events  — append-only event log. Every emitter chunk /
--                      tool_use / tool_result / lifecycle row carries a
--                      monotonic per-job (seq). The /jobs/{id}/events
--                      SSE endpoint replays seq+1..N on reconnect and
--                      then tails LISTEN/NOTIFY for new rows.
--
-- Schema decisions worth pinning:
--
--   * `chat_jobs.status` is a CHECK-constrained VARCHAR rather than a
--     Postgres enum — enums require a separate ALTER TYPE migration to
--     add a value, and we may want to add states (e.g. retrying) without
--     a schema-change PR.
--
--   * `(job_id, seq) PK` on chat_job_events forces monotonic per-job
--     ordering. Seq is allocated under an advisory lock keyed by
--     hashtext(job_id::text) — same pattern as session_events (mig 133)
--     so two emitters can't race a duplicate seq even under WAL replay.
--
--   * `chat_job_events.kind` is constrained to the four taxonomy values
--     in the design doc (chunk / tool_use / tool_result / lifecycle).
--     Extending requires touching this migration + the emitter; that's
--     intentional friction — silently-added kinds break the SSE replay
--     parser.
--
--   * Retention is owned by a janitor (separate PR / cron), not by a
--     TTL trigger. Janitor sweep: DELETE WHERE finished_at IS NOT NULL
--     AND finished_at < NOW() - INTERVAL '24 hours'.
--
-- Wrapped in BEGIN/COMMIT (same pattern as migrations 133/136) so a
-- failure on any index/comment after a successful CREATE TABLE doesn't
-- leave a half-applied state when run via `docker exec psql` per
-- ~/.claude/.../migration_apply_pattern.md.

BEGIN;

-- ───────────────────────────── chat_jobs ─────────────────────────────
CREATE TABLE IF NOT EXISTS chat_jobs (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id          UUID NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    -- FK to tenants/users (mig 133, 136 set the same precedent) so a
    -- deleted tenant / user doesn't leave orphan chat_jobs rows.
    -- ON DELETE CASCADE is consistent with the session_id FK above —
    -- tearing down a tenant rips its async jobs too. Confirmed via
    -- `SELECT FROM _migrations WHERE filename='137_chat_jobs.sql'` =
    -- 0 rows on 2026-05-18 (un-applied), so we edit 137 in-place per
    -- the immutability-only-after-apply principle.
    tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status              VARCHAR(16) NOT NULL DEFAULT 'queued',
    -- The user's prompt is captured here so a worker pickup can rehydrate
    -- the turn without re-reading the request body (and so a retried
    -- worker has the same input).
    request_content     TEXT NOT NULL,
    -- Pointer to the persisted ChatMessage row once the worker writes
    -- the assistant response. NULL while running.
    result_message_id   UUID NULL REFERENCES chat_messages(id) ON DELETE SET NULL,
    error               TEXT NULL,
    cancel_requested    BOOLEAN NOT NULL DEFAULT FALSE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at         TIMESTAMPTZ NULL,
    CONSTRAINT chat_jobs_status_check
        CHECK (status IN ('queued', 'running', 'done', 'failed', 'cancelled'))
);

-- Polling/finder index: a janitor or "list my jobs" endpoint scans
-- (tenant_id, created_at) — both columns are non-null, narrow b-tree
-- order is enough.
CREATE INDEX IF NOT EXISTS idx_chat_jobs_tenant_created
    ON chat_jobs(tenant_id, created_at);

-- Per-session lookup ("show the latest job for session X" for the
-- web client's reconnect path).
CREATE INDEX IF NOT EXISTS idx_chat_jobs_session_created
    ON chat_jobs(session_id, created_at DESC);

-- Active-jobs scan (janitor cancels jobs that have outlived their
-- session, monitoring counts running rows).
CREATE INDEX IF NOT EXISTS idx_chat_jobs_status_active
    ON chat_jobs(status)
 WHERE status IN ('queued', 'running');


-- ──────────────────────────── chat_job_events ────────────────────────
CREATE TABLE IF NOT EXISTS chat_job_events (
    job_id      UUID NOT NULL REFERENCES chat_jobs(id) ON DELETE CASCADE,
    seq         BIGINT NOT NULL,
    kind        VARCHAR(16) NOT NULL,
    payload     JSONB NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (job_id, seq),
    CONSTRAINT chat_job_events_kind_check
        CHECK (kind IN ('chunk', 'tool_use', 'tool_result', 'lifecycle'))
);

-- Replay scans use the PK directly: WHERE job_id = ? AND seq > ?
-- ORDER BY seq. No additional index needed.

-- Retention sweep helper: DELETE WHERE created_at < cutoff — typically
-- driven by a job-level janitor that joins back through chat_jobs, but
-- a standalone time-range scan is cheap with this index.
CREATE INDEX IF NOT EXISTS idx_chat_job_events_created
    ON chat_job_events(created_at);


-- Record migration application.
INSERT INTO _migrations(filename) VALUES ('137_chat_jobs.sql')
ON CONFLICT DO NOTHING;

COMMIT;
