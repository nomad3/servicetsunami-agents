CREATE TABLE IF NOT EXISTS conversation_episodes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    session_id UUID REFERENCES chat_sessions(id) ON DELETE SET NULL,
    summary TEXT NOT NULL,
    key_topics JSONB DEFAULT '[]',
    key_entities JSONB DEFAULT '[]',
    mood VARCHAR(30),
    outcome VARCHAR(100),
    message_count INTEGER DEFAULT 0,
    source_channel VARCHAR(50),
    embedding vector(768),
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_episodes_tenant ON conversation_episodes(tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_episodes_session ON conversation_episodes(session_id);
CREATE INDEX IF NOT EXISTS idx_episodes_embedding ON conversation_episodes USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
