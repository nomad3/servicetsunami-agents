# Cloudflare Tunnel Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose local Docker Compose stack to the internet via Cloudflare Tunnel, serving both `servicetsunami.com` and `agentprovision.com` from this laptop.

**Architecture:** Cloudflare Tunnel (outbound-only) → routes `*/api/*` to FastAPI (:8001), everything else to React SPA (:8002). Both domains, one tunnel, one stack.

**Tech Stack:** Cloudflare Tunnel (`cloudflared`), Docker Compose, FastAPI CORS, React branding.

**Spec:** `docs/plans/2026-03-16-cloudflare-tunnel-design.md`

---

## File Structure

### New Files
| File | Responsibility |
|------|---------------|
| `cloudflared/config.yml` | Tunnel routing: domains → local services |
| `cloudflared/.gitkeep` | Placeholder (credentials.json is gitignored) |
| `apps/api/app/api/v1/branding.py` | `GET /api/v1/branding` — domain-based branding |

### Modified Files
| File | Change |
|------|--------|
| `docker-compose.yml` | Add `cloudflared` service |
| `.gitignore` | Add `cloudflared/credentials.json` |
| `apps/api/app/main.py` | Add CORS origins for both domains |
| `apps/api/app/api/v1/routes.py` | Mount branding router |

---

## Task 1: Create Cloudflare Tunnel

**Manual steps (cannot be automated):**

- [ ] **Step 1: Install cloudflared**
```bash
brew install cloudflared
```

- [ ] **Step 2: Login to Cloudflare**
```bash
cloudflared tunnel login
# Opens browser — select your Cloudflare account
```

- [ ] **Step 3: Create the tunnel**
```bash
cloudflared tunnel create servicetsunami
# Output: Created tunnel servicetsunami with id <TUNNEL_ID>
# Saves credentials to ~/.cloudflared/<TUNNEL_ID>.json
```

- [ ] **Step 4: Note the tunnel ID**
Save the `<TUNNEL_ID>` — needed for DNS and config.

---

## Task 2: Configure DNS

- [ ] **Step 1: Route servicetsunami.com**
```bash
cloudflared tunnel route dns servicetsunami servicetsunami.com
cloudflared tunnel route dns servicetsunami www.servicetsunami.com
```

- [ ] **Step 2: Route agentprovision.com**
```bash
cloudflared tunnel route dns servicetsunami agentprovision.com
cloudflared tunnel route dns servicetsunami www.agentprovision.com
```

- [ ] **Step 3: Verify in Cloudflare dashboard**
Both domains should show CNAME records pointing to `<TUNNEL_ID>.cfargotunnel.com`.

- [ ] **Step 4: Set SSL mode**
In Cloudflare dashboard → SSL/TLS → set to **Full** for both domains.

---

## Task 3: Tunnel Config + Docker Compose

**Files:**
- Create: `cloudflared/config.yml`
- Modify: `docker-compose.yml`
- Modify: `.gitignore`

- [ ] **Step 1: Create cloudflared directory**
```bash
mkdir -p cloudflared
```

- [ ] **Step 2: Copy credentials**
```bash
cp ~/.cloudflared/<TUNNEL_ID>.json cloudflared/credentials.json
```

- [ ] **Step 3: Create config.yml**

```yaml
# cloudflared/config.yml
tunnel: servicetsunami
credentials-file: /etc/cloudflared/credentials.json

ingress:
  # API routes
  - hostname: servicetsunami.com
    path: /api/*
    service: http://api:8000
  - hostname: agentprovision.com
    path: /api/*
    service: http://api:8000

  # Web SPA (catch-all per domain)
  - hostname: servicetsunami.com
    service: http://web:80
  - hostname: agentprovision.com
    service: http://web:80

  # Reject everything else
  - service: http_status:404
```

- [ ] **Step 4: Add to .gitignore**
```
cloudflared/credentials.json
```

- [ ] **Step 5: Add cloudflared service to docker-compose.yml**

Add before `test-db`:
```yaml
  cloudflared:
    image: cloudflare/cloudflared:latest
    command: tunnel --config /etc/cloudflared/config.yml run
    volumes:
      - ./cloudflared:/etc/cloudflared:ro
    depends_on:
      - api
      - web
    restart: unless-stopped
```

- [ ] **Step 6: Start and verify**
```bash
DB_PORT=8003 API_PORT=8001 WEB_PORT=8002 docker-compose up -d
docker logs servicetsunami-agents-cloudflared-1
# Should show: "Connection registered" and "Tunnel is connected"
```

- [ ] **Step 7: Test from browser**
Open `https://servicetsunami.com` — should see the React SPA.
Open `https://servicetsunami.com/api/v1/` — should see API health response.

- [ ] **Step 8: Commit**
```bash
git add cloudflared/config.yml cloudflared/.gitkeep .gitignore docker-compose.yml
git commit -m "feat: add Cloudflare Tunnel to docker-compose"
```

---

## Task 4: Domain Branding Endpoint

**Files:**
- Create: `apps/api/app/api/v1/branding.py`
- Modify: `apps/api/app/api/v1/routes.py`

- [ ] **Step 1: Create branding endpoint**

```python
# apps/api/app/api/v1/branding.py
"""Domain-based branding — returns brand config based on Host header."""
from fastapi import APIRouter, Request

router = APIRouter()

DOMAIN_BRANDING = {
    "servicetsunami.com": {
        "brand_name": "ServiceTsunami",
        "logo_url": "/assets/servicetsunami-logo.png",
        "theme": "ocean",
        "tagline": "AI Agent Orchestration Platform",
    },
    "agentprovision.com": {
        "brand_name": "AgentProvision",
        "logo_url": "/assets/agentprovision-logo.png",
        "theme": "ocean",
        "tagline": "Enterprise AI Agent Platform",
    },
}

DEFAULT_BRANDING = DOMAIN_BRANDING["servicetsunami.com"]


@router.get("/branding")
def get_branding(request: Request):
    """Return branding config for the current domain. No auth required."""
    host = (
        request.headers.get("x-forwarded-host")
        or request.headers.get("host")
        or ""
    ).split(":")[0].lower()

    # Match domain (strip www)
    if host.startswith("www."):
        host = host[4:]

    return DOMAIN_BRANDING.get(host, DEFAULT_BRANDING)
```

- [ ] **Step 2: Mount in routes.py**

Add to `apps/api/app/api/v1/routes.py`:
```python
from app.api.v1 import branding
api_router.include_router(branding.router, tags=["branding"])
```

- [ ] **Step 3: Commit**
```bash
git add apps/api/app/api/v1/branding.py apps/api/app/api/v1/routes.py
git commit -m "feat: add domain-based branding endpoint"
```

---

## Task 5: CORS Configuration

**Files:**
- Modify: `apps/api/app/main.py`

- [ ] **Step 1: Update CORS origins**

Find the `CORSMiddleware` setup in `apps/api/app/main.py` and add both production domains:

```python
origins = [
    "https://servicetsunami.com",
    "https://www.servicetsunami.com",
    "https://agentprovision.com",
    "https://www.agentprovision.com",
    "http://localhost:8002",
    "http://localhost:3000",
]
```

- [ ] **Step 2: Verify CORS works**
```bash
curl -I https://servicetsunami.com/api/v1/ -H "Origin: https://servicetsunami.com"
# Should include: Access-Control-Allow-Origin: https://servicetsunami.com
```

- [ ] **Step 3: Commit**
```bash
git add apps/api/app/main.py
git commit -m "feat: add CORS for servicetsunami.com and agentprovision.com"
```

---

## Task 6: Google OAuth Callbacks

- [ ] **Step 1: Update Google Cloud Console**
Add both callback URLs in GCP → APIs & Services → Credentials → OAuth client:
- `https://servicetsunami.com/api/v1/oauth/google/callback`
- `https://agentprovision.com/api/v1/oauth/google/callback`

- [ ] **Step 2: Update .env redirect URI**
In `apps/api/.env`, change:
```
GOOGLE_REDIRECT_URI=https://servicetsunami.com/api/v1/oauth/google/callback
```

- [ ] **Step 3: Commit**
```bash
git add apps/api/.env
git commit -m "feat: update OAuth redirect URI for production domain"
```

---

## Summary

| Task | What | Time |
|------|------|------|
| 1 | Create tunnel (manual) | 5 min |
| 2 | Configure DNS | 5 min |
| 3 | Tunnel config + docker-compose | 10 min |
| 4 | Branding endpoint | 10 min |
| 5 | CORS configuration | 5 min |
| 6 | OAuth callbacks | 5 min |

**Total: ~40 minutes.** After this, both domains are live from your laptop.
