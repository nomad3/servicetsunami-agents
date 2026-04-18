# Security Remediation Plan — Post-Pentest

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remediate 4 remaining open security findings from the 2026-04-18 penetration test.

**Architecture:** Minimal targeted fixes — no refactoring. Each task is independent and self-contained.

**Tech Stack:** FastAPI, Python, Docker Compose, Cloudflare tunnel config, React

**Reference audit:** `docs/report/2026-04-18-full-security-audit.md`  
**Reference pentest:** `docs/report/2026-04-18-pentest-verification.md`

---

### Task 1: Block `/internal/` Routes at Cloudflare (Finding N4 / Original Finding 5)

**Files:**
- Modify: `cloudflared/config.yml`

**Context:** All `/api/v1/*/internal/*` endpoints are reachable from the internet. The only protection is the `X-Internal-Key` header. Cloudflare tunnel should reject these paths before they reach the API.

- [ ] **Step 1: Read current Cloudflare config**

```bash
cat cloudflared/config.yml
```

- [ ] **Step 2: Add `notFound` ingress rule for `/internal/` paths**

Add a rule BEFORE the catch-all `agentprovision.com` ingress rule:

```yaml
ingress:
  - hostname: agentprovision.com
    path: /api/.*/internal/.*
    service: http_status:404
  - hostname: agentprovision.com
    service: http://api:80
  # ... rest of rules
```

- [ ] **Step 3: Restart cloudflared pod**

```bash
docker compose restart cloudflared
```

- [ ] **Step 4: Verify internal endpoint blocked from outside**

```bash
curl -o /dev/null -w "%{http_code}" \
  -H "X-Internal-Key: $(awk '/API_INTERNAL_KEY/{print $NF}' apps/api/.env | cut -d= -f2)" \
  "https://agentprovision.com/api/v1/oauth/internal/token/gmail?tenant_id=752626d9-8b2c-4aa2-87ef-c458d48bd38a"
# Expected: 404
```

- [ ] **Step 5: Verify internal endpoint still works from inside Docker network**

```bash
docker exec servicetsunami-agents-code-worker-1 python3 -c "
import os, httpx
key = os.environ.get('API_INTERNAL_KEY','')
resp = httpx.get('http://api:8000/api/v1/oauth/internal/token/gemini_cli',
    params={'tenant_id': '752626d9-8b2c-4aa2-87ef-c458d48bd38a'},
    headers={'X-Internal-Key': key})
print('HTTP', resp.status_code)
"
# Expected: 200
```

- [ ] **Step 6: Commit**

```bash
git add cloudflared/config.yml
git commit -m "security: block /internal/ routes at Cloudflare tunnel"
```

---

### Task 2: Fix `postMessage('*')` Wildcard Origin in OAuth Callback (Finding N2)

**Files:**
- Modify: `apps/api/app/api/v1/oauth.py`
- Modify: `apps/api/app/core/config.py`

**Context:** OAuth success callback sends user's email to `window.opener` with `'*'` as target origin. Any window can receive it.

- [ ] **Step 1: Add FRONTEND_URL to config**

In `apps/api/app/core/config.py`, add:
```python
FRONTEND_URL: str = "https://agentprovision.com"
```

- [ ] **Step 2: Update CALLBACK_HTML template**

In `apps/api/app/api/v1/oauth.py`, change the postMessage line in `CALLBACK_HTML`:
```python
# Before
  window.opener && window.opener.postMessage(
    {{ type: '{msg_type}', provider: '{provider}', email: '{email}' }}, '*');

# After
  window.opener && window.opener.postMessage(
    {{ type: '{msg_type}', provider: '{provider}', email: '{email}' }}, '{allowed_origin}');
```

- [ ] **Step 3: Pass allowed_origin through `_cb()` helper**

Update the `_cb()` function signature and all call sites to pass `allowed_origin=settings.FRONTEND_URL`.

- [ ] **Step 4: Verify in response**

```bash
curl -s "http://localhost:8000/api/v1/oauth/google/callback?code=test" | grep postMessage
# Should show: postMessage({...}, 'https://agentprovision.com')
# Should NOT show: postMessage({...}, '*')
```

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/api/v1/oauth.py apps/api/app/core/config.py
git commit -m "security: restrict postMessage origin in OAuth callback"
```

---

### Task 3: Move Password Recovery Email Out of URL Path (Finding N3)

**Files:**
- Modify: `apps/api/app/api/v1/auth.py`
- Modify: `apps/api/app/schemas/auth.py`
- Modify: `apps/web/src/pages/LoginPage.js` (or wherever the forgot password form is)

**Context:** `POST /api/v1/auth/password-recovery/{email}` puts email in URL path → logged in access logs and Cloudflare logs.

- [ ] **Step 1: Add PasswordRecoveryRequest schema**

In `apps/api/app/schemas/auth.py`:
```python
class PasswordRecoveryRequest(BaseModel):
    email: EmailStr
```

- [ ] **Step 2: Change route to use request body**

In `apps/api/app/api/v1/auth.py`:
```python
# Before
@router.post("/password-recovery/{email}")
def recover_password(request: Request, email: str, db: Session = Depends(deps.get_db)):

# After
@router.post("/password-recovery")
def recover_password(request: Request, body: PasswordRecoveryRequest, db: Session = Depends(deps.get_db)):
    email = body.email
```

- [ ] **Step 3: Update frontend forgot-password API call**

Find where frontend calls `/password-recovery/` and change to POST body:
```javascript
// Before
await api.post(`/auth/password-recovery/${email}`)

// After
await api.post('/auth/password-recovery', { email })
```

- [ ] **Step 4: Verify email no longer in access logs**

```bash
curl -X POST "http://localhost:8000/api/v1/auth/password-recovery" \
  -H "Content-Type: application/json" \
  -d '{"email":"test@example.com"}'
docker logs servicetsunami-agents-api-1 --tail 3 | grep "password-recovery"
# URL should NOT contain the email address
```

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/api/v1/auth.py apps/api/app/schemas/auth.py apps/web/src/...
git commit -m "security: move password recovery email to POST body to avoid log exposure"
```

---

### Task 4: Remove `--dangerously-skip-permissions` from Code-Worker (Finding N5 / Original Finding 6)

**Files:**
- Modify: `apps/code-worker/workflows.py`

**Context:** Three call sites use `--dangerously-skip-permissions` and two use `--dangerously-bypass-approvals-and-sandbox`. These disable Claude Code's permission gate entirely. Replacing with `--allowedTools` limits blast radius.

**Note:** This is a behavioral change — test thoroughly. The code-worker already runs as non-root `codeworker` user.

- [ ] **Step 1: Find all occurrences**

```bash
grep -n "dangerously" apps/code-worker/workflows.py
```

- [ ] **Step 2: Replace with `--allowedTools` allowlist**

For each `--dangerously-skip-permissions`, replace with:
```python
"--allowedTools", _build_allowed_tools_from_mcp(mcp_config_json),
```

The `_build_allowed_tools_from_mcp()` helper already exists in workflows.py and builds the tool allowlist from the MCP config.

For `--dangerously-bypass-approvals-and-sandbox` (Gemini/Copilot paths), remove entirely — those CLIs don't need this flag.

- [ ] **Step 3: Test with a simple code task**

Trigger a WhatsApp message that dispatches a code task (e.g., "create a simple hello world script"). Confirm the code-worker completes it successfully.

- [ ] **Step 4: Commit**

```bash
git add apps/code-worker/workflows.py
git commit -m "security: replace --dangerously-skip-permissions with --allowedTools allowlist"
```

---

## Deployment Note

After all tasks, run the full security verification:

```bash
# All 4 verifications should pass:
# 1. /internal/ blocked from internet
# 2. postMessage has specific origin not '*'  
# 3. Email not in password-recovery URL path
# 4. No --dangerously-skip-permissions in workflows.py
grep "dangerously" apps/code-worker/workflows.py  # should return empty
```
