-- Add session_blob column to persist neonize WhatsApp session data across pod restarts.
-- The blob stores a gzip-compressed copy of the neonize SQLite DB file.
ALTER TABLE channel_accounts ADD COLUMN IF NOT EXISTS session_blob BYTEA;
