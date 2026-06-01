#!/bin/bash
# Lightweight per-agent PostgreSQL container for worktree isolation.
#
# Unlike test-stack.sh (which starts the full docker-compose stack with
# MCP server, nginx, etc.), this script starts ONLY a bare Postgres container.
# Each agent gets its own container on a unique port — no mutex needed.
#
# Usage:
#   eval $(./scripts/agent-db.sh up)     # Start + export DATABASE_URL
#   ./scripts/agent-db.sh down           # Stop and remove container
#   ./scripts/agent-db.sh status         # Check if container is running
#
# The integration_db fixture creates per-test databases automatically,
# so all we need is a running Postgres instance.

set -eo pipefail

SCRIPT_DIR="$( dirname "${BASH_SOURCE[0]}" )"
PROJECT_DIR="$( cd "$SCRIPT_DIR/.." && pwd )"

PG_USER="adcp_user"
PG_PASS="secure_password_change_me"
PG_DB="adcp_test"
PG_IMAGE="postgres:17-alpine"

# Container name: deterministic per worktree (so up/down/status work across calls)
WORKTREE_ID=$(basename "$PROJECT_DIR")
CONTAINER_NAME="agent-pg-${WORKTREE_ID}"

# State file (in the worktree, not shared)
STATE_FILE="${PROJECT_DIR}/.agent-db.env"

# Port allocation hardened for parallel worktree execution
# (salesagent-18h.12). Mirrors tests/e2e/conftest.py:find_free_port /
# port_scan_start and scripts/test-stack.sh:find_ports EXACTLY -- keep all
# implementations in sync:
#   * scan origin scattered by PID so parallel agents diverge instead of
#     converging on the same lowest free port,
#   * probe binds the all-interfaces address (the same way Docker
#     publishes -p host:container) so a port already taken by another
#     stack on 0.0.0.0 is detected,
#   * wrap-around scan so the full range is still searched.
find_port() {
    python3 -c "
import os, socket
lo, hi = 50000, 60000
span = hi - lo
origin = lo + (os.getpid() % span)
for i in range(span):
    p = lo + ((origin - lo + i) % span)
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
    try:
        s.bind(('', p))
        print(p)
        break
    except OSError:
        pass
    finally:
        s.close()
"
}

# Reap stopped per-agent Postgres containers left by removed/abandoned
# worktrees (e.g. agent-pg-<id> in 'Exited' state). They hold no port
# while stopped but accumulate and can mask a name clash; a running one
# from a live worktree is never touched (different name, still up).
reap_stopped_agent_dbs() {
    docker ps -a --filter "name=agent-pg-" --filter "status=exited" \
        --format '{{.Names}}' 2>/dev/null | while read -r name; do
            [ -z "$name" ] && continue
            [ "$name" = "$CONTAINER_NAME" ] && continue
            docker rm -f "$name" >/dev/null 2>&1 || true
        done
}

wait_ready() {
    local port=$1
    local deadline=$(($(date +%s) + 30))
    while [ $(date +%s) -lt $deadline ]; do
        if docker exec "$CONTAINER_NAME" pg_isready -U "$PG_USER" >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    return 1
}

cmd_up() {
    # If already running, just re-export
    if [ -f "$STATE_FILE" ]; then
        source "$STATE_FILE"
        if docker ps -q -f "name=^${CONTAINER_NAME}$" | grep -q .; then
            cat "$STATE_FILE"
            return 0
        fi
        # Stale state file — clean up
        rm -f "$STATE_FILE"
    fi

    # Remove any leftover container, then reap exited siblings.
    docker rm -f "$CONTAINER_NAME" 2>/dev/null || true
    reap_stopped_agent_dbs

    # Bounded retry on port-collision: a sibling worktree can grab a probed
    # port in the TOCTOU window before `docker run` publishes it. PID
    # scatter keeps the next attempt diverging (salesagent-18h.12).
    local port started=false attempt
    for attempt in 1 2 3 4 5; do
        port=$(find_port)
        if [ -z "$port" ]; then
            echo "ERROR: Could not find free port in 50000-60000 range" >&2
            exit 1
        fi
        if docker run -d \
            --name "$CONTAINER_NAME" \
            -p "127.0.0.1:${port}:5432" \
            -e POSTGRES_USER="$PG_USER" \
            -e POSTGRES_PASSWORD="$PG_PASS" \
            -e POSTGRES_DB="$PG_DB" \
            "$PG_IMAGE" \
            >/dev/null 2>/tmp/agent-db-run.$$; then
            started=true
            break
        fi
        if grep -qiE "port is already allocated|address already in use|bind.*failed" /tmp/agent-db-run.$$ 2>/dev/null; then
            echo "# Port ${port} collided (attempt ${attempt}) — retrying" >&2
            docker rm -f "$CONTAINER_NAME" 2>/dev/null || true
            rm -f /tmp/agent-db-run.$$
            continue
        fi
        echo "ERROR: docker run failed (non-port error):" >&2
        cat /tmp/agent-db-run.$$ >&2
        rm -f /tmp/agent-db-run.$$
        docker rm -f "$CONTAINER_NAME" 2>/dev/null || true
        exit 1
    done
    rm -f /tmp/agent-db-run.$$
    if [ "$started" != true ]; then
        echo "ERROR: Could not start Postgres after 5 port attempts" >&2
        exit 1
    fi

    if ! wait_ready "$port"; then
        echo "ERROR: Postgres failed to start within 30s" >&2
        docker logs "$CONTAINER_NAME" >&2
        docker rm -f "$CONTAINER_NAME" 2>/dev/null || true
        exit 1
    fi

    local db_url="postgresql://${PG_USER}:${PG_PASS}@localhost:${port}/${PG_DB}"

    # Write state file
    cat > "$STATE_FILE" <<EOF
export DATABASE_URL="${db_url}"
export AGENT_PG_CONTAINER="${CONTAINER_NAME}"
export AGENT_PG_PORT=${port}
export ADCP_TESTING=true
export ENCRYPTION_KEY="${ENCRYPTION_KEY:-PEg0SNGQyvzi4Nft-ForSzK8AGXyhRtql1MgoUsfUHk=}"  # TEST ONLY — never use in production
export GEMINI_API_KEY="${GEMINI_API_KEY:-test_key}"
EOF

    # Print for eval
    cat "$STATE_FILE"

    echo "# Agent DB ready: ${CONTAINER_NAME} on port ${port}" >&2
}

cmd_down() {
    docker rm -f "$CONTAINER_NAME" 2>/dev/null || true
    rm -f "$STATE_FILE"
    echo "# Agent DB stopped: ${CONTAINER_NAME}" >&2
}

cmd_status() {
    if docker ps -q -f "name=^${CONTAINER_NAME}$" | grep -q .; then
        local port=$(docker port "$CONTAINER_NAME" 5432 2>/dev/null | cut -d: -f2)
        echo "Running: ${CONTAINER_NAME} on port ${port}" >&2
        if [ -f "$STATE_FILE" ]; then
            cat "$STATE_FILE"
        fi
    else
        echo "Not running: ${CONTAINER_NAME}" >&2
    fi
}

case "${1:-}" in
    up)     cmd_up ;;
    down)   cmd_down ;;
    status) cmd_status ;;
    *)
        echo "Usage: $0 {up|down|status}" >&2
        echo "" >&2
        echo "Start a per-agent Postgres container for integration tests." >&2
        echo "  eval \$($0 up)     # Start and export DATABASE_URL" >&2
        echo "  $0 down            # Stop container" >&2
        exit 1
        ;;
esac
