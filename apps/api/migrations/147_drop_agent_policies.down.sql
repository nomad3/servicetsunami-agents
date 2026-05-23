-- Rollback for 147_drop_agent_policies.sql.
--
-- Recreates the table schema as it stood in migration 097 so a
-- forward-incompatible rollback can re-mount the deprecated API
-- if absolutely required. Down migrations are best-effort —
-- preferred recovery is a fresh forward migration if the policy
-- substrate ever needs to come back (it should NOT; ValueArbitration
-- is the long-term home — see 2026-05-23 design).

CREATE TABLE IF NOT EXISTS agent_policies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id UUID REFERENCES agents(id) ON DELETE CASCADE,
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    policy_type VARCHAR(30) NOT NULL,
    config JSONB NOT NULL DEFAULT '{}'::jsonb,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_agent_policies_agent_id ON agent_policies (agent_id);
CREATE INDEX IF NOT EXISTS ix_agent_policies_tenant_id ON agent_policies (tenant_id);
CREATE INDEX IF NOT EXISTS ix_agent_policies_policy_type ON agent_policies (policy_type);
