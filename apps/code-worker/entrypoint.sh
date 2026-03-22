#!/usr/bin/env bash
set -euo pipefail

# Strip trailing whitespace/newlines from secrets (K8s secret mounts can add them)
GITHUB_TOKEN="$(echo -n "${GITHUB_TOKEN}" | tr -d '[:space:]')"

# Mark /workspace as safe (ownership may differ across pod restarts)
git config --global --add safe.directory /workspace

echo "[code-worker] Setting up repository..."
if [ -d /workspace/.git ]; then
    # Verify repo is valid; if not, remove and re-clone
    if cd /workspace && git rev-parse --git-dir >/dev/null 2>&1; then
        echo "[code-worker] Updating existing repo..."
        git fetch origin && git checkout main && git reset --hard origin/main
    else
        echo "[code-worker] Removing corrupted repo..."
        rm -rf /workspace/.git /workspace/*
        git clone "https://${GITHUB_TOKEN}@github.com/nomad3/servicetsunami-agents.git" /workspace
    fi
else
    git clone "https://${GITHUB_TOKEN}@github.com/nomad3/servicetsunami-agents.git" /workspace
fi

# Configure git identity for commits
cd /workspace
git config user.email "code-worker@servicetsunami.com"
git config user.name "ServiceTsunami Code Worker"

# Configure gh CLI
echo -n "${GITHUB_TOKEN}" | gh auth login --with-token 2>/dev/null || true

# Proxy localhost:11434 → ollama:11434 so Codex CLI --oss can reach Ollama
if [ -n "${OLLAMA_HOST:-}" ]; then
    OLLAMA_TARGET="${OLLAMA_HOST#http://}"  # strip http:// prefix
    echo "[code-worker] Starting socat proxy: localhost:11434 → ${OLLAMA_TARGET}"
    socat TCP-LISTEN:11434,fork,reuseaddr TCP:"${OLLAMA_TARGET}" &
fi

echo "[code-worker] Starting Temporal worker..."
cd /app
exec python -m worker
