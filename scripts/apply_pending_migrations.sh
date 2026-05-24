#!/usr/bin/env bash
# apply_pending_migrations.sh — apply any apps/api/migrations/*.sql not
# yet recorded in the `_migrations` tracker table.
#
# Lives in scripts/ so the CI deploy workflow and a developer running
# `./scripts/apply_pending_migrations.sh` after `docker compose up -d`
# both hit the same code path. Idempotent — re-running it after a
# successful pass is a no-op.
#
# Tracker format quirk: rows inserted before 2026-04 use the bare stem
# (`091_blackboard_chat_session_and_source_node`); rows inserted after
# the PR-Q1 backfill (#402) use the full filename including `.sql`.
# We compare on the stem so both are treated as applied, and write
# new rows with the full filename to match the documented contract.
#
# Exit codes:
#   0  — success (zero or more migrations applied)
#   1  — a migration failed; psql output is preserved and the
#        tracker row is NOT inserted, so a re-run will retry.
#   2  — db container not running / not reachable.
set -euo pipefail

cd "$(dirname "$0")/.."

DB_CONTAINER="${DB_CONTAINER:-servicetsunami-agents-db-1}"
DB_NAME="${DB_NAME:-agentprovision}"
MIG_DIR="apps/api/migrations"

# ── preflight ──────────────────────────────────────────────────────
if ! docker ps --format '{{.Names}}' | grep -qx "$DB_CONTAINER"; then
  echo "[migrations] error: db container '$DB_CONTAINER' is not running" >&2
  exit 2
fi

if ! docker exec "$DB_CONTAINER" pg_isready -U postgres -d "$DB_NAME" >/dev/null 2>&1; then
  echo "[migrations] error: postgres in '$DB_CONTAINER' is not ready" >&2
  exit 2
fi

if [ ! -d "$MIG_DIR" ]; then
  echo "[migrations] error: $MIG_DIR not found (run from repo root)" >&2
  exit 2
fi

# ── discover ───────────────────────────────────────────────────────
# Build {applied_stems} from the tracker. Strip a trailing `.sql` so
# both legacy (bare stem) and current (`.sql`) rows count as applied.
# The query is wrapped so a transient psql failure doesn't get
# converted to a "no migrations applied" lie by `set -e` + pipefail
# silently terminating the pipeline. We capture stderr too and bail
# loudly if the read fails — better to fail the deploy than to
# re-apply every shipped migration on a flaky connection.
if ! applied_stems_raw=$(docker exec "$DB_CONTAINER" psql -U postgres -d "$DB_NAME" -At \
      -c "SELECT filename FROM _migrations;" 2>&1); then
  echo "[migrations] error: could not read _migrations tracker:" >&2
  echo "$applied_stems_raw" >&2
  exit 2
fi
applied_stems=$(printf '%s\n' "$applied_stems_raw" | sed 's/\.sql$//' | sort -u)

# Build {file_stems} from the working tree. Down migrations are rollback
# artifacts and must never be auto-applied by the forward migration runner.
#
# `shopt -s nullglob` makes an unmatched glob expand to nothing
# rather than the literal pattern, so an empty migrations directory
# under `set -euo pipefail` doesn't abort the script before the
# empty-list guard below can run (reviewer B1, 2026-05-12).
shopt -s nullglob
file_paths_arr=()
for path in "$MIG_DIR"/*.sql; do
  case "$(basename "$path")" in
    *.down.sql) continue ;;
  esac
  file_paths_arr+=("$path")
done
shopt -u nullglob
if [ ${#file_paths_arr[@]} -eq 0 ]; then
  echo "[migrations] no .sql files in $MIG_DIR — nothing to do"
  exit 0
fi
# Stable sort by filename. The migrations are numerically prefixed
# (NNN_name.sql) so lexical sort = numeric sort up to 999.
IFS=$'\n' file_paths=$(printf '%s\n' "${file_paths_arr[@]}" | sort)
unset IFS

# ── plan ──────────────────────────────────────────────────────────
pending=()
while IFS= read -r path; do
  base=$(basename "$path")
  stem="${base%.sql}"
  if ! printf '%s\n' "$applied_stems" | grep -qFx "$stem"; then
    pending+=("$base")
  fi
done <<< "$file_paths"

if [ ${#pending[@]} -eq 0 ]; then
  echo "[migrations] no pending migrations (applied=$(printf '%s\n' "$applied_stems" | grep -c .))"
  exit 0
fi

echo "[migrations] applying ${#pending[@]} pending migration(s):"
for f in "${pending[@]}"; do echo "  - $f"; done

# ── apply ─────────────────────────────────────────────────────────
applied_count=0
for base in "${pending[@]}"; do
  src="$MIG_DIR/$base"
  echo "[migrations] → $base"
  if ! docker cp "$src" "$DB_CONTAINER:/tmp/_pending_migration.sql"; then
    echo "[migrations] error: docker cp failed for $base" >&2
    exit 1
  fi

  # ON_ERROR_STOP=1 makes psql exit non-zero on the first failing
  # statement instead of plowing through the rest of the file with
  # the txn poisoned. Without it a half-applied migration looks like
  # success and the tracker row would be inserted — exactly the kind
  # of drift this script exists to prevent.
  if ! docker exec "$DB_CONTAINER" psql -v ON_ERROR_STOP=1 -U postgres -d "$DB_NAME" \
        -f /tmp/_pending_migration.sql; then
    echo "[migrations] error: $base failed — tracker NOT updated, re-run will retry" >&2
    exit 1
  fi

  # Insert AFTER the migration succeeds so a failed apply doesn't
  # leave a "this is done" lie in the tracker. Use the full filename
  # (`.sql`) per the current contract — legacy bare-stem rows still
  # de-dup via the stem-comparison above.
  #
  # `psql -v` + the `:'fn'` substitution binds the filename as a
  # single-quoted literal so a future migration named like
  # `091_o'malley.sql` can't break the INSERT. psql variable
  # substitution only happens when SQL comes in via stdin (NOT
  # `-c`), so we feed the statement via heredoc. Today's filename
  # convention forbids quotes, but the failure mode (silent tracker
  # drift → infinite retry on every deploy) is exactly the bug class
  # this script exists to prevent (reviewer I1, 2026-05-12).
  docker exec -i "$DB_CONTAINER" psql -v "fn=$base" -U postgres -d "$DB_NAME" >/dev/null <<'SQL'
INSERT INTO _migrations (filename) VALUES (:'fn') ON CONFLICT DO NOTHING;
SQL
  applied_count=$((applied_count + 1))
done

echo "[migrations] done — applied $applied_count migration(s)"
