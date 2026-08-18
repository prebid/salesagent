#!/usr/bin/env bash
#
# In-network test runner (Option 1).
#
# Runs the test suites INSIDE the compose network instead of on the host. The
# runner container reaches Postgres and the app server by SERVICE NAME
# (postgres:5432, proxy:8000), so this path publishes NO host ports and cannot
# hit the host-port TOCTOU race that scripts/test-stack.sh suffers when it
# guesses a free port in 50000-60000 and a sibling stack grabs it before
# `docker up` binds it.
#
# Each `docker compose -p <project>` gets its own isolated bridge network, so
# `postgres`/`proxy` here can never collide with another stack's — many of these
# can run concurrently with zero port coordination.
#
# STATUS — all six suites run in-network, addressing every dependency by SERVICE
# NAME (postgres:5432, proxy:8000, creative-agent:8080, runner alias `tests`):
#   unit              -> no DB (DATABASE_URL unset by the unit tox env)
#   integration       -> suite DB (/adcp_test) + creative-agent for the 18
#                        test_creative_agent_live tests (CREATIVE_AGENT_URL)
#   bdd               -> suite DB (/adcp_test), xdist
#   admin             -> suite DB (/adcp_test)
#   e2e               -> SERVER DB (/adcp via E2E_DATABASE_URL, per-suite tox
#                        override); server reached at proxy:8000; webhooks call
#                        back to the runner via ADCP_WEBHOOK_HOST=tests
#   ui                -> SERVER DB (/adcp) + playwright chromium baked into
#                        Dockerfile.test; browser drives proxy:8000
#
# Per-suite DB split: integration/bdd/admin use the runner's DATABASE_URL
# (/adcp_test); e2e/ui override it to E2E_DATABASE_URL (/adcp) in their tox envs.
# Both DBs live on the same `postgres` service — different database names, no
# collision, and NO published host ports anywhere.
#
# Usage:
#   ./run_all_tests.sh                          # all six suites in-network (default)
#   ./run_all_tests.sh ci                       # same, explicit
#   ./run_all_tests.sh unit,integration         # explicit suite list
#   ./run_all_tests.sh quick                    # no-Docker unit+integration (delegates to host runner)
#   ./run_all_tests.sh ci tests/path -k name    # targeted run (delegates to host runner)
set -euo pipefail

COMPOSE_FILE="docker-compose.e2e.yml"
# PID-suffixed project name -> isolated network per run. No host ports means
# concurrent runs never contend; the suffix just keeps container names distinct.
# Compose rejects uppercase project names — lowercase whatever we're given.
export COMPOSE_PROJECT_NAME="$(printf '%s' "${COMPOSE_PROJECT_NAME:-adcp-innet-$$}" | tr '[:upper:]' '[:lower:]')"
# The tests container runs as THIS user (docker-compose.e2e.yml `tests.user`), so
# everything it writes into the bind-mounted repo -- test-results/, .tox/,
# schemas/, logs -- is owned by whoever launched the run, on any host. Derived
# here rather than written into compose: a literal uid is correct on exactly one
# machine, and pinning the CI box's turned GitHub Actions red on `.tox`.
#
# Both are overridable. A host whose artifacts must stay writable by SEVERAL
# identities (the CI box shares group `ci` across sacirunner and claudeuser)
# exports TEST_GID to that shared group instead of taking the primary one.
export TEST_UID="${TEST_UID:-$(id -u)}"
export TEST_GID="${TEST_GID:-$(id -g)}"
# The delivery-webhook scheduler runs on the SERVER (adcp-server), gated by this
# interval. docker-compose.e2e.yml defaults it empty (scheduler off); the host
# e2e path sets it to 5 via conftest. Mirror that so test_daily_delivery_webhook
# gets a report. Compose interpolates this into the adcp-server service env.
export DELIVERY_WEBHOOK_INTERVAL="${DELIVERY_WEBHOOK_INTERVAL:-5}"
# Argument contract — back-compat with the historical MODE words so the
# pre-existing callers (Makefile quality-full/test-full, docs) keep working:
#   (no arg) | ci                 -> all six suites, in-network (the default)
#   ci <pytest-target> [args...]  -> targeted run  (delegated to the host runner)
#   quick                         -> no-Docker unit+integration (host runner)
#   <comma,list>                  -> explicit tox suite list, in-network
# The in-network path always builds the full compose stack, so it can't honor
# the "quick == no Docker" or the targeted contracts — those delegate to the
# verbatim host runner that already implements them (DRY, single source).
ALL_SUITES="unit,integration,bdd,admin,e2e,ui"
DELEGATE=0
case "${1:-ci}" in
    quick) DELEGATE=1 ;;
    ci) if [ -n "${2:-}" ]; then DELEGATE=1; else SUITES="$ALL_SUITES"; fi ;;
    *) SUITES="$1" ;;
esac

# Testability seam: resolve the argument contract and exit BEFORE any Docker
# call so tests/unit/test_run_all_tests_contract.py can assert it without a stack.
if [ -n "${RUN_ALL_TESTS_RESOLVE_ONLY:-}" ]; then
    if [ "$DELEGATE" = 1 ]; then echo "RESOLVED delegate-host: $*"; else echo "RESOLVED suites=$SUITES"; fi
    exit 0
fi

if [ "$DELEGATE" = 1 ]; then
    exec "$(dirname "$0")/run_all_tests_host.sh" "$@"
fi

# Fast bdd path: when per-worker e2e stacks are provisioned (E2E_WORKERS>0), swap
# the plain serial `bdd` env for the two-pass split — bdd_inprocess (the
# a2a/mcp/rest bulk, parallelized by BDD_XDIST_N) then bdd_e2e (the e2e_rest
# transport, fanned across the per-worker servers by BDD_E2E_XDIST_N). Without
# E2E_WORKERS the plain serial `bdd` runs unchanged, so CI and small runners are
# unaffected. Phase B below provisions the servers and exports BDD_E2E_XDIST_N.
if [ "${E2E_WORKERS:-0}" -gt 0 ] 2>/dev/null; then
    # Token-exact swap: a plain-substring ${SUITES/bdd/...} would mangle an
    # explicit bdd_inprocess/bdd_e2e suite argument (bdd_e2e -> bdd_e2e_e2e).
    SUITES=",$SUITES,"
    SUITES="${SUITES/,bdd,/,bdd_inprocess,bdd_e2e,}"
    SUITES="${SUITES#,}"; SUITES="${SUITES%,}"
    # bdd_inprocess reads BDD_XDIST_N (compose pins it to 0 = serial by default),
    # so the in-process bulk only parallelizes if we export a worker count here.
    # Default to `auto` (PYTEST_XDIST_AUTO_NUM_WORKERS) so the swap is actually
    # fast on its own — without this the ~23m->~3.5m in-process win never lands.
    export BDD_XDIST_N="${BDD_XDIST_N:-auto}"
    echo "Fast bdd path: E2E_WORKERS=$E2E_WORKERS BDD_XDIST_N=$BDD_XDIST_N -> suites=$SUITES"
fi

RESULTS_DIR="test-results/innet_$(date +%d%m%y_%H%M)"
mkdir -p "$RESULTS_DIR"

dc() { docker compose -f "$COMPOSE_FILE" -p "$COMPOSE_PROJECT_NAME" --profile runner "$@"; }

cleanup() {
    # Per-worker e2e servers are `docker compose run` containers (not `up`), so
    # `dc down` won't remove them — do it explicitly.
    docker ps -aq --filter "name=${COMPOSE_PROJECT_NAME}-server-gw" | xargs -r docker rm -f >/dev/null 2>&1 || true
    dc down -v >/dev/null 2>&1 || true
}
trap cleanup EXIT

# The pinned reference creative-agent image (adcp@<pin>) is built once by the
# single-source script; compose reuses it as the `creative-agent` service (no
# :9999). Host run_all_tests.sh uses the same script (same pin) — no divergence.
echo "Building pinned creative-agent image (single-sourced)..."
scripts/creative-agent-stack.sh build

echo "Building image + bringing up the app stack in-network (project: $COMPOSE_PROJECT_NAME)..."
dc build postgres adcp-server proxy tests

# Bring up Postgres + the app server + proxy + the pinned creative-agent (and its
# own registry Postgres). None publish host ports — all reached by service name.
# Pre-create logs/ group-writable + setgid BEFORE adcp-server starts: it
# bind-mounts .:/app and creates logs/audit.log at import time (uid 1001,
# its own baked umask) -- when that umask strips the group-write bit the
# tests container (a different uid, 1003 here) can create the dir but not
# write into it, and every suite dies at collection with
# `PermissionError: '/app/logs/audit.log'`. Owning it here first means
# adcp-server writes into an already-correct dir instead of racing to
# create it (confirmed live: sa-93d37d7c, sa-c9acaf66 both landed
# drwxr-sr-x -- not group-writable -- and are latent failures until fixed).
mkdir -p logs && chmod 2775 logs

dc up -d postgres adcp-server proxy creative-pg creative-agent

echo "Waiting for Postgres + server health (in-network)..."
deadline=$(( $(date +%s) + 360 ))
pg=false srv=false
while [ "$(date +%s)" -lt "$deadline" ]; do
    [ "$pg" = false ] && dc exec -T postgres pg_isready -U adcp_user >/dev/null 2>&1 && pg=true && echo "  Postgres ready"
    [ "$srv" = false ] && dc exec -T adcp-server curl -sf http://localhost:8080/health >/dev/null 2>&1 && srv=true && echo "  Server ready"
    [ "$pg" = true ] && [ "$srv" = true ] && break
    sleep 3
done
[ "$pg" = true ] || { echo "Postgres never became ready"; dc logs postgres; exit 1; }

# The suites use the adcp_test database (matches scripts/test-stack.sh).
dc exec -T postgres psql -U adcp_user -d postgres -c "CREATE DATABASE adcp_test" >/dev/null 2>&1 || true

# ── Per-worker e2e server stacks (parallel bdd_e2e) ──────────────────────────
# When E2E_WORKERS=N, provision N isolated (server + DB) stacks so the e2e_rest
# transport can run in parallel. Each xdist worker gwK targets <project>-server-gwK
# / adcp_gwK (routed by tests/bdd/conftest.py e2e_stack via E2E_PER_WORKER=1).
# DBs are cloned from a migrated template (adcp_e2e_template) so per-worker setup
# is a fast copy, not N migration runs. Off by default (E2E_WORKERS unset).
E2E_ENV_ARGS=""
if [ "${E2E_WORKERS:-0}" -gt 0 ] 2>/dev/null; then
    N="$E2E_WORKERS"
    _admin="postgresql://adcp_user:secure_password_change_me@postgres:5432"
    psql_admin() { dc exec -T postgres psql -U adcp_user -d postgres -c "$1" >/dev/null 2>&1 || true; }
    echo "Provisioning $N per-worker e2e server stacks..."
    psql_admin "DROP DATABASE IF EXISTS adcp_e2e_template"
    psql_admin "CREATE DATABASE adcp_e2e_template"
    # Fail fast: if the template migration fails, every per-worker DB below would
    # be cloned from an un-migrated template and the whole e2e_rest pass would
    # error confusingly. Surface it here instead.
    if ! dc run --rm --no-deps -e DATABASE_URL="$_admin/adcp_e2e_template?sslmode=disable" \
            tests python scripts/ops/migrate.py >/dev/null 2>&1; then
        echo "ERROR: e2e template migration failed — aborting per-worker provisioning" >&2
        exit 1
    fi
    for i in $(seq 0 $((N - 1))); do
        psql_admin "DROP DATABASE IF EXISTS adcp_gw$i"
        psql_admin "CREATE DATABASE adcp_gw$i TEMPLATE adcp_e2e_template"
        dc run -d --no-deps --name "${COMPOSE_PROJECT_NAME}-server-gw$i" \
            -e DATABASE_URL="$_admin/adcp_gw$i?sslmode=disable" adcp-server >/dev/null
    done
    echo "  waiting for $N per-worker servers to become healthy..."
    for i in $(seq 0 $((N - 1))); do
        wd=$(( $(date +%s) + 120 )); ok=false
        while [ "$(date +%s)" -lt "$wd" ]; do
            docker exec "${COMPOSE_PROJECT_NAME}-server-gw$i" curl -sf http://localhost:8080/health >/dev/null 2>&1 && ok=true && break
            sleep 2
        done
        [ "$ok" = true ] && echo "    server-gw$i ready" || echo "    server-gw$i NOT ready (continuing)"
    done
    # COMPOSE_PROJECT_NAME must reach pytest so conftest e2e_stack builds the FULL
    # server name "<project>-server-gwN" (short "server-gwN" doesn't resolve).
    E2E_ENV_ARGS="-e E2E_PER_WORKER=1 -e BDD_E2E_XDIST_N=$N -e COMPOSE_PROJECT_NAME=$COMPOSE_PROJECT_NAME"
fi

# Run the suites in-network. DATABASE_URL=postgres:5432 (service name) is baked
# into the `tests` service environment — no host port, no scan, no race.
# --use-aliases gives this run container the `tests` network alias so the server
# can call webhooks back to it (ADCP_WEBHOOK_HOST=tests) by name.
#
# SERIAL (no `-p`): run_all_tests.sh runs `tox -p` on the HOST, where each env is
# its own process tree with the full host RAM. Packing all six suites into ONE
# container and running them concurrently OOM-kills them (exit -9) — and bdd's
# `-n auto` alone spawns one worker per host CPU (~17), each loading the app.
# Serial execution keeps peak memory to a single suite; PYTEST_XDIST_AUTO_NUM_WORKERS
# (set on the tests service) caps bdd's worker count so it can't blow memory or
# trip the xdist loadscope rescheduler. Same suites, same outcomes — just not
# wall-clock parallel inside the one container.
echo "Running suites in-network (serial): $SUITES"
# Capture the suite exit code without aborting under `set -e` — reports must
# still be extracted and the security audit must still run on a suite failure.
RC=0
# Structural backstop, not another one-off path pin: ANY service that writes
# into the bind-mounted repo before this point (adcp-server, the per-worker
# gwN servers, postgres, creative-agent) can leave files/dirs non-group-
# writable depending on its own container's umask -- same class of bug
# regardless of which path it is (logs/audit.log today, something else
# tomorrow). Re-sweep the whole tree writable right before the one container
# that needs it runs, the same way salesagent-remote-run already does once
# at sync time -- catches whatever showed up in between instead of requiring
# another bug report + another path pinned by hand.
chmod -R g+w . 2>/dev/null || true
chmod -R go-w .git 2>/dev/null || true

dc run --rm --use-aliases $E2E_ENV_ARGS tests tox -e "$SUITES" || RC=$?

# tox writes per-suite JSON into /app/.tox, which is a plain bind-mounted dir
# now (Aug 2026: the tox_data named volume it used to live on was removed --
# a fresh named volume's mountpoint is always created root:root by the Docker
# daemon regardless of the tests container's own `user:` override, which
# permanently blocked the non-root test runner from `.tox/<env>` on every
# single run). No throwaway extraction container needed any more -- .tox is
# just a normal host directory, already right where $RESULTS_DIR is.
echo "Collecting JSON reports..."
# Loud, not silent (Aug 2026): this used to be `2>/dev/null || true`, which
# once ate a real failure completely silently -- a full 23-minute run
# finished clean (exit 0, all 7 suites really passed, .tox/*.json all
# present and correct) but test-results/ never got populated, with zero
# trace of why. Re-mkdir defensively right before copying (idempotent, cheap
# insurance against $RESULTS_DIR having been removed or never created for
# any reason) and let a real failure actually say something instead of
# vanishing 23 minutes of work without a trace.
mkdir -p "$RESULTS_DIR"
if ! cp .tox/*.json "$RESULTS_DIR/"; then
    echo "WARNING: failed to copy JSON reports into $RESULTS_DIR/ -- see error above." >&2
    echo "         Reports are still in .tox/*.json inside $(pwd) if you need them by hand." >&2
fi
echo "Reports: $RESULTS_DIR/"
ls -1 "$RESULTS_DIR"/*.json 2>/dev/null || echo "  (no JSON reports extracted)"

# Security audit (uv-secure) — runs on the HOST (scans uv.lock; no Docker). The
# host runner runs this too; keep parity so the canonical local gate still scans
# for known vulnerabilities. Single-sourced in scripts/security-audit.sh (also
# called by .github/workflows/ci.yml, so CI and local can't drift).
# RUN_ALL_SKIP_AUDIT=1 skips it: the CI in-network job has no host uvx (it
# deliberately skips _setup-env) and the dedicated "Security Audit" CI check
# already owns this scan — a silent command-not-found here must not fail an
# otherwise-green suite run.
if [ "${RUN_ALL_SKIP_AUDIT:-0}" = "1" ]; then
    echo "Security audit skipped (RUN_ALL_SKIP_AUDIT=1 — owned by the dedicated CI check)"
elif ! command -v uvx >/dev/null 2>&1; then
    echo "Security audit FAILED — uvx not on PATH (install uv or set RUN_ALL_SKIP_AUDIT=1)"
    [ "$RC" -eq 0 ] && RC=1
else
    echo "Running security audit (uv-secure)..."
    if ./scripts/security-audit.sh --no-check-uv-tool 2>/dev/null; then
        echo "Security audit passed"
    else
        echo "Security audit FAILED — run: ./scripts/security-audit.sh"
        [ "$RC" -eq 0 ] && RC=1
    fi
fi

exit $RC
