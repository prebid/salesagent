#!/bin/bash
# Test runner script for pre-push hook validation
# Implements the testing workflow documented in CLAUDE.md
#
# ⚠️  RECOMMENDED: Run './run_all_tests.sh ci' before pushing
#     This runs tests exactly like GitHub Actions with PostgreSQL container
#     and catches database-specific issues that quick mode misses.

set -e  # Exit on first error

# Get the directory of the script (works even when called from git hooks)
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Determine test mode
MODE=${1:-ci}  # Default to ci if no argument

echo "🧪 Running tests in '$MODE' mode..."
echo ""

# Find available ports dynamically using a helper script that avoids race conditions
echo "🔍 Finding available ports..."

# Use Python to find a block of 4 available ports (reduces race conditions)
read POSTGRES_PORT MCP_PORT A2A_PORT ADMIN_PORT <<< $(uv run python -c "
import socket
import random

def find_free_port_block(count=4, start=50000, end=60000):
    '''Find a block of consecutive free ports'''
    for base_port in range(start, end - count):
        sockets = []
        try:
            # Try to bind all ports in the block
            for i in range(count):
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.bind(('127.0.0.1', base_port + i))
                sockets.append(s)

            # Success! Return the ports
            ports = [base_port + i for i in range(count)]
            for s in sockets:
                s.close()
            return ports
        except OSError:
            # One of the ports was in use, close what we opened and try next block
            for s in sockets:
                s.close()
            continue
    raise RuntimeError('Could not find available port block')

ports = find_free_port_block()
print(' '.join(map(str, ports)))
")

echo -e "${GREEN}✓ Using dynamic ports: PostgreSQL=$POSTGRES_PORT, MCP=$MCP_PORT, A2A=$A2A_PORT, Admin=$ADMIN_PORT${NC}"
echo ""

# Docker compose setup function - starts entire stack once
setup_docker_stack() {
    echo -e "${BLUE}🐳 Starting complete Docker stack (PostgreSQL + servers)...${NC}"

    # Use unique project name to isolate from local dev environment
    # This ensures test containers don't interfere with your running local containers
    local TEST_PROJECT_NAME="adcp-test-$$"  # $$ = process ID, ensures uniqueness
    export COMPOSE_PROJECT_NAME="$TEST_PROJECT_NAME"

    # Clean up ONLY this test project's containers/volumes (not your local dev!)
    echo "Cleaning up any existing TEST containers (project: $TEST_PROJECT_NAME)..."
    docker-compose -p "$TEST_PROJECT_NAME" down -v 2>/dev/null || true
    # DO NOT run docker volume prune - that affects ALL Docker volumes!

    # If ports are still in use, find new ones
    if lsof -i :${POSTGRES_PORT} >/dev/null 2>&1; then
        echo "Port conflict detected, finding new port block..."
        read POSTGRES_PORT MCP_PORT A2A_PORT ADMIN_PORT <<< $(uv run python -c "
import socket

def find_free_port_block(count=4, start=50000, end=60000):
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
        echo "Using new ports: PostgreSQL=${POSTGRES_PORT}, MCP=${MCP_PORT}, A2A=${A2A_PORT}, Admin=${ADMIN_PORT}"
    fi

    # Export environment for docker-compose
    export POSTGRES_PORT
    export ADCP_SALES_PORT=$MCP_PORT
    export A2A_PORT
    export ADMIN_UI_PORT=$ADMIN_PORT
    # DATABASE_URL is used by both app code AND integration tests
    # Integration tests ignore the database name and create unique databases per test
    export DATABASE_URL="postgresql://adcp_user:secure_password_change_me@localhost:${POSTGRES_PORT}/adcp_test"
    export ADCP_TESTING=true
    export CREATE_SAMPLE_DATA=true
    export GEMINI_API_KEY="${GEMINI_API_KEY:-test_key}"

    # Build and start services
    echo "Building Docker images (this may take 2-3 minutes on first run)..."
    if ! docker-compose -p "$TEST_PROJECT_NAME" build --progress=plain 2>&1 | grep -E "(Step|#|Building|exporting)" | tail -20; then
        echo -e "${RED}❌ Docker build failed${NC}"
        exit 1
    fi

    echo "Starting Docker services..."
    if ! docker-compose -p "$TEST_PROJECT_NAME" up -d; then
        echo -e "${RED}❌ Docker services failed to start${NC}"
        docker-compose -p "$TEST_PROJECT_NAME" logs
        exit 1
    fi

    # Wait for services to be ready
    echo "Waiting for services to be ready..."
    local max_wait=120
    local start_time=$(date +%s)

    while true; do
        local elapsed=$(($(date +%s) - start_time))

        if [ $elapsed -gt $max_wait ]; then
            echo -e "${RED}❌ Services failed to start within ${max_wait}s${NC}"
            docker-compose logs
            exit 1
        fi

        # Check PostgreSQL
        if docker-compose -p "$TEST_PROJECT_NAME" exec -T postgres pg_isready -U adcp_user >/dev/null 2>&1; then
            echo -e "${GREEN}✓ PostgreSQL is ready (${elapsed}s)${NC}"
            break
        fi

        sleep 2
    done

    # Run migrations
    echo "Running database migrations..."
    # Use docker-compose exec to run migrations inside the container
    if ! docker-compose -p "$TEST_PROJECT_NAME" exec -T postgres psql -U adcp_user -d postgres -c "CREATE DATABASE adcp_test" 2>/dev/null; then
        echo "Database adcp_test already exists, continuing..."
    fi

    # Export for tests - MUST match docker-compose.yml POSTGRES_PASSWORD
    export DATABASE_URL="postgresql://adcp_user:secure_password_change_me@localhost:${POSTGRES_PORT}/adcp_test"

    echo -e "${GREEN}✓ Docker stack is ready${NC}"
    echo "  PostgreSQL: localhost:${POSTGRES_PORT}"
    echo "  MCP Server: localhost:${MCP_PORT}"
    echo "  A2A Server: localhost:${A2A_PORT}"
    echo "  Admin UI: localhost:${ADMIN_PORT}"
}

# Docker teardown function
teardown_docker_stack() {
    echo -e "${BLUE}🐳 Stopping TEST Docker stack (project: $COMPOSE_PROJECT_NAME)...${NC}"
    docker-compose -p "$COMPOSE_PROJECT_NAME" down -v 2>/dev/null || true

    # Prune dangling volumes created by tests (only removes unused volumes)
    echo "Cleaning up dangling Docker volumes..."
    docker volume prune -f --filter "label!=preserve" 2>/dev/null || true

    echo -e "${GREEN}✓ Test containers and volumes cleaned up (your local dev containers are untouched)${NC}"
}

# Trap to ensure cleanup on exit
cleanup() {
    if [ "$MODE" == "ci" ]; then
        teardown_docker_stack
    fi
}
trap cleanup EXIT

# Quick mode: unit tests + integration tests + import validation
if [ "$MODE" == "quick" ]; then
    echo "📦 Step 1/3: Validating critical imports..."

    # Check if key imports work (catches missing imports early)
    if ! uv run python -c "from src.core.tools import get_products_raw, create_media_buy_raw" 2>/dev/null; then
        echo -e "${RED}❌ Import validation failed!${NC}"
        echo "One or more A2A raw functions cannot be imported."
        exit 1
    fi

    if ! uv run python -c "from src.core.tools.products import _get_products_impl; from src.core.tools.media_buy_create import _create_media_buy_impl" 2>/dev/null; then
        echo -e "${RED}❌ Import validation failed!${NC}"
        echo "One or more shared implementation functions cannot be imported."
        exit 1
    fi

    echo -e "${GREEN}✅ Imports validated${NC}"
    echo ""

    echo "🧪 Step 2/3: Running unit tests..."
    # Exclude tests that require a real database connection
    if ! uv run pytest tests/unit/ -m "not requires_db" -q --tb=line -q; then
        echo -e "${RED}❌ Unit tests failed!${NC}"
        exit 1
    fi
    echo -e "${GREEN}✅ Unit tests passed${NC}"
    echo ""

    echo "🔗 Step 3/4: Running integration tests..."
    # Exclude tests that require a real database connection or running server
    if ! uv run pytest tests/integration/ -m "not requires_db and not requires_server and not skip_ci" -x --tb=line -q; then
        echo -e "${RED}❌ Integration tests failed!${NC}"
        exit 1
    fi
    echo -e "${GREEN}✅ Integration tests passed${NC}"
    echo ""

    echo "🔗 Step 4/4: Running integration_v2 tests..."
    # integration_v2 tests don't need database in quick mode (they're excluded with requires_db marker)
    if ! uv run pytest tests/integration_v2/ -m "not requires_db and not requires_server and not skip_ci" -x --tb=line -q; then
        echo -e "${RED}❌ Integration V2 tests failed!${NC}"
        exit 1
    fi

    echo -e "${GREEN}✅ All quick tests passed${NC}"
    echo ""
    echo -e "${YELLOW}ℹ️  Note: E2E tests, database tests, and server-dependent tests not run in quick mode${NC}"
    echo "   Run './run_all_tests.sh ci' for complete validation"
    exit 0
fi

# CI mode: Like GitHub Actions - with full Docker stack
if [ "$MODE" == "ci" ]; then
    # Setup complete Docker stack once
    setup_docker_stack

    echo "📦 Step 1/4: Validating imports..."

    # Check all critical imports (unset DATABASE_URL to avoid connection attempts)
    if ! env -u DATABASE_URL uv run python -c "from src.core.tools import get_products_raw, create_media_buy_raw, get_media_buy_delivery_raw, sync_creatives_raw, list_creatives_raw, list_creative_formats_raw, list_authorized_properties_raw" 2>/dev/null; then
        echo -e "${RED}❌ Import validation failed!${NC}"
        exit 1
    fi

    # Check implementation functions can be imported from their respective modules
    if ! env -u DATABASE_URL uv run python -c "from src.core.tools.products import _get_products_impl; from src.core.tools.media_buy_create import _create_media_buy_impl; from src.core.tools.media_buy_delivery import _get_media_buy_delivery_impl; from src.core.tools.creatives import _sync_creatives_impl, _list_creatives_impl; from src.core.tools.creative_formats import _list_creative_formats_impl; from src.core.tools.properties import _list_authorized_properties_impl" 2>/dev/null; then
        echo -e "${RED}❌ Import validation failed!${NC}"
        exit 1
    fi

    echo -e "${GREEN}✅ Imports validated${NC}"
    echo ""

    echo "🧪 Step 2/4: Running unit tests..."
    # Unit tests should run without DATABASE_URL to ensure they don't accidentally use real DB
    if ! env -u DATABASE_URL ADCP_TESTING=true uv run pytest tests/unit/ -q --tb=line -q; then
        echo -e "${RED}❌ Unit tests failed!${NC}"
        exit 1
    fi
    echo -e "${GREEN}✅ Unit tests passed${NC}"
    echo ""

    echo "🔗 Step 3/5: Running integration tests (WITH database)..."
    # Run ALL integration tests (including requires_db) - exactly like CI
    # Keep DATABASE_URL set so integration tests can access the PostgreSQL container
    if ! DATABASE_URL="$DATABASE_URL" ADCP_TESTING=true uv run pytest tests/integration/ -q --tb=line -m "not requires_server and not skip_ci" \
          --ignore=tests/integration/test_a2a_error_responses.py \
          --ignore=tests/integration/test_a2a_skill_invocation.py \
          --ignore=tests/integration/test_get_products_format_id_filter.py; then
        echo -e "${RED}❌ Integration tests failed!${NC}"
        exit 1
    fi
    echo -e "${GREEN}✅ Integration tests passed${NC}"
    echo ""

    echo "🔗 Step 4/5: Running integration_v2 tests (WITH database)..."
    # Run integration_v2 tests with PostgreSQL access
    if ! DATABASE_URL="$DATABASE_URL" ADCP_TESTING=true uv run pytest tests/integration_v2/ -q --tb=line -q -m "not requires_server and not skip_ci"; then
        echo -e "${RED}❌ Integration V2 tests failed!${NC}"
        exit 1
    fi
    echo -e "${GREEN}✅ Integration V2 tests passed${NC}"
    echo ""

    echo "�� Step 5/5: Running e2e tests..."
    # E2E tests now use the ALREADY RUNNING Docker stack (no duplicate setup!)
    # Pass flag to tell E2E tests to use existing services
    # conftest.py will start/stop services with --build flag to ensure fresh images
    # Explicitly set standard ports (overrides any workspace-specific CONDUCTOR_* vars)
    if ! ADCP_SALES_PORT=$MCP_PORT A2A_PORT=$A2A_PORT ADMIN_UI_PORT=$ADMIN_PORT POSTGRES_PORT=$POSTGRES_PORT ADCP_TESTING=true GEMINI_API_KEY="${GEMINI_API_KEY:-test_key}" uv run pytest tests/e2e/ -q --tb=line -q; then
        echo -e "${RED}❌ E2E tests failed!${NC}"
        exit 1
    fi
    echo -e "${GREEN}✅ E2E tests passed${NC}"
    echo ""

    echo -e "${GREEN}✅ All CI tests passed!${NC}"
    echo ""
    echo -e "${BLUE}ℹ️  CI mode used single Docker stack for all tests (efficient!)${NC}"
    exit 0
fi

# Unknown mode
echo -e "${RED}❌ Unknown test mode: $MODE${NC}"
echo ""
echo "Usage: ./run_all_tests.sh [quick|ci]"
echo ""
echo "Modes:"
echo "  quick  - Unit tests + integration tests (no database)"
echo "           Fast validation for rapid iteration (~1 min)"
echo "           Skips database-dependent tests"
echo ""
echo "  ci     - Full test suite with PostgreSQL + Docker Compose (DEFAULT)"
echo "           Runs unit + integration + e2e with real database (~5-10 min)"
echo "           Starts PostgreSQL container for integration tests"
echo "           Starts full Docker Compose stack for e2e tests (builds images)"
echo "           EXACTLY matches GitHub Actions CI environment"
echo ""
echo "Examples:"
echo "  ./run_all_tests.sh            # Run CI mode (default, recommended)"
echo "  ./run_all_tests.sh quick      # Fast iteration during development"
echo "  ./run_all_tests.sh ci         # Explicit CI mode (same as default)"
echo ""
echo "💡 Tip: Use 'quick' for rapid development, 'ci' before pushing to catch all bugs"
exit 1
