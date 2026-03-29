CREATE TABLE IF NOT EXISTS user_activities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    user_id UUID REFERENCES users(id),
    event_type VARCHAR(50) NOT NULL,
    source_shell VARCHAR(100),
    app_name VARCHAR(255),
    window_title VARCHAR(500),
    detail JSONB DEFAULT '{}',
    duration_secs FLOAT,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_user_activities_tenant ON user_activities(tenant_id);
CREATE INDEX IF NOT EXISTS idx_user_activities_user ON user_activities(user_id);
CREATE INDEX IF NOT EXISTS idx_user_activities_tenant_created ON user_activities(tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_user_activities_app ON user_activities(tenant_id, app_name);
