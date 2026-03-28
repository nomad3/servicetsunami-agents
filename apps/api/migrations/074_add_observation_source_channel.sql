ALTER TABLE knowledge_observations ADD COLUMN IF NOT EXISTS source_channel VARCHAR(50);
ALTER TABLE knowledge_observations ADD COLUMN IF NOT EXISTS source_ref VARCHAR(500);
