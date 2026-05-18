# 2026-05-17 — Gemini CLI auth, Save-failed picker, recurring disk pressure, password recovery

Date: 2026-05-17
Owner: Alpha platform
Status: Session summary (shipped)

## Scope

Single session that started as "verify the Gemini CLI integration end-to-end on the `thesimondigitalnomad@gmail.com` tenant" and uncovered four production bugs plus the structural cause of a recurring incident pattern. Five PRs shipped against `main`. The user's standing rule (every BLOCKER + IMPORTANT review finding fixed in the same PR) was honored across all five.

## What we discovered

1. **Gemini CLI OAuth was broken on every new tenant.** "Access blocked: redirect_uri_mismatch (Error 400)" before any code could be returned. We initially thought Google had revoked the OAuth client (`681255809395-…`). They had not — the api-owned PKCE flow shipped in PR #535 was signing the URL with the *wrong* client_id because the `_load_gemini_oauth_client()` regex matched the FIRST `*.apps.googleusercontent.com` in the `@google/gemini-cli` npm bundle. That literal turned out to be `CLOUD_SDK_CLIENT_ID = 764086051850-…` re-exported from `google-auth-library`, not the cli's own `OAUTH_CLIENT_ID = 681255809395-…` declared further down. Google rejected the `https://codeassist.google.com/authcode` redirect for that client.
2. **Docker VM disk was at 94 %** (5.5 GB free of 87 GB) inside the Docker Desktop VM, even though the host disk had 67 GB free. The `apt-get install` step in the deploy build failed with exit 100, the workflow silently `Skipping api (not in compose or build error)` and reported `success`, and the api ran yesterday's code without anyone noticing.
3. **Code-worker writable layer was at 29 GB** — same 2026-05-04 incident pattern. Two power tenants' `/home/codeworker/st_sessions/<tenant>/.local/lib` had grown to ~5.8 GB each, plus ~3 GB of pip cache each. This recurs because per-tenant `$HOME` lives on the container's writable layer with no quota.
4. **The InlineCliPicker showed `Save failed`** on every non-superuser tenant. `PUT /api/v1/features` was tightened to `require_superuser`, which broke the picker for everyone who wasn't a platform admin. Plus new tenants defaulted to `claude_code` even when Claude wasn't connected, so the dropdown surfaced `"Claude Code (disconnected)"` with no way to change it.
5. **Password recovery emails never arrived.** `POST /api/v1/password-recovery/{email}` generated a reset token, wrote the hash to the DB, and just logged `Password reset token generated for {email}` — never sent an email. The literal `# In a real app, send an email here.` at `apps/api/app/api/v1/auth.py:484` had been there for months. PR #430 with the full sender + 25 security findings already existed open but had never been merged.

## PRs landed

| PR | Title | Branch | Files | What it does |
|---:|------|--------|------:|---|
| **#539** | `fix(gemini-auth): anchor regex to OAUTH_CLIENT_ID literal` | `fix/gemini-oauth-pick-correct-client-id` | 1 | Anchored both regexes to `OAUTH_CLIENT_ID = "…"` / `OAUTH_CLIENT_SECRET = "…"` named literals so the gemini-cli installed-app client is always picked, never the CLOUD_SDK re-export. Verified locally against the installed bundle. |
| **#541** | `fix(features): allow tenant members to save default_cli_platform` | `fix/features-tenant-cli-save` | 3 | Relaxed `PUT /features` to `get_current_active_user`. Replaced the blocklist approach with a default-deny `_MEMBER_WRITABLE_FIELDS` allowlist after the superpowers review caught that `active_llm_provider` and the `*_enabled` toggles were tenant-wide DoS surface, not preferences. WARNING-level log on dropped fields. 6 service tests + endpoint-level test included. |
| **#540** | `feat(code-worker): persist tenant HOME on workspaces volume (#267 Phase 1)` | `feat/code-worker-tenant-home-persist` | 9 | `tenant_home_dir()` helper mirroring `tenant_workspace_dir`; `env["HOME"]` redirect in all 5 CLI executors; one-shot legacy `.gemini/` rescue copy with `0o600` chmod on `oauth_creds.json`/`credentials.json`/`google_accounts.json`; logger.warning on redirect failure; path-traversal test parity. 381 tests pass. |
| **#430** | `feat(auth): hardened password-recovery email sender + 2-step flow` | `feat/auth-email-sender-password-recovery` | 14 | Real SMTP sender with hostname allowlist (SendGrid/Postmark/Mailgun/Gmail/Fastmail/SES), CRLF stripping, SMTP_SSL on 465 / STARTTLS+verified-ctx on 587 with post-upgrade `isinstance(SSLSocket)` guard, CSRF cookie binding scoped to `/api/v1/auth/reset-password`, `password_changed_at` column + JWT-iat floor that rejects `iat=None` when `pwc` is set, `@`-in-`EMAIL_FROM` guard, token-in-URL-fragment + SPA scrub, SMTP/CSRF/iat/attempt-counter tests, env-var contract declared across `.env.example` + helm values. |
| **#538** | `docs(plans): cap per-tenant HOME in code-worker (task #264)` | `docs/code-worker-tenant-home-cap` | 1 | Design doc that PR #540 implements Phase 1 of. Already merged earlier. |

`#539` was the gemini fix, `#541/#540/#430` shipped in that merge order after a coordinated disk-check between each so the single-Mac runner didn't stack three builds.

## The recurring incident pattern — root cause

Same pattern as 2026-05-04 (`docker_disk_full_recovery.md`):

1. Per-tenant `$HOME` lives on the container writable layer. Sandboxed CLIs do `pip install --user` / `npm install -g` into `/home/codeworker/st_sessions/<tenant>/.local`. Each tenant duplicates ~5.8 GB of Python pkgs + ~3 GB of pip cache.
2. Docker Desktop VM has an 87 GB hard cap. Once 2–3 power tenants saturate it, the next deploy's `apt-get install` step fails for no-space-left.
3. The deploy workflow's per-service build is gated by `if api build succeeded ...` and silently skips on failure, **but the workflow as a whole still reports `success`**. Operators see `Docker Desktop Deployment: success` and never know prod is on yesterday's code.
4. Once the api container restarts (any cause — including just being recreated as a side effect of `docker compose up -d`), it crashloops on `/tmp/agentprovision_reports` `[Errno 28] No space left on device` because the VM has no slack.

**The structural fix landed in #540.** With `$HOME` now on the persistent `workspaces` named volume (host disk, not VM overlay), the code-worker writable layer collapses on every recycle. Post-deploy observation: **9.96 GB → 65.7 MB writable layer**. VM disk went **24 G → 33 G free**. Phase 2 (per-tenant quota walker + pip-race serialization with `fcntl.flock`) is in the design doc; not yet implemented.

## Verification

- **Gemini OAuth, end-to-end:** clicked Connect with Google on the `thesimondigitalnomad@gmail.com` tenant → Google showed the standard consent screen (NOT the `redirect_uri_mismatch` error) → consent → `oauth_creds.json` blob landed in the vault → integration marked Connected.
- **CLI picker save, end-to-end:** `PUT /api/v1/features { default_cli_platform: "gemini_cli" }` from a non-superuser tenant returned **200** (was **403 Superuser required**). Subsequent `GET /api/v1/features` confirmed persistence.
- **Code-worker writable layer:** snapshot pre-deploy 9.96 GB → post-deploy 65.7 MB. No code-worker recycle was needed during the incident or after — first deploy after #540 merge naturally recreated the container.
- **Password recovery email:** code is live but sender is in **log-only mode** until the SMTP env vars are populated on the runner (`EMAIL_SMTP_HOST`, `EMAIL_SMTP_USERNAME`, `EMAIL_SMTP_PASSWORD`, `EMAIL_FROM`). The PR's startup path is non-crashing without them — falls back to a WARNING log per email.

## Open follow-ups

- **#267 Phase 2 — per-tenant quota walker.** Design lives in `docs/plans/2026-05-17-code-worker-tenant-home-cap-design.md`. Needs a `du`-based walker after each CLI call that prunes `.cache/*` and oldest `.local/lib/python*/site-packages/*` above a 2 GiB soft cap, plus the `fcntl.flock` serialization noted in the I3 review finding on #540.
- **Deploy workflow silent-skip.** The `.github/workflows/docker-desktop-deploy.yaml` `Skipping api (not in compose or build error)` branch needs to **fail** the workflow run instead of reporting `success`. Same workflow needs a pre-flight Docker VM disk check that aborts before kicking the build if free space < ~10 GB. Both are part of task #267 but not in PR #540.
- **Password recovery SMTP env vars.** User to populate `EMAIL_SMTP_HOST/USERNAME/PASSWORD/EMAIL_FROM` on the runner (or GCP Secret Manager for helm). Until then the sender silently log-onlys.
- **`default_cli_platform` default for new tenants.** Schema + model still default to `"claude_code"`. The picker now copes with that gracefully (shows "(disconnected)") and the save endpoint is fixed, so this is no longer a hard bug — but a follow-up to flip the default to `"auto"` (RL routing) would remove the inert "(disconnected)" label entirely. Tried it during this session, hooks reverted it twice; deferred.
- **Async chat-result pattern for Cloudflare 524 SSE.** Design doc shipped in PR #537. Phase 1 implementation pending.

## Process learnings

1. **Worktrees are mandatory for parallel subagents.** Two non-isolated agents running `gh pr checkout` in the shared working tree caused branch-state interleaving that contaminated my fix commit (14 files instead of 2). Going forward every parallel agent dispatch uses `git worktree add /tmp/wt-<topic>` or the `Agent.isolation: worktree` flag.
2. **Superpowers code review is now standing process.** Saved as a feedback memory: every PR I open gets a `superpowers:code-reviewer` agent pass, every BLOCKER + IMPORTANT fixed in the same PR before merge. Five PRs in this session, every one of them turned up findings the initial implementation missed.
3. **Disk pressure makes the deploy workflow lie.** A `success` conclusion on `Docker Desktop Deployment` does NOT mean the new code is running. Always verify the container's actual code (`docker exec ... grep …` for the change you expected) before declaring victory.
4. **The user's "no local docker builds" rule is load-bearing.** Local builds bypass CI and create images with no provenance. The right escape hatch when CI silently fails is to free disk + retrigger the CI workflow, never `docker compose build` on the runner.

## Plan-doc convention going forward

Per the user's directive on 2026-05-17 (now `feedback_always_document_plans.md` in agent memory): every multi-step task or session lands in `docs/plans/YYYY-MM-DD-<topic>.md`. The TaskList stays in-conversation; the durable record lives here.
