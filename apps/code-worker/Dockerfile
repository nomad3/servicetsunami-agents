FROM python:3.11-slim

# Install system deps: git, gh CLI, Node.js 22
RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl gnupg ca-certificates socat && \
    # Node.js 22
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y nodejs && \
    # GitHub CLI
    curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | \
        gpg --dearmor -o /usr/share/keyrings/githubcli-archive-keyring.gpg && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list && \
    apt-get update && apt-get install -y gh && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Install CLI providers.
#
# @openai/codex is pinned to 0.130.0 (2026-05-16) because the rmcp client
# transport contract is version-sensitive — see
# ``docs/plans/2026-05-16-codex-mcp-transport-mismatch-research.md``.
# 0.130.0 honours ``experimental_use_rmcp_client = true`` and POSTs JSON-RPC
# to the configured StreamableHttp URL. A future Codex release could
# refactor that surface (rename the flag, drop streamable-HTTP, etc.) and
# silently break the streamable-HTTP mount in mcp-tools; pinning makes the
# break a controlled bump-and-test rather than a surprise on image rebuild.
RUN npm install -g \
        @anthropic-ai/claude-code \
        @openai/codex@0.130.0 \
        @google/gemini-cli \
        @qwen-code/qwen-code@0.15.11 \
        opencode-ai \
        @github/copilot

# Python deps
WORKDIR /app
# Phase 1.5: build context is the repo root (see docker-compose.yml). All
# subsequent COPY paths are repo-rooted so the canonical
# packages/cli_orchestrator/ package is reachable alongside the worker
# source.
COPY apps/code-worker/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Worker source first
COPY apps/code-worker/ .

# Canonical cli_orchestrator package — the worker imports it as
# ``cli_orchestrator`` (top-level) so it must sit on sys.path. Putting it
# at /app/cli_orchestrator/ achieves that since /app is the WORKDIR and
# Python adds CWD to sys.path implicitly for the worker entrypoint.
COPY packages/cli_orchestrator /app/cli_orchestrator

# Non-root user
RUN useradd -m -u 1000 codeworker && \
    mkdir -p /workspace && chown codeworker:codeworker /workspace && \
    mkdir -p /home/codeworker/.config /home/codeworker/.claude/session-env && \
    chown -R codeworker:codeworker /home/codeworker
USER codeworker

ENTRYPOINT ["/app/entrypoint.sh"]
