#!/bin/bash
# Targeted test runner — handles DB setup and saves results automatically.
#
# Designed for agent use: one command, deterministic output, results persisted.
#
# Usage:
#   scripts/run-test.sh tests/unit/test_creative.py           # Unit tests (no DB)
#   scripts/run-test.sh tests/integration/test_creative_v3.py  # Integration (auto DB)
#   scripts/run-test.sh tests/unit/test_creative.py -k "test_name"  # With pytest args
#   scripts/run-test.sh tests/unit/ tests/integration/         # Multiple paths
#
# Results saved to: test-results/run-<timestamp>/
#   - results.json   (pytest JSON report)
#   - coverage.json  (coverage data if --cov flags passed)
#   - output.log     (full stdout+stderr)
#
# DB lifecycle:
#   - If any test path contains "integration" or "e2e", starts a Postgres container
#   - Container is auto-cleaned on exit
#   - If DATABASE_URL is already set, skips DB setup (uses existing)

set -eo pipefail

cd "$( dirname "${BASH_SOURCE[0]}" )/.."
[ -f .env ] && { set -a; source .env; set +a; }

GREEN='\033[0;32m' RED='\033[0;31m' BLUE='\033[0;34m' NC='\033[0m'

# --- Parse args ---
TEST_PATHS=()
PYTEST_EXTRA=()
parsing_paths=true

for arg in "$@"; do
    if [[ "$parsing_paths" == true && "$arg" != -* && ( -f "$arg" || -d "$arg" ) ]]; then
        TEST_PATHS+=("$arg")
    else
        parsing_paths=false
        PYTEST_EXTRA+=("$arg")
    fi
done

if [ ${#TEST_PATHS[@]} -eq 0 ]; then
    echo "Usage: scripts/run-test.sh <test-file-or-dir> [pytest-args...]"
    exit 1
fi

# --- Results directory ---
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
RESULTS_DIR="test-results/run-${TIMESTAMP}"
mkdir -p "$RESULTS_DIR"

# Keep only the last 20 run-* directories
ls -dt test-results/run-*/ 2>/dev/null | tail -n +21 | xargs rm -rf 2>/dev/null || true

# --- DB setup (if integration/e2e tests detected) ---
NEEDS_DB=false
for p in "${TEST_PATHS[@]}"; do
    if [[ "$p" == *integration* || "$p" == *e2e* ]]; then
        NEEDS_DB=true
        break
    fi
done

DB_CONTAINER=""
cleanup_db() {
    if [ -n "$DB_CONTAINER" ]; then
        echo -e "${BLUE}Stopping test database...${NC}"
        docker rm -f "$DB_CONTAINER" 2>/dev/null || true
    fi
}
trap cleanup_db EXIT

if [[ "$NEEDS_DB" == true && -z "$DATABASE_URL" ]]; then
    echo -e "${BLUE}Starting test database...${NC}"

    # Find available port
    DB_PORT=$(uv run python -c "
import socket
for p in range(50000, 60000):
    try:
        s = socket.socket(); s.bind(('127.0.0.1', p)); s.close(); print(p); break
    except OSError: s.close()
")

    DB_CONTAINER="run-test-pg-$$"
    docker run -d --name "$DB_CONTAINER" \
        -e POSTGRES_USER=adcp_user \
        -e POSTGRES_PASSWORD=secure_password_change_me \
        -e POSTGRES_DB=adcp_test \
        -p "${DB_PORT}:5432" \
        postgres:16-alpine >/dev/null

    export DATABASE_URL="postgresql://adcp_user:secure_password_change_me@localhost:${DB_PORT}/adcp_test"

    # Wait for Postgres
    deadline=$(($(date +%s) + 30))
    while [ $(date +%s) -lt $deadline ]; do
        docker exec "$DB_CONTAINER" pg_isready -U adcp_user >/dev/null 2>&1 && break
        sleep 1
    done

    echo -e "${GREEN}Database ready on port ${DB_PORT}${NC}"

elif [[ "$NEEDS_DB" == true ]]; then
    echo -e "${BLUE}Using existing DATABASE_URL${NC}"
fi

# --- Run tests ---
echo -e "${BLUE}Running: ${TEST_PATHS[*]} ${PYTEST_EXTRA[*]}${NC}"
echo "Results: ${RESULTS_DIR}/"

set +e
uv run pytest "${TEST_PATHS[@]}" \
    --json-report --json-report-file="${RESULTS_DIR}/results.json" \
    -v --tb=short \
    "${PYTEST_EXTRA[@]}" \
    2>&1 | tee "${RESULTS_DIR}/output.log"
EXIT_CODE=${PIPESTATUS[0]}
set -e

# --- Summary ---
if [ -f "${RESULTS_DIR}/results.json" ]; then
    uv run python -c "
import json, sys
r = json.load(open('${RESULTS_DIR}/results.json'))
s = r.get('summary', {})
passed = s.get('passed', 0)
failed = s.get('failed', 0)
error = s.get('error', 0)
skipped = s.get('skipped', 0)
total = s.get('total', passed + failed + error + skipped)
duration = r.get('duration', 0)
print(f'  Total: {total} | Passed: {passed} | Failed: {failed} | Error: {error} | Skipped: {skipped} | Duration: {duration:.1f}s')
if failed > 0 or error > 0:
    print('  Failed tests:')
    for t in r.get('tests', []):
        if t.get('outcome') in ('failed', 'error'):
            print(f'    - {t[\"nodeid\"]}')
" 2>/dev/null || true
fi

echo ""
echo -e "Results saved to: ${BLUE}${RESULTS_DIR}/${NC}"

exit $EXIT_CODE
