# GitHub integration: SSH key support (OAuth-blocked orgs — NFL, ustwo)

**Date:** 2026-05-31
**Status:** Plan (Codex + Luna review pending)
**Owner:** Simon
**Why:** Some orgs **block OAuth apps** (NFL — SAML SSO / OAuth-app restrictions) and the user's ustwo repos use SSH. The integration OAuth token (HTTPS) can't reach them. So the GitHub integration must ALSO accept an SSH key, and the worker must do `git clone git@github.com:org/repo` with it — complementing the gh/OAuth HTTPS path (#745/#746).
**Decisions (Simon):** (1) key model = **personal/fine-grained key** (one key tied to a GitHub account already authorized for the org's repos). (2) intake = **paste the OpenSSH private key, github.com only** (no custom-host UI; github.com host keys already pre-baked in the worker image).

## Grounding (existing pattern)

Credentials are key-value rows: `IntegrationCredential(credential_key, credential_value[encrypted], credential_type)`, saved via `store_credential()` (Fernet) and read via `oauth.py` internal endpoints. The worker already fetches the OAuth token via `GET /api/v1/oauth/internal/token/github`. SSH slots in as a **new `credential_key = "ssh_private_key"`** (+ optional `"ssh_passphrase"`) on the same `github` integration — no new table.

## Design (3 chained PRs)

### PR 1 — Storage + internal fetch (backend foundation)
- **Save:** `POST /api/v1/integrations/github/ssh-key` (auth: tenant user) — body `{ private_key, passphrase? }`. Validates it parses as an OpenSSH private key (reject otherwise); `store_credential(tenant, "github", "ssh_private_key", private_key, type="ssh_key")` (+ passphrase). Audit-logged. `DELETE` to remove. `GET` returns only presence + fingerprint (never the key).
- **Internal fetch:** `GET /api/v1/oauth/internal/ssh-key/github?tenant_id=…` (X-Internal-Key, superuser/internal only, blocked from public internet like other `/internal/*`) → `{ private_key, passphrase? }` for the worker. Honors the `github_primary_account` pin (migration 113) like `_fetch_github_token`.
- Tests: save validates key format; fetch returns it; non-internal callers rejected; GET-presence never leaks the key.

### PR 2 — Worker wiring (makes SSH clones work, all CLIs)
- `workflows.py` `_fetch_github_ssh_key(tenant_id)` (mirrors `_fetch_github_token`). In the shared `execute_chat_cli` setup, when a key exists: write it to an **ephemeral `0600` keyfile** under the per-turn session scratch (NOT a persistent HOME), and put `GIT_SSH_COMMAND="ssh -i <keyfile> -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=10"` into the **per-turn env** (CLI-agnostic — claude/codex/gemini/copilot inherit it, same shape as the gh OAuth helper). Passphrase (if any) handled via `ssh-agent` started per-turn OR rejected at save time (recommend **passphrase-less** keys for automation; if a passphrase is set, use a per-turn `ssh-agent` + `SSH_AUTH_SOCK`). **Delete the keyfile + kill the agent after the turn** (finally).
- **Cross-tenant guard** (Codex lesson from #746): strip/replace `GIT_SSH_COMMAND` per-turn; never leave a prior tenant's key path in a shared env. Like `_apply_git_credential_env`, set-or-strip.
- Now `git clone git@github.com:org/repo` uses the key; `https://github.com/...` still uses the OAuth helper — git picks per URL scheme.
- Tests: keyfile written 0600 + GIT_SSH_COMMAND points at it when a key exists; both stripped when none (no bleed); keyfile removed after the turn.

### PR 3 — `/integrations` UI
- GitHub integration card gains an **"SSH key (for OAuth-blocked orgs)"** section: a textarea to paste the private key (+ optional passphrase), Save/Remove, and a status line showing the stored key's fingerprint (never the key). Calls PR 1's endpoints. Copy explains: use a key on an account authorized for the org; recommend a passphrase-less deploy/fine-grained key.

## Security
- Private key **Fernet-encrypted at rest** (the vault). **Ephemeral 0600 keyfile** on the worker, under per-turn scratch, **deleted after the turn** (never persisted to a HOME). `BatchMode=yes` → no passphrase prompt → fail fast, never hangs (the #746 lesson). `IdentitiesOnly=yes` → only the provided key. `StrictHostKeyChecking=yes` with pre-baked github host keys → no MITM. Internal fetch endpoint blocked from public internet. GET-presence never returns the key.
- Recommend (UI copy) a **dedicated / fine-grained key**, not the user's primary `id_rsa`.

## Verification
- PR 1: save a test key → internal fetch returns it; GET shows fingerprint only; external caller 403.
- PR 2: with a key set for a tenant, a worker turn writes the 0600 keyfile + GIT_SSH_COMMAND; `git ls-remote git@github.com:<an-authorized-private-repo>` succeeds in-container; keyfile gone after; no key → stripped, SSH clone fails fast (BatchMode).
- PR 3: paste a key in `/integrations` → fingerprint shows; a claude/codex turn then clones an SSH-only repo.

## Process
plan (this) → Codex + Luna review → PR1 → PR2 (chained off PR1) → PR3. Each Codex+Luna reviewed.

## Review folded (Codex + Luna)

**Codex BLOCKERs (must do):**
1. **Never write the key to the persistent `session_dir`** (`/home/codeworker/st_sessions/<tenant>` is NOT ephemeral). Write to a unique per-turn `turn_<uuid>/` dir `0700`, key as a random filename inside, and `shutil.rmtree` the whole dir in a `finally`.
2. **`GIT_SSH_COMMAND` (and never `SSH_AUTH_SOCK`) lives ONLY in the per-turn subprocess env**, set-or-STRIP like `_apply_git_credential_env` — do NOT mirror the `os.environ["GITHUB_TOKEN"]` process-global pattern (bleed).
3. **Reject passphrase-protected keys at save** — no `ssh-agent` (a second secret channel harder to prove cleaned). Recommend passphrase-less dedicated keys.

**Codex IMPORTANT:**
- SSH command adds `-o UserKnownHostsFile=/dev/null -o GlobalKnownHostsFile=/etc/ssh/ssh_known_hosts` so a tenant-writable HOME `known_hosts` can't participate (only the image's pre-baked host keys).
- **Never log key material / `GIT_SSH_COMMAND` / argv / env; scrub the keyfile path from any surfaced error.**
- **Sensitivity callout:** a Fernet key + DB compromise yields decryptable PRIVATE KEYS — materially higher sensitivity than OAuth tokens. Document it; gate the internal fetch endpoint hard (internal-only, audited).
- Ops: fingerprint-only status, audit save/delete/fetch/use, `last_used_at`, clean rotate flow.

**Luna (lead) — posture (revises the UI emphasis, not Simon's paste mechanism):**
- Make a **dedicated, read-only deploy key the PRIMARY UI path**; personal/fine-grained pasted key = **allowed escape hatch WITH a sharp warning**: "This key may grant the worker the same repo access you have. Prefer a dedicated read-only deploy key."
- **Default guidance = READ-ONLY.** Write-capable keys are an explicit advanced choice (a write-capable worker can alter source / CI / deploy manifests / supply chain). Copy: "Only use write-capable keys when the worker must push branches/tags."
- State explicitly as a **product constraint**: v1 = single key, github.com only (no multiple identities / custom hosts).

**Net design changes vs v1:** per-turn `turn_<uuid>/` 0700 dir for the keyfile (not session_dir); set-or-strip `GIT_SSH_COMMAND` in the subprocess env only; reject passphrase keys at save; `UserKnownHostsFile=/dev/null` + global-only host keys; no logging of key/path; UI primary = read-only deploy key, personal allowed-with-warning; audit + fingerprint-only status + `last_used_at`.
