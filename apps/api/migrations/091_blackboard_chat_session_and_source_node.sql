-- Migration 091: Add chat_session_id to blackboards and source_node_id to blackboard_entries

-- Link blackboards to the chat session that spawned them
ALTER TABLE blackboards ADD COLUMN IF NOT EXISTS chat_session_id UUID REFERENCES chat_sessions(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_blackboards_chat_session_id ON blackboards(chat_session_id);

-- Federation-readiness: track which node authored an entry (NULL = local)
ALTER TABLE blackboard_entries ADD COLUMN IF NOT EXISTS source_node_id VARCHAR(100) DEFAULT NULL;

INSERT INTO _migrations (filename)
VALUES ('091_blackboard_chat_session_and_source_node')
ON CONFLICT (filename) DO NOTHING;
