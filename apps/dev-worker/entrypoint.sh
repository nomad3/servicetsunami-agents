#!/usr/bin/env bash
set -euo pipefail

echo "[dev-worker] Cloning repository..."
if [ ! -d /workspace/.git ]; then
    git clone "https://${GITHUB_TOKEN}@github.com/nomad3/servicetsunami-agents.git" /workspace
else
    cd /workspace && git fetch origin && git checkout main && git pull origin main
fi

# Configure git identity for commits
cd /workspace
git config user.email "dev-worker@servicetsunami.com"
git config user.name "ServiceTsunami Dev Worker"

# Configure gh CLI
echo "${GITHUB_TOKEN}" | gh auth login --with-token 2>/dev/null || true

echo "[dev-worker] Starting Temporal worker..."
exec python -m worker
