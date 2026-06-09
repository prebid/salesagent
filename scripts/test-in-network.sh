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
#   scripts/test-in-network.sh                          # all six suites
#   scripts/test-in-network.sh unit,integration         # explicit suite list
set -uo pipefail

COMPOSE_FILE="docker-compose.e2e.yml"
# PID-suffixed project name -> isolated network per run. No host ports means
# concurrent runs never contend; the suffix just keeps container names distinct.
# Compose rejects uppercase project names — lowercase whatever we're given.
export COMPOSE_PROJECT_NAME="$(printf '%s' "${COMPOSE_PROJECT_NAME:-adcp-innet-$$}" | tr '[:upper:]' '[:lower:]')"
SUITES="${1:-unit,integration,bdd,admin,e2e,ui}"
RESULTS_DIR="test-results/innet_$(date +%d%m%y_%H%M)"
mkdir -p "$RESULTS_DIR"

dc() { docker compose -f "$COMPOSE_FILE" -p "$COMPOSE_PROJECT_NAME" --profile runner "$@"; }

cleanup() { dc down -v >/dev/null 2>&1 || true; }
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
dc run --rm --use-aliases tests tox -e "$SUITES"
RC=$?

# tox writes per-suite JSON into /app/.tox, which is the `tox_data` NAMED VOLUME
# (kept off the bind mount so venvs don't live on the slow host tree). The host
# .tox is therefore empty — extract the reports from the volume with a throwaway
# container before the cleanup trap runs `down -v` and removes it.
echo "Extracting JSON reports from the tox_data volume..."
docker run --rm \
    -v "${COMPOSE_PROJECT_NAME}_tox_data:/t:ro" \
    -v "$(pwd)/${RESULTS_DIR}:/out" \
    alpine sh -c 'cp /t/*.json /out/ 2>/dev/null || true'
echo "Reports: $RESULTS_DIR/"
ls -1 "$RESULTS_DIR"/*.json 2>/dev/null || echo "  (no JSON reports extracted)"

exit $RC
