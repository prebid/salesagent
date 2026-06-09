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
# STATUS (what is verified to run in-network):
#   unit, integration, bdd, admin  -> server-less (in-process TestClient / Flask
#       test client, or integration spawns its server subprocess in-container);
#       need only Postgres. VERIFIED: unit/admin/bdd pass; integration 1974 pass
#       (minus the 18 creative_agent_live tests, which need CREATIVE_AGENT_URL +
#       a creative-agent on the network, and one pre-existing template url_for
#       failure unrelated to networking).
#
# NOT YET WIRED (TODO — these still address the server as localhost:<host-port>):
#   e2e  -> ~10 conftest/test fixtures build http://localhost:{port}; need a
#           server-host indirection (proxy) and DB host (postgres) split.
#   ui   -> same localhost issue + needs a playwright browser in Dockerfile.test.
#   creative-agent -> add the service to the network + set CREATIVE_AGENT_URL so
#           test_creative_agent_live.py can run.
#
# Usage:
#   scripts/test-in-network.sh                          # unit,integration,bdd,admin
#   scripts/test-in-network.sh unit,integration         # explicit suite list
set -uo pipefail

COMPOSE_FILE="docker-compose.e2e.yml"
# PID-suffixed project name -> isolated network per run. No host ports means
# concurrent runs never contend; the suffix just keeps container names distinct.
export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-adcp-innet-$$}"
SUITES="${1:-unit,integration,bdd,admin}"
RESULTS_DIR="test-results/innet_$(date +%d%m%y_%H%M)"
mkdir -p "$RESULTS_DIR"

dc() { docker compose -f "$COMPOSE_FILE" -p "$COMPOSE_PROJECT_NAME" --profile runner "$@"; }

cleanup() { dc down -v >/dev/null 2>&1 || true; }
trap cleanup EXIT

echo "Building image + bringing up the app stack in-network (project: $COMPOSE_PROJECT_NAME)..."
dc build postgres adcp-server proxy tests

# Bring up Postgres + the app server + proxy. None of these need to publish host
# ports for the in-network runner — it reaches them by service name.
dc up -d postgres adcp-server proxy

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
echo "Running suites in-network: $SUITES"
dc run --rm tests tox -e "$SUITES" -p
RC=$?

# tox writes per-suite JSON into /app/.tox (bind-mounted to the host tree).
cp .tox/*.json "$RESULTS_DIR/" 2>/dev/null || true
echo "Reports: $RESULTS_DIR/"

exit $RC
