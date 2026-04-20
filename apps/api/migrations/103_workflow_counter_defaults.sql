-- Migration 103: Make dynamic_workflow counter columns NOT NULL with defaults.
--
-- An earlier direct-SQL insert (Cardiac Report Generator for Brett's tenant)
-- left run_count / installs / rating / etc. as NULL, which caused
-- GET /dynamic-workflows/templates/browse to return HTTP 500 via Pydantic
-- ResponseValidationError (schema required non-null int). The Pydantic schema
-- has since been relaxed to Optional, but the DB-side invariant should also
-- hold so future partial inserts can't reintroduce the bug.

BEGIN;

-- Backfill NULLs to 0 before applying NOT NULL constraint.
UPDATE dynamic_workflows
SET run_count = COALESCE(run_count, 0),
    installs = COALESCE(installs, 0),
    rating = COALESCE(rating, 0)
WHERE run_count IS NULL OR installs IS NULL OR rating IS NULL;

-- Enforce non-null with sensible defaults on every counter/stat column.
ALTER TABLE dynamic_workflows
    ALTER COLUMN run_count SET DEFAULT 0,
    ALTER COLUMN run_count SET NOT NULL,
    ALTER COLUMN installs SET DEFAULT 0,
    ALTER COLUMN installs SET NOT NULL,
    ALTER COLUMN rating SET DEFAULT 0,
    ALTER COLUMN rating SET NOT NULL;

COMMIT;
