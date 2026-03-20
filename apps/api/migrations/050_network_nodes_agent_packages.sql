-- 050: STP node registry + agent packages
-- Adds the API-side foundation for distributed node registration and
-- content-addressed agent package publishing.

CREATE TABLE IF NOT EXISTS network_nodes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    tailscale_ip VARCHAR(64),
    status VARCHAR(20) NOT NULL DEFAULT 'online' CHECK (status IN ('online', 'suspect', 'offline')),
    last_heartbeat TIMESTAMP NOT NULL DEFAULT now(),
    capabilities JSONB,
    max_concurrent_tasks INTEGER NOT NULL DEFAULT 3,
    current_load DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    pricing_tier VARCHAR(20) NOT NULL DEFAULT 'standard',
    total_tasks_completed INTEGER NOT NULL DEFAULT 0,
    avg_execution_time_ms DOUBLE PRECISION,
    reputation_score DOUBLE PRECISION NOT NULL DEFAULT 0.5,
    created_at TIMESTAMP NOT NULL DEFAULT now(),
    updated_at TIMESTAMP NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_network_nodes_tenant_id ON network_nodes(tenant_id);
CREATE INDEX IF NOT EXISTS ix_network_nodes_status ON network_nodes(status);
CREATE INDEX IF NOT EXISTS ix_network_nodes_last_heartbeat ON network_nodes(last_heartbeat);

CREATE TABLE IF NOT EXISTS agent_packages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    creator_tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    version VARCHAR(50) NOT NULL DEFAULT '0.1.0',
    content_hash VARCHAR(64) NOT NULL,
    signature TEXT,
    creator_public_key TEXT,
    skill_id UUID REFERENCES skills(id) ON DELETE SET NULL,
    metadata JSONB,
    required_tools JSONB,
    required_cli VARCHAR(50) NOT NULL DEFAULT 'any',
    pricing_tier VARCHAR(20) NOT NULL DEFAULT 'simple',
    quality_score DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    total_executions INTEGER NOT NULL DEFAULT 0,
    downloads INTEGER NOT NULL DEFAULT 0,
    status VARCHAR(20) NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'published', 'suspended')),
    package_content TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT now(),
    updated_at TIMESTAMP NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_agent_packages_tenant_id ON agent_packages(tenant_id);
CREATE INDEX IF NOT EXISTS ix_agent_packages_creator_tenant_id ON agent_packages(creator_tenant_id);
CREATE INDEX IF NOT EXISTS ix_agent_packages_name ON agent_packages(name);
CREATE INDEX IF NOT EXISTS ix_agent_packages_content_hash ON agent_packages(content_hash);
CREATE UNIQUE INDEX IF NOT EXISTS ux_agent_packages_tenant_name_version ON agent_packages(tenant_id, name, version);
