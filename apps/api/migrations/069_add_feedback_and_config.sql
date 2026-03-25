-- Feedback records: human responses to morning reports
CREATE TABLE IF NOT EXISTS feedback_records (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    report_id TEXT, -- reference to morning report notification
    candidate_id UUID REFERENCES policy_candidates(id) ON DELETE SET NULL,
    feedback_type TEXT NOT NULL, -- approval, rejection, direction, correction
    content TEXT NOT NULL, -- raw message
    parsed_intent TEXT, -- approve_routing_change, reject_platform, etc.
    applied BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_feedback_records_tenant ON feedback_records(tenant_id);

-- Per-decision-point exploration config
CREATE TABLE IF NOT EXISTS decision_point_config (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    decision_point TEXT NOT NULL, -- chat_response, agent_routing, code_task
    exploration_rate NUMERIC(4,3) NOT NULL DEFAULT 0.10,
    exploration_mode TEXT NOT NULL DEFAULT 'balanced', -- off, balanced, targeted
    target_platforms TEXT[] DEFAULT '{}',
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE(tenant_id, decision_point)
);
CREATE INDEX idx_decision_point_config_tenant ON decision_point_config(tenant_id);
