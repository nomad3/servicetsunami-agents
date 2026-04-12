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

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Install grpcio-tools (build-time only, needs protobuf<7) and compile protos.
# The generated *_pb2*.py files will be copied to the production stage;
# grpcio-tools itself stays in the builder and is NOT in the production venv.
COPY ./proto /app/proto
COPY ./scripts/gen_proto.sh /app/scripts/gen_proto.sh
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
COPY --chown=appuser:appgroup ./app /app/app

# Copy pre-compiled gRPC stubs from builder (proto compilation happened there
# with grpcio-tools + protobuf<7; production runs protobuf>=7 for neonize compat)
COPY --from=builder --chown=appuser:appgroup /app/app/generated /app/app/generated

# Create storage directory with correct permissions
RUN mkdir -p /app/storage && chown -R appuser:appgroup /app/storage

# Switch to non-root user
USER appuser

# Environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8000/api/v1/ || exit 1

# Run the application
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "*"]
