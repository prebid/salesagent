#!/bin/bash
# Test runner — orchestrates Docker lifecycle and tox-based test execution.
#
# Prerequisites: tox + tox-uv (install: uv tool install tox --with tox-uv)
#
# Usage (host path — invoked directly, or reached via `./run_all_tests.sh quick`
# and `./run_all_tests.sh ci <target>`, which delegate here):
#   ./run_all_tests_host.sh           # Docker + all 6 suites via tox (default)
#   ./run_all_tests_host.sh quick     # No Docker: unit + integration
#   ./run_all_tests_host.sh ci tests/integration/test_file.py -k test_name
#   ./run_all_tests_host.sh ci tests/integration/ -m creative     # scoped by entity

set -euo pipefail

cd "$( dirname "${BASH_SOURCE[0]}" )"
[ -f .env ] && { set -a; source .env; set +a; }

GREEN='\033[0;32m' RED='\033[0;31m' BLUE='\033[0;34m' NC='\033[0m'

MODE=${1:-ci}
PYTEST_TARGET="${2:-}"
PYTEST_ARGS="${@:3}"
RESULTS_DIR="$(pwd)/test-results/$(date +%d%m%y_%H%M)"
mkdir -p "$RESULTS_DIR"

# The `collection` env grades what a real session collected, so the sessions
# that run before it have to publish that. Exported here rather than left to the
# caller: this runner runs `tox -p -o`, which includes `collection`, and those
# tests fail loudly on a missing artifact rather than skipping -- correctly, but
# it means the runner cannot leave the variable unset.
export PYTEST_COLLECTION_MANIFEST="${PYTEST_COLLECTION_MANIFEST:-$RESULTS_DIR/collection-manifest}"
export BDD_LIVENESS_SESSIONS="${BDD_LIVENESS_SESSIONS:-$RESULTS_DIR/liveness-sessions}"

# Keep only the last 10 result directories
ls -dt "$(pwd)/test-results"/*/ 2>/dev/null | tail -n +11 | xargs rm -rf

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

# Suite reports this script may publish. Named once so the pre-run purge and the
# copy below cannot drift apart — a report present in one list and absent from
# the other is how a stale file survives.
_REPORT_SUITES="unit integration e2e admin bdd ui collection"

purge_stale_reports() {
    # `.tox/` persists between invocations, so a suite that dies before writing
    # leaves the PREVIOUS run's report there and collect_reports cannot tell it
    # apart from one this run produced. Deleting first makes a stale report
    # unrepresentable rather than detectable: afterwards a report exists only if
    # THIS run wrote it. Same fix as run_all_tests.sh; this script had the
    # identical defect and no pre-run purge.
    for name in $_REPORT_SUITES; do
        rm -f ".tox/${name}.json"
    done
}

collect_reports() {
    # Copy JSON reports from .tox/ to results dir
    mkdir -p "$RESULTS_DIR"
    for name in $_REPORT_SUITES; do
        [ -f ".tox/${name}.json" ] && cp ".tox/${name}.json" "$RESULTS_DIR/"
    done
    # Explicit return 0 — without this, the function inherits the exit code
    # of the last ``[ -f X ] && cp X Y`` test, which is 1 when the final
    # file (ui.json) is missing in quick mode. Under ``set -e`` that would
    # propagate to the caller and the script would exit before the summary.
    return 0
}

# --- Quick mode (no Docker) ---
if [ "$MODE" = "quick" ]; then
    validate_imports
    echo -e "${BLUE}Running unit + integration via tox...${NC}"
    # Disable BOTH errexit and pipefail around the tox call:
    # - errexit so a non-zero tox exit doesn't kill the script before the summary
    # - pipefail so the async ``>(tee ...)`` subshell's exit code can't poison ``$?``
    # Capture tox's own exit code via PIPESTATUS[0] (robust to both interpretations).
    set +eo pipefail
    # Redirect to file + stdout via process substitution to avoid tox-uv fd leak
    # that causes pipes (| tee) to hang after tox exits.
    purge_stale_reports
    tox -e unit,integration -p > >(tee "$RESULTS_DIR/tox.log") 2>&1
    TOX_RC=${PIPESTATUS[0]}
    set -eo pipefail
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

    # Pinned reference creative agent (salesagent-kczg): the authoritative run
    # must NOT silently hit the live public agent (its catalog drifts). This
    # mirrors the CI `creative` matrix group exactly — the commit pin is
    # single-sourced in scripts/creative-agent-stack.sh. Idempotent (reuses a
    # healthy stack across runs); torn down with the Docker stack on EXIT.
    ./scripts/creative-agent-stack.sh up
    export CREATIVE_AGENT_URL="$(./scripts/creative-agent-stack.sh url)"

    # ─── Outbound egress escape hatch — TEST PATH ONLY (#1589, salesagent-e6h0) ──
    # The line above exports an https://agent.localhost:<port>/... URL
    # (scripts/creative-agent-stack.sh's own TLS front, salesagent-40qh) at a
    # reserved loopback address, which src/core/security/outbound_http.py
    # refuses for the ADDRESS reason. docker-compose.e2e.yml opens the same
    # hatch for the in-network runner; tox.ini only PASSES it through
    # (pass_env, not setenv), so on this host path nothing creates it and the
    # two authoritative run paths would diverge — in-network green, host red,
    # same commit.
    #
    # SSL_CERT_FILE is required too: `uv run pytest`/`tox` below run ON THIS
    # HOST, not inside the dockerized server (which gets its own SSL_CERT_FILE
    # from docker-compose.e2e.yml directly) — so the test PROCESS itself needs
    # to trust the generated CA to dial the same https fronts (creative-agent,
    # the webhook capture at webhooks.adcp.test — salesagent-amht.3) the
    # server dials. The COMBINED bundle (system CA + our private CA), never
    # the private CA alone — that broke `uv sync` against real pypi.org once
    # already.
    #
    # There is no scheme hatch anymore (salesagent-e6h0 deleted it) — every
    # outbound origin here is TLS-fronted, so there is nothing left to relax.
    #
    # Set here and NOT in quick mode or at file scope, deliberately: quick mode
    # starts no Docker and no creative-agent stack, so it has no loopback fixture
    # to accommodate, and it is the mode that carries the in-process refusal
    # grading (set_flags() in tests/integration/test_outbound_http.py, plus the
    # BDD refusal scenarios). An ambient private-range hatch there would make
    # the seam accept exactly the addresses it exists to reject.
    #
    # Literal "true" — the seam compares the lowercased string.
    export ADCP_OUTBOUND_ALLOW_PRIVATE="true"
    export SSL_CERT_FILE="$(pwd)/.test-tls/combined-ca.pem"

    trap './scripts/creative-agent-stack.sh down 2>/dev/null || true; ./scripts/test-stack.sh down 2>/dev/null || true' EXIT

    if [ -n "$PYTEST_TARGET" ]; then
        # Targeted test run — see "set +eo pipefail / PIPESTATUS[0]" rationale in quick mode.
        echo -e "${BLUE}Running targeted: $PYTEST_TARGET $PYTEST_ARGS${NC}"
        set +eo pipefail
        uv run pytest "$PYTEST_TARGET" \
            -m "not skip_ci" \
            --json-report --json-report-file="$RESULTS_DIR/targeted.json" --json-report-indent=2 \
            -q --tb=line $PYTEST_ARGS > >(tee "$RESULTS_DIR/targeted.log") 2>&1
        TOX_RC=${PIPESTATUS[0]}
        set -eo pipefail
        [ "$TOX_RC" -ne 0 ] && FAILURES="targeted"
    else
        echo -e "${BLUE}Running all 6 suites in parallel via tox...${NC}"
        set +eo pipefail
        purge_stale_reports
        tox -p -o > >(tee "$RESULTS_DIR/tox.log") 2>&1
        TOX_RC=${PIPESTATUS[0]}
        set -eo pipefail
        collect_reports
        # Determine success from JSON reports, not tox exit code —
        # PIPESTATUS[0] with process substitution returns stale values.
        _any_fail=false
        for _rpt in "$RESULTS_DIR"/*.json; do
            [ -f "$_rpt" ] || continue
            if python3 -c "import json,sys; d=json.load(open('$_rpt')); sys.exit(0 if d.get('exitcode',1)==0 else 1)" 2>/dev/null; then
                :
            else
                _any_fail=true; break
            fi
        done
        [ "$_any_fail" = true ] && FAILURES="tox"

        # Coverage combine runs separately — tox -p hangs when the coverage
        # env fails (e.g. missing .coverage.e2e from HTTP-only e2e tests).
        # Coverage combine — run separately, non-fatal
        echo -e "${BLUE}Combining coverage...${NC}"
        tox -e coverage || echo -e "${BLUE}Coverage combine failed (non-fatal)${NC}"
    fi
else
    echo "Usage: ./run_all_tests.sh [quick|ci]"
    echo "  ci (default) — Docker + all 6 suites via tox"
    echo "  quick        — no Docker: unit + integration"
    exit 1
fi

# --- Truncation check ---
# Same predicate run_all_tests.sh applies, on the same JSON, for the same
# reason: every mode above decides success from an exit code or from each
# report's own `exitcode`, and a truncated run is green by both. `quick` runs
# the unit env too, which is now parallel, so this path is exposed to the same
# hole. See scripts/check_truncated_reports.py.
if ls "$RESULTS_DIR"/*.json >/dev/null 2>&1; then
    if ! python3 scripts/check_truncated_reports.py "$RESULTS_DIR"; then
        FAILURES="${FAILURES:+$FAILURES }truncated"
    fi
fi

# --- Security audit ---
# Ignored-vulnerabilities list + uv-secure invocation are single-sourced in
# scripts/security-audit.sh (same script is called by .github/workflows/ci.yml,
# so CI and local cannot drift).
echo -e "${BLUE}Running security audit (uv-secure)...${NC}"
if ./scripts/security-audit.sh --no-check-uv-tool 2>/dev/null; then
    echo -e "${GREEN}Security audit passed${NC}"
else
    echo -e "${RED}Security audit FAILED — run: ./scripts/security-audit.sh${NC}"
    FAILURES="${FAILURES:+$FAILURES }security"
fi

# --- Summary ---
FAILURES="${FAILURES:-}"
echo "================================================================"
echo "Reports: $RESULTS_DIR/"
ls "$RESULTS_DIR"/*.json 2>/dev/null | while read f; do echo "  $(basename $f)"; done
[ -z "$FAILURES" ] && echo -e "${GREEN}ALL PASSED${NC}" && exit 0
echo -e "${RED}FAILED:$FAILURES${NC}" && exit 1
