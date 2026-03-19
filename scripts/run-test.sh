#!/bin/bash
# Fast targeted test runner for iterative development.
#
# Auto-detects infrastructure needs based on test type:
#   unit          → No infrastructure (just env vars)
#   integration   → Bare Postgres via agent-db.sh (lightweight, persists)
#   bdd           → Bare Postgres via agent-db.sh (same as integration)
#   e2e / admin   → Full Docker stack via test-stack.sh (app + nginx + postgres)
#
# Override auto-detection with explicit flags:
#   --unit    Force unit-test mode (no DB)
#   --db      Force agent-db mode (bare Postgres)
#   --stack   Force full Docker stack mode
#
# Usage:
#   scripts/run-test.sh tests/unit/test_schemas.py -k "test_brand" -x
#   scripts/run-test.sh tests/integration/test_products.py -x -v
#   scripts/run-test.sh tests/bdd/ -q
#   scripts/run-test.sh tests/e2e/test_a2a_endpoints.py -x -v
#   scripts/run-test.sh tests/admin/test_pages.py -x
#   scripts/run-test.sh --db tests/bdd/ -k "uc004" -x   # explicit DB
#
# Teardown:
#   .claude/skills/agent-db/agent-db.sh down   # Stop agent-db
#   scripts/test-stack.sh down                  # Stop Docker stack
#   make test-stack-down                        # Same via Makefile

set -eo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_DIR="$( cd "$SCRIPT_DIR/.." && pwd )"
AGENT_DB="$PROJECT_DIR/.claude/skills/agent-db/agent-db.sh"
TEST_STACK="$PROJECT_DIR/scripts/test-stack.sh"
STACK_ENV="$PROJECT_DIR/.test-stack.env"

usage() {
    echo "Usage: scripts/run-test.sh [--unit|--db|--stack] <test-path> [pytest-args...]" >&2
    echo "" >&2
    echo "Infrastructure modes:" >&2
    echo "  --unit   No infrastructure (unit tests)" >&2
    echo "  --db     Bare Postgres via agent-db (integration, bdd)" >&2
    echo "  --stack  Full Docker stack (e2e, admin)" >&2
    echo "  (auto)   Detected from test path if no flag given" >&2
    echo "" >&2
    echo "Examples:" >&2
    echo "  scripts/run-test.sh tests/unit/test_schemas.py -k test_brand -x" >&2
    echo "  scripts/run-test.sh tests/integration/test_products.py -x -v" >&2
    echo "  scripts/run-test.sh tests/bdd/ -q" >&2
    echo "  scripts/run-test.sh tests/e2e/test_a2a_endpoints.py -x -v" >&2
    echo "  scripts/run-test.sh tests/admin/test_pages.py -x" >&2
    echo "  scripts/run-test.sh --db tests/bdd/ -k uc004 -x" >&2
    echo "" >&2
    echo "Teardown:" >&2
    echo "  .claude/skills/agent-db/agent-db.sh down   # integration/bdd DB" >&2
    echo "  scripts/test-stack.sh down                  # e2e/admin Docker stack" >&2
    exit 1
}

[ $# -eq 0 ] && usage

# ── Parse explicit mode flag ────────────────────────────────────────
infra=""
case "$1" in
    --unit)  infra="unit";  shift ;;
    --db)    infra="db";    shift ;;
    --stack) infra="stack"; shift ;;
esac

[ $# -eq 0 ] && usage

TARGET="$1"

# ── Auto-detect infrastructure from test path (if no explicit flag) ──
if [ -z "$infra" ]; then
    case "$TARGET" in
        *e2e*|*admin*)          infra="stack" ;;
        *integration*|*bdd*)    infra="db"    ;;
        *)                      infra="unit"  ;;
    esac
fi

# ── Set up infrastructure ───────────────────────────────────────────
setup_unit() {
    export ADCP_TESTING=true
    export ENCRYPTION_KEY="${ENCRYPTION_KEY:-PEg0SNGQyvzi4Nft-ForSzK8AGXyhRtql1MgoUsfUHk=}"  # TEST ONLY
    export GEMINI_API_KEY="${GEMINI_API_KEY:-test_key}"
}

setup_db() {
    if [ ! -f "$AGENT_DB" ]; then
        echo "ERROR: agent-db.sh not found at $AGENT_DB" >&2
        exit 1
    fi
    # Idempotent — reuses existing container
    eval "$("$AGENT_DB" up)"
}

setup_stack() {
    if [ ! -f "$TEST_STACK" ]; then
        echo "ERROR: test-stack.sh not found at $TEST_STACK" >&2
        exit 1
    fi

    # Reuse running stack if healthy
    if [ -f "$STACK_ENV" ]; then
        source "$STACK_ENV"
        if [ -n "${ADCP_SALES_PORT:-}" ] && curl -sf "http://localhost:${ADCP_SALES_PORT}/health" >/dev/null 2>&1; then
            echo "Reusing existing Docker test stack (port $ADCP_SALES_PORT)" >&2
            source "$STACK_ENV"
            return
        fi
    fi

    echo "Starting full Docker test stack..." >&2
    "$TEST_STACK" up
    if [ ! -f "$STACK_ENV" ]; then
        echo "ERROR: test-stack.sh did not create $STACK_ENV" >&2
        exit 1
    fi
    source "$STACK_ENV"
}

case "$infra" in
    unit)  setup_unit  ;;
    db)    setup_db    ;;
    stack) setup_stack ;;
esac

# ── Run pytest ──────────────────────────────────────────────────────
cd "$PROJECT_DIR"
uv run pytest "$@"
