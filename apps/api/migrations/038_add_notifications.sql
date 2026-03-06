-- Notifications table for proactive alerts from Luna
CREATE TABLE IF NOT EXISTS notifications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    title VARCHAR(255) NOT NULL,
    body TEXT,
    source VARCHAR(50) NOT NULL,
    priority VARCHAR(20) NOT NULL DEFAULT 'medium',
    read BOOLEAN NOT NULL DEFAULT FALSE,
    dismissed BOOLEAN NOT NULL DEFAULT FALSE,
    reference_id VARCHAR(255),
    reference_type VARCHAR(50),
    metadata JSONB,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_notifications_tenant_id ON notifications(tenant_id);
CREATE INDEX IF NOT EXISTS ix_notifications_created_at ON notifications(created_at);
CREATE INDEX IF NOT EXISTS ix_notifications_tenant_unread ON notifications(tenant_id, read) WHERE read = FALSE;
