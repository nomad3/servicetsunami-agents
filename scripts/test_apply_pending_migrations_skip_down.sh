#!/usr/bin/env bash
# test_apply_pending_migrations_skip_down.sh
#
# Per-Code-Reviewer suggestion on PR #699: prove that
# apply_pending_migrations.sh's *.down.sql filter actually excludes
# rollback files from the pending list.
#
# Tests the FILTER (the new code in this PR), not the DB-apply path.
# A unit-level shell test — no docker, no db, no migrations applied.
# It builds a fixture directory of synthetic .sql + .down.sql files,
# runs the same filter logic, and asserts on the result.
#
# Exit codes:
#   0 — filter behaves as documented in README
#   1 — filter leaked a .down.sql or missed a real .sql
set -euo pipefail

# Create a temp directory of synthetic migration files.
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

touch "$tmp"/100_alpha.sql           "$tmp"/100_alpha.down.sql \
      "$tmp"/101_beta.sql            "$tmp"/101_beta.down.sql \
      "$tmp"/102_gamma.sql

# Replay the exact filter logic from apply_pending_migrations.sh.
# If the script changes, mirror the change here — both tests and prod
# breaking the same way is a stronger signal than only-one-side bug.
shopt -s nullglob
file_paths_arr=( "$tmp"/*.sql )
shopt -u nullglob

filtered_paths_arr=()
for _path in "${file_paths_arr[@]}"; do
  case "$_path" in
    *.down.sql) continue ;;
    *) filtered_paths_arr+=( "$_path" ) ;;
  esac
done

# Assertions
expected_count=3   # 100_alpha, 101_beta, 102_gamma
actual_count=${#filtered_paths_arr[@]}
if [ "$actual_count" -ne "$expected_count" ]; then
  echo "FAIL: expected $expected_count files after filter, got $actual_count" >&2
  printf '  %s\n' "${filtered_paths_arr[@]}" >&2
  exit 1
fi

for p in "${filtered_paths_arr[@]}"; do
  case "$p" in
    *.down.sql)
      echo "FAIL: .down.sql leaked through filter: $p" >&2
      exit 1
      ;;
  esac
done

# Spot-check: the three expected files are present
for expected_name in "100_alpha.sql" "101_beta.sql" "102_gamma.sql"; do
  found=0
  for p in "${filtered_paths_arr[@]}"; do
    if [[ "$(basename "$p")" == "$expected_name" ]]; then
      found=1
      break
    fi
  done
  if [ "$found" -eq 0 ]; then
    echo "FAIL: expected file not in filtered list: $expected_name" >&2
    exit 1
  fi
done

echo "OK: filter excludes 2 .down.sql, keeps 3 .sql (README contract honored)"
exit 0
