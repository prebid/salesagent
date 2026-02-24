#!/bin/bash
# Test runner — both modes produce JSON reports in test-results/<ddmmyy_HHmm>/
#
# Usage:
#   ./run_all_tests.sh           # Docker + all 5 suites (default)
#   ./run_all_tests.sh quick     # No Docker: unit + integration
#   ./run_all_tests.sh ci tests/integration/test_file.py -k test_name

set -eo pipefail

cd "$( dirname "${BASH_SOURCE[0]}" )"
[ -f .env ] && { set -a; source .env; set +a; }

GREEN='\033[0;32m' RED='\033[0;31m' BLUE='\033[0;34m' YELLOW='\033[1;33m' NC='\033[0m'

MODE=${1:-ci}
PYTEST_TARGET="${2:-}"
PYTEST_ARGS="${@:3}"
RESULTS_DIR="test-results/$(date +%d%m%y_%H%M)"
FAILURES=""
mkdir -p "$RESULTS_DIR"

echo "Mode: $MODE | Reports: $RESULTS_DIR/"

# --- Helpers ---

find_ports() {
    uv run python -c "
import socket
for p in range(50000, 60000):
    try:
        s1, s2 = socket.socket(), socket.socket()
        s1.bind(('127.0.0.1', p)); s2.bind(('127.0.0.1', p+1))
        s1.close(); s2.close(); print(p, p+1); break
    except OSError: s1.close(); s2.close()
"
}

run_suite() {  # run_suite <label> <name> <dir> [pytest args...]
    local label=$1 name=$2 dir=$3; shift 3
    echo -e "${BLUE}[${label}] ${name}...${NC}"
    if uv run pytest "$dir" "$@" \
        --json-report --json-report-file="$RESULTS_DIR/${name}.json" --json-report-indent=2 \
        -q --tb=line 2>&1 | tee "$RESULTS_DIR/${name}.log"; then
        echo -e "${GREEN}${name} PASSED${NC}"
    else
        echo -e "${RED}${name} FAILED${NC}"
        FAILURES="$FAILURES $name"
    fi
    echo ""
}

validate_imports() {
    echo "Validating imports..."
    if ! uv run python -c "
from src.core.tools import get_products_raw, create_media_buy_raw
from src.core.tools.products import _get_products_impl
from src.core.tools.media_buy_create import _create_media_buy_impl
" 2>/dev/null; then
        echo -e "${RED}Import validation failed!${NC}"; exit 1
    fi
    echo -e "${GREEN}Imports OK${NC}"; echo ""
}

dc() { docker-compose -f docker-compose.e2e.yml -p "$COMPOSE_PROJECT_NAME" "$@"; }

setup_docker() {
    echo -e "${BLUE}Starting Docker stack...${NC}"
    read POSTGRES_PORT MCP_PORT <<< $(find_ports)
    export COMPOSE_PROJECT_NAME="adcp-test-$$"
    dc down -v 2>/dev/null || true

    export POSTGRES_PORT ADCP_SALES_PORT=$MCP_PORT ADCP_TESTING=true CREATE_SAMPLE_DATA=true
    export DATABASE_URL="postgresql://adcp_user:secure_password_change_me@localhost:${POSTGRES_PORT}/adcp_test"
    export DELIVERY_WEBHOOK_INTERVAL=5
    export GEMINI_API_KEY="${GEMINI_API_KEY:-test_key}"
    export ENCRYPTION_KEY="${ENCRYPTION_KEY:-PEg0SNGQyvzi4Nft-ForSzK8AGXyhRtql1MgoUsfUHk=}"

    dc build --progress=plain 2>&1 | grep -E "(Step|#|Building|exporting)" | tail -10
    dc up -d || { dc logs; exit 1; }

    echo "Waiting for services..."
    local deadline=$(($(date +%s) + 120))
    local pg=false srv=false
    while [ $(date +%s) -lt $deadline ]; do
        [ "$pg" = false ] && dc exec -T postgres pg_isready -U adcp_user >/dev/null 2>&1 && pg=true && echo -e "${GREEN}PostgreSQL ready${NC}"
        [ "$srv" = false ] && curl -sf "http://localhost:${MCP_PORT}/health" >/dev/null 2>&1 && srv=true && echo -e "${GREEN}Server ready${NC}"
        [ "$pg" = true ] && [ "$srv" = true ] && break
        sleep 2
    done
    [ "$pg" = false ] || [ "$srv" = false ] && { echo -e "${RED}Timeout waiting for services${NC}"; dc logs; exit 1; }

    dc exec -T postgres psql -U adcp_user -d postgres -c "CREATE DATABASE adcp_test" 2>/dev/null || true
    echo -e "${GREEN}Stack ready (pg:$POSTGRES_PORT srv:$MCP_PORT)${NC}"; echo ""
}

cleanup() { [ "$MODE" = ci ] && { dc down -v 2>/dev/null || true; }; }
trap cleanup EXIT

# --- Quick mode ---
if [ "$MODE" = "quick" ]; then
    validate_imports
    run_suite "1/3" unit       tests/unit/           -m "not requires_db"
    run_suite "2/3" integration tests/integration/   -m "not requires_db and not requires_server and not skip_ci"
    run_suite "3/3" integration_v2 tests/integration_v2/ -m "not requires_db and not requires_server and not skip_ci"

# --- CI mode ---
elif [ "$MODE" = "ci" ]; then
    setup_docker
    _saved_db="$DATABASE_URL"; unset DATABASE_URL
    validate_imports
    export DATABASE_URL="$_saved_db"

    if [ -n "$PYTEST_TARGET" ]; then
        run_suite "1/1" targeted "$PYTEST_TARGET" -m "not requires_server and not skip_ci" $PYTEST_ARGS
    else
        _saved_db="$DATABASE_URL"; unset DATABASE_URL
        run_suite "1/5" unit tests/unit/
        export DATABASE_URL="$_saved_db"
        run_suite "2/5" integration    tests/integration/    -m "not requires_server and not skip_ci"
        run_suite "3/5" integration_v2 tests/integration_v2/ -m "not requires_server and not skip_ci"
        ADCP_SALES_PORT=$MCP_PORT run_suite "4/5" e2e tests/e2e/
        [ -d tests/ui ] && ADCP_SALES_PORT=$MCP_PORT run_suite "5/5" ui tests/ui/ -m "not requires_server and not slow" || echo -e "${YELLOW}[5/5] UI tests skipped${NC}"
    fi
else
    echo "Usage: ./run_all_tests.sh [quick|ci]"
    echo "  ci (default) — Docker + all 5 suites"
    echo "  quick        — no Docker, unit + integration"
    exit 1
fi

# --- Summary ---
echo "================================================================"
echo "Reports: $RESULTS_DIR/"
ls "$RESULTS_DIR"/*.json 2>/dev/null | while read f; do echo "  $(basename $f)"; done
[ -z "$FAILURES" ] && echo -e "${GREEN}ALL PASSED${NC}" && exit 0
echo -e "${RED}FAILED:$FAILURES${NC}" && exit 1
