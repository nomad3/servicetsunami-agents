-- 047: Universal Webhook Connectors
-- Adds webhook_connectors and webhook_delivery_logs tables for
-- bidirectional webhook support (inbound + outbound).

CREATE TABLE IF NOT EXISTS webhook_connectors (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    direction VARCHAR(10) NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    slug VARCHAR(64) UNIQUE,
    target_url TEXT,
    events JSONB NOT NULL DEFAULT '[]'::jsonb,
    headers JSONB,
    auth_type VARCHAR(20) NOT NULL DEFAULT 'none',
    secret TEXT,
    payload_transform JSONB,
    enabled BOOLEAN NOT NULL DEFAULT true,
    status VARCHAR(20) NOT NULL DEFAULT 'active',
    last_triggered_at TIMESTAMP,
    trigger_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT now(),
    updated_at TIMESTAMP DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_webhook_connectors_tenant_id ON webhook_connectors(tenant_id);
CREATE UNIQUE INDEX IF NOT EXISTS ix_webhook_connectors_slug ON webhook_connectors(slug);
CREATE INDEX IF NOT EXISTS ix_webhook_connectors_tenant_enabled ON webhook_connectors(tenant_id) WHERE enabled = true;

CREATE TABLE IF NOT EXISTS webhook_delivery_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    webhook_connector_id UUID NOT NULL REFERENCES webhook_connectors(id) ON DELETE CASCADE,
    direction VARCHAR(10) NOT NULL,
    event_type VARCHAR(100) NOT NULL,
    payload JSONB,
    response_status INTEGER,
    response_body TEXT,
    success BOOLEAN NOT NULL DEFAULT false,
    error_message TEXT,
    duration_ms INTEGER,
    attempt INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_webhook_delivery_logs_webhook_id ON webhook_delivery_logs(webhook_connector_id);
CREATE INDEX IF NOT EXISTS ix_webhook_delivery_logs_tenant_id ON webhook_delivery_logs(tenant_id);
CREATE INDEX IF NOT EXISTS ix_webhook_delivery_logs_created_at ON webhook_delivery_logs(created_at);
