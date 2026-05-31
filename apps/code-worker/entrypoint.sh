#!/usr/bin/env bash
set -euo pipefail

# Strip trailing whitespace/newlines from secrets (K8s secret mounts can add them)
GITHUB_TOKEN="$(echo -n "${GITHUB_TOKEN}" | tr -d '[:space:]')"

# Mark /workspace as safe (ownership may differ across pod restarts)
git config --global --add safe.directory /workspace

# Only treat the token as usable if it's real — never the ghp_placeholder
# compose default (2026-05-31). Faking gh-auth with a bad token makes git retry
# against it; leaving git/gh cleanly unauthenticated + GIT_TERMINAL_PROMPT=0
# (baked in the image) makes a credential-less clone fail FAST instead of
# prompting on the PTY and hanging the 25-min turn timeout.
_HAS_REAL_TOKEN=0
if [ -n "${GITHUB_TOKEN}" ] && [ "${GITHUB_TOKEN}" != "ghp_placeholder" ]; then
    _HAS_REAL_TOKEN=1
fi
# NOTE: git→github.com auth is wired ONCE, system-wide, in the Dockerfile
# (credential.https://github.com.helper = !gh auth git-credential). That helper
# resolves the per-tenant GitHub OAuth token (the /integrations connection) from
# GH_TOKEN/GITHUB_TOKEN in each turn's env — shared by EVERY CLI (claude, codex,
# gemini, copilot). No per-HOME / per-CLI git credential config needed here.

# Workspace self-repo setup is BEST-EFFORT: the worker clones tenant repos into
# the workspaces volume per-turn, so a failure here must never abort startup
# (set -e + the new fail-fast git env could otherwise exit the worker). Wrap in
# set +e and skip the token-clone entirely without a real token.
echo "[code-worker] Setting up repository (branch: ${GIT_BRANCH:-main})..."
set +e
if [ -d /workspace/.git ] && ( cd /workspace && git rev-parse --git-dir >/dev/null 2>&1 ); then
    echo "[code-worker] Updating existing repo..."
    ( cd /workspace && git fetch origin && git checkout "${GIT_BRANCH:-main}" && git reset --hard "origin/${GIT_BRANCH:-main}" )
elif [ "${_HAS_REAL_TOKEN}" = "1" ]; then
    echo "[code-worker] Cloning workspace repo..."
    rm -rf /workspace/.git /workspace/* 2>/dev/null
    # Clean URL — auth comes from the credential helper above (no token in argv).
    git clone --branch "${GIT_BRANCH:-main}" "https://github.com/nomad3/agentprovision-agents.git" /workspace
else
    echo "[code-worker] No real GITHUB_TOKEN — skipping workspace self-clone (non-fatal)."
fi
set -e

# Configure git identity for commits (global so it applies even if /workspace
# is empty / not a repo).
git config --global user.email "code-worker@agentprovision.com"
git config --global user.name "AgentProvision Code Worker"

# Configure gh CLI ONLY with a real token (never the placeholder).
if [ "${_HAS_REAL_TOKEN}" = "1" ]; then
    echo -n "${GITHUB_TOKEN}" | gh auth login --with-token 2>/dev/null || true
fi

# Start OpenCode server in background (local Gemma 4 via host Ollama)
# Keeps warm so _execute_opencode_chat() gets ~3s responses instead of ~90s cold starts
OPENCODE_PORT="${OPENCODE_PORT:-8200}"
echo "[code-worker] Starting OpenCode server on port ${OPENCODE_PORT}..."

# Write opencode config for the server.
#
# mcp servers — without this block, OpenCode comes up with ZERO MCP tools
# registered, even though apps/mcp-server is running on port 8086 with all
# 156 AgentProvision tools (find_entities, search_knowledge, recall_memory,
# etc.). Before the platform-routing flip that started defaulting Luna to
# OpenCode, the same chat path went through Claude Code which wrote its
# own .claude.json with the mcpServers block from
# `cli_session_manager._build_mcp_config()`. OpenCode never got the same
# treatment — the persistent-server commit (7e5cd727) only wired the
# Ollama provider. Result: every Luna chat through WhatsApp lost
# find_entities/search_knowledge/recall_memory access without anyone
# noticing because Gmail/Calendar still resolved via the user's external
# Claude.ai connectors, so the symptom looked like "MCP works but
# AgentProvision tools are gone".
#
# Tenant scoping: per-tool tenant_id is injected by the prompt prefix in
# cli_executors/opencode.py (`Always pass tenant_id in ALL MCP tool calls`),
# so the static config only needs the X-Internal-Key header. Each MCP
# tool call already takes tenant_id as an argument.
MCP_TOOLS_URL_DEFAULT="http://mcp-tools:8086/sse"
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
  "model": "ollama/${OPENCODE_MODEL:-gemma4}",
  "mcp": {
    "agentprovision": {
      "type": "remote",
      "url": "${MCP_TOOLS_URL:-${MCP_TOOLS_URL_DEFAULT}}",
      "enabled": true,
      "headers": {
        "X-Internal-Key": "${MCP_API_KEY:-dev_mcp_key}"
      }
    }
  }
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
