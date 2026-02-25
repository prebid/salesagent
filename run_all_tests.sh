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

run_suite_bg() {  # run_suite_bg <label> <name> <cmd> [args...]
    # Run a command in background, capture exit code to temp file.
    # Sets BGPID to the background process PID.
    # <cmd> is the command to run (e.g., "uv" or "true"/"false" for testing).
    local label=$1 name=$2 cmd=$3; shift 3
    (
        echo -e "${BLUE}[${label}] ${name}...${NC}"
        local rc=0
        if [ "$cmd" = "true" ] || [ "$cmd" = "false" ]; then
            # Simple commands for testing
            $cmd || rc=1
        else
            # Real pytest execution
            if $cmd "$@" \
                --json-report --json-report-file="$RESULTS_DIR/${name}.json" --json-report-indent=2 \
                -q --tb=line > "$RESULTS_DIR/${name}.log" 2>&1; then
                echo -e "${GREEN}${name} PASSED${NC}"
            else
                rc=1
                echo -e "${RED}${name} FAILED${NC}"
            fi
        fi
        echo "$rc" > "$RESULTS_DIR/.exitcode.${name}"
    ) &
    BGPID=$!
}

collect_results() {  # collect_results <name1> <name2> ...
    # Read exit code files and build FAILURES string.
    # Prints per-suite summary.
    FAILURES=""
    for name in "$@"; do
        local exitfile="$RESULTS_DIR/.exitcode.${name}"
        if [ -f "$exitfile" ]; then
            local rc
            rc=$(cat "$exitfile")
            if [ "$rc" != "0" ]; then
                FAILURES="$FAILURES $name"
                echo -e "${RED}${name} FAILED${NC}"
            else
                echo -e "${GREEN}${name} PASSED${NC}"
            fi
        else
            FAILURES="$FAILURES $name"
            echo -e "${RED}${name} MISSING (no exit code file)${NC}"
        fi
    done
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

cleanup() {
    if [ "$MODE" = "ci" ] && [ -n "${COMPOSE_PROJECT_NAME:-}" ]; then
        dc down -v 2>/dev/null || true
    fi
}
trap cleanup EXIT

# --- Source-only mode (for testing) ---
# When sourced with --source-only, define functions but don't execute anything
if [ "$MODE" = "--source-only" ]; then
    trap - EXIT  # Remove cleanup trap when just sourcing
    return 0 2>/dev/null || exit 0
fi

# --- Quick mode (parallel) ---
if [ "$MODE" = "quick" ]; then
    validate_imports

    PIDS=()

    # Run all 3 suites in parallel
    run_suite_bg "1/3" unit "uv" run pytest tests/unit/ -m "not requires_db"
    PIDS+=($BGPID)

    run_suite_bg "2/3" integration "uv" run pytest tests/integration/ -m "not requires_db and not requires_server and not skip_ci"
    PIDS+=($BGPID)

    run_suite_bg "3/3" integration_v2 "uv" run pytest tests/integration_v2/ -m "not requires_db and not requires_server and not skip_ci"
    PIDS+=($BGPID)

    echo -e "${BLUE}Waiting for 3 parallel suites...${NC}"
    for pid in "${PIDS[@]}"; do
        wait "$pid" 2>/dev/null || true
    done

    # Print per-suite log output
    echo ""
    echo -e "${BLUE}--- Suite Logs ---${NC}"
    for name in unit integration integration_v2; do
        if [ -f "$RESULTS_DIR/${name}.log" ]; then
            echo -e "${BLUE}=== ${name} ===${NC}"
            cat "$RESULTS_DIR/${name}.log"
            echo ""
        fi
    done

    collect_results unit integration integration_v2

# --- CI mode ---
elif [ "$MODE" = "ci" ]; then
    # Phase 0: validate_imports (fast, no deps) + start Docker in parallel
    _saved_db="${DATABASE_URL:-}"
    unset DATABASE_URL
    validate_imports

    # Restore DATABASE_URL for setup_docker
    if [ -n "$_saved_db" ]; then
        export DATABASE_URL="$_saved_db"
    fi

    setup_docker

    if [ -n "$PYTEST_TARGET" ]; then
        # Targeted mode: single suite, sequential
        run_suite "1/1" targeted "$PYTEST_TARGET" -m "not requires_server and not skip_ci" $PYTEST_ARGS
    else
        # Phase 1+2: Run all 5 suites in parallel
        PIDS=()

        # Unit tests run with DATABASE_URL unset (env override for the subshell)
        DATABASE_URL= run_suite_bg "1/5" unit "uv" run pytest tests/unit/
        PIDS+=($BGPID)

        # Integration suites (need Postgres only)
        run_suite_bg "2/5" integration "uv" run pytest tests/integration/ -m "not requires_server and not skip_ci"
        PIDS+=($BGPID)

        run_suite_bg "3/5" integration_v2 "uv" run pytest tests/integration_v2/ -m "not requires_server and not skip_ci"
        PIDS+=($BGPID)

        # E2E + UI suites (need Postgres + server)
        ADCP_SALES_PORT=$MCP_PORT run_suite_bg "4/5" e2e "uv" run pytest tests/e2e/
        PIDS+=($BGPID)

        if [ -d tests/ui ]; then
            ADCP_SALES_PORT=$MCP_PORT run_suite_bg "5/5" ui "uv" run pytest tests/ui/ -m "not requires_server and not slow"
            PIDS+=($BGPID)
        else
            echo -e "${YELLOW}[5/5] UI tests skipped (no tests/ui directory)${NC}"
        fi

        echo -e "${BLUE}Waiting for parallel suites...${NC}"
        for pid in "${PIDS[@]}"; do
            wait "$pid" 2>/dev/null || true
        done

        # Print per-suite log output
        echo ""
        echo -e "${BLUE}--- Suite Logs ---${NC}"
        for name in unit integration integration_v2 e2e ui; do
            if [ -f "$RESULTS_DIR/${name}.log" ]; then
                echo -e "${BLUE}=== ${name} ===${NC}"
                cat "$RESULTS_DIR/${name}.log"
                echo ""
            fi
        done

        collect_results unit integration integration_v2 e2e ui
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
