# syntax=docker/dockerfile:1.4
# Multi-stage build for smaller image
# Cache bust: 2026-02-27

# ── supercronic build stage ───────────────────────────────────────────
# We build from source on Go 1.26.3 rather than pulling the upstream
# release binary because upstream v0.2.45 is still compiled against
# Go 1.26.2, which carries 5 stdlib HIGH CVEs (DNS, HTTP/2, mail, Dial):
#   CVE-2026-3388, CVE-2026-33854, CVE-2026-39820, CVE-2026-39836, CVE-2026-42499
# All are fixed in Go 1.25.10 / 1.26.3. Pinning the toolchain here lets
# us clear the gate without waiting on aptible/supercronic to cut a
# new release.
FROM golang:1.26.3-alpine AS supercronic-builder
RUN apk add --no-cache git
ARG SUPERCRONIC_VERSION=v0.2.45
RUN git clone --depth 1 --branch ${SUPERCRONIC_VERSION} https://github.com/aptible/supercronic.git /src
WORKDIR /src
# Build static binaries for both arches we publish.
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -ldflags='-s -w' -o /out/supercronic-linux-amd64 . && \
    CGO_ENABLED=0 GOOS=linux GOARCH=arm64 go build -ldflags='-s -w' -o /out/supercronic-linux-arm64 .

FROM python:3.13-slim AS builder

# Disable man pages and docs to speed up apt operations
RUN echo 'path-exclude /usr/share/doc/*' > /etc/dpkg/dpkg.cfg.d/01_nodoc && \
    echo 'path-exclude /usr/share/man/*' >> /etc/dpkg/dpkg.cfg.d/01_nodoc && \
    echo 'path-exclude /usr/share/groff/*' >> /etc/dpkg/dpkg.cfg.d/01_nodoc && \
    echo 'path-exclude /usr/share/info/*' >> /etc/dpkg/dpkg.cfg.d/01_nodoc && \
    echo 'path-exclude /usr/share/lintian/*' >> /etc/dpkg/dpkg.cfg.d/01_nodoc && \
    echo 'path-exclude /usr/share/linda/*' >> /etc/dpkg/dpkg.cfg.d/01_nodoc && \
    sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources

# Install build dependencies in one layer
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get -o Acquire::Retries=5 -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30 update --error-on=any && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    git

# Install uv (cacheable). Keep this pinned to CI's UV_VERSION.
ARG UV_VERSION=0.11.15
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-cache-dir "uv==${UV_VERSION}"

# Set up caching for uv
ENV UV_CACHE_DIR=/cache/uv
ENV UV_TOOL_DIR=/cache/uv-tools
ENV UV_PYTHON_PREFERENCE=only-system
ENV UV_LINK_MODE=copy

# Copy project files
WORKDIR /app
COPY pyproject.toml uv.lock ./

# Layer-cache key for the install step. ``make compose-build`` /
# ``make compose-up`` set this to the current ``shasum -a 256 uv.lock``;
# bare ``docker compose build`` callers must pass it explicitly:
#   --build-arg LOCKFILE_HASH=$(shasum -a 256 uv.lock | awk '{print $1}')
# Why: BuildKit's COPY layer hash on ``uv.lock`` can short-circuit even
# when content changed (cache-mount edge case), so the install layer
# silently reuses a stale venv. Threading the lockfile hash as an ARG
# changes the layer cache key whenever lockfile content changes.
# Default value forces an explicit build — no silent regression on CI.
ARG LOCKFILE_HASH=set-this-build-arg

# Install production dependencies with caching and increased timeout
ENV UV_HTTP_TIMEOUT=300
RUN --mount=type=cache,target=/cache/uv \
    --mount=type=cache,target=/root/.cache/pip \
    if [ "${LOCKFILE_HASH}" = "set-this-build-arg" ]; then \
        echo "ERROR: build arg LOCKFILE_HASH not set." >&2; \
        echo "Use 'make compose-build' (or pass --build-arg LOCKFILE_HASH=\$(shasum -a 256 uv.lock | awk '{print \$1}'))" >&2; \
        echo "Skipping the arg silently regresses dependency bumps — see CLAUDE.md." >&2; \
        exit 1; \
    fi && \
    echo "Installing dependencies for lockfile=${LOCKFILE_HASH}" && \
    uv sync --frozen --no-dev

# Runtime stage
FROM python:3.13-slim

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
    echo 'path-exclude /usr/share/linda/*' >> /etc/dpkg/dpkg.cfg.d/01_nodoc && \
    sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources

# Install runtime dependencies (build deps stay in builder)
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get -o Acquire::Retries=5 -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30 update --error-on=any && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    libpq5 \
    nginx

# Copy the per-arch supercronic binary we just built from source on Go
# 1.26.3. See the ``supercronic-builder`` stage header for the CVE list
# that drove this off the upstream release binary.
ARG TARGETARCH
COPY --from=supercronic-builder /out/supercronic-linux-${TARGETARCH} /usr/local/bin/supercronic
RUN chmod +x /usr/local/bin/supercronic

WORKDIR /app

# Cache bust for COPY layer - change this value to force rebuild
ARG CACHE_BUST=2026-02-27-GAM-API-BUMP
RUN echo "Cache bust: $CACHE_BUST"

# Build provenance — surfaced in the admin UI footer so bug reports
# can be traced back to the exact image build. ``unknown`` defaults
# keep bare ``docker build`` working; Makefile / GitHub Actions /
# docker-compose all pass real values.
ARG GIT_SHA=unknown
ARG GIT_BRANCH=unknown
ENV APP_GIT_SHA=$GIT_SHA
ENV APP_GIT_BRANCH=$GIT_BRANCH

# Copy runtime application files explicitly. Avoid broad COPY so local
# credentials, agent config, tests, and generated caches cannot enter images.
COPY alembic.ini pyproject.toml uv.lock crontab ./
COPY alembic/ alembic/
COPY config/ config/
COPY core/ core/
COPY scripts/ scripts/
COPY src/ src/
COPY static/ static/
COPY templates/ templates/

# Copy pre-built virtual environment from builder stage (runtime deps only)
COPY --from=builder /app/.venv /app/.venv

# Copy nginx configs - run_all_services.py selects based on ADCP_MULTI_TENANT
# Default: single-tenant (path-based routing, localhost upstreams)
# ADCP_MULTI_TENANT=true: multi-tenant (subdomain routing)
# Development config included for docker-compose.yml multi-container setup
COPY config/nginx/nginx-single-tenant.conf /etc/nginx/nginx-single-tenant.conf
COPY config/nginx/nginx-multi-tenant.conf /etc/nginx/nginx-multi-tenant.conf
COPY config/nginx/nginx-development.conf /etc/nginx/nginx-development.conf

# Create nginx directories with proper permissions
RUN mkdir -p /var/log/nginx /var/run && \
    chown -R www-data:www-data /var/log/nginx /var/run

# Add .venv to PATH and set PYTHONPATH for module imports
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH="/app"
ENV PYTHONUNBUFFERED=1

# Default port
ENV ADCP_PORT=8000
ENV ADCP_HOST=0.0.0.0

# core/main.py serves MCP, A2A, and the Flask admin from one Starlette
# binary on $ADCP_PORT. The bundled nginx thread in run_all_services.py
# is unused on this fork — kept off via SKIP_NGINX=true.
ENV SKIP_NGINX=false
# Server-owned adapter schedulers replace the bundled supercronic inventory
# sweep in the default container runtime. Operators can still opt back into
# cron by overriding this, but should not run both mechanisms together.
ENV SKIP_CRON=false

# Expose the unified python port directly. Fly.io / upstream proxy
# talks to this port; no in-image reverse proxy.
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=5 \
      CMD ["python", "scripts/healthcheck.py", "8000"]


# Use venv Python directly as entrypoint (prepares for hardened images that lack bash)
ENTRYPOINT ["/app/.venv/bin/python", "scripts/deploy/run_all_services.py"]
