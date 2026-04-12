#!/usr/bin/env bash
# AgentProvision Local Kubernetes Deployment Script
# Target: k3d (k3s in Docker) — same K8s flavor as on-prem deployment
# Cluster name: agentprovision (context: k3d-agentprovision)
# Setup: k3d cluster create --config kubernetes/k3d-config.yaml
#
# Usage:
#   ./scripts/deploy_k8s_local.sh              # Deploy everything
#   ./scripts/deploy_k8s_local.sh --skip-build # Deploy without rebuilding images
#   ./scripts/deploy_k8s_local.sh --infra-only # Deploy only infrastructure (postgres, redis, temporal)

set -euo pipefail

NAMESPACE="agentprovision"
CHART_PATH="./helm/charts/microservice"
VALUES_DIR="./helm/values"
SKIP_BUILD=false
INFRA_ONLY=false

for arg in "$@"; do
  case $arg in
    --skip-build) SKIP_BUILD=true ;;
    --infra-only) INFRA_ONLY=true ;;
  esac
done

echo "=== AgentProvision K8s Local Deploy (Rancher Desktop) ==="

# ── Pre-flight checks ────────────────────────────────────────
kubectl config use-context k3d-agentprovision 2>/dev/null || {
  echo "ERROR: k3d-agentprovision context not found."
  echo "  Create the cluster: k3d cluster create --config kubernetes/k3d-config.yaml"
  exit 1
}

kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -

# ── Build Docker images ──────────────────────────────────────
if [ "$SKIP_BUILD" = false ]; then
  echo ""
  echo "=== Building Docker images ==="

  build_image() {
    local name=$1 path=$2
    echo "  Building $name..."
    if docker build -t "$name:latest" "$path" --quiet 2>/dev/null; then
      echo "  ✓ $name"
    else
      echo "  ✗ $name FAILED"
      return 1
    fi
  }

  # Core services
  build_image "agentprovision-api" "./apps/api"
  build_image "agentprovision-web" "./apps/web"

  # Workers (orchestration-worker shares api image with different entrypoint)
  docker tag agentprovision-api:latest agentprovision-orchestration-worker:latest
  echo "  ✓ agentprovision-orchestration-worker (tagged from api)"

  # Code worker
  build_image "agentprovision-code-worker" "./apps/code-worker"

  # MCP Tools
  build_image "agentprovision-mcp-tools" "./apps/mcp-server"

  # Rust services
  build_image "agentprovision-embedding-service" "./apps/embedding-service"
  build_image "agentprovision-memory-core" "./apps/memory-core"

  # Import images into k3d cluster (k3d nodes don't see host docker images)
  echo ""
  echo "=== Importing images into k3d cluster ==="
  k3d image import \
    agentprovision-api:latest \
    agentprovision-orchestration-worker:latest \
    agentprovision-web:latest \
    agentprovision-code-worker:latest \
    agentprovision-mcp-tools:latest \
    agentprovision-embedding-service:latest \
    agentprovision-memory-core:latest \
    --cluster agentprovision 2>&1 | tail -5
fi

# ── Deploy Infrastructure ────────────────────────────────────
echo "=== Deploying Infrastructure ==="

helm upgrade --install postgresql "$CHART_PATH" \
  -n "$NAMESPACE" -f "$VALUES_DIR/postgresql-local.yaml" --wait --timeout 90s || true
echo "  ✓ postgresql"

helm upgrade --install redis "$CHART_PATH" \
  -n "$NAMESPACE" -f "$VALUES_DIR/redis-local.yaml" --wait --timeout 60s || true
echo "  ✓ redis"

# Wait for postgres to be accepting connections before starting temporal
echo "  Waiting for PostgreSQL to accept connections..."
for i in $(seq 1 30); do
  if kubectl exec -n "$NAMESPACE" deploy/postgresql -- \
    pg_isready -U postgres -d agentprovision 2>/dev/null | grep -q "accepting"; then
    echo "  ✓ PostgreSQL ready"
    break
  fi
  sleep 2
done

helm upgrade --install temporal "$CHART_PATH" \
  -n "$NAMESPACE" -f "$VALUES_DIR/temporal-local.yaml" --wait --timeout 120s || true
echo "  ✓ temporal"

if [ "$INFRA_ONLY" = true ]; then
  echo ""
  echo "=== Infrastructure deployed. Use --skip-build to deploy services. ==="
  kubectl get pods -n "$NAMESPACE"
  exit 0
fi

# ── Deploy Rust Services ─────────────────────────────────────
echo ""
echo "=== Deploying Rust Services ==="

helm upgrade --install embedding-service "$CHART_PATH" \
  -n "$NAMESPACE" \
  -f "$VALUES_DIR/embedding-service.yaml" \
  -f "$VALUES_DIR/embedding-service-local.yaml" || true
echo "  ✓ embedding-service"

helm upgrade --install memory-core "$CHART_PATH" \
  -n "$NAMESPACE" \
  -f "$VALUES_DIR/memory-core.yaml" \
  -f "$VALUES_DIR/memory-core-local.yaml" || true
echo "  ✓ memory-core"

# ── Deploy Application Services ──────────────────────────────
echo ""
echo "=== Deploying Application Services ==="

helm upgrade --install api "$CHART_PATH" \
  -n "$NAMESPACE" -f "$VALUES_DIR/agentprovision-api-local.yaml" || true
echo "  ✓ api"

helm upgrade --install mcp-tools "$CHART_PATH" \
  -n "$NAMESPACE" -f "$VALUES_DIR/agentprovision-mcp-local.yaml" || true
echo "  ✓ mcp-tools"

helm upgrade --install orchestration-worker "$CHART_PATH" \
  -n "$NAMESPACE" -f "$VALUES_DIR/agentprovision-orchestration-worker-local.yaml" || true
echo "  ✓ orchestration-worker"

helm upgrade --install web "$CHART_PATH" \
  -n "$NAMESPACE" -f "$VALUES_DIR/agentprovision-web-local.yaml" || true
echo "  ✓ web"

helm upgrade --install code-worker "$CHART_PATH" \
  -n "$NAMESPACE" -f "$VALUES_DIR/agentprovision-code-worker-local.yaml" || true
echo "  ✓ code-worker"

# ── Run pending migrations ───────────────────────────────────
echo ""
echo "=== Running migrations ==="
MIGRATION_DIR="./apps/api/migrations"

# Wait for postgres pod to be fully ready
sleep 3
PG_POD=$(kubectl get pod -n "$NAMESPACE" -l app.kubernetes.io/name=postgresql -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")

if [ -n "$PG_POD" ]; then
  # Create tracking table
  kubectl exec -n "$NAMESPACE" "$PG_POD" -- \
    psql -U postgres agentprovision -c \
    "CREATE TABLE IF NOT EXISTS _migrations (filename TEXT PRIMARY KEY, applied_at TIMESTAMP DEFAULT now());" 2>/dev/null || true

  APPLIED=0
  SKIPPED=0
  for f in $(ls "$MIGRATION_DIR"/*.sql 2>/dev/null | sort); do
    BASENAME=$(basename "$f")
    ALREADY=$(kubectl exec -n "$NAMESPACE" "$PG_POD" -- \
      psql -U postgres agentprovision -tAc \
      "SELECT COUNT(*) FROM _migrations WHERE filename = '$BASENAME';" 2>/dev/null || echo "0")
    if [ "$ALREADY" = "1" ]; then
      SKIPPED=$((SKIPPED + 1))
      continue
    fi
    echo "  Applying: $BASENAME"
    kubectl cp "$f" "$NAMESPACE/$PG_POD:/tmp/migration.sql"
    if kubectl exec -n "$NAMESPACE" "$PG_POD" -- \
      psql -U postgres agentprovision -f /tmp/migration.sql 2>/dev/null; then
      kubectl exec -n "$NAMESPACE" "$PG_POD" -- \
        psql -U postgres agentprovision -c \
        "INSERT INTO _migrations (filename) VALUES ('$BASENAME') ON CONFLICT DO NOTHING;" 2>/dev/null
      APPLIED=$((APPLIED + 1))
    fi
  done
  echo "  Migrations: $APPLIED applied, $SKIPPED already applied"
else
  echo "  WARNING: PostgreSQL pod not found, skipping migrations"
fi

# ── Summary ──────────────────────────────────────────────────
echo ""
echo "=== Deployment Status ==="
kubectl get pods -n "$NAMESPACE" -o wide
echo ""
echo "=== Helm Releases ==="
helm list -n "$NAMESPACE"
echo ""

# ── Cloudflare Tunnel (in-cluster) ────────────────────────
echo "=== Deploying Cloudflare tunnel ==="
kubectl apply -f kubernetes/cloudflared-deployment.yaml 2>/dev/null || true
sleep 5

# Verify tunnel
TUNNEL_STATUS=$(curl -s -o /dev/null -w '%{http_code}' https://agentprovision.com/api/v1/ 2>/dev/null || echo '000')
echo "  Tunnel: $TUNNEL_STATUS"

echo ""
echo "=== Access ==="
echo "  Public API:  https://agentprovision.com/api/v1/"
echo "  Public Web:  https://agentprovision.com/"
echo "  DB debug:    kubectl port-forward -n $NAMESPACE svc/postgresql 5432:5432"
echo "  API debug:   kubectl port-forward -n $NAMESPACE svc/api 8000:80"
