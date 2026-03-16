# Cloudflare Tunnel — Local Laptop as Production Server

> Expose local Docker Compose services to the internet via Cloudflare Tunnel. Both `servicetsunami.com` and `agentprovision.com` served from this laptop.

**Date:** 2026-03-16
**Status:** Approved

---

## Goal

Use Cloudflare Tunnel to make this laptop the production server for `servicetsunami.com` and `agentprovision.com`. No port forwarding, no static IP, no GKE needed. Cloudflare handles SSL, DDoS protection, and DNS.

## Architecture

```
Internet
  │
  ├── servicetsunami.com ──→ Cloudflare DNS (CNAME → tunnel)
  ├── agentprovision.com ──→ Cloudflare DNS (CNAME → tunnel)
  │
  └── Cloudflare Tunnel (cloudflared in Docker Compose)
        │
        ├── */api/* → localhost:8001 (FastAPI)
        └── *       → localhost:8002 (React SPA)

Laptop (Docker Compose — internal only)
  ├── web          :8002
  ├── api          :8001
  ├── db           :8003  (PostgreSQL + pgvector)
  ├── temporal     :7233
  ├── code-worker
  ├── mcp-tools    :8087
  ├── mcp-server   :8086
  └── cloudflared          (tunnel daemon)
```

Only web and API are exposed. DB, Temporal, MCP, and code-worker are internal-only.

## Tunnel Configuration

**`~/.cloudflared/config.yml`:**
```yaml
tunnel: servicetsunami
credentials-file: /etc/cloudflared/credentials.json

ingress:
  # API routes (path-based, checked first)
  - hostname: servicetsunami.com
    path: /api/*
    service: http://api:8000
  - hostname: agentprovision.com
    path: /api/*
    service: http://api:8000

  # OAuth callbacks
  - hostname: servicetsunami.com
    path: /api/v1/oauth/*
    service: http://api:8000
  - hostname: agentprovision.com
    path: /api/v1/oauth/*
    service: http://api:8000

  # Web SPA (catch-all)
  - hostname: servicetsunami.com
    service: http://web:80
  - hostname: agentprovision.com
    service: http://web:80

  # Default
  - service: http_status:404
```

Note: Inside Docker Compose, `cloudflared` uses Docker service names (`api`, `web`) not `localhost`.

## DNS Setup

For each domain in Cloudflare dashboard:

| Type | Name | Content | Proxy |
|------|------|---------|-------|
| CNAME | `@` | `<tunnel-id>.cfargotunnel.com` | Proxied |
| CNAME | `www` | `<tunnel-id>.cfargotunnel.com` | Proxied |

SSL mode: **Full (strict)** — Cloudflare terminates SSL, tunnel encrypted to origin.

## Docker Compose Service

```yaml
cloudflared:
  image: cloudflare/cloudflared:latest
  command: tunnel --config /etc/cloudflared/config.yml run
  volumes:
    - ./cloudflared:/etc/cloudflared
  depends_on:
    - api
    - web
  restart: unless-stopped
```

Credentials stored in `./cloudflared/credentials.json` (gitignored).

## Domain-Based Branding

**API endpoint:** `GET /api/v1/branding` (public, no auth)

Reads `Host` header from request. Returns brand config:

```python
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
```

**Frontend:** React SPA calls `/api/v1/branding` on mount, applies brand_name and logo to login page and sidebar. Uses `window.location.hostname` as fallback.

**CORS:** API allows both domains as origins:
```python
origins = [
    "https://servicetsunami.com",
    "https://agentprovision.com",
    "http://localhost:8002",  # local dev
]
```

## OAuth Considerations

Google OAuth callback URLs need both domains registered:
- `https://servicetsunami.com/api/v1/oauth/google/callback`
- `https://agentprovision.com/api/v1/oauth/google/callback`

Or use a single callback domain and redirect. Simpler to register both in GCP Console.

## Setup Steps

### 1. Create Tunnel (one-time)
```bash
brew install cloudflared
cloudflared tunnel login
cloudflared tunnel create servicetsunami
# Saves credentials to ~/.cloudflared/<tunnel-id>.json
```

### 2. Configure DNS (one-time per domain)
```bash
cloudflared tunnel route dns servicetsunami servicetsunami.com
cloudflared tunnel route dns servicetsunami agentprovision.com
```

### 3. Write Config
Create `./cloudflared/config.yml` with the tunnel config above.
Copy credentials: `cp ~/.cloudflared/<tunnel-id>.json ./cloudflared/credentials.json`

### 4. Add to Docker Compose
Add the `cloudflared` service to `docker-compose.yml`.

### 5. Add Branding Endpoint
Create `GET /api/v1/branding` with domain mapping.

### 6. Update CORS
Add both domains to API CORS allowed origins.

### 7. Start
```bash
DB_PORT=8003 API_PORT=8001 WEB_PORT=8002 docker-compose up -d
```

Both domains are now live, served from this laptop.

## Security

- Cloudflare Tunnel is outbound-only — no open ports on the laptop
- SSL terminated at Cloudflare edge (automatic certificates)
- API still requires JWT auth for all protected endpoints
- DB, Temporal, MCP are not exposed — Docker internal network only
- Credentials file (`credentials.json`) is gitignored

## Uptime

Laptop must be on and connected to internet. When tunnel is down, Cloudflare shows its default 502 error page. No fallback.

## Files Changed

| File | Change |
|------|--------|
| `docker-compose.yml` | Add `cloudflared` service |
| `cloudflared/config.yml` | Tunnel routing config (new) |
| `cloudflared/credentials.json` | Tunnel credentials (gitignored, new) |
| `.gitignore` | Add `cloudflared/credentials.json` |
| `apps/api/app/api/v1/branding.py` | Domain branding endpoint (new or extend existing) |
| `apps/api/app/main.py` | Add CORS for both domains |
