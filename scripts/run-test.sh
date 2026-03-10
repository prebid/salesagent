#!/bin/bash
# Fast targeted test runner for iterative development.
#
# Auto-detects test type and starts the right infrastructure:
#   - Unit tests:        No infrastructure needed
#   - Integration tests: Bare Postgres via agent-db.sh (lightweight)
#   - E2E / UI tests:    Full Docker stack via test-stack.sh (app + nginx + postgres)
#
# Infrastructure persists after the test for fast iteration.
#
# Usage:
#   scripts/run-test.sh tests/unit/test_schemas.py -k "test_brand" -x
#   scripts/run-test.sh tests/integration/test_products.py::test_brand -x -v
#   scripts/run-test.sh tests/integration/  # run all integration tests
#   scripts/run-test.sh tests/e2e/test_a2a_endpoints_working.py -x -v
#   scripts/run-test.sh tests/ui/test_comprehensive_pages.py -x
#
# Teardown:
#   .claude/skills/agent-db/agent-db.sh down   # Stop agent-db (integration)
#   scripts/test-stack.sh down                  # Stop full Docker stack (e2e/ui)
#   make test-stack-down                        # Same as above via Makefile

set -eo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_DIR="$( cd "$SCRIPT_DIR/.." && pwd )"
AGENT_DB="$PROJECT_DIR/.claude/skills/agent-db/agent-db.sh"
TEST_STACK="$PROJECT_DIR/scripts/test-stack.sh"
STACK_ENV="$PROJECT_DIR/.test-stack.env"

if [ $# -eq 0 ]; then
    echo "Usage: scripts/run-test.sh <test-path> [pytest-args...]" >&2
    echo "" >&2
    echo "Examples:" >&2
    echo "  scripts/run-test.sh tests/unit/test_schemas.py -k test_brand -x" >&2
    echo "  scripts/run-test.sh tests/integration/test_products.py -x -v" >&2
    echo "  scripts/run-test.sh tests/integration/ -x -q" >&2
    echo "  scripts/run-test.sh tests/e2e/test_a2a_endpoints_working.py -x -v" >&2
    echo "  scripts/run-test.sh tests/ui/test_comprehensive_pages.py -x" >&2
    echo "" >&2
    echo "Teardown:" >&2
    echo "  .claude/skills/agent-db/agent-db.sh down   # integration DB" >&2
    echo "  scripts/test-stack.sh down                  # e2e/ui Docker stack" >&2
    exit 1
fi

TARGET="$1"

# Detect infrastructure needs based on test path
infra="none"
case "$TARGET" in
    *e2e*|*ui*)
        infra="docker-stack"
        ;;
    *integration*)
        infra="agent-db"
        ;;
esac

if [ "$infra" = "docker-stack" ]; then
    # E2E/UI tests need the full Docker stack (app + nginx + postgres)
    if [ ! -f "$TEST_STACK" ]; then
        echo "ERROR: test-stack.sh not found at $TEST_STACK" >&2
        exit 1
    fi

    # Check if stack is already running: .test-stack.env exists + health check
    stack_running=false
    if [ -f "$STACK_ENV" ]; then
        source "$STACK_ENV"
        if [ -n "${ADCP_SALES_PORT:-}" ] && curl -sf "http://localhost:${ADCP_SALES_PORT}/health" >/dev/null 2>&1; then
            stack_running=true
        fi
    fi

    if $stack_running; then
        echo "Reusing existing Docker test stack (port $ADCP_SALES_PORT)" >&2
        # Re-source to export all vars into current shell
        source "$STACK_ENV"
    else
        echo "Starting full Docker test stack..." >&2
        "$TEST_STACK" up
        if [ ! -f "$STACK_ENV" ]; then
            echo "ERROR: test-stack.sh did not create $STACK_ENV" >&2
            exit 1
        fi
        source "$STACK_ENV"
    fi

elif [ "$infra" = "agent-db" ]; then
    # Integration tests need bare Postgres via agent-db
    if [ ! -f "$AGENT_DB" ]; then
        echo "ERROR: agent-db.sh not found at $AGENT_DB" >&2
        exit 1
    fi

    # Capture env vars from agent-db (idempotent — reuses existing container)
    eval "$("$AGENT_DB" up)"

else
    # Unit tests — just need test env vars
    export ADCP_TESTING=true
    export ENCRYPTION_KEY="${ENCRYPTION_KEY:-PEg0SNGQyvzi4Nft-ForSzK8AGXyhRtql1MgoUsfUHk=}"  # TEST ONLY — never use in production
    export GEMINI_API_KEY="${GEMINI_API_KEY:-test_key}"
fi

# Run pytest with all provided arguments
cd "$PROJECT_DIR"
uv run pytest "$@"
