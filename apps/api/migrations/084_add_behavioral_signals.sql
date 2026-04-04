-- Migration 084: Add behavioral_signals table for Gap 2 (Learning)

CREATE TABLE behavioral_signals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,

    -- Source context
    message_id UUID,
    session_id UUID,

    -- Suggestion details
    suggestion_type VARCHAR(50) NOT NULL,
    suggestion_text TEXT NOT NULL,
    suggestion_tag VARCHAR(50),

    -- Action tracking
    acted_on BOOLEAN,           -- NULL=pending, TRUE=acted, FALSE=ignored
    action_timestamp TIMESTAMP,
    action_evidence TEXT,

    -- Signal quality
    confidence FLOAT DEFAULT 0.0,
    match_score FLOAT,

    -- Expiry
    expires_after_hours INTEGER DEFAULT 24,

    -- Extra context
    context JSONB DEFAULT '{}'::jsonb,

    -- Embedding for semantic matching
    embedding vector(768),

    created_at TIMESTAMP DEFAULT now() NOT NULL,
    updated_at TIMESTAMP DEFAULT now() NOT NULL
);

CREATE INDEX idx_behavioral_signals_tenant ON behavioral_signals(tenant_id);
CREATE INDEX idx_behavioral_signals_pending
    ON behavioral_signals(tenant_id, acted_on)
    WHERE acted_on IS NULL;
CREATE INDEX idx_behavioral_signals_type ON behavioral_signals(tenant_id, suggestion_type);
CREATE INDEX idx_behavioral_signals_embedding
    ON behavioral_signals
    USING ivfflat (embedding vector_cosine_ops)
    WHERE embedding IS NOT NULL;
