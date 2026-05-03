-- 114_user_preferences_value_json.sql
-- Generalize user_preferences to hold rich JSON payloads (gesture bindings + future).
-- Existing simple-string preferences (response_length, tone, emoji_usage, etc.) keep using `value`.
-- Rich payloads go in `value_json` (JSONB, capped at 64KB).

ALTER TABLE user_preferences
  ADD COLUMN IF NOT EXISTS value_json JSONB NULL;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'user_preferences_value_json_size_cap'
  ) THEN
    ALTER TABLE user_preferences
      ADD CONSTRAINT user_preferences_value_json_size_cap
      CHECK (value_json IS NULL OR octet_length(value_json::text) <= 65536);
  END IF;
END $$;

ALTER TABLE user_preferences
  ALTER COLUMN value DROP NOT NULL;

COMMENT ON COLUMN user_preferences.value_json IS
  'Optional rich JSON payload for preferences that exceed the 200-char value column. '
  'Used by gesture_bindings and similar preference types. Capped at 64KB.';

INSERT INTO _migrations(filename) VALUES ('114_user_preferences_value_json.sql')
ON CONFLICT DO NOTHING;
