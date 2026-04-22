-- Migration: 095_agent_ownership_and_status
ALTER TABLE agents ADD COLUMN IF NOT EXISTS owner_user_id UUID REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE agents ADD COLUMN IF NOT EXISTS team_id UUID REFERENCES agent_groups(id) ON DELETE SET NULL;
ALTER TABLE agents ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'production';
ALTER TABLE agents ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1;
ALTER TABLE agents ADD COLUMN IF NOT EXISTS successor_agent_id UUID REFERENCES agents(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_agents_owner_user_id ON agents(owner_user_id);
CREATE INDEX IF NOT EXISTS idx_agents_team_id ON agents(team_id);
CREATE INDEX IF NOT EXISTS idx_agents_status ON agents(status);

INSERT INTO _migrations(filename) VALUES ('095_agent_ownership_and_status') ON CONFLICT DO NOTHING;
