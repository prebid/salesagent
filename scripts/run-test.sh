#!/bin/bash
# Fast targeted test runner for iterative development.
#
# Wraps agent-db.sh for integration tests (auto-starts Postgres) and
# runs unit tests directly. Leaves the DB container running for reuse.
#
# Usage:
#   scripts/run-test.sh tests/integration/test_products.py::test_brand -x -v
#   scripts/run-test.sh tests/unit/test_schemas.py -k "test_brand" -x
#   scripts/run-test.sh tests/integration/  # run all integration tests
#
# NOT a teardown script — the container persists for fast iteration.
# Call `.claude/skills/agent-db/agent-db.sh down` when done.

set -eo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_DIR="$( cd "$SCRIPT_DIR/.." && pwd )"
AGENT_DB="$PROJECT_DIR/.claude/skills/agent-db/agent-db.sh"

if [ $# -eq 0 ]; then
    echo "Usage: scripts/run-test.sh <test-path> [pytest-args...]" >&2
    echo "" >&2
    echo "Examples:" >&2
    echo "  scripts/run-test.sh tests/integration/test_products.py -x -v" >&2
    echo "  scripts/run-test.sh tests/unit/test_schemas.py -k test_brand -x" >&2
    echo "  scripts/run-test.sh tests/integration/ -x -q" >&2
    exit 1
fi

TARGET="$1"

# Detect if this is an integration test
needs_db=false
case "$TARGET" in
    *integration*|*e2e*)
        needs_db=true
        ;;
esac

if $needs_db; then
    # Start agent-db if needed (idempotent — reuses existing container)
    if [ ! -f "$AGENT_DB" ]; then
        echo "ERROR: agent-db.sh not found at $AGENT_DB" >&2
        exit 1
    fi

    # Capture env vars from agent-db
    eval "$("$AGENT_DB" up)"
else
    # Unit tests — just need ADCP_TESTING
    export ADCP_TESTING=true
    export ENCRYPTION_KEY="${ENCRYPTION_KEY:-PEg0SNGQyvzi4Nft-ForSzK8AGXyhRtql1MgoUsfUHk=}"
    export GEMINI_API_KEY="${GEMINI_API_KEY:-test_key}"
fi

# Run pytest with all provided arguments
cd "$PROJECT_DIR"
uv run pytest "$@"
