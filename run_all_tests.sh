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
MODE=${1:-full}  # Default to full if no argument
USE_DOCKER=${USE_DOCKER:-false}  # Set USE_DOCKER=true to run with PostgreSQL

echo "🧪 Running tests in '$MODE' mode..."
if [ "$USE_DOCKER" = "true" ]; then
    echo -e "${BLUE}🐳 Docker mode enabled - using PostgreSQL container${NC}"
fi
echo ""

# Docker setup function (like CI does)
setup_postgres_container() {
    CONTAINER_NAME="adcp-test-postgres-$$"

    echo -e "${BLUE}🐳 Starting PostgreSQL container...${NC}"

    # Check if container already exists
    if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
        echo "Removing existing container..."
        docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
    fi

    # Start PostgreSQL container (exactly like CI)
    docker run -d \
        --name "$CONTAINER_NAME" \
        -e POSTGRES_USER=adcp_user \
        -e POSTGRES_PASSWORD=test_password \
        -e POSTGRES_DB=adcp_test \
        -p 5433:5432 \
        --health-cmd="pg_isready -U adcp_user" \
        --health-interval=10s \
        --health-timeout=5s \
        --health-retries=5 \
        postgres:15 >/dev/null

    # Wait for PostgreSQL to be ready (like CI does)
    echo "Waiting for PostgreSQL to be ready..."
    for i in {1..30}; do
        if docker exec "$CONTAINER_NAME" pg_isready -U adcp_user >/dev/null 2>&1; then
            echo -e "${GREEN}✓ PostgreSQL is ready${NC}"
            break
        fi
        if [ $i -eq 30 ]; then
            echo -e "${RED}❌ PostgreSQL failed to start${NC}"
            docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
            exit 1
        fi
        sleep 1
    done

    # Export database URL
    export DATABASE_URL="postgresql://adcp_user:test_password@localhost:5433/adcp_test"
    export ADCP_TESTING=true

    # Run migrations (like CI does)
    echo "Running database migrations..."
    if ! uv run python scripts/ops/migrate.py 2>/dev/null; then
        echo -e "${YELLOW}⚠️  Migration warning (continuing)${NC}"
    fi

    echo "$CONTAINER_NAME"
}

# Docker teardown function
teardown_postgres_container() {
    local container_name=$1
    if [ ! -z "$container_name" ]; then
        echo -e "${BLUE}🐳 Stopping PostgreSQL container...${NC}"
        docker rm -f "$container_name" >/dev/null 2>&1 || true
    fi
}

# Trap to ensure cleanup on exit
cleanup() {
    if [ ! -z "$POSTGRES_CONTAINER" ]; then
        teardown_postgres_container "$POSTGRES_CONTAINER"
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

    if ! uv run python -c "from src.core.main import _get_products_impl, _create_media_buy_impl" 2>/dev/null; then
        echo -e "${RED}❌ Import validation failed!${NC}"
        echo "One or more shared implementation functions cannot be imported."
        exit 1
    fi

    echo -e "${GREEN}✅ Imports validated${NC}"
    echo ""

    echo "🧪 Step 2/3: Running unit tests..."
    if ! uv run pytest tests/unit/ -x --tb=short -q; then
        echo -e "${RED}❌ Unit tests failed!${NC}"
        exit 1
    fi
    echo -e "${GREEN}✅ Unit tests passed${NC}"
    echo ""

    echo "🔗 Step 3/3: Running integration tests..."
    # Exclude tests that require a real database connection
    if ! uv run pytest tests/integration/ -m "not requires_db" -x --tb=line -q; then
        echo -e "${RED}❌ Integration tests failed!${NC}"
        exit 1
    fi

    echo -e "${GREEN}✅ All quick tests passed${NC}"
    echo ""
    echo -e "${YELLOW}ℹ️  Note: E2E tests and database-dependent tests not run in quick mode${NC}"
    echo "   Run './run_all_tests.sh full' for complete validation"
    exit 0
fi

# CI mode: Like GitHub Actions - with PostgreSQL container
if [ "$MODE" == "ci" ]; then
    # Setup PostgreSQL container
    POSTGRES_CONTAINER=$(setup_postgres_container)

    echo "📦 Step 1/4: Validating imports..."

    # Check all critical imports
    if ! uv run python -c "from src.core.tools import get_products_raw, create_media_buy_raw, get_media_buy_delivery_raw, sync_creatives_raw, list_creatives_raw, list_creative_formats_raw, list_authorized_properties_raw" 2>/dev/null; then
        echo -e "${RED}❌ Import validation failed!${NC}"
        exit 1
    fi

    if ! uv run python -c "from src.core.main import _get_products_impl, _create_media_buy_impl, _get_media_buy_delivery_impl, _sync_creatives_impl, _list_creatives_impl, _list_creative_formats_impl, _list_authorized_properties_impl" 2>/dev/null; then
        echo -e "${RED}❌ Import validation failed!${NC}"
        exit 1
    fi

    echo -e "${GREEN}✅ Imports validated${NC}"
    echo ""

    echo "🧪 Step 2/4: Running unit tests..."
    if ! uv run pytest tests/unit/ -x --tb=short -q; then
        echo -e "${RED}❌ Unit tests failed!${NC}"
        exit 1
    fi
    echo -e "${GREEN}✅ Unit tests passed${NC}"
    echo ""

    echo "🔗 Step 3/4: Running integration tests (WITH database)..."
    # Run ALL integration tests (including requires_db) - exactly like CI
    if ! uv run pytest tests/integration/ -x --tb=short -q -m "not requires_server and not skip_ci"; then
        echo -e "${RED}❌ Integration tests failed!${NC}"
        exit 1
    fi
    echo -e "${GREEN}✅ Integration tests passed${NC}"
    echo ""

    echo "�� Step 4/4: Running e2e tests..."
    if ! uv run pytest tests/e2e/ -x --tb=short -q --skip-docker; then
        echo -e "${RED}❌ E2E tests failed!${NC}"
        exit 1
    fi
    echo -e "${GREEN}✅ E2E tests passed${NC}"
    echo ""

    echo -e "${GREEN}✅ All CI tests passed!${NC}"
    echo ""
    echo -e "${BLUE}ℹ️  CI mode ran with PostgreSQL container (like GitHub Actions)${NC}"
    exit 0
fi

# Full mode: all tests
if [ "$MODE" == "full" ]; then
    echo "📦 Step 1/4: Validating imports..."

    # Check all critical imports
    if ! uv run python -c "from src.core.tools import get_products_raw, create_media_buy_raw, get_media_buy_delivery_raw, sync_creatives_raw, list_creatives_raw, list_creative_formats_raw, list_authorized_properties_raw" 2>/dev/null; then
        echo -e "${RED}❌ Import validation failed!${NC}"
        exit 1
    fi

    if ! uv run python -c "from src.core.main import _get_products_impl, _create_media_buy_impl, _get_media_buy_delivery_impl, _sync_creatives_impl, _list_creatives_impl, _list_creative_formats_impl, _list_authorized_properties_impl" 2>/dev/null; then
        echo -e "${RED}❌ Import validation failed!${NC}"
        exit 1
    fi

    echo -e "${GREEN}✅ Imports validated${NC}"
    echo ""

    echo "🧪 Step 2/4: Running unit tests..."
    if ! uv run pytest tests/unit/ -x --tb=short; then
        echo -e "${RED}❌ Unit tests failed!${NC}"
        exit 1
    fi
    echo -e "${GREEN}✅ Unit tests passed${NC}"
    echo ""

    echo "🔗 Step 3/4: Running integration tests..."
    if ! uv run pytest tests/integration/ -x --tb=short; then
        echo -e "${RED}❌ Integration tests failed!${NC}"
        exit 1
    fi
    echo -e "${GREEN}✅ Integration tests passed${NC}"
    echo ""

    echo "🌍 Step 4/4: Running e2e tests..."
    if ! uv run pytest tests/e2e/ -x --tb=short; then
        echo -e "${RED}❌ E2E tests failed!${NC}"
        exit 1
    fi
    echo -e "${GREEN}✅ E2E tests passed${NC}"
    echo ""

    echo -e "${GREEN}✅ All tests passed!${NC}"
    exit 0
fi

# Unknown mode
echo -e "${RED}❌ Unknown test mode: $MODE${NC}"
echo ""
echo "Usage: ./run_all_tests.sh [quick|ci|full]"
echo ""
echo "Modes:"
echo "  quick  - Unit tests + integration tests (no database)"
echo "           Fast validation for pre-push hook (~1 min)"
echo ""
echo "  ci     - Like GitHub Actions: PostgreSQL container + all tests"
echo "           Runs unit + integration + e2e with real database (~3-5 min)"
echo "           Automatically starts/stops PostgreSQL container"
echo ""
echo "  full   - All tests with SQLite (no container needed)"
echo "           Unit + integration + e2e tests (~3-5 min)"
echo ""
echo "Examples:"
echo "  ./run_all_tests.sh quick      # Fast pre-push validation"
echo "  ./run_all_tests.sh ci         # Test like CI does (with PostgreSQL)"
echo "  ./run_all_tests.sh full       # Full test suite (SQLite)"
exit 1
