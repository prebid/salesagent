#!/usr/bin/env bash
# Pinned reference creative-agent stack — SINGLE SOURCE OF TRUTH for the pin.
#
# salesagent-kczg: the local authoritative run (run_all_tests.sh) must NOT hit
# the live public creative agent (https://creative.adcontextprotocol.org). Its
# catalog drifts (it dropped display_image/html/js/video_standard), turning
# tests/integration/test_creative_agent_live.py red non-deterministically.
# CI isolates these via a containerized adcp monolith pinned to a known-good
# commit; this script mirrors that EXACTLY so CI and local cannot diverge.
# .github/workflows/ci.yml calls this same script (single source of the pin).
#
# Usage:
#   scripts/creative-agent-stack.sh up     # idempotent: build+run if needed, wait healthy
#   scripts/creative-agent-stack.sh down   # stop+rm containers+network (keeps image/tarball cache)
#   scripts/creative-agent-stack.sh url    # print CREATIVE_AGENT_URL
set -euo pipefail

# Pin to a known-good commit — upstream HEAD has broken migrations
# (community_points FK violation). Bump deliberately, never to HEAD.
ADCP_PIN="ca70dd1e2a6c"

IMAGE="adcp-creative-agent"
NET="creative-net"
PG="adcp-postgres"
AGENT="creative-agent"
SRC="/tmp/adcp-server-${ADCP_PIN}"
HEALTH="http://localhost:9999/api/creative-agent/health"
CREATIVE_AGENT_URL="http://localhost:9999/api/creative-agent"

_healthy() { curl -sf -m 3 "$HEALTH" >/dev/null 2>&1; }

# Retry transient network failures (ECONNRESET during npm/tarball fetch in CI).
_retry() {
    local max="${1:-3}"
    shift
    local attempt=1 delay=2
    while [ "$attempt" -le "$max" ]; do
        if "$@"; then
            return 0
        fi
        if [ "$attempt" -eq "$max" ]; then
            break
        fi
        echo "[creative-agent] attempt ${attempt}/${max} failed, retrying in ${delay}s..." >&2
        sleep "$delay"
        delay=$((delay * 2))
        attempt=$((attempt + 1))
    done
    return 1
}

_fetch_tarball() {
    mkdir -p "$SRC"
    curl -sLf "https://github.com/adcontextprotocol/adcp/archive/${ADCP_PIN}.tar.gz" \
        | tar xz -C "$SRC" --strip-components=1
}

_build_image() {
    local cache_scope="adcp-creative-agent-${ADCP_PIN}"
    if [ -n "${ACTIONS_CACHE_URL:-}" ] && [ -n "${ACTIONS_RUNTIME_TOKEN:-}" ]; then
        echo "[creative-agent] buildx build $IMAGE (adcp@${ADCP_PIN}, gha cache scope=${cache_scope})"
        docker buildx build \
            --load \
            -t "$IMAGE" \
            --cache-from "type=gha,scope=${cache_scope}" \
            --cache-to "type=gha,mode=max,scope=${cache_scope}" \
            "$SRC"
    else
        echo "[creative-agent] docker build $IMAGE (adcp@${ADCP_PIN})"
        docker build -t "$IMAGE" "$SRC"
    fi
}

cmd_url() { echo "$CREATIVE_AGENT_URL"; }

cmd_up() {
    if _healthy; then
        echo "[creative-agent] already healthy on :9999 (reuse)"
        return 0
    fi

    # Source tarball pinned to ADCP_PIN (cached by pin in the path)
    if [ ! -f "$SRC/Dockerfile" ]; then
        echo "[creative-agent] fetching adcp@${ADCP_PIN}"
        _retry 3 _fetch_tarball
    fi

    if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
        _retry 3 _build_image
    fi

    docker network inspect "$NET" >/dev/null 2>&1 || docker network create "$NET"

    if ! docker ps --format '{{.Names}}' | grep -qx "$PG"; then
        docker rm -f "$PG" >/dev/null 2>&1 || true
        docker run -d --network "$NET" --name "$PG" \
            -e POSTGRES_DB=adcp_registry -e POSTGRES_USER=adcp -e POSTGRES_PASSWORD=localdev \
            postgres:16 >/dev/null
        sleep 5
    fi

    if ! docker ps --format '{{.Names}}' | grep -qx "$AGENT"; then
        docker rm -f "$AGENT" >/dev/null 2>&1 || true
        docker run -d --network "$NET" --name "$AGENT" -p 9999:8080 \
            -e NODE_ENV=production -e PORT=8080 \
            -e DATABASE_URL=postgresql://adcp:localdev@${PG}:5432/adcp_registry \
            -e RUN_MIGRATIONS=true -e ALLOW_INSECURE_COOKIES=true \
            -e DEV_USER_EMAIL=ci@test.com -e DEV_USER_ID=ci-user \
            -e AGENT_TOKEN_ENCRYPTION_SECRET=local-ci-encryption-key-32chars!! \
            -e WORKOS_API_KEY=sk_test_dummy -e WORKOS_CLIENT_ID=client_dummy \
            "$IMAGE" >/dev/null
    fi

    echo "[creative-agent] waiting for health..."
    for _ in $(seq 1 60); do
        if _healthy; then echo "[creative-agent] healthy on :9999"; return 0; fi
        sleep 2
    done
    echo "[creative-agent] FAILED to become healthy" >&2
    docker logs "$AGENT" 2>&1 | tail -30 >&2
    return 1
}

cmd_down() {
    docker rm -f "$AGENT" >/dev/null 2>&1 || true
    docker rm -f "$PG" >/dev/null 2>&1 || true
    docker network rm "$NET" >/dev/null 2>&1 || true
    echo "[creative-agent] torn down (image + ${SRC} cache kept for fast reuse)"
}

case "${1:-}" in
    up) cmd_up ;;
    down) cmd_down ;;
    url) cmd_url ;;
    *) echo "usage: $0 {up|down|url}" >&2; exit 2 ;;
esac
