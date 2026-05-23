# AgentProvision API - Production Dockerfile
# Multi-stage build for optimal image size

# ===== Build Stage =====
FROM python:3.11-slim AS builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    libmagic1 \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Phase 1.5: build context is the repo root (see docker-compose.yml). All
# COPY paths are now repo-rooted so packages/cli_orchestrator/ is
# reachable alongside the API source.

# Install Python dependencies from the hash-locked file (F15 P1
# supply-chain hardening). ``requirements.in`` is the human-edited
# source of truth; ``requirements.lock`` is the pip-compile output
# pinned + hashed. ``--require-hashes`` rejects any wheel whose
# sha256 doesn't match the lock, defeating PyPI typosquats and
# compromised-release supply-chain attacks on direct AND transitive
# deps. Regenerate via:
#     pip-compile --generate-hashes --output-file=requirements.lock \
#       requirements.in
COPY apps/api/requirements.lock .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir --require-hashes -r requirements.lock

# Install grpcio-tools (build-time only, needs protobuf<7) and compile protos.
# The generated *_pb2*.py files will be copied to the production stage;
# grpcio-tools itself stays in the builder and is NOT in the production venv.
COPY apps/api/proto /app/proto
COPY apps/api/scripts/gen_proto.sh /app/scripts/gen_proto.sh
RUN pip install --no-cache-dir "grpcio-tools>=1.62.0,<2.0" "protobuf>=5.26.1,<7.0.0" && \
    bash /app/scripts/gen_proto.sh && \
    pip install --no-cache-dir "protobuf>=7.34.1"

# ===== Production Stage =====
FROM python:3.11-slim

# Labels
LABEL maintainer="AgentProvision Team" \
      version="1.0.0" \
      description="AgentProvision API - FastAPI Backend"

# Create non-root user for security
RUN groupadd --gid 1000 appgroup && \
    useradd --uid 1000 --gid appgroup --shell /bin/bash --create-home appuser

# Install runtime dependencies only
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    libmagic1 \
    curl \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Install CLI tools for device-auth / OAuth flows
RUN npm install -g @openai/codex @anthropic-ai/claude-code @google/gemini-cli

WORKDIR /app

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy application code
COPY --chown=appuser:appgroup apps/api/app /app/app

# PR-D-3: install.sh + install.ps1 served at agentprovision.com/install.{sh,ps1}.
# Resolved by app/api/v1/install_scripts.py via Path(__file__).parents[3]/static
# which lands at /app/static at runtime.
COPY --chown=appuser:appgroup apps/api/static /app/static

# Copy pre-compiled gRPC stubs from builder (proto compilation happened there
# with grpcio-tools + protobuf<7; production runs protobuf>=7 for neonize compat)
COPY --from=builder --chown=appuser:appgroup /app/app/generated /app/app/generated

# Canonical cli_orchestrator package (Phase 1.5). Re-exported by the
# apps/api shim modules under app/services/cli_orchestrator/. Sits at
# /app/cli_orchestrator/ so the top-level ``cli_orchestrator`` import
# resolves via /app on sys.path (uvicorn's CWD).
COPY --chown=appuser:appgroup packages/cli_orchestrator /app/cli_orchestrator

# Create storage directory with correct permissions
RUN mkdir -p /app/storage && chown -R appuser:appgroup /app/storage

# PR #514 / B3: curated platform-docs surface for the dashboard
# Files-mode "platform" scope. Copied from the repo's /docs tree so
# super-admins see release notes / plans / changelog, NOT the source
# tree. Verified clean of secrets (grep for sk-*/ghp_*/AIza*/PEM
# returned zero matches at the time of the copy). The workspace API
# reads from PLATFORM_DOCS_ROOT which defaults to this path.
COPY --chown=appuser:appgroup docs /opt/agentprovision/platform-docs
# Per-tenant workspace mount-point. Created here so the directory
# exists with the right ownership before the PVC / named volume is
# mounted on top (the mount preserves the underlying perms).
RUN mkdir -p /var/agentprovision/workspaces && \
    chown -R appuser:appgroup /var/agentprovision/workspaces

# Switch to non-root user
USER appuser

# Environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000 \
    IN_DOCKER=1

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8000/api/v1/ || exit 1

# Run the application
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "*"]
