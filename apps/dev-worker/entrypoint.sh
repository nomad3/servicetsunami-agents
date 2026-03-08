#!/usr/bin/env bash
set -euo pipefail

# Strip trailing whitespace/newlines from secrets (K8s secret mounts can add them)
GITHUB_TOKEN="$(echo -n "${GITHUB_TOKEN}" | tr -d '[:space:]')"

# Mark /workspace as safe (ownership may differ across pod restarts)
git config --global --add safe.directory /workspace

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
echo -n "${GITHUB_TOKEN}" | gh auth login --with-token 2>/dev/null || true

echo "[dev-worker] Starting Temporal worker..."
exec python -m worker
