-- Proactive actions: Luna-initiated messages
CREATE TABLE IF NOT EXISTS proactive_actions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    agent_slug TEXT NOT NULL DEFAULT 'luna',
    action_type TEXT NOT NULL, -- nudge, followup, briefing, alert, analysis
    trigger_type TEXT NOT NULL, -- stalled_goal, overdue_commitment, cold_lead, expiring_assertion, new_email, calendar_prep
    target_ref TEXT, -- goal_id, commitment_id, entity_id
    priority TEXT NOT NULL DEFAULT 'medium', -- high, medium, low
    content TEXT NOT NULL,
    channel TEXT NOT NULL DEFAULT 'notification', -- whatsapp, notification, email
    status TEXT NOT NULL DEFAULT 'pending', -- pending, sent, acknowledged, dismissed
    scheduled_at TIMESTAMP,
    sent_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_proactive_actions_tenant ON proactive_actions(tenant_id, status);
