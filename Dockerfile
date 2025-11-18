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

# Disable man pages and docs to speed up apt operations
RUN echo 'path-exclude /usr/share/doc/*' > /etc/dpkg/dpkg.cfg.d/01_nodoc && \
    echo 'path-exclude /usr/share/man/*' >> /etc/dpkg/dpkg.cfg.d/01_nodoc && \
    echo 'path-exclude /usr/share/groff/*' >> /etc/dpkg/dpkg.cfg.d/01_nodoc && \
    echo 'path-exclude /usr/share/info/*' >> /etc/dpkg/dpkg.cfg.d/01_nodoc && \
    echo 'path-exclude /usr/share/lintian/*' >> /etc/dpkg/dpkg.cfg.d/01_nodoc && \
    echo 'path-exclude /usr/share/linda/*' >> /etc/dpkg/dpkg.cfg.d/01_nodoc

# Install runtime dependencies
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    git

# Install uv (cacheable)
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-cache-dir uv

# Create non-root user
RUN useradd -m -u 1000 adcp

WORKDIR /app

# Cache bust for COPY layer - change this value to force rebuild
ARG CACHE_BUST=2025-10-23-FIX-PROMOTED-OFFERING-V4
RUN echo "Cache bust: $CACHE_BUST"

# Copy application code
COPY . .

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

# Set ownership after creating venv
RUN chown -R adcp:adcp /app

# Switch to non-root user
USER adcp

# Add .venv to PATH
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

# Default port
ENV ADCP_PORT=8080
ENV ADCP_HOST=0.0.0.0

# Expose ports (MCP, Admin UI, A2A)
EXPOSE 8080 8001 8091

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

# Use ENTRYPOINT to ensure the script runs
ENTRYPOINT ["/bin/bash", "./scripts/deploy/entrypoint.sh"]
