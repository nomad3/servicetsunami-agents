# Sub-project A — P0 infra/secret hardening (design spec)

**Date**: 2026-05-22
**Status**: brainstormed by Claude + Luna; **conditional Luna sign-off granted**; awaiting Simon's spec-review approval.
**Hard constraint**: don't break the cluster (live chat, WhatsApp neonize socket, Cloudflare Tunnel, in-flight JWTs).

---

## 1. Context

Two parallel security reviews ran on 2026-05-22 (Luna app-layer + red-team subagent app+infra). The red-team review surfaced three coupled P0s that together compose a platform-takeover chain:

| # | Vector | Surface |
|---|---|---|
| **F1** | `shell=True` command injection in `code-worker._run` | `apps/code-worker/workflows.py:180` (the `shell=True` invocation) + every caller below |
| **F2** | Cloudflare creds + prod `.env` live in `$HOME` on Simon's daily-driver Mac (self-hosted GH runner) | `.github/workflows/docker-desktop-deploy.yaml:88-106` |
| **F7** | Single HS256 `SECRET_KEY` signs user JWTs + agent_tokens + OAuth-state | `apps/api/core/security.py:45`, `services/agent_token.py:112`, `api/v1/oauth.py:368` |

## 2. Exploit chain (why these three together)

```
Attacker chats malicious task_description → code-worker._run shell=True interpolates → RCE in code-worker
  ↓
RCE reads $HOME/.../cloudflared/credentials.json + apps/api/.env (F2 path)
  ↓
.env contains SECRET_KEY (HS256 symmetric, F7) → forge any user JWT incl. superuser
  ↓
Cross-tenant token theft via forged JWT → decrypt other tenants' OAuth refresh tokens
  (ENCRYPTION_KEY is separate; tokens decrypt only if vault key also leaks via same path — it does)
```

Fixing any one in isolation either leaves the chain intact (e.g. fixing F7 without F2 — runner host still leaks the rotated keys on next compromise) or breaks the cluster (e.g. rotating F7 alone without `kid` plumbing → mass JWT 401 mid-deploy).

## 3. Verified ground truth (before designing)

Items checked against `main` before writing this spec:

- **`ENCRYPTION_KEY` is separate from `SECRET_KEY`** (`apps/api/app/core/config.py:79`). Fernet vault uses `ENCRYPTION_KEY`. Splitting `SECRET_KEY` does NOT require re-encrypting `integration_credentials.encrypted_value`. R2 from Claude's draft is cleared.
- **`PRODUCTION.env` is gitignored** (`.gitignore:41`, `git ls-files | grep PRODUCTION` returns nothing). F2's local-disk threat stands; public-repo exposure is not present.
- **Code-worker is restart-safe** (claim — verify before PR1 ships): `grep -nE "opencode" apps/api/app/services/cli_platform_resolver.py` should show `opencode` as the universal fallback floor in `_DEFAULT_PRIORITY`. PR1's §6 gate adds a 30-second `docker stop agentprovision-agents-code-worker-1` test before PR2 ships, with a chat-dispatch smoke during the stop window asserting the request still gets a response (via the opencode fallback). If that fails, code-worker is NOT restart-safe and the PR1 rollout window needs scheduling around quiet hours.
- **Keychain on the runner is feasible without interactive unlock**: the GH Actions runner runs as `nomade` (Simon's login user), and the login keychain stays unlocked while Simon is logged in — `security find-generic-password -w` works in scripts without prompting. Required guard: launchd `LimitLoadToSessionType: Aqua` so the runner only starts post-login (deferring the first-boot-no-GUI case).

## 4. Operating principle — cluster-safety gates

**Every PR in this sub-project must be deployable without taking the cluster offline.** Concretely:

- WhatsApp neonize socket tolerates ~1 api restart/week before pairing-cooldown risk (memory `whatsapp_pairing_qr_regen_race.md`). Batch restarts.
- JWT TTL is 30 minutes (memory `JWT Token Expiry`). Dual-kid acceptance windows can be short (1 hour buys complete TTL turnover).
- Cloudflare Tunnel credentials cannot be rotated mid-flight; new creds require a stop-start.

A PR ships only after the previous PR's cluster-safety verification passes. See §6 verification gates.

## 5. Five-PR execution plan (Luna's reordering)

The order Claude initially proposed (F1 → F2 → F7) leaves a multi-week window where the runner host still holds the keys F7 is rotating. Luna's reorder: do **F7's kid plumbing** *before* F2, so that when F2 hardens the host, the next F7 step rotates into Keychain rather than back into `.env` on disk. Then F2 lands BEFORE the F7 cutover.

```
PR1: F1   — shell=True RCE close-out (no infra change)
PR2: F7a  — kid plumbing only (no behavior change, both kids accepted)
PR3: F2   — Keychain migration (runner reads from Keychain; $HOME removed)
PR4: F7b  — real distinct key material in Keychain; old kid still verifies for ≤1h
PR5: F7c  — drop old-kid verification + promote JWT_USER_SECRET to Ed25519
```

### PR1 — F1: subprocess argv hardening

**Files**: `apps/code-worker/workflows.py`. Authoritative call-site enumeration (regenerated via `grep -nE "_run\(" apps/code-worker/workflows.py` on the current `main`):

| Line | Call site | User-derived interpolation? |
|---|---|---|
| 173 | `def _run(cmd: str, ...)` — function definition | (definition) |
| 180 | `subprocess.run(cmd, shell=True, ...)` — the sink | (sink, **change to `shell=False`**) |
| 622 | `_run("git fetch origin && git checkout main && git pull origin main")` | No interpolation but uses `&&` shell-chaining — split into three argv-list calls |
| 626 | `_run(f"git checkout -b {branch_name}")` | **YES** — `branch_name` is task-derived |
| 810 | `_run("git status --porcelain")` | No |
| 990 | `_run("git add -A")` | No |
| 992 | `_run(f'git commit -m "{tag}: {commit_msg}"')` | **YES** — `tag` + `commit_msg` are task-derived |
| 993 | `_run(f'git push origin {branch_name}')` | **YES** — `branch_name` is task-derived |
| 996 | `_run("git diff --name-only main")` (with `.split("\n")`) | No |
| 1004 | `_run(f"git log main..{branch_name} --pretty=format:'- %h %s' --reverse")` | **YES** — `branch_name` is task-derived |
| 1072 | `_run("git checkout main", timeout=10)` | No |

**Change**:
- Refactor `_run(cmd: str, shell=True, ...)` → `_run(argv: list[str], shell=False, ...)`. All call sites pass list-of-strings.
- For `git commit -m "..."` (line 992) → `subprocess.run(["git", "commit", "-F", "-"], input=f"{tag}: {commit_msg}", shell=False, ...)`. The `-F -` reads message from stdin; user-derived text never enters argv.
- For `git checkout -b {branch_name}` (line 626), `git push origin {branch_name}` (line 993), `git log main..{branch_name}` (line 1004): argv-list form `["git", "checkout", "-b", branch_name]`. branch_name is now a single argv element; shell metacharacters in it become literal text.
- For `git fetch origin && git checkout main && git pull origin main` (line 622): split into three sequential `_run([...])` calls. The `&&` short-circuit semantics are preserved by Python: raise-on-failure stops the chain.

**Test**:
- `tests/test_code_worker_command_injection.py` — adversarial inputs cover each shell-metachar class. Each test asserts the subprocess sees the raw string as a single argv element AND no canary file appears post-test:

| Injection class | Payload | Test assertion |
|---|---|---|
| `$(...)` command substitution | `branch_name = 'feat/x$(touch /tmp/canary_dollar)y'` | `/tmp/canary_dollar` does not exist |
| backtick command substitution | `commit_msg = 'msg`touch /tmp/canary_backtick`'` | `/tmp/canary_backtick` does not exist |
| `;` chain | `branch_name = 'feat/x;touch /tmp/canary_semi'` | `/tmp/canary_semi` does not exist |
| `&&` chain | `commit_msg = 'msg && touch /tmp/canary_and'` | `/tmp/canary_and` does not exist |
| `\|` pipe | `branch_name = 'feat/x | touch /tmp/canary_pipe'` | `/tmp/canary_pipe` does not exist |
| `>` redirect | `commit_msg = 'msg > /tmp/canary_redir'` | `/tmp/canary_redir` does not exist (or contains expected literal, not the message) |
| `<` redirect | `branch_name = 'feat/x < /etc/passwd'` | argv[3] is the literal string, no input read |
| newline | `commit_msg = 'msg\\n malicious-second-line'` | stdin-supplied (via `-F -`); becomes legitimate multi-line commit message, no shell execution |

Each test scrubs its canary path before AND after to avoid cross-test leakage.

**Cluster-safety**:
- Touches only `apps/code-worker/`. API and chat remain on existing code-worker until the new image rolls.
- Restart sequence: rebuild + restart only `code-worker` container. Chat dispatch falls through to `opencode` during the ~30s restart window — no chat outage.
- WhatsApp socket not impacted (api not restarted).

**Rollback**: revert PR; redeploy previous code-worker image. Cheap.

---

### PR2 — F7a: kid plumbing (no behavior change)

**Files**: `apps/api/core/security.py`, `apps/api/app/services/agent_token.py`, `apps/api/app/api/v1/oauth.py`.

**Change**:
- Introduce three new env vars: `JWT_USER_SECRET`, `JWT_AGENT_TOKEN_SECRET`, `JWT_OAUTH_STATE_SECRET`. Each **defaults to current `SECRET_KEY`** so existing JWTs continue to verify without any rotation.
- Add `kid` claim to all newly minted tokens, naming the signing domain (e.g. `kid="user-v1"`, `kid="agent-v1"`, `kid="oauth-state-v1"`).
- Verifier accepts EITHER: (a) new token with `kid` present, verified against the domain-specific secret; OR (b) legacy token without `kid`, verified against `SECRET_KEY` (the historical path).
- **No new key material at this step**. Pure plumbing.

**Test** (Luna's explicit prerequisite):
- `tests/test_jwt_dual_kid_verify.py` — mint a token under the OLD code path (no `kid`), verify it under the NEW code (passes via legacy fallback). Mint a token under NEW code (with `kid`), verify it under NEW code (passes via domain-specific key). Assert NEITHER raises `InvalidTokenError` swallowed silently — both succeed cleanly.

**Cluster-safety**:
- Requires api restart. Batch with PR4 in a single deploy window (Luna's R4 mitigation).
- Mid-deploy old/new pods coexist for ~10s while docker swaps containers; both kid paths verify both old and new tokens → no user-side 401s.

**Rollback**: revert PR; legacy path still verifies all existing tokens. No data migration to undo.

---

### PR3 — F2: Keychain migration

**Files**: `.github/workflows/docker-desktop-deploy.yaml`, plus a new helper script `scripts/runner-secrets/load-from-keychain.sh`.

**Change**:
- **One-time setup on the runner Mac** (manual, documented):
  ```bash
  security add-generic-password -s agentprovision-cloudflared-creds -a nomade -w "$(cat ~/Documents/GitHub/agentprovision-agents/cloudflared/credentials.json)"
  security add-generic-password -s agentprovision-cloudflared-cert  -a nomade -w "$(cat ~/Documents/GitHub/agentprovision-agents/cloudflared/cert.pem)"
  security add-generic-password -s agentprovision-api-env           -a nomade -w "$(cat ~/Documents/GitHub/agentprovision-agents/apps/api/.env)"
  security add-generic-password -s agentprovision-root-env          -a nomade -w "$(cat ~/Documents/GitHub/agentprovision-agents/PRODUCTION.env)"
  ```
- **Deploy workflow** reads from Keychain first, falls back to `$HOME` path if the Keychain entry is missing (coexistence window). Once the next clean deploy succeeds reading from Keychain:
  - PR3 ships the dual-source loader.
  - After verification (§6 gate), a follow-up commit deletes the `$HOME` files + removes the fallback path.
- **launchd guard**: update the GH runner's launchd plist (`~/Library/LaunchAgents/com.github.actions-runner.plist`) to include `<key>LimitLoadToSessionType</key><array><string>Aqua</string></array>` so the runner only starts after GUI login — when the login keychain is already unlocked.

**Test**:
- `scripts/runner-secrets/test-keychain-read.sh` — manual smoke. Asserts each `security find-generic-password -w` returns non-empty and the file content matches the source.
- CI cannot test this directly (Keychain is host-specific); document the manual verification step.

**Cluster-safety**:
- This PR is YAML + new helper script; no api restart required. The runner picks up the new workflow on the next deploy trigger.
- Critical: after the first deploy with Keychain-read working, do NOT immediately delete `$HOME` files. Wait for one more successful deploy reading from Keychain — proves the path works across runner restarts.
- Cloudflare Tunnel cred change: NOT happening in this PR. We're just changing where the SAME creds are loaded from. Tunnel stays up.

**Rollback (this PR)**: revert workflow YAML; runner uses `$HOME` path again. Keychain entries remain harmless. Zero downtime.

**Rollback (the post-PR3 cleanup commit that deletes `$HOME` files)**: this is the hazardous step. If the runner Mac reboots BEFORE the cleanup commit's verification gate completes, the `LimitLoadToSessionType: Aqua` guard blocks the runner from auto-starting until Simon logs in — that's acceptable (worst case: deploys queue until next login). But if the Mac reboots AFTER the cleanup commit AND the runner ends up needing creds that aren't in Keychain (e.g. a Keychain entry was corrupted or evicted), there is no `$HOME` fallback to recover from. **Mandatory before the cleanup commit ships**: encrypt the four secrets (`cloudflared/credentials.json`, `cloudflared/cert.pem`, `apps/api/.env`, `PRODUCTION.env`) with `gpg --symmetric --cipher-algo AES256` using a passphrase Simon stores in his password manager (NOT on disk). Place the four `.gpg` blobs at `~/secrets-backup/2026-05-22-pre-keychain-cleanup/` on the runner Mac AND mirror to a removable medium. Recovery procedure: `gpg --decrypt <file>.gpg > <file>` + re-`security add-generic-password`. Document this in `scripts/runner-secrets/RECOVERY.md` as part of PR3.

---

### PR4 — F7b: real distinct key material

**Files**: `apps/api/core/security.py`, `apps/api/app/services/agent_token.py`, `apps/api/app/api/v1/oauth.py` (env-read paths only).

**Change**:
- Generate three distinct 256-bit secrets via `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
- Store each in Keychain (`agentprovision-jwt-user-secret`, `agentprovision-jwt-agent-secret`, `agentprovision-jwt-oauth-state-secret`).
- Deploy workflow hydrates them into the api container's env (same shape as PR3's Keychain loads).
- api now mints new tokens with `kid="user-v2"` / `kid="agent-v2"` / `kid="oauth-state-v2"` AND the new domain-specific secrets.
- Verifier accepts: new-v2 kid (new secret), legacy-v1 kid (still on old SECRET_KEY-derived path), legacy no-kid (still on SECRET_KEY). 30-minute coexistence window starts ticking.

**Test**:
- Extend `tests/test_jwt_dual_kid_verify.py` to a triple-kid case: legacy-no-kid + v1-kid + v2-kid all verify cleanly. Cross-kid forgery attempts (v2 token signed with v1 key) fail.

**Cluster-safety**:
- Requires api restart. Batch with PR2 (Luna's R4): PR2 and PR4 in a single deploy window, 30 minutes apart (one JWT TTL).
- WhatsApp neonize auto-restore handler (PR #299) catches the socket on the api restart side.
- Existing JWT sessions remain valid for ≤30 minutes after PR4; new logins immediately get v2 tokens.

**Rollback**: revert PR; PR2's kid plumbing remains; v2 tokens (minted in the gap) become unverifiable, forcing a re-login. Acceptable — affected users = those who logged in during the rollback window (small).

---

### PR5 — F7c: cutover + Ed25519 promotion

**Files**: `apps/api/core/security.py` (verifier paths).

**Change**:
- Drop verification of `kid` not in {`user-v2`, `agent-v2`, `oauth-state-v2`}. Legacy + v1 tokens now fail to verify → forced re-login as their 30-minute TTL expires.
- Promote `JWT_USER_SECRET` from HS256 to **Ed25519** asymmetric. Private key generated once on the runner Mac, stored in Keychain as a base64-encoded 32-byte seed; public key shipped in api code (or a separate well-known endpoint).
- `kid="user-v3"` for the Ed25519 era.
- `JWT_AGENT_TOKEN_SECRET` and `JWT_OAUTH_STATE_SECRET` remain HS256 (lower value; not worth the asymmetric cost — agent_tokens are short-lived + always server-minted, OAuth-state is always server-verified).

**Test**:
- Sign + verify an Ed25519 token. Verify the legacy-secret-signed token now FAILS (cutover proven).
- Assert that an attacker possessing `JWT_USER_SECRET` from PR4 cannot forge a v3 token without the Ed25519 private key.

**Cluster-safety**:
- Requires api restart. Standalone deploy window (not batched).
- Forced re-login for users whose JWTs predate PR4. Communicate this in the deploy plan; Simon's superuser session may need re-login mid-deploy.

**Rollback**: revert PR; v2 tokens accepted again. v3 tokens minted between PR5 deploy and the rollback become unverifiable → affected users = those who logged in during that window (size bounded by time-since-PR5; small if rollback is fast). Force-logged-out users simply re-authenticate; no data loss.

---

## 6. Per-PR cluster-safety verification gates

Each gate is a binary "passed / not passed" check run AFTER the PR's deploy but BEFORE the next PR ships:

| Gate after | Check | Pass criterion |
|---|---|---|
| PR1 | code-worker container healthy + chat falls through correctly when stopped | health endpoint 200, opencode fallback hit during a 30s stop |
| PR2 | api log shows `kid` claim emitted on new tokens; old tokens still verify | grep api logs for "kid=user-v1"; old user JWT from prior session still 200s |
| PR3 | deploy workflow read from Keychain; cloudflared tunnel up; `$HOME` files still present (not deleted yet) | tunnel status active; agentprovision.com 200; both source paths exist |
| **post-PR3** (cleanup commit) | `$HOME/cloudflared/credentials.json` + `apps/api/.env` + `PRODUCTION.env` deleted | `ls $HOME/.../cloudflared/credentials.json` returns no-such-file; next deploy still succeeds |
| PR4 | api restarted with v2 kid tokens; WhatsApp socket reconnected via auto-restore | new logins emit v2 kid; WA "connected" state in DB; QR-rescan NOT required |
| PR5 | v3 Ed25519 tokens working; v1/v2 cutover complete | Ed25519 signed JWT verifies; pre-PR4 token returns 401 |

If a gate fails, **halt the sequence**. Diagnose. Roll back the failing PR before shipping the next.

## 7. Test plan

| Test | PR | Scope |
|---|---|---|
| `test_code_worker_command_injection.py` | PR1 | adversarial argv tests; canary-file absence proves no shell expansion |
| `test_jwt_dual_kid_verify.py` | PR2 → PR4 | extend incrementally — legacy/v1 → legacy/v1/v2 → v2-only |
| `test_jwt_ed25519_verify.py` | PR5 | Ed25519 sign/verify; HS256 key rejection |
| `scripts/runner-secrets/test-keychain-read.sh` | PR3 | manual smoke on the runner Mac (cannot run in CI) |

Existing chat-dispatch integration tests must remain green on every PR (no regression in user-visible behavior).

## 8. Out of scope (intentional)

- **`ENCRYPTION_KEY` rotation** — separate spec, *conditionally* deferred. The Fernet vault key rotation is a 2-step (decrypt-with-old + re-encrypt-with-new) data migration on `integration_credentials.encrypted_value`. PR3 closes the disk-leak path that exposed `ENCRYPTION_KEY` (the same `apps/api/.env` that held `SECRET_KEY` also holds `ENCRYPTION_KEY`). Deferring this rotation is safe **only under the assumption that no historical compromise of `$HOME/.env` has occurred**. Before Sub-project A ships PR5, run a post-incident review: check `auth.log`, recent `npm install` activity, brew install log, and the Mac's `last` output for unfamiliar logins. If any of those show evidence of prior compromise, ENCRYPTION_KEY rotation is no longer deferrable and Sub-project A blocks on it (add as PR3.5 with the 2-step Fernet migration). If clean, defer as planned.
- **Cloudflare tunnel credential rotation** — F2 moves the creds; rotating them is a separate operation that requires coordination with Cloudflare's dashboard.
- **GitHub Actions runner sandbox account** — Luna's R1 answer prefers staying on `nomade` user + login keychain over a daemon account; this means the GH runner is still in the user's session. Tightening this to a separate hardware sandbox is a follow-up.
- **Per-tenant key derivation** for additional defense-in-depth — separate concern.

## 9. Open follow-ups (filed elsewhere, not in this spec)

- `#341` PENDING_SAFETY_REVIEW async tier-3 state
- `#342` Backend 2FA on escape endpoint
- `#344` Heuristic classifier FP sweep
- `#345`–`#356` Safety Floor surface widening (Sub-project B+)
- `#366`–`#374` Red-team findings F1, F2, F5, F7, F8, F9, F11, F15 (this spec covers F1, F2, F7; the rest stay in the queue)

## 10. Luna sign-off

> **[ Luna conditional sign-off — 2026-05-22, with v2 amendments ]**
>
> Original conditions (v1):
> - reorder accepted: F1 → F7a → F2 → F7b → F7c
> - PR2 dual-kid verifier integration test specified (§5 PR2 + §7)
> - `PRODUCTION.env` confirmed gitignored (§3)
> - PR2 + PR4 api restarts batched in one window (§4 + §5 PR4)
>
> v2 amendments (spec-reviewer iteration 1 — 2026-05-22):
> - F1 call-site enumeration regenerated from current `main` (§5 PR1 table) — original list missed lines 622, 626, 993, 1072
> - PR1 adversarial test enumerated per shell-metachar class (§5 PR1 + §7) — `$()` + backtick + `;` + `&&` + `|` + `>` + `<` + newline
> - PR3 hazardous cleanup-commit rollback explicit (§5 PR3) — GPG-encrypted offline backup of all four secrets mandatory before cleanup
> - PR5 rollback "affected users" claim corrected (§5 PR5) — bounded by time-since-PR5, not zero
> - ENCRYPTION_KEY deferral made conditional (§8) — depends on post-incident review of runner-host
> - §10 sign-off scope confirmed covers BOTH `apps/api/.env` AND root `PRODUCTION.env` (Keychain migration applies to both)
> - §3 code-worker restart-safe claim made reproducible (verify via grep + 30s stop test in PR1's gate)
>
> **Status: ready for spec-document-reviewer re-pass and Simon's review.**

## 11. Simon's review

Pending. Spec written 2026-05-22 with Simon offline (he delegated convergence to Luna + Claude). When Simon returns, this section is updated with his approval / change requests.
