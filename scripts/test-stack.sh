#!/bin/bash
# Docker test stack lifecycle for tox-based test execution.
#
# Usage:
#   ./scripts/test-stack.sh up     # Start Docker stack, export ports
#   ./scripts/test-stack.sh down   # Stop and clean up
#   ./scripts/test-stack.sh status # Check if stack is running
#
# After 'up', the script writes port assignments to .test-stack.env
# which tox environments read via pass_env.

set -euo pipefail

cd "$( dirname "${BASH_SOURCE[0]}" )/.."
[ -f .env ] && { set -a; source .env; set +a; }

GREEN='\033[0;32m' RED='\033[0;31m' BLUE='\033[0;34m' NC='\033[0m'
ENV_FILE=".test-stack.env"

# Port allocation hardened for parallel worktree execution
# (salesagent-18h.12). Mirrors tests/e2e/conftest.py:find_free_port /
# port_scan_start EXACTLY -- keep the three implementations in sync:
#   * scan origin scattered by PID so parallel agents diverge instead of
#     converging on the same lowest free port (the root cause of the
#     mass e2e nginx-502 collisions),
#   * probe binds the all-interfaces address (the same way Docker
#     publishes -p host:container) so a port already taken by another
#     stack on 0.0.0.0 is detected,
#   * wrap-around scan so the full range is still searched.
# Emits THREE ports: postgres, the plaintext proxy, and the TLS listener
# (salesagent-tgzb). The TLS port is allocated the same way as the other two
# rather than pinned to 8443, because several stacks run concurrently on one box.
find_ports() {
    uv run python -c "
import os, socket
lo, hi = 50000, 60000
span = hi - lo
origin = lo + (os.getpid() % span)
for i in range(span - 1):
    p = lo + ((origin - lo + i) % span)
    if p + 2 >= hi:
        continue
    socks = [socket.socket() for _ in range(3)]
    for s in socks:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
    try:
        for offset, s in enumerate(socks):
            s.bind(('', p + offset))
        print(p, p + 1, p + 2); break
    except OSError:
        pass
    finally:
        for s in socks:
            s.close()
"
}

# Reap abandoned test stacks before allocating. A run killed with -9
# (OOM / agent timeout) never fires run_all_tests.sh's EXIT trap, leaving
# an adcp-test-<pid> compose stack holding ports in 50000-60000 and
# serving 502 once its upstream dies (salesagent-18h.12). Containers older
# than the threshold belong to no live run on this host, so reap them.
reap_abandoned_stacks() {
    local max_age_min=${STALE_STACK_MAX_AGE_MIN:-90}
    docker ps -a --filter "name=adcp-test-" --format '{{.Names}}' 2>/dev/null \
        | sed -E 's/-(proxy|adcp-server|postgres)-[0-9]+$//' | sort -u \
        | while read -r proj; do
            [ -z "$proj" ] && continue
            # Never touch this run's own project.
            [ "$proj" = "${COMPOSE_PROJECT_NAME:-}" ] && continue
            local cid started age_min
            cid=$(docker ps -aq --filter "name=${proj}-" 2>/dev/null | head -1)
            [ -z "$cid" ] && continue
            started=$(docker inspect -f '{{.State.StartedAt}}' "$cid" 2>/dev/null)
            [ -z "$started" ] && continue
            age_min=$(( ( $(date +%s) - $(date -j -f "%Y-%m-%dT%H:%M:%S" "${started%%.*}" +%s 2>/dev/null \
                || date -d "$started" +%s 2>/dev/null || echo "$(date +%s)") ) / 60 ))
            if [ "$age_min" -ge "$max_age_min" ]; then
                echo -e "${BLUE}Reaping abandoned stack ${proj} (age ${age_min}m)${NC}"
                docker-compose -f docker-compose.e2e.yml -p "$proj" down -v 2>/dev/null \
                    || docker rm -f $(docker ps -aq --filter "name=${proj}-") 2>/dev/null || true
            fi
        done
}

# Host stack: pytest runs on the host and reaches the stack over localhost, so
# overlay the ports file (the base compose publishes no host ports — reserved for
# the in-network runner, run_all_tests.sh).
# v1 `docker-compose` binary when present, else the v2 `docker compose` plugin
# (hosts like the CI box ship only v2; without this fallback dc() exits 127 and
# the `dc build | grep | tail` pipeline dies SILENTLY under pipefail).
dc() {
    if command -v docker-compose >/dev/null 2>&1; then
        docker-compose -f docker-compose.e2e.yml -f docker-compose.e2e.ports.yml -p "${COMPOSE_PROJECT_NAME:-adcp-test-$$}" "$@"
    else
        docker compose -f docker-compose.e2e.yml -f docker-compose.e2e.ports.yml -p "${COMPOSE_PROJECT_NAME:-adcp-test-$$}" "$@"
    fi
}

cmd_up() {
    echo -e "${BLUE}Starting Docker test stack...${NC}"

    export COMPOSE_PROJECT_NAME="adcp-test-$$"
    reap_abandoned_stacks

    export ADCP_TESTING=true CREATE_SAMPLE_DATA=true
    export DELIVERY_WEBHOOK_INTERVAL=5
    export GEMINI_API_KEY="${GEMINI_API_KEY:-test_key}"
    export ENCRYPTION_KEY="${ENCRYPTION_KEY:-PEg0SNGQyvzi4Nft-ForSzK8AGXyhRtql1MgoUsfUHk=}"  # TEST ONLY — never use in production

    # The tls-proxy service bind-mounts .test-tls/; without the material the
    # mount materialises an empty directory and nginx refuses to start. Idempotent
    # — a current certificate is left exactly as it is, so concurrent stacks
    # already serving it are undisturbed.
    scripts/dev/ensure-test-tls.sh

    # Allocate this stack's network slice the same way its ports are allocated
    # (salesagent-mp53.9). The e2e network is pinned to a NON-PRIVATE range so
    # the server reaches its webhook receiver at an address production's SSRF
    # gate accepts unpatched; a fixed value means the second concurrent stack
    # fails with "Pool overlaps with other one on this address space".
    if [ -z "${E2E_NETWORK_SUBNET:-}" ]; then
        eval "export $(scripts/dev/alloc-e2e-subnet.sh)"
        echo "  e2e network slice: $E2E_NETWORK_SUBNET"
    fi

    # Bounded retry on port-collision: a sibling worktree can still grab a
    # probed port in the TOCTOU window before `docker up` publishes it.
    # Re-allocate (PID scatter keeps the next attempt diverging) instead of
    # failing the whole suite -- the half-started stack + nginx 502 is what
    # blocked all e2e tests (salesagent-18h.12).
    local up_ok=false attempt
    for attempt in 1 2 3; do
        read POSTGRES_PORT MCP_PORT TLS_PORT <<< $(find_ports)
        if [ -z "$POSTGRES_PORT" ] || [ -z "$MCP_PORT" ] || [ -z "$TLS_PORT" ]; then
            echo -e "${RED}No free port triple in 50000-60000 (attempt $attempt)${NC}"
            sleep 2; continue
        fi
        export POSTGRES_PORT ADCP_SALES_PORT=$MCP_PORT ADCP_TLS_PORT=$TLS_PORT
        export DATABASE_URL="postgresql://adcp_user:secure_password_change_me@127.0.0.1:${POSTGRES_PORT}/adcp_test"
        dc down -v 2>/dev/null || true
        dc build --progress=plain 2>&1 | grep -E "(Step|#|Building|exporting)" | tail -10
        if dc up -d 2>&1 | tee /tmp/dc-up-$$.log; then
            up_ok=true; rm -f /tmp/dc-up-$$.log; break
        fi
        if grep -qiE "port is already allocated|address already in use|bind.*failed" /tmp/dc-up-$$.log; then
            echo -e "${BLUE}Port collision on attempt $attempt (pg:$POSTGRES_PORT srv:$MCP_PORT) — retrying with fresh ports${NC}"
            dc down -v 2>/dev/null || true
            rm -f /tmp/dc-up-$$.log
            continue
        fi
        echo -e "${RED}docker-compose up failed (non-port error)${NC}"
        rm -f /tmp/dc-up-$$.log; dc logs; exit 1
    done
    [ "$up_ok" = true ] || { echo -e "${RED}Could not bring up stack after 3 attempts${NC}"; dc logs; exit 1; }

    echo "Waiting for services..."
    # Cold boot budget: ~10s container start + ~30s migrations (170 of them) +
    # ~2min FastAPI/Admin/MCP/A2A/scheduler init. 120s was too tight on cold
    # Docker image cache. 360s gives margin without making genuine hangs slow
    # to surface.
    local deadline=$(($(date +%s) + 360))
    local pg=false srv=false tls=false
    while [ $(date +%s) -lt $deadline ]; do
        [ "$pg" = false ] && dc exec -T postgres pg_isready -U adcp_user >/dev/null 2>&1 && pg=true && echo -e "${GREEN}PostgreSQL ready${NC}"
        [ "$srv" = false ] && curl -sf "http://localhost:${MCP_PORT}/health" >/dev/null 2>&1 && srv=true && echo -e "${GREEN}Server ready${NC}"
        # A REAL handshake against the dotted name, verified with the generated
        # CA. Not "is the port open": the whole point of the TLS listener is that
        # https at this host is true rather than advertised, and a half-started
        # listener that is skipped past is the vacuity salesagent-tgzb removes.
        [ "$tls" = false ] && curl -sf --cacert .test-tls/ca.pem "https://agent.localhost:${TLS_PORT}/health" >/dev/null 2>&1 && tls=true && echo -e "${GREEN}TLS listener ready (agent.localhost:${TLS_PORT})${NC}"
        [ "$pg" = true ] && [ "$srv" = true ] && [ "$tls" = true ] && break
        sleep 2
    done
    [ "$pg" = false ] || [ "$srv" = false ] && { echo -e "${RED}Timeout waiting for services${NC}"; dc logs; exit 1; }
    if [ "$tls" = false ]; then
        echo -e "${RED}TLS listener never completed a verified handshake at https://agent.localhost:${TLS_PORT}/health${NC}"
        echo -e "${RED}  (agent.localhost must resolve to loopback — RFC 6761 — and .test-tls/ must hold a current CA + leaf)${NC}"
        dc logs tls-proxy; exit 1
    fi

    dc exec -T postgres psql -U adcp_user -d postgres -c "CREATE DATABASE adcp_test" 2>/dev/null || true

    # Re-derive ports from the ACTUAL Docker bindings, not the pre-allocated
    # values. The allocated POSTGRES_PORT/MCP_PORT can drift from what compose
    # actually bound (collision-retry / reused container), which previously
    # wrote a wrong port to .test-stack.env — every suite then connected to the
    # wrong port and integration cascaded DB connection errors / "invalid
    # response to SSL negotiation". Reading `docker compose port` makes the env
    # file always match reality.
    _real_pg=$(dc port postgres 5432 2>/dev/null | sed -E 's/.*:([0-9]+)$/\1/')
    _real_srv=$(dc port proxy 8000 2>/dev/null | sed -E 's/.*:([0-9]+)$/\1/')
    _real_tls=$(dc port tls-proxy 8443 2>/dev/null | sed -E 's/.*:([0-9]+)$/\1/')
    if [ -n "$_real_pg" ] && [ "$_real_pg" != "$POSTGRES_PORT" ]; then
        echo -e "${BLUE}Corrected POSTGRES_PORT $POSTGRES_PORT -> $_real_pg (actual Docker binding)${NC}"
        POSTGRES_PORT="$_real_pg"
    fi
    [ -n "$_real_srv" ] && MCP_PORT="$_real_srv"
    [ -n "$_real_tls" ] && TLS_PORT="$_real_tls"
    export POSTGRES_PORT ADCP_SALES_PORT=$MCP_PORT ADCP_TLS_PORT=$TLS_PORT
    export DATABASE_URL="postgresql://adcp_user:secure_password_change_me@127.0.0.1:${POSTGRES_PORT}/adcp_test"

    # Write env file for tox to source
    cat > "$ENV_FILE" <<EOF
export COMPOSE_PROJECT_NAME="$COMPOSE_PROJECT_NAME"
export POSTGRES_PORT=$POSTGRES_PORT
export ADCP_SALES_PORT=$MCP_PORT
export ADCP_TLS_PORT=$TLS_PORT
export E2E_TLS_BASE_URL="https://agent.localhost:$TLS_PORT"
export E2E_CA_BUNDLE="$(pwd)/.test-tls/ca.pem"
export DATABASE_URL="$DATABASE_URL"
export ADCP_TESTING=true
export CREATE_SAMPLE_DATA=true
export DELIVERY_WEBHOOK_INTERVAL=5
export GEMINI_API_KEY="${GEMINI_API_KEY}"
export ENCRYPTION_KEY="${ENCRYPTION_KEY}"
EOF

    echo -e "${GREEN}Stack ready (pg:$POSTGRES_PORT srv:$MCP_PORT tls:$TLS_PORT)${NC}"
    echo -e "${BLUE}Env written to $ENV_FILE — source it before running tox${NC}"
    echo ""
    echo "Run tests with:"
    echo "  source $ENV_FILE && tox -p"
}

cmd_down() {
    if [ -f "$ENV_FILE" ]; then
        source "$ENV_FILE"
        rm -f "$ENV_FILE"
    fi
    if [ -n "${COMPOSE_PROJECT_NAME:-}" ]; then
        dc down -v 2>/dev/null || true
        echo -e "${GREEN}Stack stopped${NC}"
    else
        echo "No running test stack found"
    fi
}

cmd_status() {
    if [ -f "$ENV_FILE" ]; then
        source "$ENV_FILE"
        echo "Stack env: $ENV_FILE"
        echo "  POSTGRES_PORT=$POSTGRES_PORT"
        echo "  ADCP_SALES_PORT=$ADCP_SALES_PORT"
        echo "  ADCP_TLS_PORT=${ADCP_TLS_PORT:-<unset>}"
        echo "  COMPOSE_PROJECT_NAME=$COMPOSE_PROJECT_NAME"
        dc ps 2>/dev/null || echo "Stack not running"
    else
        echo "No test stack env file found ($ENV_FILE)"
    fi
}

case "${1:-}" in
    up) cmd_up ;;
    down) cmd_down ;;
    status) cmd_status ;;
    *)
        echo "Usage: $0 {up|down|status}"
        exit 1
        ;;
esac
