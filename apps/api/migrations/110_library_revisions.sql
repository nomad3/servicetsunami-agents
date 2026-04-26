-- 110 — Library revisions audit log.
--
-- Tracks before/after state for skill prompt edits and agent config edits
-- driven from the chat side via the MCP `update_skill_definition` and
-- `update_agent_definition` tools (see PR5 / Phase 4 of the skills
-- marketplace redesign).
--
-- target_type is one of 'skill' or 'agent'.
-- target_ref is the skill slug for skills, the agent UUID stringified for
-- agents. We use a single string column so a future 'workflow' or
-- 'integration' target can land here without a schema change.
--
-- Idempotent.

CREATE TABLE IF NOT EXISTS library_revisions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    target_type     VARCHAR(32) NOT NULL,
    target_ref      VARCHAR(255) NOT NULL,
    actor_user_id   UUID REFERENCES users(id) ON DELETE SET NULL,
    reason          TEXT,
    before_value    JSONB,
    after_value     JSONB,
    created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_library_revisions_lookup
    ON library_revisions (tenant_id, target_type, target_ref, created_at DESC);

INSERT INTO _migrations(filename) VALUES ('110_library_revisions.sql')
ON CONFLICT DO NOTHING;
