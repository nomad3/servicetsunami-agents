CREATE TABLE IF NOT EXISTS user_preferences (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    preference_type VARCHAR(50) NOT NULL,
    value VARCHAR(200) NOT NULL,
    confidence FLOAT DEFAULT 0.5,
    evidence_count INTEGER DEFAULT 1,
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_user_pref ON user_preferences(tenant_id, COALESCE(user_id, '00000000-0000-0000-0000-000000000000'), preference_type);
CREATE INDEX IF NOT EXISTS idx_user_pref_tenant ON user_preferences(tenant_id);
