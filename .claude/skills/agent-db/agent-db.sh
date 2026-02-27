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

find_port() {
    python3 -c "
import socket
for p in range(50000, 60000):
    try:
        s = socket.socket()
        s.bind(('127.0.0.1', p))
        s.close()
        print(p)
        break
    except OSError:
        s.close()
"
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

    # Remove any leftover container
    docker rm -f "$CONTAINER_NAME" 2>/dev/null || true

    local port=$(find_port)
    if [ -z "$port" ]; then
        echo "ERROR: Could not find free port in 50000-60000 range" >&2
        exit 1
    fi

    # Start bare Postgres — no volumes, no compose, minimal config
    docker run -d \
        --name "$CONTAINER_NAME" \
        -p "127.0.0.1:${port}:5432" \
        -e POSTGRES_USER="$PG_USER" \
        -e POSTGRES_PASSWORD="$PG_PASS" \
        -e POSTGRES_DB="$PG_DB" \
        "$PG_IMAGE" \
        >/dev/null

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
export ENCRYPTION_KEY="${ENCRYPTION_KEY:-PEg0SNGQyvzi4Nft-ForSzK8AGXyhRtql1MgoUsfUHk=}"
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
