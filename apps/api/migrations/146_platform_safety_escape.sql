-- 146_platform_safety_escape.sql
--
-- Platform Safety Floor — admin escape grants + admin audit
-- (design §7 + §12 + PR 6 of the safety-floor sequence).
--
-- Two tables:
--
--   platform_safety_escape_grants — time-boxed override windows
--     scoped to a specific (user_id, session_id). The classifier
--     consult sees an active grant and skips the floor block
--     for that specific scope. Auto-expires; no manual revoke.
--
--   platform_safety_admin_audit — every escape grant creation,
--     every refusal that fired during a grant window, every
--     refusal that fired AGAINST a non-grant context. Separate
--     from platform_safety_events (which is the operator-facing
--     refusal audit) so platform-admin-only signal stays separate
--     from operator-visible counters.

CREATE TABLE platform_safety_escape_grants (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    -- The platform admin who created the grant. NEVER null.
    issued_by_user_id UUID NOT NULL REFERENCES users(id),
    -- The scope: which user + session does this grant cover? The
    -- consult skip ONLY happens when both match. A grant for
    -- (user=A, session=X) does NOT relax the floor for (user=B,
    -- session=Y).
    scoped_user_id  UUID NOT NULL,
    scoped_session_id UUID NOT NULL,
    -- One of the platform safety category keys, or '*' for any
    -- category. '*' is the corpus-curation case; specific
    -- categories are the red-team / law-enforcement-cooperation
    -- cases.
    category        TEXT NOT NULL,
    -- Required: free-text justification. Required for audit.
    reason          TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at      TIMESTAMPTZ NOT NULL,
    -- An admin can revoke a grant before expiry by setting
    -- revoked_at. Consults check `revoked_at IS NULL AND
    -- expires_at > now()`. Revocation is an audit event in the
    -- admin_audit table.
    revoked_at      TIMESTAMPTZ
);

CREATE INDEX idx_platform_safety_escape_active
    ON platform_safety_escape_grants (
        scoped_user_id, scoped_session_id, expires_at DESC
    )
    WHERE revoked_at IS NULL;

CREATE INDEX idx_platform_safety_escape_tenant_created
    ON platform_safety_escape_grants (tenant_id, created_at DESC);


CREATE TABLE platform_safety_admin_audit (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Event type:
    --   'grant_created'   — admin opened a new escape grant
    --   'grant_revoked'   — admin manually revoked an unexpired grant
    --   'block_in_window' — a floor refusal fired DURING a grant window
    --                       (the grant covered a different category or
    --                       the scope didn't match; recorded for audit)
    --   'block_no_window' — a floor refusal fired with no active grant
    --                       (normal operations; not all of these — only
    --                       the ones admins explicitly enable via the
    --                       category filter — get recorded here)
    event_type      TEXT NOT NULL,
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    actor_user_id   UUID,  -- the admin (grant_created / grant_revoked)
                            -- or NULL for system-recorded block events
    grant_id        UUID REFERENCES platform_safety_escape_grants(id),
    category        TEXT,
    -- Free-text detail. NEVER contains user message text.
    detail          TEXT NOT NULL DEFAULT '',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_platform_safety_admin_audit_created
    ON platform_safety_admin_audit (created_at DESC);

CREATE INDEX idx_platform_safety_admin_audit_event_type
    ON platform_safety_admin_audit (event_type, created_at DESC);

COMMENT ON TABLE platform_safety_escape_grants IS
    'Time-boxed admin overrides scoped to (user, session). See design §7.';
COMMENT ON TABLE platform_safety_admin_audit IS
    'Platform-admin-only audit log for safety-floor grant lifecycle + block events.';
