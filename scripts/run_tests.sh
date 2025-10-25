#!/bin/bash
# Test runner script for local and CI environments

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "🧪 AdCP Sales Agent Test Runner"
echo "================================"

# Parse arguments
TEST_TYPE=${1:-all}
VERBOSE=${2:-}

# Set environment variables
export PYTEST_CURRENT_TEST=true
export ADCP_TESTING=true
export GEMINI_API_KEY=${GEMINI_API_KEY:-test_key}
export GOOGLE_CLIENT_ID=${GOOGLE_CLIENT_ID:-test_client_id}
export GOOGLE_CLIENT_SECRET=${GOOGLE_CLIENT_SECRET:-test_secret}
export SUPER_ADMIN_EMAILS=${SUPER_ADMIN_EMAILS:-test@example.com}

# Configure database usage (PostgreSQL only)
DEFAULT_POSTGRES_PORT=${POSTGRES_PORT:-5432}
DEFAULT_DATABASE_URL=${DEFAULT_DATABASE_URL:-postgresql://adcp_user:secure_password_change_me@localhost:${DEFAULT_POSTGRES_PORT}/adcp_test}

if [ -z "$DATABASE_URL" ]; then
    export DATABASE_URL="$DEFAULT_DATABASE_URL"
    echo "📊 Using PostgreSQL database: $DATABASE_URL"
elif [[ "$DATABASE_URL" == sqlite* ]]; then
    echo "❌ SQLite is not supported. Set DATABASE_URL to a PostgreSQL connection string."
    exit 1
else
    echo "📊 Using database: $DATABASE_URL"
fi

if [ -z "$DB_TYPE" ]; then
    export DB_TYPE="postgresql"
fi

# Function to run tests
run_tests() {
    local test_path=$1
    local test_name=$2
    local extra_args=$3

    echo -e "\n${YELLOW}Running $test_name...${NC}"

    if [ "$VERBOSE" = "-v" ]; then
        uv run pytest $test_path -v --tb=short $extra_args
    else
        uv run pytest $test_path --tb=line -q $extra_args
    fi

    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✅ $test_name passed${NC}"
        return 0
    else
        echo -e "${RED}❌ $test_name failed${NC}"
        return 1
    fi
}

# Check for skip decorators
check_skip_decorators() {
    echo -e "\n${YELLOW}Checking for skip decorators...${NC}"

    # Exclude lines that are just searching for skip decorators
    if grep -r "@pytest\.mark\.skip" tests/ --include="test_*.py" | grep -v "pytest.skip(" | grep -v "grep.*@pytest\.mark\.skip"; then
        echo -e "${RED}❌ Found skip decorators in tests!${NC}"
        echo "Remove all @pytest.mark.skip decorators"
        exit 1
    else
        echo -e "${GREEN}✅ No skip decorators found${NC}"
    fi
}

# Initialize database if needed
init_database() {
    if [[ "$DATABASE_URL" == postgresql* || "$DATABASE_URL" == postgres* ]]; then
        echo -e "\n${YELLOW}Running database migrations...${NC}"
        python migrate.py
        if [ $? -eq 0 ]; then
            echo -e "${GREEN}✅ Migrations completed${NC}"
        else
            echo -e "${YELLOW}⚠️  Migration failed, continuing with existing schema${NC}"
        fi
    else
        echo -e "${YELLOW}⚠️  Skipping migrations for non-PostgreSQL database URL${NC}"
    fi
}

# Main test execution
main() {
    # Always check for skip decorators first
    check_skip_decorators

    # Initialize database
    init_database

    # Track overall success
    ALL_PASSED=true

    case $TEST_TYPE in
        smoke)
            run_tests "tests/smoke/" "Smoke Tests" "-m smoke" || ALL_PASSED=false
            ;;
        unit)
            run_tests "tests/unit/" "Unit Tests" "" || ALL_PASSED=false
            ;;
        integration)
            run_tests "tests/integration/" "Integration Tests" "-m 'not requires_server and not skip_ci'" || ALL_PASSED=false
            ;;
        e2e)
            # Start servers if needed for E2E tests
            echo -e "${YELLOW}Note: E2E tests require running servers${NC}"
            run_tests "tests/e2e/" "E2E Tests" "" || ALL_PASSED=false
            ;;
        all)
            # Run tests in order of importance
            run_tests "tests/smoke/" "Smoke Tests" "-m smoke" || ALL_PASSED=false
            run_tests "tests/unit/" "Unit Tests" "" || ALL_PASSED=false
            run_tests "tests/integration/" "Integration Tests" "-m 'not requires_server and not skip_ci'" || ALL_PASSED=false
            ;;
        quick)
            # Quick subset for development
            run_tests "tests/smoke/test_smoke_basic.py" "Basic Smoke Tests" "" || ALL_PASSED=false
            run_tests "tests/unit/" "Unit Tests" "-x --tb=line" || ALL_PASSED=false
            ;;
        ci)
            # CI-optimized test run
            export DATABASE_URL=${CI_DATABASE_URL:-postgresql://adcp_user:test_password@localhost:${DEFAULT_POSTGRES_PORT}/adcp_test}
            export DB_TYPE="postgresql"
            init_database
            run_tests "tests/smoke/" "Smoke Tests" "-m smoke --junit-xml=test-results/smoke.xml" || ALL_PASSED=false
            run_tests "tests/unit/" "Unit Tests" "--junit-xml=test-results/unit.xml" || ALL_PASSED=false
            run_tests "tests/integration/" "Integration Tests" "-m 'not requires_server and not skip_ci' --junit-xml=test-results/integration.xml" || ALL_PASSED=false
            ;;
        *)
            echo "Usage: $0 [smoke|unit|integration|e2e|all|quick|ci] [-v]"
            echo ""
            echo "Test types:"
            echo "  smoke       - Run smoke tests only (critical paths)"
            echo "  unit        - Run unit tests only"
            echo "  integration - Run integration tests only"
            echo "  e2e         - Run end-to-end tests (requires servers)"
            echo "  all         - Run all tests (default)"
            echo "  quick       - Quick test subset for development"
            echo "  ci          - CI-optimized test run with XML output"
            echo ""
            echo "Options:"
            echo "  -v          - Verbose output"
            exit 1
            ;;
    esac

    # Final summary
    echo ""
    echo "================================"
    if [ "$ALL_PASSED" = true ]; then
        echo -e "${GREEN}✅ All tests passed!${NC}"
        exit 0
    else
        echo -e "${RED}❌ Some tests failed${NC}"
        exit 1
    fi
}

# Run main function
main
