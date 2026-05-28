#!/usr/bin/env bash
# Smoke runner for every scripts/smell/* script.
# Invokes each with safe defaults and pipes through `python -m json.tool` to
# assert the output is valid JSON. Exits non-zero on the first failure.
set -euo pipefail

cd "$(dirname "$0")/../.."

scripts=(
  "python3 scripts/smell/_findings.py"
  "python3 scripts/smell/unmounted_routes.py"
  "python3 scripts/smell/unimported_symbols.py"
  "python3 scripts/smell/unregistered_workflows.py"
  "node    scripts/smell/unrouted_pages.js"
  "python3 scripts/smell/migration_drift.py"
  "python3 scripts/smell/reexport_only.py"
  "python3 scripts/smell/docstring_redundancy.py"
  "python3 scripts/smell/missing_session_event.py"
  "python3 scripts/smell/missing_rl_log.py"
  "python3 scripts/smell/tenant_filter_check.py"
  "python3 scripts/smell/log_errors.py --since 5m"
  "python3 scripts/smell/nesting_depth.py"
)

fail=0
for s in "${scripts[@]}"; do
  printf '  %-60s ... ' "$s"
  if out=$(eval "$s" 2>/dev/null) && printf '%s' "$out" | python3 -m json.tool >/dev/null 2>&1; then
    fc=$(printf '%s' "$out" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['preflight']['exit_summary'], len(d['findings']))")
    echo "OK ($fc)"
  else
    echo "FAIL"
    fail=1
  fi
done

if [ "$fail" -ne 0 ]; then
  echo "Phase-0 smoke FAILED"
  exit 1
fi
echo "Phase-0 smoke OK ($(date -u +%Y-%m-%dT%H:%M:%SZ))"
