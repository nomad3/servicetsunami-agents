FROM python:3.11-slim

# Install system deps: git, gh CLI, Node.js 22, bzip2 (for Goose tarball)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl gnupg ca-certificates socat bzip2 && \
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
#
# Kimi K2 (Moonshot AI) is intentionally absent from this image: there
# is no npm package for it, and the executor in
# ``cli_executors/kimi.py`` talks to Moonshot's OpenAI-compatible HTTP
# endpoint directly via httpx. Do not add a Kimi npm install here.
RUN npm install -g \
        @anthropic-ai/claude-code \
        @openai/codex@0.130.0 \
        @google/gemini-cli \
        @qwen-code/qwen-code@0.15.11 \
        opencode-ai \
        @github/copilot

# Goose (Block) — Wave 2d. Apache-2.0 Rust agent CLI. Block ships
# pre-built binaries on GitHub releases; we pin to v1.34.1 because
# upstream releases frequently and a floating tag = surprise breakage
# (their JSON output / command surface has shifted across minor
# versions in the past). NOTE: github.com/block/goose 301-redirects to
# github.com/aaif-goose/goose in this environment (the project moved
# under Block's AAIF org). curl --location follows the redirect so the
# canonical block/goose URL keeps working in CI; if the redirect ever
# breaks, the resolved URL is on aaif-goose/goose at the same path.
ARG GOOSE_VERSION=v1.34.1
RUN curl -fsSL --retry 3 --location \
        -o /tmp/goose.tar.bz2 \
        "https://github.com/block/goose/releases/download/${GOOSE_VERSION}/goose-x86_64-unknown-linux-gnu.tar.bz2" && \
    tar -xjf /tmp/goose.tar.bz2 -C /usr/local/bin && \
    rm /tmp/goose.tar.bz2 && \
    chmod +x /usr/local/bin/goose && \
    goose --version

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
