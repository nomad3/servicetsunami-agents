# Password recovery email sender — design + 25 security findings

Date: 2026-05-17
Owner: Alpha platform
Status: Shipped in PR #430 (merged 2026-05-18) — log-only mode until SMTP env vars are populated on the runner

## Problem

`POST /api/v1/password-recovery/{email}` generated a reset token, persisted the hash + expiry to the `users` table, and just logged `Password reset token generated for {email}` — **no email was ever sent.** The literal `# In a real app, send an email here.` had been at `apps/api/app/api/v1/auth.py:484` for months. Users requesting password recovery never received an email. Reported by the founder 2026-05-17. Tracked as task #266.

## Approach

Ship the missing sender + the 25 security findings the prior review surfaced. Real SMTP delivery (no third-party service-required); per-tenant hostname allowlist; STARTTLS / SMTP_SSL with verified context; CSRF cookie binding; JWT-iat floor so existing tokens are invalidated after reset.

## What shipped (PR #430 — 14 files, +847 / -31)

### Core sender (new)

- **`apps/api/app/services/email_sender.py`** (326 LOC, new) — `send_email(to, subject, body, ...)`. Connects to the configured SMTP host on 465 (SMTP_SSL with verified cert context) or 587 (STARTTLS with verified cert context + post-upgrade `isinstance(server.sock, ssl.SSLSocket)` guard). Refuses to call `login()` unless the socket is wrapped. Hostname allowlist enforced before opening the socket. Every header value goes through `_sanitize_header` which strips CR/LF/NUL and length-bounds to RFC 5322 limits (998 chars line, 254 mailbox, 200 subject, 80 from-name). From address validated for `@`. Connection failures + login failures + send failures all logged with non-leaking event names.

### Endpoint changes

- **`apps/api/app/api/v1/auth.py`** — `POST /password-recovery/{email}` now hands the send to `email_sender.send_password_recovery_email` via a `BackgroundTasks` dispatch (after response is flushed, to close the timing-oracle side channel). Two-tier rate limit: slowapi per-IP (`@limiter.limit("3/hour")`) plus a Redis-backed per-email cap (10 in any 24h window) with a 60s circuit-breaker fail-open if Redis is down. Identical response shape regardless of whether the email is registered (no enumeration oracle).
- `POST /reset-password` requires the `ap_reset_csrf` cookie set by the recovery endpoint to match the hash of the submitted token. Cookie scoped `path=/api/v1/auth/reset-password`, `HttpOnly=true`, `SameSite=strict`, `Secure` flipped based on prod URL detection.

### JWT-iat floor

- **Migration 130** adds `password_changed_at TIMESTAMP` to `users` and backfills `now()` for every existing row.
- **`apps/api/app/api/deps.py`** — `get_current_user` rejects any JWT with `iat < users.password_changed_at`. Helper `_jwt_iat_before_password_change` returns False when `pwc IS NULL` (legacy users pre-migration) but True when `iat IS NULL` AND `pwc IS NOT NULL` (defeats the "craft a token without iat to bypass the floor" exploit).

### Web side

- **`apps/web/src/pages/ResetPasswordPage.js`** — reads token from `window.location.hash` (URL fragment, not query string — never sent to servers, not in referer headers). Immediately scrubs the hash via `history.replaceState(null, '', location.pathname + location.search)` inside the same `useEffect` so analytics scripts loading after first paint can't capture it.
- **`apps/web/public/index.html`** — `Referrer-Policy: same-origin` meta tag.
- **`apps/web/src/services/auth.js`** — `withCredentials` on the reset-password POST so the CSRF cookie is sent.
- **`apps/web/src/i18n/locales/{en,es}/auth.json`** — reset flow strings (key parity verified).

### Env-var contract

Declared in `apps/api/.env.example`, `.env.production.example`, and `helm/values/agentprovision-api.yaml`:

```
EMAIL_SMTP_HOST=smtp.sendgrid.net        # or smtp.postmarkapp.com, AWS SES regional, etc.
EMAIL_SMTP_PORT=587                       # or 465 for implicit TLS
EMAIL_SMTP_USERNAME=apikey                # provider-specific
EMAIL_SMTP_PASSWORD=<api-key>             # provider-specific
EMAIL_SMTP_USE_TLS=true                   # mandatory; login() refused on plaintext
EMAIL_FROM=noreply@agentprovision.com
EMAIL_FROM_NAME=AgentProvision
```

Allowlisted hostnames: `smtp.sendgrid.net`, `smtp.postmarkapp.com`, `smtp.mailgun.org`, `smtp.gmail.com`, `smtp.fastmail.com`, `email-smtp.*.amazonaws.com`, `localhost`.

## 25 security findings (status)

### BLOCKERs (7, all fixed)

- **B-1** Token in URL fragment, SPA scrubs via `replaceState`, Referrer-Policy meta tag
- **B-2** Path param validated as email regex (length 3–254), CRLF/NUL stripped from every SMTP header
- **B-3** Reset-link hostname allowlist (`agentprovision.com`, `app.*`, `luna.*`, `localhost`); scheme pinned to https except localhost
- **B-4** `password_changed_at` column + JWT-iat floor + `iat IS NULL` rejection (review IMP-2 follow-up)
- **B-5** CSRF cookie binding (`ap_reset_csrf`, HttpOnly + SameSite=Strict + path scoped)
- **B-6** SMTP_SSL on 465, STARTTLS with verified ctx on 587, refuse `login()` if not `SSLSocket`
- **B-7** SMTP_HOST allowlist

### IMPORTANTs (11, all fixed)

I-1 attempt counter (3 burns the token), I-2 BackgroundTasks dispatch (closes timing oracle), I-3 enumeration parity (identical response shape + status), I-4 Redis-backed second-tier rate limit, I-5 `@limiter.limit("3/hour")`, I-6 fragment scrub timing, I-7 `EMAIL_FROM` `@` sanity (review follow-up), I-8 helm + env-example contract, I-9 i18n key parity en↔es, I-10 cookie path scoped tight (review follow-up), I-11 `_log_safe_email_id` HMAC truncation

### NITs (7)

Most addressed; a few deferred (full-table migration backfill batching past 1M users, NIT-5 email-in-query-string scrub).

## Tests

- `apps/api/tests/test_security_fixes.py` — sender SMTP_SSL + STARTTLS + isinstance-SSLSocket refusal, CSRF cookie flag assertion, JWT-iat e2e, `iat IS NULL` rejection, attempt-counter burn.
- `apps/api/tests/test_auth.py` — recovery enumeration parity, rate-limit assertion.
- CI green on all 3 required checks (api pytest, api integration, web jest).

## Operational status

PR #430 is merged and deployed. The sender is in **log-only mode** because the SMTP env vars are not yet populated on the self-hosted runner machine. To enable real delivery:

1. Choose an SMTP provider (recommended: SendGrid for transactional, ~$0/mo at recovery-email volumes).
2. Get an API key.
3. Populate `EMAIL_SMTP_HOST/USERNAME/PASSWORD/EMAIL_FROM` in the runner's `.env` (per [[deployment_current_state]]).
4. Restart the api: `docker compose restart api`.
5. Trigger a real password recovery on a test tenant and confirm the email arrives.

For helm / GKE: same env vars in `helm/values/agentprovision-api.yaml`'s ExternalSecret pulling from GCP Secret Manager (`agentprovision-email-smtp-host`, `…-username`, `…-password`).

## Risks

- **Provider rate limit at scale.** SendGrid free tier is ~100/day. Once we have >10 active tenants with password resets, upgrade the SendGrid plan or move to Postmark/Mailgun.
- **Spam classification.** First emails from a new domain often land in spam. SPF + DKIM + DMARC records on `agentprovision.com` must be configured at the DNS layer before launch. Outside this PR's scope but worth a follow-up.
- **Link tampering.** The reset link's hostname allowlist is hardcoded. If we ever serve from a new subdomain (e.g. `tenant.agentprovision.com`), add it to `_ALLOWED_HOSTS` in `email_sender.py`.

## References

- PR #430 — https://github.com/nomad3/agentprovision-agents/pull/430
- Session summary that landed this work alongside the gemini fix + cli-picker + code-worker persistence: [[2026-05-17-gemini-cli-picker-and-disk-pressure-session]]
- Task #266 — Password recovery email never arrives
