#!/bin/bash
# Test runner — orchestrates Docker lifecycle and tox-based test execution.
#
# Prerequisites: tox + tox-uv (install: uv tool install tox --with tox-uv)
#
# Usage:
#   ./run_all_tests.sh           # Docker + all 5 suites via tox (default)
#   ./run_all_tests.sh quick     # No Docker: unit + integration + integration_v2
#   ./run_all_tests.sh ci tests/integration/test_file.py -k test_name

set -eo pipefail

cd "$( dirname "${BASH_SOURCE[0]}" )"
[ -f .env ] && { set -a; source .env; set +a; }

GREEN='\033[0;32m' RED='\033[0;31m' BLUE='\033[0;34m' NC='\033[0m'

MODE=${1:-ci}
PYTEST_TARGET="${2:-}"
PYTEST_ARGS="${@:3}"
RESULTS_DIR="test-results/$(date +%d%m%y_%H%M)"
mkdir -p "$RESULTS_DIR"

# Keep only the last 10 result directories
ls -dt test-results/*/ 2>/dev/null | tail -n +11 | xargs rm -rf

echo "Mode: $MODE | Reports: $RESULTS_DIR/"

# --- Helpers ---

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

collect_reports() {
    # Copy JSON reports from .tox/ to results dir
    for name in unit integration integration_v2 e2e ui; do
        [ -f ".tox/${name}.json" ] && cp ".tox/${name}.json" "$RESULTS_DIR/"
    done
}

# --- Quick mode (no Docker) ---
if [ "$MODE" = "quick" ]; then
    validate_imports
    echo -e "${BLUE}Running unit + integration + integration_v2 via tox...${NC}"
    set +e
    tox -e unit,integration,integration_v2 -p 2>&1 | tee "$RESULTS_DIR/tox.log"
    TOX_RC=${PIPESTATUS[0]}
    set -e
    collect_reports
    [ "$TOX_RC" -ne 0 ] && FAILURES="tox"

# --- CI mode (Docker + all suites) ---
elif [ "$MODE" = "ci" ]; then
    _saved_db="${DATABASE_URL:-}"
    unset DATABASE_URL
    validate_imports
    if [ -n "$_saved_db" ]; then export DATABASE_URL="$_saved_db"; fi

    # Start Docker stack (writes .test-stack.env)
    ./scripts/test-stack.sh up
    source .test-stack.env
    trap './scripts/test-stack.sh down 2>/dev/null || true' EXIT

    if [ -n "$PYTEST_TARGET" ]; then
        # Targeted test run
        echo -e "${BLUE}Running targeted: $PYTEST_TARGET $PYTEST_ARGS${NC}"
        set +e
        uv run pytest "$PYTEST_TARGET" \
            -m "not requires_server and not skip_ci" \
            --json-report --json-report-file="$RESULTS_DIR/targeted.json" --json-report-indent=2 \
            -q --tb=line $PYTEST_ARGS 2>&1 | tee "$RESULTS_DIR/targeted.log"
        TOX_RC=${PIPESTATUS[0]}
        set -e
        [ "$TOX_RC" -ne 0 ] && FAILURES="targeted"
    else
        echo -e "${BLUE}Running all 5 suites in parallel via tox...${NC}"
        set +e
        tox -p -o 2>&1 | tee "$RESULTS_DIR/tox.log"
        TOX_RC=${PIPESTATUS[0]}
        set -e
        collect_reports
        [ "$TOX_RC" -ne 0 ] && FAILURES="tox"
    fi
else
    echo "Usage: ./run_all_tests.sh [quick|ci]"
    echo "  ci (default) — Docker + all 5 suites via tox"
    echo "  quick        — no Docker: unit + integration + integration_v2"
    exit 1
fi

# --- Summary ---
FAILURES="${FAILURES:-}"
echo "================================================================"
echo "Reports: $RESULTS_DIR/"
ls "$RESULTS_DIR"/*.json 2>/dev/null | while read f; do echo "  $(basename $f)"; done
[ -z "$FAILURES" ] && echo -e "${GREEN}ALL PASSED${NC}" && exit 0
echo -e "${RED}FAILED:$FAILURES${NC}" && exit 1
