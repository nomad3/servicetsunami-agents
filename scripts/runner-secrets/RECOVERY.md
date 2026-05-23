# Runner secrets — recovery procedure

The PR3 / F2 Keychain migration removes the four secret files from
`$HOME` after a successful coexistence window. **Before the cleanup
commit lands**, the four secrets must be backed up offline so the
runner Mac can be recovered if a Keychain entry is corrupted, evicted,
or the keychain itself is destroyed.

This document is the operational runbook. Do NOT skip the backup step.
The cleanup commit's deploy plan blocks on §1 verification.

---

## Why offline backup is mandatory

After the cleanup commit ships:

- `cloudflared/credentials.json`, `cloudflared/cert.pem`, `apps/api/.env`,
  `PRODUCTION.env` no longer exist on the runner Mac's filesystem.
- The only live copy is in the macOS login keychain.
- `LimitLoadToSessionType: Aqua` on the runner launchd plist means the
  runner only starts post-login → if the Mac reboots and Simon hasn't
  logged in yet, deploys queue (acceptable). But:
- If the Keychain entry is **corrupted** or **evicted** (Keychain
  rebuild, OS upgrade quirk, accidental `security delete-generic-password`),
  there is no fallback path. The cluster cannot deploy until the
  secret is re-added.

Mitigation: a GPG-symmetric-encrypted offline backup of all four
secrets, stored on the runner Mac **and** mirrored to a removable
medium (USB stick / SD card in a safe place). Recovery is a single
`gpg --decrypt` + `security add-generic-password -U` per secret.

The passphrase lives in Simon's password manager (1Password / Bitwarden).
**Never** on disk on the runner Mac — that would re-introduce the
exact disk-leak path F2 closes.

---

## 1. Create the backup (BEFORE the cleanup commit ships)

Run on the runner Mac, from the repo root:

```bash
BACKUP_DIR="$HOME/secrets-backup/$(date +%Y-%m-%d)-pre-keychain-cleanup"
mkdir -p "$BACKUP_DIR"

for triplet in \
  "agentprovision-cloudflared-creds:cloudflared-credentials.json" \
  "agentprovision-cloudflared-cert:cloudflared-cert.pem" \
  "agentprovision-api-env:apps-api.env" \
  "agentprovision-root-env:PRODUCTION.env" \
; do
  svc="${triplet%%:*}"
  out="${triplet#*:}"
  security find-generic-password -s "$svc" -a "$USER" -w \
    | gpg --symmetric --cipher-algo AES256 --batch --yes \
          --passphrase-file <(read -s -p "passphrase: " p; echo "$p") \
          -o "$BACKUP_DIR/$out.gpg"
  echo "encrypted → $BACKUP_DIR/$out.gpg"
done

ls -la "$BACKUP_DIR"
```

After the script finishes, **mirror `$BACKUP_DIR` to a removable
medium** (USB stick, SD card). Store the removable medium in a
physical safe location (drawer, lockbox — not on the runner desk).

Verify by decrypting one blob into `/dev/null` and confirming the
passphrase round-trips:

```bash
gpg --decrypt "$BACKUP_DIR/cloudflared-credentials.json.gpg" > /dev/null
# Type passphrase; expects exit 0 + no error output.
```

---

## 2. Recover a single lost secret

If one Keychain entry is corrupted or evicted:

```bash
BACKUP_DIR="$HOME/secrets-backup/<date>-pre-keychain-cleanup"

# Decrypt + re-add. The plaintext NEVER touches disk.
gpg --decrypt "$BACKUP_DIR/apps-api.env.gpg" \
  | security add-generic-password -U \
      -s agentprovision-api-env -a "$USER" -w "$(cat)"
```

`-U` updates the existing (potentially corrupted) entry in place. No
restart required on the api side until the next deploy.

If multiple secrets are lost, repeat per service:

| Service                                  | Backup filename                  |
|------------------------------------------|----------------------------------|
| `agentprovision-cloudflared-creds`       | `cloudflared-credentials.json.gpg` |
| `agentprovision-cloudflared-cert`        | `cloudflared-cert.pem.gpg`       |
| `agentprovision-api-env`                 | `apps-api.env.gpg`               |
| `agentprovision-root-env`                | `PRODUCTION.env.gpg`             |

---

## 3. Recover from total Keychain loss

If the entire login keychain is destroyed (OS reinstall, deliberate
reset, file corruption):

1. Log in to the runner Mac as `nomade`. Confirm the login keychain
   is unlocked.
2. Re-add all four entries using §2's loop, one per secret.
3. Trigger a deploy. The dual-source loader (during coexistence) or
   the Keychain-only loader (post-cleanup) reads the freshly
   re-added entries.
4. Confirm tunnel up + api healthy before clearing the queue of
   queued deploys.

If the removable-medium backup is **also** lost: there is no
recovery path. This is the same risk profile as losing the master
password for the password manager itself. Mitigation: the removable
medium IS the second copy; storing it physically separate from the
runner is the point.

---

## 4. Lifecycle

- **Backup refresh**: re-run §1 any time a source secret rotates.
  Specifically: any time `apps/api/.env` is edited (SECRET_KEY rotation,
  DB password change, etc.), or after PR4 generates new JWT secrets.
- **Cleanup**: delete old `$BACKUP_DIR` directories quarterly. The
  removable medium copy is the long-term archive; the on-disk copy
  is convenience-only.
- **Passphrase rotation**: change the GPG passphrase + re-encrypt
  all backups once per year, or after any suspected compromise of
  the password-manager record.
