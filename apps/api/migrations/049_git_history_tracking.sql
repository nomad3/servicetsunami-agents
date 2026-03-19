-- 049_git_history_tracking.sql
-- Git History Tracking: extends knowledge graph for git commits, contributors, PRs, and file hotspots.
-- Adds columns for tracking entity recall/reference/feedback counters and git extraction metadata.

-- 1. Add recall/reference/feedback counters to knowledge_entities (for memory quality tracking)
ALTER TABLE knowledge_entities ADD COLUMN IF NOT EXISTS recall_count INTEGER DEFAULT 0;
ALTER TABLE knowledge_entities ADD COLUMN IF NOT EXISTS reference_count INTEGER DEFAULT 0;
ALTER TABLE knowledge_entities ADD COLUMN IF NOT EXISTS feedback_score FLOAT DEFAULT 0.0;
ALTER TABLE knowledge_entities ADD COLUMN IF NOT EXISTS last_recalled_at TIMESTAMP;

-- 2. Add extraction source tracking to knowledge_entities (cross-platform quality)
ALTER TABLE knowledge_entities ADD COLUMN IF NOT EXISTS extraction_platform VARCHAR(50);
ALTER TABLE knowledge_entities ADD COLUMN IF NOT EXISTS extraction_agent VARCHAR(100);

-- 3. Add entity_id to knowledge_observations (link observations to entities)
ALTER TABLE knowledge_observations ADD COLUMN IF NOT EXISTS entity_id UUID REFERENCES knowledge_entities(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_knowledge_obs_entity ON knowledge_observations(entity_id);

-- 4. Add source_platform and source_agent to knowledge_observations (git provenance)
ALTER TABLE knowledge_observations ADD COLUMN IF NOT EXISTS source_platform VARCHAR(50);
ALTER TABLE knowledge_observations ADD COLUMN IF NOT EXISTS source_agent VARCHAR(100);

-- 5. Add changed_by_platform to knowledge_entity_history
ALTER TABLE knowledge_entity_history ADD COLUMN IF NOT EXISTS changed_by_platform VARCHAR(50);
ALTER TABLE knowledge_entity_history ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS idx_entity_history_tenant ON knowledge_entity_history(tenant_id);

-- 6. Add span_id to rl_experiences for observability correlation
ALTER TABLE rl_experiences ADD COLUMN IF NOT EXISTS span_id UUID;

-- 7. Index for observation type queries (git_commit, git_pr, file_hotspot)
CREATE INDEX IF NOT EXISTS idx_knowledge_obs_type ON knowledge_observations(observation_type);
CREATE INDEX IF NOT EXISTS idx_knowledge_obs_tenant ON knowledge_observations(tenant_id);
