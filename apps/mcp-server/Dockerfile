FROM python:3.11-slim

WORKDIR /app

# Create non-root user
RUN groupadd -g 2001 mcpuser && \
    useradd -r -u 2001 -g mcpuser -m mcpuser

# Install system dependencies for postgres client and build tools
# ffmpeg: required by yt-dlp for audio extraction in the extract_media MCP
# primitive (Luna Learn from Media, T2.1) — yt-dlp shells out to ffmpeg
# for codec conversion / muxing of downloaded YouTube/Instagram audio.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    gcc \
    python3-dev \
    curl \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies separately to avoid resolution issues
# Note: removed playwright, sentence-transformers, einops to save space
# IMPORTANT: this list must stay in sync with pyproject.toml's `dependencies`
# block. The next pip install runs with `--no-deps`, so anything added to
# pyproject.toml that isn't repeated here will silently NOT be installed.
COPY pyproject.toml .
RUN pip install --no-cache-dir --break-system-packages psycopg2-binary sqlalchemy asyncpg httpx pyarrow pandas pydantic pydantic-settings python-dotenv fastapi uvicorn python-multipart mcp playwright==1.50.0 'python-jose>=3.3.0' 'beautifulsoup4>=4.12.0' 'yt-dlp>=2024.10.0'

# Install the project
RUN pip install --no-cache-dir --break-system-packages --no-deps .

# Copy source
COPY src/ src/
COPY pyproject.toml .

# Set ownership
RUN chown -R mcpuser:mcpuser /app

# Health check — FastMCP SSE transport exposes /sse (GET returns 200 when ready).
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -sf -o /dev/null http://localhost:8086/sse || exit 1

# Expose MCP port
EXPOSE 8086

# Default environment variables
ENV FASTMCP_PORT=8086
ENV FASTMCP_HOST=0.0.0.0

# Run as non-root
USER mcpuser

# Run the FastMCP tools server (streamable-http on /mcp).
# `src.server` is the deprecated REST scrape/postgres app and is no longer
# the entry point.
CMD ["python", "-m", "src.mcp_serve"]
