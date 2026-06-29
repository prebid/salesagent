# syntax=docker/dockerfile:1.4
# Multi-stage build for smaller image
# Cache bust: 2026-02-27
ARG PYTHON_VERSION=3.12
ARG PYTHON_BASE_DIGEST=sha256:090ba77e2958f6af52a5341f788b50b032dd4ca28377d2893dcf1ecbdfdfe203
# Consumed by BuildKit for reproducible layer timestamps (PR 6 release pipeline).
ARG SOURCE_DATE_EPOCH=0
# Canonical uv version: .uv-version (CI reads the file; guard keeps this ARG in sync).
ARG UV_VERSION=0.11.15
# Manifest-list digest for uv 0.11.15 (resolves per TARGETPLATFORM; do not pin a single-arch manifest).
ARG UV_IMAGE_DIGEST=sha256:e590846f4776907b254ac0f44b5b380347af5d90d668138ca7938d1b0c2f98d3

FROM --platform=$TARGETPLATFORM ghcr.io/astral-sh/uv:${UV_VERSION}@${UV_IMAGE_DIGEST} AS uv
FROM python:${PYTHON_VERSION}-slim@${PYTHON_BASE_DIGEST} AS builder

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

COPY --from=uv /uv /uvx /bin/

# Set up caching for uv
ENV UV_CACHE_DIR=/cache/uv
ENV UV_TOOL_DIR=/cache/uv-tools
ENV UV_PYTHON_PREFERENCE=only-system
# Outside /app so docker-compose `.:/app` bind mounts never shadow the venv
# (anonymous /app/.venv volumes persist stale deps across image rebuilds — #1310).
ENV UV_PROJECT_ENVIRONMENT=/opt/venv

# Copy project files
WORKDIR /app
COPY pyproject.toml uv.lock ./

# Install dependencies with caching and increased timeout
# This layer will be cached as long as pyproject.toml and uv.lock don't change
ENV UV_HTTP_TIMEOUT=300
# Install runtime deps only — dev group (pytest, factory-boy, ruff, etc.) stays out
# of the production image. See [dependency-groups].dev in pyproject.toml.
RUN --mount=type=cache,target=/cache/uv \
    --mount=type=cache,target=/root/.cache/pip \
    uv sync --frozen --no-dev

# Runtime stage
FROM python:${PYTHON_VERSION}-slim@${PYTHON_BASE_DIGEST}

# OCI labels for GitHub Container Registry
LABEL org.opencontainers.image.title="AdCP Sales Agent"
LABEL org.opencontainers.image.description="Reference implementation of an AdCP (Ad Context Protocol) Sales Agent. See docs/quickstart.md for deployment options."
LABEL org.opencontainers.image.url="https://github.com/prebid/salesagent"
LABEL org.opencontainers.image.source="https://github.com/prebid/salesagent"
LABEL org.opencontainers.image.documentation="https://github.com/prebid/salesagent/blob/main/docs/quickstart.md"
LABEL org.opencontainers.image.vendor="Agentic Advertising Foundation"
LABEL org.opencontainers.image.licenses="MIT"

# Disable man pages and docs to speed up apt operations
RUN echo 'path-exclude /usr/share/doc/*' > /etc/dpkg/dpkg.cfg.d/01_nodoc && \
    echo 'path-exclude /usr/share/man/*' >> /etc/dpkg/dpkg.cfg.d/01_nodoc && \
    echo 'path-exclude /usr/share/groff/*' >> /etc/dpkg/dpkg.cfg.d/01_nodoc && \
    echo 'path-exclude /usr/share/info/*' >> /etc/dpkg/dpkg.cfg.d/01_nodoc && \
    echo 'path-exclude /usr/share/lintian/*' >> /etc/dpkg/dpkg.cfg.d/01_nodoc && \
    echo 'path-exclude /usr/share/linda/*' >> /etc/dpkg/dpkg.cfg.d/01_nodoc

# Install runtime dependencies including nginx (no gcc/libpq-dev/git — build deps stay in builder)
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    nginx

# Install supercronic for cron jobs (container-friendly cron)
ARG TARGETARCH
RUN SUPERCRONIC_ARCH=$(case "${TARGETARCH}" in "arm64") echo "linux-arm64" ;; *) echo "linux-amd64" ;; esac) && \
    curl -fsSL "https://github.com/aptible/supercronic/releases/download/v0.2.41/supercronic-${SUPERCRONIC_ARCH}" \
    -o /usr/local/bin/supercronic && \
    chmod +x /usr/local/bin/supercronic

WORKDIR /app

# Cache bust for COPY layer - change this value to force rebuild
ARG CACHE_BUST=2026-02-27-GAM-API-BUMP
RUN echo "Cache bust: $CACHE_BUST"

# Copy application code
COPY . .

# Copy pre-built virtual environment from builder stage (contains all compiled deps)
COPY --from=builder /opt/venv /opt/venv

# Copy nginx configs - run_all_services.py selects based on ADCP_MULTI_TENANT
# Default: single-tenant (path-based routing, localhost upstreams)
# ADCP_MULTI_TENANT=true: multi-tenant (subdomain routing)
# Development config included for docker-compose.yml multi-container setup
COPY config/nginx/nginx-single-tenant.conf /etc/nginx/nginx-single-tenant.conf
COPY config/nginx/nginx-multi-tenant.conf /etc/nginx/nginx-multi-tenant.conf
COPY config/nginx/nginx-development.conf /etc/nginx/nginx-development.conf

# Non-root runtime user (D34 — issue #1234 PR 5)
RUN groupadd -r -g 1001 app && useradd -r -u 1001 -g app -s /usr/sbin/nologin app && \
    mkdir -p /var/log/nginx /var/run && \
    chown -R app:app /app /opt/venv /var/log/nginx /var/run

# Venv on PATH; PYTHONPATH points at bind-mounted source in dev compose
ENV PATH="/opt/venv/bin:$PATH"
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

USER app:app

# Use venv Python directly as entrypoint (prepares for hardened images that lack bash)
ENTRYPOINT ["/opt/venv/bin/python", "scripts/deploy/run_all_services.py"]
