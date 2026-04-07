-- Memory-First Phase 1: extend conversation_episodes for window-based EpisodeWorkflow.
-- The existing table (migration 075) is reused; this adds the columns the
-- new workflow needs. All additive — no destructive changes.

ALTER TABLE conversation_episodes
    ADD COLUMN IF NOT EXISTS window_start TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS window_end TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS trigger_reason VARCHAR(30),
    ADD COLUMN IF NOT EXISTS agent_slug VARCHAR(100),
    ADD COLUMN IF NOT EXISTS generated_by VARCHAR(50);

-- Backfill existing rows so the unique constraint can be added.
UPDATE conversation_episodes
   SET window_start = created_at,
       window_end = created_at,
       trigger_reason = 'legacy',
       generated_by = 'unknown'
 WHERE window_start IS NULL;

-- Unique constraint prevents duplicate episodes from concurrent triggers.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'uk_conv_episodes_session_window'
    ) THEN
        ALTER TABLE conversation_episodes
            ADD CONSTRAINT uk_conv_episodes_session_window
            UNIQUE (session_id, window_start, window_end);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_conv_episodes_window
    ON conversation_episodes (session_id, window_end DESC);
