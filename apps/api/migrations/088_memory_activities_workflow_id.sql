-- apps/api/migrations/088_memory_activities_workflow_id.sql
-- Memory-First Phase 1: add workflow_id (workflow type) to memory_activities.
-- workflow_run_id already exists. Source identifiers (source_id, target_table,
-- target_id, actor_slug) are stored in the existing `metadata` JSON column —
-- no schema change needed for those.

ALTER TABLE memory_activities
    ADD COLUMN IF NOT EXISTS workflow_id VARCHAR(200);

CREATE INDEX IF NOT EXISTS idx_memory_activities_workflow
    ON memory_activities (workflow_id) WHERE workflow_id IS NOT NULL;

-- Expression index on the metadata JSON column for source-id dedup lookups.
CREATE INDEX IF NOT EXISTS idx_memory_activities_source_ref
    ON memory_activities ((metadata->>'source_id'), (metadata->>'source_type'))
    WHERE metadata IS NOT NULL;
