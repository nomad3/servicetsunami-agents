#!/usr/bin/env bash
set -euo pipefail

# Strip trailing whitespace/newlines from secrets (K8s secret mounts can add them)
GITHUB_TOKEN="$(echo -n "${GITHUB_TOKEN}" | tr -d '[:space:]')"

# Mark /workspace as safe (ownership may differ across pod restarts)
git config --global --add safe.directory /workspace

echo "[code-worker] Setting up repository (branch: ${GIT_BRANCH:-main})..."
if [ -d /workspace/.git ]; then
    # Verify repo is valid; if not, remove and re-clone
    if cd /workspace && git rev-parse --git-dir >/dev/null 2>&1; then
        echo "[code-worker] Updating existing repo..."
        git fetch origin && git checkout "${GIT_BRANCH:-main}" && git reset --hard "origin/${GIT_BRANCH:-main}"
    else
        echo "[code-worker] Removing corrupted repo..."
        rm -rf /workspace/.git /workspace/*
        git clone --branch "${GIT_BRANCH:-main}" "https://${GITHUB_TOKEN}@github.com/nomad3/servicetsunami-agents.git" /workspace
    fi
else
    git clone --branch "${GIT_BRANCH:-main}" "https://${GITHUB_TOKEN}@github.com/nomad3/servicetsunami-agents.git" /workspace
fi

# Configure git identity for commits
cd /workspace
git config user.email "code-worker@servicetsunami.com"
git config user.name "ServiceTsunami Code Worker"

# Configure gh CLI
echo -n "${GITHUB_TOKEN}" | gh auth login --with-token 2>/dev/null || true

# Start OpenCode server in background (local Gemma 4 via host Ollama)
# Keeps warm so _execute_opencode_chat() gets ~3s responses instead of ~90s cold starts
OPENCODE_PORT="${OPENCODE_PORT:-8200}"
echo "[code-worker] Starting OpenCode server on port ${OPENCODE_PORT}..."

# Write opencode config for the server
mkdir -p /home/codeworker/.config/opencode
cat > /home/codeworker/opencode.json <<OCEOF
{
  "\$schema": "https://opencode.ai/config.json",
  "provider": {
    "ollama": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Ollama",
      "options": {
        "baseURL": "${OPENCODE_OLLAMA_URL:-http://host.docker.internal:11434/v1}"
      },
      "models": {
        "${OPENCODE_MODEL:-gemma4}": {
          "name": "${OPENCODE_MODEL:-gemma4}"
        }
      }
    }
  },
  "model": "ollama/${OPENCODE_MODEL:-gemma4}"
}
OCEOF

cd /home/codeworker
# Try to start OpenCode server — non-fatal if it fails
(opencode serve --port "${OPENCODE_PORT}" >>/tmp/opencode-server.log 2>&1) &
OPENCODE_PID=$!
echo "[code-worker] OpenCode server PID: ${OPENCODE_PID}"
# Give it a moment to start
sleep 3
if kill -0 "${OPENCODE_PID}" 2>/dev/null; then
    echo "[code-worker] OpenCode server started successfully"
else
    echo "[code-worker] WARNING: OpenCode server failed to start (will use fallback)"
fi

echo "[code-worker] Starting Temporal worker..."
cd /app
exec python -m worker
