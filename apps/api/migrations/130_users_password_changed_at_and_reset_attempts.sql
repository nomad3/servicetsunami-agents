-- 130_users_password_changed_at_and_reset_attempts.sql
-- Security hardening for password recovery (PR-RESET-SEC, 2026-05-12).
--
-- B-4: invalidate existing JWTs after a successful password reset.
-- We can't sign-flip every issued JWT (stateless), but we CAN add a
-- per-user "password_changed_at" floor and reject any access token
-- whose `iat` is older than that timestamp inside
-- `deps.get_current_active_user`. Equivalent to a per-user
-- token-generation counter without adding a new claim, because we
-- already have `iat` in the JWT.
--
-- I-1: per-token attempt counter. After N consecutive failed reset
-- attempts for a given user, null out the token so a partially-
-- leaked token can't be brute-forced within the slowapi 5/hr per-IP
-- limit (which is per-IP, not per-user — two IPs each get 5 tries).
--
-- Both columns nullable so the migration is a pure additive change
-- and old rows behave as "never reset" (token comparison reads
-- NULL → no special treatment).

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS password_changed_at TIMESTAMP,
    ADD COLUMN IF NOT EXISTS password_reset_attempts INTEGER DEFAULT 0,
    -- B-5: requester-confirmer binding cookie value. We store the
    -- correlation ID hash (NOT plaintext) alongside the reset token
    -- so the confirm endpoint can verify the cookie came from the
    -- browser that initiated the recovery. NULL on rows that don't
    -- have an in-flight recovery — same lifecycle as the existing
    -- password_reset_token fields.
    ADD COLUMN IF NOT EXISTS password_reset_csrf_hash VARCHAR(64);

-- Backfill `password_changed_at` for existing users with `now()` so
-- the JWT-iat check doesn't lock everyone out on the next deploy.
-- This is a one-time grandfather; new JWTs minted after deploy will
-- pass the floor check trivially.
--
-- NIT-2 (round-7 review): this is a single full-table UPDATE. On a
-- multi-million-row `users` table that would acquire a long row-level
-- write lock and stall logins during deploy. Current tenant scale
-- (< 10k users platform-wide as of 2026-05-17) makes the simple form
-- safe — the UPDATE completes in well under a second. If/when this
-- migration is re-run on a much larger dataset (or a fresh tenant
-- merge balloons row count past ~500k), refactor to a batched UPDATE
-- with a `WHERE id IN (SELECT id ... LIMIT 10000)` loop and an
-- explicit transaction per batch. Today the one-shot is the right
-- call for operational simplicity.
UPDATE users
SET password_changed_at = now()
WHERE password_changed_at IS NULL;
