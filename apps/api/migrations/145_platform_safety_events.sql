-- 145_platform_safety_events.sql
--
-- Platform Safety Floor — audit table for the always-on safety layer that
-- sits ABOVE the operator value layer (#647 / migration 144).
--
-- Design: docs/plans/2026-05-21-platform-safety-floor-design.md
-- Luna sign-off: [ Luna Signed Off — Platform Safety Floor §12 ]
--
-- Privacy invariant (§5 of the design): we store the SHA256 HASH of the
-- offending message, NOT the message text itself. The hash lets us detect
-- repeated probing attempts + cross-correlate with model-level refusals,
-- but does NOT create a catalogue of "did user X query about Y" that
-- regulators could subpoena.
--
-- Indexing (§12 #2 — Luna implementation check): a partial index on
-- `enforcement_mode = 'enforced'` keeps the count-only operator view fast
-- without scanning shadow-mode rows that the operator surface excludes.

CREATE TABLE platform_safety_events (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    agent_id        UUID,
    session_id      UUID,
    user_id         UUID,
    -- SHA256(message). 64 hex chars. NEVER the raw text.
    message_hash    TEXT NOT NULL,
    -- One of the categories defined in
    -- apps/api/app/core/safety_defaults.py::PLATFORM_SAFETY_CATEGORIES
    category        TEXT NOT NULL,
    -- 1 = regex, 2 = embedding, 3 = LLM classifier
    detection_tier  INTEGER NOT NULL,
    -- 0.0-1.0 for tier 2+ (NULL for tier 1 which is binary)
    confidence      REAL,
    -- 'enforced' = the refusal actually fired against the user
    -- 'shadow'   = what tier 3 WOULD have blocked during the 14-day
    --              shadow-mode window before active enforcement flips
    --              (§12 #7 — Luna call)
    enforcement_mode TEXT NOT NULL DEFAULT 'enforced',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Standard chronological scan index for the platform-admin /admin/safety-events
-- view.
CREATE INDEX idx_platform_safety_events_created_at
    ON platform_safety_events (created_at DESC);

-- Per-tenant count-only aggregate index. The 5-minute-delayed operator-side
-- counter (§12 #3 — Luna call) reads from this.
CREATE INDEX idx_platform_safety_events_tenant_created
    ON platform_safety_events (tenant_id, created_at DESC);

-- Category-level trend index for the platform-admin dashboard.
CREATE INDEX idx_platform_safety_events_category_created
    ON platform_safety_events (category, created_at DESC);

-- Partial index for the count-only operator view (Luna §12 #2 implementation
-- check). The operator surface ONLY sees `enforcement_mode = 'enforced'`
-- rows. Shadow rows are excluded so this index keeps the aggregate cheap.
CREATE INDEX idx_platform_safety_events_enforced
    ON platform_safety_events (tenant_id, created_at DESC)
    WHERE enforcement_mode = 'enforced';

COMMENT ON TABLE platform_safety_events IS
    'Platform Safety Floor audit log. Privacy invariant: stores SHA256(message), NEVER raw text. See docs/plans/2026-05-21-platform-safety-floor-design.md.';

COMMENT ON COLUMN platform_safety_events.enforcement_mode IS
    '''enforced'' (refusal fired) or ''shadow'' (tier 3 would-have-blocked during 14-day pre-enforcement window).';
