-- apps/api/migrations/087_add_visibility_scoping.sql
-- Memory-First Phase 1: multi-agent visibility scoping (design doc §7).
-- Adds visibility + visible_to to 5 memory tables. Default 'tenant_wide'
-- preserves current behavior. Also backfills owner_agent_slug on
-- knowledge_entities and agent_memories where it was missing — design
-- doc §4.3 said it "already exists on most of these" but the reality
-- was only commitment_records + goal_records had it.

-- 1. Add owner_agent_slug to the tables that were missing it.
ALTER TABLE knowledge_entities
    ADD COLUMN IF NOT EXISTS owner_agent_slug VARCHAR(100);

ALTER TABLE agent_memories
    ADD COLUMN IF NOT EXISTS owner_agent_slug VARCHAR(100);

-- 2. Add visibility + visible_to to all 5 scoped memory tables.
DO $$
DECLARE
    t TEXT;
BEGIN
    FOR t IN SELECT unnest(ARRAY[
        'knowledge_entities',
        'commitment_records',
        'goal_records',
        'agent_memories',
        'behavioral_signals'
    ]) LOOP
        EXECUTE format(
            'ALTER TABLE %I
                 ADD COLUMN IF NOT EXISTS visibility VARCHAR(20) NOT NULL DEFAULT ''tenant_wide'',
                 ADD COLUMN IF NOT EXISTS visible_to TEXT[]', t);
    END LOOP;
END $$;

-- 3. Composite indexes for the recall visibility filter.
-- Partial indexes (WHERE visibility != 'tenant_wide') keep them tiny —
-- the vast majority of records are tenant_wide and don't need the index.

CREATE INDEX IF NOT EXISTS idx_knowledge_entities_tenant_visibility_owner
    ON knowledge_entities (tenant_id, visibility, owner_agent_slug)
    WHERE visibility != 'tenant_wide';

CREATE INDEX IF NOT EXISTS idx_knowledge_entities_visible_to_gin
    ON knowledge_entities USING GIN (visible_to)
    WHERE visibility = 'agent_group';

CREATE INDEX IF NOT EXISTS idx_commitments_tenant_visibility_owner
    ON commitment_records (tenant_id, visibility, owner_agent_slug)
    WHERE visibility != 'tenant_wide';

CREATE INDEX IF NOT EXISTS idx_agent_memories_tenant_visibility_owner
    ON agent_memories (tenant_id, visibility, owner_agent_slug)
    WHERE visibility != 'tenant_wide';
