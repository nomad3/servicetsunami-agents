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
#
# Encoding note: macOS `security add-generic-password -w VAL` stores
# VAL as a UTF-8 string but on retrieve via `-w` HEX-ENCODES the
# payload if it contains newlines or any byte that's not safely
# printable. The retrieve quirk doubles the byte-length and breaks
# every consumer that expects raw text (PEM certs, multi-line .env).
# Workaround: base64 on store + base64-decode on read. Stored
# payload is then pure ASCII with no newlines, so the hex-encoding
# quirk never fires. load-from-keychain.sh mirrors this.
for entry in "${ENTRIES[@]}"; do
  svc="${entry%%:*}"
  rel="${entry#*:}"
  abs="$REPO_ROOT/$rel"
  src_bytes="$(wc -c < "$abs" | tr -d ' ')"

  blue "→ $svc  (from $rel, $src_bytes bytes)"

  # base64 the source; tr -d '\n' collapses the line-wrap macOS adds.
  payload="$(base64 < "$abs" | tr -d '\n')"

  # -U updates if the entry exists; otherwise creates.
  # -s service, -a account, -w password value.
  # -A grants access to ANY app on this user account WITHOUT a prompt.
  #
  # Why -A: without it, the keychain ACL defaults to "creator-binary
  # only; anything else needs user prompt". The GH Actions runner's
  # bash subprocess is not the creator → security would prompt → no
  # TTY on the runner → `security find-generic-password -w` exits 36
  # (errSecInteractionNotAllowed) with empty output. Verified live
  # by the F2 diagnostic v2 (PR #719, deploy 26420631499):
  # entry exists + attrs readable + password read silently refused.
  #
  # Trade-off: any app running as $ACCOUNT can read these secrets.
  # On the dedicated runner Mac this is equivalent to the security
  # level of the $HOME fallback (any nomade process can read both).
  # Spec already accepts this trade-off per the comment below.
  #
  # Note: payload briefly visible in argv to ps. Acceptable on the
  # runner Mac (single-user); spec already accepts this trade-off.
  security add-generic-password \
    -U \
    -A \
    -s "$svc" \
    -a "$ACCOUNT" \
    -w "$payload"

  # Read back, base64-decode, compare byte length to source.
  readback_bytes="$(security find-generic-password -s "$svc" -a "$ACCOUNT" -w 2>/dev/null | base64 -D 2>/dev/null | wc -c | tr -d ' ')"
  if [[ "$readback_bytes" != "$src_bytes" ]]; then
    red "  VERIFY FAILED: readback=$readback_bytes src=$src_bytes"
    exit 1
  fi
  green "  verified ($readback_bytes bytes after base64 round-trip)"
done

echo
green "All 4 Keychain entries set + verified."
echo
blue "Next steps (manual, not in this script):"
echo "  1. Confirm: security find-generic-password -s agentprovision-api-env -a $ACCOUNT 2>&1 | head -5"
echo "  2. Do NOT delete the \$HOME source files yet — PR3 ships a dual-source loader first."
echo "  3. After the next clean deploy verifies the Keychain read path, the cleanup commit removes the \$HOME files."
echo "  4. BEFORE the cleanup commit: GPG-encrypted offline backup (see scripts/runner-secrets/RECOVERY.md)."
