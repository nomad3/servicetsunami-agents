-- 137_reviews_coalitions.sql
--
-- Phase 1 of the `alpha review` cross-CLI consensus feature
-- (see docs/plans/2026-05-18-alpha-review-consensus.md).
--
-- One row per cross-CLI review coalition. The actual per-CLI findings
-- live as `blackboard_entries` on the linked Blackboard so we reuse the
-- existing append-only + version-tracked substrate from PR #182-#205
-- (a2a_collaboration). The `findings` + `agreed_findings` columns here
-- are the *cached aggregate snapshot* the consensus aggregator writes
-- after each round so the read path doesn't have to re-walk the
-- blackboard on every poll/SSE event.
--
-- Schema decisions worth pinning:
--
--   * `ref` is opaque TEXT (a PR number "#570", a commit SHA, a
--     "path:start-end" range, or "stdin://<sha256>"). We do not parse
--     it server-side — the CLI normalizes the operator's input and
--     the leaf CLIs are handed the same opaque string. This keeps
--     `alpha review` independent of any single source-control vendor.
--
--   * `clis` is JSONB array of {name, agent_slug} so we can record
--     which CLIs participated even if the tenant later disables one.
--     The dispatch order is preserved (Postgres JSONB keeps insertion
--     order for arrays).
--
--   * `findings` is JSONB shaped as {per_cli: {claude: [...], codex:
--     [...]}, last_round: int}. `agreed_findings` is JSONB array of
--     {severity, file, line_range, descriptions: [...], cli_set:
--     [...]}. The consensus heuristic (2+ CLIs flag a substring-
--     overlapping issue) is documented in
--     app/services/review_service.py:_aggregate_findings — keep them
--     in sync.
--
--   * `status` enum (running | awaiting_response | done | failed)
--     mirrors the CoalitionWorkflow lifecycle so the SSE stream from
--     the review-id channel can replay state without consulting
--     CollaborationSession.
--
--   * `blackboard_id` is the durable join key into the existing
--     blackboard/blackboard_entries tables. ON DELETE SET NULL keeps
--     the reviews_coalitions audit trail even if a tenant prunes
--     blackboards (we keep the snapshot in `findings`).
--
-- NB: there is no down migration. Reviews-coalitions are audit-grade
-- records; dropping the table on rollback would silently lose tenant
-- review history.

CREATE TABLE reviews_coalitions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    blackboard_id UUID REFERENCES blackboards(id) ON DELETE SET NULL,
    chat_session_id UUID REFERENCES chat_sessions(id) ON DELETE SET NULL,

    -- Operator-supplied review target. Opaque to the server.
    ref TEXT NOT NULL,
    scope VARCHAR(50) NOT NULL DEFAULT 'bugs+security',

    -- Participating CLI fanout list. Shape: [{"name": "claude",
    -- "agent_slug": "..."}, ...]. Preserved across rounds.
    clis JSONB NOT NULL DEFAULT '[]'::jsonb,

    -- Round bookkeeping.
    rounds_completed INT NOT NULL DEFAULT 0,
    max_rounds INT NOT NULL DEFAULT 3,

    -- Lifecycle: running | awaiting_response | done | failed
    status VARCHAR(30) NOT NULL DEFAULT 'running',

    -- Aggregated cache. Shape documented above.
    findings JSONB NOT NULL DEFAULT '{}'::jsonb,
    agreed_findings JSONB NOT NULL DEFAULT '[]'::jsonb,

    -- For auditability — last operator-supplied diff/ref applied via
    -- POST /reviews/{id}/reply.
    last_reply_ref TEXT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_reviews_coalitions_tenant_status
    ON reviews_coalitions (tenant_id, status, created_at DESC);

CREATE INDEX idx_reviews_coalitions_blackboard
    ON reviews_coalitions (blackboard_id)
    WHERE blackboard_id IS NOT NULL;

CREATE INDEX idx_reviews_coalitions_chat_session
    ON reviews_coalitions (chat_session_id)
    WHERE chat_session_id IS NOT NULL;
