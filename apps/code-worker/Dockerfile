FROM python:3.11-slim

# Install system deps: git, gh CLI, Node.js 20 LTS
RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl gnupg ca-certificates && \
    # Node.js 20
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs && \
    # GitHub CLI
    curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | \
        gpg --dearmor -o /usr/share/keyrings/githubcli-archive-keyring.gpg && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list && \
    apt-get update && apt-get install -y gh && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Install Claude Code CLI
RUN npm install -g @anthropic-ai/claude-code

# Python deps
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Non-root user
RUN useradd -m -u 1000 codeworker && \
    mkdir -p /workspace && chown codeworker:codeworker /workspace && \
    mkdir -p /home/codeworker/.config /home/codeworker/.claude/session-env && \
    chown -R codeworker:codeworker /home/codeworker
USER codeworker

ENTRYPOINT ["/app/entrypoint.sh"]
