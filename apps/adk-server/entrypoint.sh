#!/bin/bash
set -e

# Configure git identity if env vars are present
if [ -n "$GIT_AUTHOR_NAME" ]; then
    git config --global user.name "$GIT_AUTHOR_NAME"
fi
if [ -n "$GIT_AUTHOR_EMAIL" ]; then
    git config --global user.email "$GIT_AUTHOR_EMAIL"
fi

# Configure git remote with token for push access
if [ -n "$GITHUB_TOKEN" ]; then
    # Strip trailing newlines/whitespace from token (k8s secrets often include them)
    GITHUB_TOKEN="$(echo -n "$GITHUB_TOKEN" | tr -d '[:space:]')"
    git config --global url."https://x-access-token:${GITHUB_TOKEN}@github.com/".insteadOf "https://github.com/"
    git config --global --add safe.directory /app
fi

# Start ADK server
exec python server.py
