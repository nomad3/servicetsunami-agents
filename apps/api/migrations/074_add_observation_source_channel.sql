ALTER TABLE knowledge_observations ADD COLUMN IF NOT EXISTS source_channel VARCHAR(50);
ALTER TABLE knowledge_observations ADD COLUMN IF NOT EXISTS source_ref VARCHAR(500);

CREATE INDEX IF NOT EXISTS idx_knowledge_observations_source_channel
    ON knowledge_observations(source_channel) WHERE source_channel IS NOT NULL;
