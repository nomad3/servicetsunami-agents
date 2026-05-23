#!/usr/bin/env bash
# PR3 / F2 — dual-source secret loader called by the deploy workflow.
#
# For each of the 4 PR3 secrets:
#   1. Try the macOS login keychain via `security find-generic-password -w`.
#   2. Fall back to the legacy $HOME/Documents/GitHub/agentprovision-agents/<rel>
#      path if the Keychain entry is missing/empty (coexistence window).
#   3. Write the content into the workspace destination with 0600 perms.
#
# A future cleanup commit (post-PR3, after the verification gate
# passes) will delete the $HOME source files and drop the fallback
# branch. The Keychain side stays.
#
# Usage (from the deploy workflow, repo root cwd):
#   bash scripts/runner-secrets/load-from-keychain.sh
#
# Exit codes:
#   0  — all 4 secrets loaded (mix of Keychain + fallback allowed)
#   1  — at least one secret missing from BOTH sources
#
# Logs the source chosen per secret (Keychain vs fallback) but never
# the secret value itself.

set -euo pipefail

RUNNER_USER="${RUNNER_USER:-${USER:-nomade}}"
HOME_REPO="${HOME_REPO:-$HOME/Documents/GitHub/agentprovision-agents}"

# (keychain-service, workspace-dest, $HOME-fallback-rel) triplets.
SECRETS=(
  "agentprovision-cloudflared-creds:cloudflared/credentials.json:cloudflared/credentials.json"
  "agentprovision-cloudflared-cert:cloudflared/cert.pem:cloudflared/cert.pem"
  "agentprovision-api-env:apps/api/.env:apps/api/.env"
  "agentprovision-root-env:.env:PRODUCTION.env"
)

fail=0

read_keychain() {
  # Returns the secret on stdout; non-zero exit if not found.
  local svc="$1"
  security find-generic-password -s "$svc" -a "$RUNNER_USER" -w 2>/dev/null
}

write_dest() {
  # Write content (stdin) into destination atomically with 0600 perms.
  # security ... -w appends a trailing newline; we leave it intact —
  # .env files tolerate it, JSON tolerates it, PEM expects it.
  local dst="$1"
  mkdir -p "$(dirname "$dst")"
  umask 077
  cat > "$dst.tmp"
  mv "$dst.tmp" "$dst"
  chmod 600 "$dst"
}

for triplet in "${SECRETS[@]}"; do
  IFS=':' read -r svc dst fb_rel <<< "$triplet"
  fb_abs="$HOME_REPO/$fb_rel"

  if val="$(read_keychain "$svc")" && [ -n "$val" ]; then
    printf '%s' "$val" | write_dest "$dst"
    echo "[keychain] $dst  ←  $svc"
    continue
  fi

  if [ -f "$fb_abs" ] && [ -s "$fb_abs" ]; then
    cp "$fb_abs" "$dst.tmp"
    mv "$dst.tmp" "$dst"
    chmod 600 "$dst"
    echo "[fallback] $dst  ←  $fb_abs"
    continue
  fi

  echo "ERROR: no source for $dst — Keychain[$svc] absent AND fallback[$fb_abs] missing" >&2
  fail=1
done

if (( fail )); then
  echo "load-from-keychain: one or more secrets missing; aborting deploy" >&2
  exit 1
fi

echo "load-from-keychain: all 4 secrets loaded."
