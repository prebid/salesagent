# syntax=docker/dockerfile:1.4
# Multi-stage build for smaller image
# Cache bust: 2025-10-22-2135
FROM python:3.12-slim AS builder

# Disable man pages and docs to speed up apt operations
RUN echo 'path-exclude /usr/share/doc/*' > /etc/dpkg/dpkg.cfg.d/01_nodoc && \
    echo 'path-exclude /usr/share/man/*' >> /etc/dpkg/dpkg.cfg.d/01_nodoc && \
    echo 'path-exclude /usr/share/groff/*' >> /etc/dpkg/dpkg.cfg.d/01_nodoc && \
    echo 'path-exclude /usr/share/info/*' >> /etc/dpkg/dpkg.cfg.d/01_nodoc && \
    echo 'path-exclude /usr/share/lintian/*' >> /etc/dpkg/dpkg.cfg.d/01_nodoc && \
    echo 'path-exclude /usr/share/linda/*' >> /etc/dpkg/dpkg.cfg.d/01_nodoc

# Install build dependencies in one layer
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    git

# Install uv (cacheable)
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-cache-dir uv

# Set up caching for uv
ENV UV_CACHE_DIR=/cache/uv
ENV UV_TOOL_DIR=/cache/uv-tools
ENV UV_PYTHON_PREFERENCE=only-system

# Copy project files
WORKDIR /app
COPY pyproject.toml uv.lock ./

# Install dependencies with caching and increased timeout
# This layer will be cached as long as pyproject.toml and uv.lock don't change
ENV UV_HTTP_TIMEOUT=300
RUN --mount=type=cache,target=/cache/uv \
    --mount=type=cache,target=/root/.cache/pip \
    uv sync --frozen

# Runtime stage
FROM python:3.12-slim

# OCI labels for GitHub Container Registry
LABEL org.opencontainers.image.title="AdCP Sales Agent"
LABEL org.opencontainers.image.description="Reference implementation of an AdCP (Ad Context Protocol) Sales Agent. Deploy with Docker Compose: curl -O https://raw.githubusercontent.com/adcontextprotocol/salesagent/main/docker-compose.yml && docker compose up -d"
LABEL org.opencontainers.image.url="https://github.com/adcontextprotocol/salesagent"
LABEL org.opencontainers.image.source="https://github.com/adcontextprotocol/salesagent"
LABEL org.opencontainers.image.documentation="https://github.com/adcontextprotocol/salesagent/blob/main/docs/quickstart.md"
LABEL org.opencontainers.image.vendor="Agentic Advertising Foundation"
LABEL org.opencontainers.image.licenses="MIT"

# Disable man pages and docs to speed up apt operations
RUN echo 'path-exclude /usr/share/doc/*' > /etc/dpkg/dpkg.cfg.d/01_nodoc && \
    echo 'path-exclude /usr/share/man/*' >> /etc/dpkg/dpkg.cfg.d/01_nodoc && \
    echo 'path-exclude /usr/share/groff/*' >> /etc/dpkg/dpkg.cfg.d/01_nodoc && \
    echo 'path-exclude /usr/share/info/*' >> /etc/dpkg/dpkg.cfg.d/01_nodoc && \
    echo 'path-exclude /usr/share/lintian/*' >> /etc/dpkg/dpkg.cfg.d/01_nodoc && \
    echo 'path-exclude /usr/share/linda/*' >> /etc/dpkg/dpkg.cfg.d/01_nodoc

# Install runtime dependencies including nginx
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    git \
    nginx

# Install supercronic for cron jobs (container-friendly cron)
ARG TARGETARCH
RUN SUPERCRONIC_ARCH=$(case "${TARGETARCH}" in "arm64") echo "linux-arm64" ;; *) echo "linux-amd64" ;; esac) && \
    curl -fsSL "https://github.com/aptible/supercronic/releases/download/v0.2.33/supercronic-${SUPERCRONIC_ARCH}" \
    -o /usr/local/bin/supercronic && \
    chmod +x /usr/local/bin/supercronic

# Install uv (cacheable)
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-cache-dir uv

WORKDIR /app

# Cache bust for COPY layer - change this value to force rebuild
ARG CACHE_BUST=2025-10-23-FIX-PROMOTED-OFFERING-V4
RUN echo "Cache bust: $CACHE_BUST"

# Copy application code
COPY . .

# Copy both nginx configs - run_all_services.py selects based on ADCP_MULTI_TENANT env var
# Default: simple (single-tenant, path-based routing)
# ADCP_MULTI_TENANT=true: full config with subdomain routing
COPY config/nginx/nginx-simple.conf /etc/nginx/nginx-simple.conf
COPY config/nginx/nginx.conf /etc/nginx/nginx-multi-tenant.conf

# Create nginx directories with proper permissions
RUN mkdir -p /var/log/nginx /var/run && \
    chown -R www-data:www-data /var/log/nginx /var/run

# Set up caching for uv
ENV UV_CACHE_DIR=/cache/uv
ENV UV_TOOL_DIR=/cache/uv-tools
ENV UV_PYTHON_PREFERENCE=only-system
ENV UV_PYTHON=/usr/local/bin/python3.12

# Create virtual environment and install dependencies
# This needs to be done as root first, then we'll switch to adcp user
ENV UV_HTTP_TIMEOUT=300
RUN --mount=type=cache,target=/cache/uv \
    --mount=type=cache,target=/root/.cache/pip \
    uv sync --python=/usr/local/bin/python3.12 --frozen

# Add .venv to PATH and set PYTHONPATH for module imports
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH="/app"
ENV PYTHONUNBUFFERED=1

# Default port
ENV ADCP_PORT=8080
ENV ADCP_HOST=0.0.0.0

# Expose port 8000 (nginx proxy - the only external-facing port)
# Internal services (MCP:8080, Admin:8001, A2A:8091) are accessed via nginx
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

# Use venv Python directly as entrypoint (prepares for hardened images that lack bash)
ENTRYPOINT ["/app/.venv/bin/python", "scripts/deploy/run_all_services.py"]
