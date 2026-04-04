-- Migration 083: Add session_journals table for Gap 1 (Continuity)

CREATE TABLE session_journals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,

    -- Period covered by this journal entry
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    period_type VARCHAR(50) DEFAULT 'week',

    -- Synthesized narrative
    summary TEXT NOT NULL,

    -- Extracted context (JSONB arrays)
    key_themes JSONB DEFAULT '[]'::jsonb,
    key_accomplishments JSONB DEFAULT '[]'::jsonb,
    key_challenges JSONB DEFAULT '[]'::jsonb,
    mentioned_people JSONB DEFAULT '[]'::jsonb,
    mentioned_projects JSONB DEFAULT '[]'::jsonb,

    -- Metadata
    episode_count INTEGER DEFAULT 0,
    message_count INTEGER DEFAULT 0,
    activity_score INTEGER DEFAULT 0,

    -- Embedding for semantic search (pgvector)
    embedding vector(768),

    -- Timestamps
    created_at TIMESTAMP DEFAULT now() NOT NULL,
    updated_at TIMESTAMP DEFAULT now() NOT NULL
);

CREATE INDEX idx_session_journals_tenant_period
    ON session_journals(tenant_id, period_end DESC);

CREATE INDEX idx_session_journals_embedding
    ON session_journals
    USING ivfflat (embedding vector_cosine_ops)
    WHERE embedding IS NOT NULL;
