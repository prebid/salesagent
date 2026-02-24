#!/bin/bash
# Full test suite with JSON report output for each suite.
# Outputs to test-results/<suite>.json
set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# Source .env file if it exists (machine-specific settings like GAM_SERVICE_ACCOUNT_KEY_FILE)
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

RESULTS_DIR="test-results"
rm -rf "$RESULTS_DIR"
mkdir -p "$RESULTS_DIR"

# Track failures
FAILURES=""

# Find available ports
echo "Finding available ports..."
read POSTGRES_PORT MCP_PORT <<< $(uv run python -c "
import socket
def find_free_port_block(count=2, start=50000, end=60000):
    for base_port in range(start, end - count):
        sockets = []
        try:
            for i in range(count):
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.bind(('127.0.0.1', base_port + i))
                sockets.append(s)
            ports = [base_port + i for i in range(count)]
            for s in sockets:
                s.close()
            return ports
        except OSError:
            for s in sockets:
                s.close()
            continue
    raise RuntimeError('Could not find available port block')
ports = find_free_port_block()
print(' '.join(map(str, ports)))
")
echo -e "${GREEN}Ports: PostgreSQL=$POSTGRES_PORT, Server=$MCP_PORT${NC}"

# Docker setup
TEST_PROJECT_NAME="adcp-json-test-$$"
export COMPOSE_PROJECT_NAME="$TEST_PROJECT_NAME"

cleanup() {
    echo -e "${BLUE}Tearing down Docker stack...${NC}"
    docker-compose -f docker-compose.e2e.yml -p "$TEST_PROJECT_NAME" down -v 2>/dev/null || true
}
trap cleanup EXIT

echo -e "${BLUE}Starting Docker stack...${NC}"
docker-compose -f docker-compose.e2e.yml -p "$TEST_PROJECT_NAME" down -v 2>/dev/null || true

export POSTGRES_PORT
export ADCP_SALES_PORT=$MCP_PORT
export DATABASE_URL="postgresql://adcp_user:secure_password_change_me@localhost:${POSTGRES_PORT}/adcp_test"
export ADCP_TESTING=true
export CREATE_SAMPLE_DATA=true
export DELIVERY_WEBHOOK_INTERVAL=5
export GEMINI_API_KEY="${GEMINI_API_KEY:-test_key}"
export ENCRYPTION_KEY="${ENCRYPTION_KEY:-PEg0SNGQyvzi4Nft-ForSzK8AGXyhRtql1MgoUsfUHk=}"

echo "Building Docker images..."
if ! docker-compose -f docker-compose.e2e.yml -p "$TEST_PROJECT_NAME" build --progress=plain 2>&1 | grep -E "(Step|#|Building|exporting)" | tail -20; then
    echo -e "${RED}Docker build failed${NC}"
    exit 1
fi

echo "Starting Docker services..."
if ! docker-compose -f docker-compose.e2e.yml -p "$TEST_PROJECT_NAME" up -d; then
    echo -e "${RED}Docker services failed to start${NC}"
    docker-compose -f docker-compose.e2e.yml -p "$TEST_PROJECT_NAME" logs
    exit 1
fi

# Wait for services
echo "Waiting for services..."
max_wait=120
start_time=$(date +%s)
pg_ready=false
server_ready=false

while true; do
    elapsed=$(($(date +%s) - start_time))
    if [ $elapsed -gt $max_wait ]; then
        echo -e "${RED}Services failed to start within ${max_wait}s${NC}"
        docker-compose -f docker-compose.e2e.yml -p "$TEST_PROJECT_NAME" logs
        exit 1
    fi
    if [ "$pg_ready" = false ]; then
        if docker-compose -f docker-compose.e2e.yml -p "$TEST_PROJECT_NAME" exec -T postgres pg_isready -U adcp_user >/dev/null 2>&1; then
            echo -e "${GREEN}PostgreSQL ready (${elapsed}s)${NC}"
            pg_ready=true
        fi
    fi
    if [ "$server_ready" = false ]; then
        if curl -sf "http://localhost:${MCP_PORT}/health" >/dev/null 2>&1; then
            echo -e "${GREEN}Server ready (${elapsed}s)${NC}"
            server_ready=true
        fi
    fi
    if [ "$pg_ready" = true ] && [ "$server_ready" = true ]; then
        break
    fi
    sleep 2
done

# Create test database
echo "Creating test database..."
docker-compose -f docker-compose.e2e.yml -p "$TEST_PROJECT_NAME" exec -T postgres psql -U adcp_user -d postgres -c "CREATE DATABASE adcp_test" 2>/dev/null || echo "Database already exists"
export DATABASE_URL="postgresql://adcp_user:secure_password_change_me@localhost:${POSTGRES_PORT}/adcp_test"

echo ""
echo "================================================================"
echo "  Running all test suites with JSON reporting"
echo "================================================================"
echo ""

# --- Suite 1: Unit tests ---
echo -e "${BLUE}[1/5] Unit tests...${NC}"
if env -u DATABASE_URL ADCP_TESTING=true uv run pytest tests/unit/ \
    --json-report --json-report-file="$RESULTS_DIR/unit.json" --json-report-indent=2 \
    -q --tb=line 2>&1 | tee "$RESULTS_DIR/unit.log"; then
    echo -e "${GREEN}Unit tests PASSED${NC}"
else
    echo -e "${RED}Unit tests FAILED${NC}"
    FAILURES="$FAILURES unit"
fi
echo ""

# --- Suite 2: Integration tests ---
echo -e "${BLUE}[2/5] Integration tests...${NC}"
if DATABASE_URL="$DATABASE_URL" ADCP_TESTING=true uv run pytest tests/integration/ \
    -m "not requires_server and not skip_ci" \
    --json-report --json-report-file="$RESULTS_DIR/integration.json" --json-report-indent=2 \
    -q --tb=line 2>&1 | tee "$RESULTS_DIR/integration.log"; then
    echo -e "${GREEN}Integration tests PASSED${NC}"
else
    echo -e "${RED}Integration tests FAILED${NC}"
    FAILURES="$FAILURES integration"
fi
echo ""

# --- Suite 3: Integration V2 tests ---
echo -e "${BLUE}[3/5] Integration V2 tests...${NC}"
if DATABASE_URL="$DATABASE_URL" ADCP_TESTING=true uv run pytest tests/integration_v2/ \
    -m "not requires_server and not skip_ci" \
    --json-report --json-report-file="$RESULTS_DIR/integration_v2.json" --json-report-indent=2 \
    -q --tb=line 2>&1 | tee "$RESULTS_DIR/integration_v2.log"; then
    echo -e "${GREEN}Integration V2 tests PASSED${NC}"
else
    echo -e "${RED}Integration V2 tests FAILED${NC}"
    FAILURES="$FAILURES integration_v2"
fi
echo ""

# --- Suite 4: E2E tests ---
echo -e "${BLUE}[4/5] E2E tests...${NC}"
if ADCP_SALES_PORT=$MCP_PORT \
    POSTGRES_PORT=$POSTGRES_PORT ADCP_TESTING=true GEMINI_API_KEY="${GEMINI_API_KEY:-test_key}" \
    uv run pytest tests/e2e/ \
    --json-report --json-report-file="$RESULTS_DIR/e2e.json" --json-report-indent=2 \
    -q --tb=line 2>&1 | tee "$RESULTS_DIR/e2e.log"; then
    echo -e "${GREEN}E2E tests PASSED${NC}"
else
    echo -e "${RED}E2E tests FAILED${NC}"
    FAILURES="$FAILURES e2e"
fi
echo ""

# --- Suite 5: UI tests (if they exist and are runnable) ---
if [ -d "tests/ui" ] && [ "$(find tests/ui -name 'test_*.py' | head -1)" ]; then
    echo -e "${BLUE}[5/5] UI tests...${NC}"
    if ADCP_SALES_PORT=$MCP_PORT ADCP_TESTING=true \
        uv run pytest tests/ui/ \
        --json-report --json-report-file="$RESULTS_DIR/ui.json" --json-report-indent=2 \
        -q --tb=line 2>&1 | tee "$RESULTS_DIR/ui.log"; then
        echo -e "${GREEN}UI tests PASSED${NC}"
    else
        echo -e "${RED}UI tests FAILED${NC}"
        FAILURES="$FAILURES ui"
    fi
    echo ""
else
    echo -e "${YELLOW}[5/5] UI tests skipped (no test files found)${NC}"
    echo ""
fi

# Summary
echo "================================================================"
echo "  Test Results Summary"
echo "================================================================"
echo ""
echo "JSON reports saved to: $RESULTS_DIR/"
ls -la "$RESULTS_DIR"/*.json 2>/dev/null || echo "No JSON files generated"
echo ""

if [ -z "$FAILURES" ]; then
    echo -e "${GREEN}ALL SUITES PASSED${NC}"
    exit 0
else
    echo -e "${RED}FAILURES in:$FAILURES${NC}"
    exit 1
fi
