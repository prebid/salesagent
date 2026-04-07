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

set -eo pipefail

cd "$( dirname "${BASH_SOURCE[0]}" )/.."
[ -f .env ] && { set -a; source .env; set +a; }

GREEN='\033[0;32m' RED='\033[0;31m' BLUE='\033[0;34m' NC='\033[0m'
ENV_FILE=".test-stack.env"

find_ports() {
    uv run python -c "
import socket
for p in range(50000, 60000):
    try:
        socks = [socket.socket() for _ in range(3)]
        for i, s in enumerate(socks): s.bind(('127.0.0.1', p+i))
        for s in socks: s.close()
        print(p, p+1, p+2); break
    except OSError:
        for s in socks: s.close()
"
}

dc() { docker-compose -f docker-compose.e2e.yml -p "${COMPOSE_PROJECT_NAME:-adcp-test-$$}" "$@"; }

cmd_up() {
    echo -e "${BLUE}Starting Docker test stack...${NC}"

    read POSTGRES_PORT MCP_PORT CREATIVE_AGENT_PORT <<< $(find_ports)
    export COMPOSE_PROJECT_NAME="adcp-test-$$"
    dc down -v 2>/dev/null || true

    export POSTGRES_PORT ADCP_SALES_PORT=$MCP_PORT CREATIVE_AGENT_PORT ADCP_TESTING=true CREATE_SAMPLE_DATA=true
    export DATABASE_URL="postgresql://adcp_user:secure_password_change_me@localhost:${POSTGRES_PORT}/adcp_test"
    export DELIVERY_WEBHOOK_INTERVAL=5
    export GEMINI_API_KEY="${GEMINI_API_KEY:-test_key}"
    export ENCRYPTION_KEY="${ENCRYPTION_KEY:-PEg0SNGQyvzi4Nft-ForSzK8AGXyhRtql1MgoUsfUHk=}"  # TEST ONLY — never use in production

    dc build --progress=plain 2>&1 | grep -E "(Step|#|Building|exporting)" | tail -10
    dc up -d || { dc logs; exit 1; }

    echo "Waiting for services..."
    local deadline=$(($(date +%s) + 120))
    local pg=false srv=false mock=false
    while [ $(date +%s) -lt $deadline ]; do
        [ "$pg" = false ] && dc exec -T postgres pg_isready -U adcp_user >/dev/null 2>&1 && pg=true && echo -e "${GREEN}PostgreSQL ready${NC}"
        [ "$srv" = false ] && curl -sf "http://localhost:${MCP_PORT}/health" >/dev/null 2>&1 && srv=true && echo -e "${GREEN}Server ready${NC}"
        [ "$mock" = false ] && curl -sf "http://localhost:${CREATIVE_AGENT_PORT}/api/creative-agent/health" >/dev/null 2>&1 && mock=true && echo -e "${GREEN}Creative agent ready${NC}"
        [ "$pg" = true ] && [ "$srv" = true ] && [ "$mock" = true ] && break
        sleep 2
    done
    [ "$pg" = false ] || [ "$srv" = false ] || [ "$mock" = false ] && { echo -e "${RED}Timeout waiting for services${NC}"; dc logs; exit 1; }

    dc exec -T postgres psql -U adcp_user -d postgres -c "CREATE DATABASE adcp_test" 2>/dev/null || true

    # Write env file for tox to source
    cat > "$ENV_FILE" <<EOF
export COMPOSE_PROJECT_NAME="$COMPOSE_PROJECT_NAME"
export POSTGRES_PORT=$POSTGRES_PORT
export ADCP_SALES_PORT=$MCP_PORT
export CREATIVE_AGENT_PORT=$CREATIVE_AGENT_PORT
export DATABASE_URL="$DATABASE_URL"
export ADCP_TESTING=true
export CREATE_SAMPLE_DATA=true
export DELIVERY_WEBHOOK_INTERVAL=5
export GEMINI_API_KEY="${GEMINI_API_KEY}"
export ENCRYPTION_KEY="${ENCRYPTION_KEY}"
export BDD_E2E_ENABLED=true
export E2E_BASE_URL="http://localhost:${MCP_PORT}"
export E2E_POSTGRES_URL="postgresql://adcp_user:secure_password_change_me@localhost:${POSTGRES_PORT}/adcp"
export CREATIVE_AGENT_URL="http://localhost:${CREATIVE_AGENT_PORT}/api/creative-agent"
EOF

    echo -e "${GREEN}Stack ready (pg:$POSTGRES_PORT srv:$MCP_PORT creative:$CREATIVE_AGENT_PORT)${NC}"
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
        echo "  CREATIVE_AGENT_PORT=${CREATIVE_AGENT_PORT:-not set}"
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
