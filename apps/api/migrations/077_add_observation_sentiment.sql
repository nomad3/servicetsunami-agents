ALTER TABLE knowledge_observations ADD COLUMN IF NOT EXISTS sentiment VARCHAR(20);
CREATE INDEX IF NOT EXISTS idx_obs_sentiment ON knowledge_observations(tenant_id, sentiment) WHERE sentiment IS NOT NULL;
