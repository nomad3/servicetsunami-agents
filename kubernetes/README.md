# kubernetes/

Cluster-level Kubernetes manifests applied **outside** of the per-service Helm charts. The Helm charts live in [`../helm/`](../helm/) — see [`../helm/README.md`](../helm/README.md) for the chart reference. Use these manifests when you need namespace-scoped or cluster-scoped objects that the per-service charts shouldn't own.

Local deploys via Rancher Desktop K8s use [`../scripts/deploy_k8s_local.sh`](../scripts/deploy_k8s_local.sh) which applies these manifests + runs Helm + applies migrations + brings up the tunnel.

## Contents

```
kubernetes/
├── cloudflared-deployment.yaml   # in-cluster Cloudflare tunnel pod (routes
│                                 # agentprovision.com → api/web)
├── ingress.yaml                  # legacy GKE Ingress (production used Cloudflare
│                                 # tunnel pod instead; kept for reference)
├── k3d-config.yaml               # k3d cluster bootstrap config
├── namespaces/                   # `agentprovision`, others
├── external-secrets/             # ExternalSecrets for GCP Secret Manager
├── gateway/                      # Gateway API resources (HTTPRoute, etc.)
└── network-policies/             # NetworkPolicy rules for tenant isolation
```

## Cloudflare tunnel

The cloudflared deployment is an **in-cluster pod** that holds an outbound TCP tunnel to Cloudflare. It routes `agentprovision.com` → `api:8000` + `web:80`, and `luna.agentprovision.com` → `api:8000` + `luna-client:80`. There is no port-forward and no external load balancer.

The `notFound` rule in the tunnel config blocks `/api/v1/*/internal/*` from public-internet traffic (#207, 2026-04-22) — these endpoints are still reachable in-cluster for service-to-service calls authenticated with `X-Internal-Key`.

Credentials live in the `cloudflared-creds` Secret. To rotate or rebuild:

```bash
kubectl -n agentprovision get secret cloudflared-creds -o yaml
kubectl -n agentprovision rollout restart deployment/cloudflared
```

## External secrets

`external-secrets/` contains `ExternalSecret` resources synced from GCP Secret Manager (when the cluster has the External Secrets Operator installed). Each app's Helm chart references these via `externalSecret.enabled: true` in its values file.

For local dev on Rancher Desktop these are not used — secrets come from `apps/api/.env` via the regular Kubernetes `Secret` objects created by Helm.

## Apply patterns

```bash
# Bootstrap namespaces
kubectl apply -f kubernetes/namespaces/

# Cloudflare tunnel
kubectl apply -f kubernetes/cloudflared-deployment.yaml

# Network policies (tenant isolation)
kubectl apply -f kubernetes/network-policies/

# External secrets (only on clusters with the operator)
kubectl apply -f kubernetes/external-secrets/
```

The `deploy_k8s_local.sh` script handles ordering for local deploys.

## Hard rule

Never edit the cloudflared-deployment.yaml in production without coordinating — the tunnel is the only public ingress path. Use a feature branch + PR + reviewed deploy.
