-- 046_rl_memory_distributed.sql
-- RL & Memory for Distributed CLI Agents — schema foundation
-- Note: Some columns may already exist from migration 049 (git history tracking).
-- All statements use IF NOT EXISTS / IF NOT EXISTS patterns for idempotency.

-- 1. Knowledge Observations table (referenced by MCP record_observation tool)
-- Note: table may already exist from migration 030
CREATE TABLE IF NOT EXISTS knowledge_observations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    entity_id UUID REFERENCES knowledge_entities(id) ON DELETE SET NULL,
    observation_text TEXT NOT NULL,
    observation_type VARCHAR(50) DEFAULT 'fact',
    source_type VARCHAR(50) DEFAULT 'conversation',
    source_platform VARCHAR(50),
    source_agent VARCHAR(100),
    confidence FLOAT DEFAULT 1.0,
    embedding vector(768),
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_knowledge_obs_tenant ON knowledge_observations(tenant_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_obs_entity ON knowledge_observations(entity_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_obs_type ON knowledge_observations(observation_type);

-- 2. Knowledge Entity History table (referenced by MCP update_entity tool)
-- Note: table may already exist from migration 030
CREATE TABLE IF NOT EXISTS knowledge_entity_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_id UUID NOT NULL REFERENCES knowledge_entities(id) ON DELETE CASCADE,
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    version INTEGER NOT NULL DEFAULT 1,
    properties_snapshot JSONB,
    attributes_snapshot JSONB,
    change_reason TEXT,
    changed_by UUID,
    changed_by_platform VARCHAR(50),
    changed_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_entity_history_entity ON knowledge_entity_history(entity_id);
CREATE INDEX IF NOT EXISTS idx_entity_history_tenant ON knowledge_entity_history(tenant_id);

-- 3. Add recall/reference/feedback counters to knowledge_entities
ALTER TABLE knowledge_entities ADD COLUMN IF NOT EXISTS recall_count INTEGER DEFAULT 0;
ALTER TABLE knowledge_entities ADD COLUMN IF NOT EXISTS reference_count INTEGER DEFAULT 0;
ALTER TABLE knowledge_entities ADD COLUMN IF NOT EXISTS feedback_score FLOAT DEFAULT 0.0;
ALTER TABLE knowledge_entities ADD COLUMN IF NOT EXISTS last_recalled_at TIMESTAMP;

-- 4. Add embedding column to agent_memories for direct semantic search
ALTER TABLE agent_memories ADD COLUMN IF NOT EXISTS content_embedding vector(768);

-- 5. Add span_id to rl_experiences for observability correlation
ALTER TABLE rl_experiences ADD COLUMN IF NOT EXISTS span_id UUID;

-- 6. Add source tracking to knowledge_entities for cross-platform quality
ALTER TABLE knowledge_entities ADD COLUMN IF NOT EXISTS extraction_platform VARCHAR(50);
ALTER TABLE knowledge_entities ADD COLUMN IF NOT EXISTS extraction_agent VARCHAR(100);
