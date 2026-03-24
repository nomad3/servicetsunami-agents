-- 052: Safety governance phase 1
-- Unified risk taxonomy uses a static code catalog; this migration adds
-- tenant/channel policy overrides for governed actions.

CREATE TABLE IF NOT EXISTS tenant_action_policies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    action_type VARCHAR(50) NOT NULL,
    action_name VARCHAR(150) NOT NULL,
    channel VARCHAR(50) NOT NULL DEFAULT '*',
    decision VARCHAR(30) NOT NULL,
    rationale TEXT,
    enabled BOOLEAN NOT NULL DEFAULT true,
    created_by UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_tenant_action_policy_unique
    ON tenant_action_policies(tenant_id, action_type, action_name, channel);

CREATE INDEX IF NOT EXISTS idx_tenant_action_policy_tenant
    ON tenant_action_policies(tenant_id);

CREATE INDEX IF NOT EXISTS idx_tenant_action_policy_lookup
    ON tenant_action_policies(tenant_id, action_type, action_name, channel)
    WHERE enabled = true;
