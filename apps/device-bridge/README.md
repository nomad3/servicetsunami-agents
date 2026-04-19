# Luna Device Bridge

Local-network microservice that exposes RTSP cameras (and other edge hardware)
to Luna via WebRTC and HTTP snapshots. Runs on a machine that has direct L2/L3
reach to the cameras — typically a homelab NUC, a Raspberry Pi, or the user's
laptop — NOT in the main Kubernetes cluster.

## Deployment scope

This service is **not** part of the production Docker / Helm / Terraform deploy
pipeline. It is intentionally excluded because:

1. It must run on the same LAN as the cameras (cluster pods can't reach RTSP).
2. Each bridge is per-tenant / per-site, not a shared platform component.
3. Authentication is bridge-local (`DEVICE_BRIDGE_TOKEN` env var), not platform
   JWT — so treat the bridge as an untrusted edge device from the cluster's
   perspective.

If you later want to containerize it for a specific tenant site, add a per-site
Dockerfile + compose file under `apps/device-bridge/deploy/<site>/` rather than
wiring it into the main Helm charts.

## Running locally

```bash
cd apps/device-bridge
pip install -r requirements.txt
export DEVICE_BRIDGE_TOKEN=<shared-secret-with-luna-api>
export LUNA_API_URL=https://agentprovision.com/api/v1
export BRIDGE_CORS_ORIGINS=https://agentprovision.com,http://localhost:3000
python main.py
```

Service listens on `:8088`.

## Endpoints

All endpoints except `/status` require `X-Bridge-Token: <DEVICE_BRIDGE_TOKEN>`
or `Authorization: Bearer <DEVICE_BRIDGE_TOKEN>`.

| Method | Path                               | Purpose                          |
|--------|------------------------------------|----------------------------------|
| POST   | `/cameras`                         | Register an RTSP camera          |
| POST   | `/cameras/{id}/snapshot`           | Capture a JPEG frame (base64)    |
| POST   | `/bridge/connect`                  | WebRTC SDP offer/answer exchange |
| GET    | `/status`                          | Public health/status (no auth)   |

## Security notes

- `rtsp_url` on camera registration MUST use `rtsp://` or `rtsps://` scheme.
  The bridge rejects anything else (SSRF guard).
- CORS is restricted to `BRIDGE_CORS_ORIGINS` (comma-separated allowlist).
- No platform credentials / JWT should ever be forwarded to the bridge.
