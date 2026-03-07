-- Migration 039: Extend metadata richness across knowledge, memory, and traces
-- Adds provenance, attribution, cost tracking, and temporal validity fields

-- ═══════════════════════════════════════════════════════════════
-- 1. Knowledge Relations: add versioning and temporal validity
-- ═══════════════════════════════════════════════════════════════
ALTER TABLE knowledge_relations
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT NOW(),
    ADD COLUMN IF NOT EXISTS updated_by_agent_id UUID REFERENCES agents(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS confidence_source VARCHAR(50) DEFAULT 'extraction',
    ADD COLUMN IF NOT EXISTS valid_from TIMESTAMP,
    ADD COLUMN IF NOT EXISTS valid_until TIMESTAMP;

-- ═══════════════════════════════════════════════════════════════
-- 2. Knowledge Entities: add extraction provenance
-- ═══════════════════════════════════════════════════════════════
ALTER TABLE knowledge_entities
    ADD COLUMN IF NOT EXISTS updated_by_agent_id UUID REFERENCES agents(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS extraction_model VARCHAR(100),
    ADD COLUMN IF NOT EXISTS data_quality_score FLOAT,
    ADD COLUMN IF NOT EXISTS tags JSONB DEFAULT '[]',
    ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP;

-- ═══════════════════════════════════════════════════════════════
-- 3. Agent Memories: add confidence, tags, entity links
-- ═══════════════════════════════════════════════════════════════
ALTER TABLE agent_memories
    ADD COLUMN IF NOT EXISTS confidence FLOAT DEFAULT 1.0,
    ADD COLUMN IF NOT EXISTS tags JSONB DEFAULT '[]',
    ADD COLUMN IF NOT EXISTS related_entity_ids JSONB DEFAULT '[]',
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT NOW(),
    ADD COLUMN IF NOT EXISTS decay_rate FLOAT DEFAULT 1.0;

-- ═══════════════════════════════════════════════════════════════
-- 4. Memory Activities: add agent and user attribution
-- ═══════════════════════════════════════════════════════════════
ALTER TABLE memory_activities
    ADD COLUMN IF NOT EXISTS agent_id UUID REFERENCES agents(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS task_id UUID REFERENCES agent_tasks(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS change_delta JSONB;

-- ═══════════════════════════════════════════════════════════════
-- 5. Execution Traces: add error details, LLM cost, nesting
-- ═══════════════════════════════════════════════════════════════
ALTER TABLE execution_traces
    ADD COLUMN IF NOT EXISTS error_message TEXT,
    ADD COLUMN IF NOT EXISTS input_tokens INTEGER,
    ADD COLUMN IF NOT EXISTS output_tokens INTEGER,
    ADD COLUMN IF NOT EXISTS cost_usd NUMERIC(10, 6),
    ADD COLUMN IF NOT EXISTS parent_step_id UUID REFERENCES execution_traces(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS retry_count INTEGER DEFAULT 0;

-- ═══════════════════════════════════════════════════════════════
-- 6. Skill Configs: add usage tracking
-- ═══════════════════════════════════════════════════════════════
ALTER TABLE skill_configs
    ADD COLUMN IF NOT EXISTS last_used_at TIMESTAMP,
    ADD COLUMN IF NOT EXISTS call_count INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS error_count INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS success_count INTEGER DEFAULT 0;

-- ═══════════════════════════════════════════════════════════════
-- 7. Indexes for new query patterns
-- ═══════════════════════════════════════════════════════════════
CREATE INDEX IF NOT EXISTS idx_knowledge_entities_deleted_at ON knowledge_entities(deleted_at) WHERE deleted_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_knowledge_entities_tags ON knowledge_entities USING GIN(tags);
CREATE INDEX IF NOT EXISTS idx_agent_memories_tags ON agent_memories USING GIN(tags);
CREATE INDEX IF NOT EXISTS idx_agent_memories_related_entities ON agent_memories USING GIN(related_entity_ids);
CREATE INDEX IF NOT EXISTS idx_memory_activities_agent_id ON memory_activities(agent_id) WHERE agent_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_execution_traces_parent ON execution_traces(parent_step_id) WHERE parent_step_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_execution_traces_cost ON execution_traces(cost_usd) WHERE cost_usd IS NOT NULL;
