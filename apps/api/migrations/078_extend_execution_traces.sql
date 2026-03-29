-- Make task_id nullable (traces can belong to chat sessions without agent tasks)
ALTER TABLE execution_traces ALTER COLUMN task_id DROP NOT NULL;

-- Add session_id for chat-originated traces
ALTER TABLE execution_traces ADD COLUMN IF NOT EXISTS session_id UUID REFERENCES chat_sessions(id);
CREATE INDEX IF NOT EXISTS idx_execution_traces_session_id ON execution_traces(session_id);

-- Make step_order default to 0 for simple single-step traces
ALTER TABLE execution_traces ALTER COLUMN step_order SET DEFAULT 0;
