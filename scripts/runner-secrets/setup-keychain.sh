#!/usr/bin/env bash
# PR3 / F2 — Keychain migration: one-time setup on the runner Mac.
#
# Adds (or updates with -U) four generic-password entries to the
# login keychain so the GH Actions runner can read them instead of
# the $HOME files that F2 closes out. See
# docs/superpowers/specs/2026-05-22-subproject-a-infra-secret-hardening-design.md §5 PR3.
#
# Idempotent: -U updates if the entry already exists; safe to re-run
# after rotating a source file.
#
# Run from the repo root on Simon's runner Mac (the host that runs
# the self-hosted GH Actions runner). Keychain prompts may appear if
# the login keychain is locked — approve them.
#
#   bash scripts/runner-secrets/setup-keychain.sh
#
# Verifies each entry by reading it back and comparing byte length
# against the source file. Fails loudly if anything mismatches.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ACCOUNT="${USER:-nomade}"

# (service-name, source-path-relative-to-repo-root) pairs.
ENTRIES=(
  "agentprovision-cloudflared-creds:cloudflared/credentials.json"
  "agentprovision-cloudflared-cert:cloudflared/cert.pem"
  "agentprovision-api-env:apps/api/.env"
  "agentprovision-root-env:PRODUCTION.env"
)

red()   { printf '\033[31m%s\033[0m\n' "$*" >&2; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }
blue()  { printf '\033[34m%s\033[0m\n' "$*"; }

blue "Repo:    $REPO_ROOT"
blue "Account: $ACCOUNT"
echo

# Pre-flight: all four source files must exist + be non-empty.
for entry in "${ENTRIES[@]}"; do
  rel="${entry#*:}"
  abs="$REPO_ROOT/$rel"
  if [[ ! -f "$abs" ]]; then
    red "MISSING source file: $abs"
    exit 1
  fi
  if [[ ! -s "$abs" ]]; then
    red "EMPTY source file: $abs"
    exit 1
  fi
done
green "Pre-flight OK — all 4 source files present."
echo

# Add or update each entry.
for entry in "${ENTRIES[@]}"; do
  svc="${entry%%:*}"
  rel="${entry#*:}"
  abs="$REPO_ROOT/$rel"
  src_bytes="$(wc -c < "$abs" | tr -d ' ')"

  blue "→ $svc  (from $rel, $src_bytes bytes)"

  # -U updates if the entry exists; otherwise creates.
  # -s service, -a account, -w password value.
  # Note: -w "$(cat file)" briefly exposes the value in argv to
  # other processes on this host. Acceptable on the runner Mac
  # (single-user); the spec already accepts this trade-off.
  security add-generic-password \
    -U \
    -s "$svc" \
    -a "$ACCOUNT" \
    -w "$(cat "$abs")"

  # Read back + length-compare. We avoid printing the secret.
  readback_bytes="$(security find-generic-password -s "$svc" -a "$ACCOUNT" -w 2>/dev/null | wc -c | tr -d ' ')"
  # `security ... -w` appends a trailing newline; allow ±1.
  delta=$(( readback_bytes - src_bytes ))
  if (( delta < -1 || delta > 1 )); then
    red "  VERIFY FAILED: readback=$readback_bytes src=$src_bytes (delta=$delta)"
    exit 1
  fi
  green "  verified ($readback_bytes bytes)"
done

echo
green "All 4 Keychain entries set + verified."
echo
blue "Next steps (manual, not in this script):"
echo "  1. Confirm: security find-generic-password -s agentprovision-api-env -a $ACCOUNT 2>&1 | head -5"
echo "  2. Do NOT delete the \$HOME source files yet — PR3 ships a dual-source loader first."
echo "  3. After the next clean deploy verifies the Keychain read path, the cleanup commit removes the \$HOME files."
echo "  4. BEFORE the cleanup commit: GPG-encrypted offline backup (see scripts/runner-secrets/RECOVERY.md)."
