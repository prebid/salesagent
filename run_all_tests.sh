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
# TEST_UID/TEST_GID export removed with docker-compose.e2e.yml's `tests.user`
# pin (Aug 2026) -- see the comment there. In short: this derived the pin from
# `id -u` in a process that runs as root under the CI supervisor, so it
# resolved to 0:0 and pinned the image default; and on the rootless CI box
# container root already maps to the invoking user, which is what the pin was
# for. .tox moved back onto a named volume in the same change, so the `.tox`
# ownership failure that motivated deriving it no longer has a bind mount to
# happen on.
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

# UTC, not local. The name is built by whichever client launches the run while the
# reports land under the box's UTC clock, so a CEST client produced a directory named
# two hours ahead of its own payload. That name then sorted lexicographically above
# every genuinely later run, so `ls -t`-style "newest directory" lookups resolved to it
# and reported an older run's totals — or an empty husk — under the current run's id.
# Both clocks agree only in UTC.
RESULTS_DIR="test-results/innet_$(date -u +%d%m%y_%H%M)"
mkdir -p "$RESULTS_DIR"

dc() { docker compose -f "$COMPOSE_FILE" -p "$COMPOSE_PROJECT_NAME" --profile runner "$@"; }

cleanup() {
    # Per-worker e2e servers AND their TLS sidecars are `docker compose run`
    # containers (not `up`), so `dc down` won't remove them — do it explicitly.
    # The sidecars MUST be matched too: a leaked `-tls-gwN.adcp.test` container
    # keeps its dotted DNS name registered and poisons the next run's lookups.
    for _stray in server-gw tls-gw; do
        docker ps -aq --filter "name=${COMPOSE_PROJECT_NAME}-${_stray}" | xargs -r docker rm -f >/dev/null 2>&1 || true
    done
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

# TLS material for the tls-proxy service and the per-worker sidecars below. It
# must exist before `up`: the service bind-mounts .test-tls/, and an absent
# directory would materialise empty and nginx would refuse to start. The host
# wrapper needs a Python with `cryptography`; the in-network CI job has neither
# uv nor the project venv on the host, so fall back to the runner image (which
# writes through the same `.:/app` bind mount).
echo "Ensuring the test stack's TLS material..."
scripts/dev/ensure-test-tls.sh || dc run --rm --no-deps -T tests python scripts/dev/gen_test_tls.py

# Allocate this stack's network slice BEFORE `up` (salesagent-mp53.9). The e2e
# network is pinned to a NON-PRIVATE range so the server can reach its webhook
# receiver at an address production's SSRF gate accepts on its own terms — but a
# fixed value is a concurrency break: the second stack on the box dies with
# "Pool overlaps with other one on this address space". Measured, not theorised.
# Without this export the compose default applies and every concurrent stack
# asks for the same slice.
if [ -z "${E2E_NETWORK_SUBNET:-}" ]; then
    eval "export $(scripts/dev/alloc-e2e-subnet.sh)"
    echo "  e2e network slice: $E2E_NETWORK_SUBNET"
fi

# Bring up Postgres + the app server + proxy + the TLS listener + the pinned
# creative-agent (and its own registry Postgres). None publish host ports — all
# reached by service name. tls-proxy is in this explicit list deliberately: it is
# a normal `up` service, and omitting it would leave E2E_TLS_BASE_URL pointing at
# nothing while every https scenario reported green on the http branch.
# webhook-capture and counterparty-origin are in this list deliberately: they are
# ORIGINS the SERVER dials, routed by SNI through tls-proxy, and a service that is
# declared in compose but never started answers 502 from nginx — which reads as a
# verifier or signing failure three assertions later rather than as a missing
# service. (salesagent-mp53.9 shipped webhook-capture without adding it here and
# got away with it only because nothing in-network dialled it yet: its egress test
# checks DNS and gate arithmetic, and its contract test runs the service
# in-process. salesagent-mp53.8's counterparty walk is the first leg that actually
# needs an origin up, and it failed exactly this way.) The guard
# tests/unit/test_architecture_e2e_origin_services_start.py pins the pairing.
# Pre-create logs/ AND the specific files src/core/audit_logger.py opens --
# audit.log/error.log via FileHandler at IMPORT time (crashes collection
# immediately on PermissionError), structured.jsonl/security.jsonl lazily via
# open(path, "a"). setgid + 2775 on the directory only controls the GROUP of
# NEW files, not their write bit, and it does nothing for files that already
# exist. Worse: chmod cannot fix a file it doesn't own -- only the owner (or
# root) may change a file's mode, being in the same group is not enough -- so
# a stale ci:ci 0644 file left by a prior adcp-server run silently defeats
# both this and the `chmod -R g+w .` sweep below (EPERM, swallowed by its own
# `|| true`). Removing and recreating is what actually works: unlink is
# governed by the DIRECTORY's write bit (which we own), not the file's own
# owner, so `rm -f` succeeds even on a ci-owned file; the fresh file this
# process then creates is ours. Verified live: ci:ci 0644 -> sacirunner:ci 0664.
mkdir -p logs
chmod 2775 logs
for f in audit.log error.log structured.jsonl security.jsonl; do
    rm -f "logs/$f" 2>/dev/null || true
    # Two statements, not `: > "logs/$f" && chmod ...`. errexit exempts every command
    # in an AND-OR list except the last, so as an &&-list a failed truncate merely
    # short-circuits: chmod is skipped, the loop continues, and the script still exits
    # 0. Verified A/B (with a directory planted at logs/audit.log to force the
    # failure): &&-list -> "REACHED-END", exit 0; split -> exit 1 at the truncate.
    : > "logs/$f"
    # 666, not 664. The point of this block is that the adcp-server container can
    # WRITE these; 664 only achieves that if the container's user shares the file's
    # group, and it does not. The server runs as the image's `app` (uid/gid 1001,
    # no supplementary groups), while these files are owned by whoever ran the
    # script -- and the bind-mount of this directory onto /app SHADOWS the image's
    # own `chown -R app:app /app`, so host-side permissions are what decide. Group
    # never matches, so `app` falls through to the OTHER bits: r-- under 664, and
    # the server dies on PermissionError while opening audit.log at import time,
    # taking every per-worker server container unhealthy with it.
    # The setgid bit set on the directory above does not rescue this either: it
    # controls the GROUP of new files, not their write bit.
    # 666 is also consistent with the rest of a run directory, which is already
    # world-writable; these are ephemeral per-run test logs, not durable state.
    chmod 666 "logs/$f"
done

dc up -d postgres adcp-server proxy tls-proxy creative-pg creative-agent webhook-capture counterparty-origin

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
    # stderr is KEPT (only chatter goes to /dev/null): `|| true` here means a
    # failed CREATE lets the migration below run against a database that does
    # not exist, and the abort message then blames the migration for it.
    psql_admin() { dc exec -T postgres psql -U adcp_user -d postgres -c "$1" >/dev/null || true; }
    echo "Provisioning $N per-worker e2e server stacks..."
    psql_admin "DROP DATABASE IF EXISTS adcp_e2e_template"
    psql_admin "CREATE DATABASE adcp_e2e_template"
    # Fail fast: if the template migration fails, every per-worker DB below would
    # be cloned from an un-migrated template and the whole e2e_rest pass would
    # error confusingly. Surface it here instead.
    if ! dc run --rm --no-deps -e DATABASE_URL="$_admin/adcp_e2e_template?sslmode=disable" \
            tests python scripts/ops/migrate.py; then
        echo "ERROR: e2e template migration failed — aborting per-worker provisioning" >&2
        exit 1
    fi
    for i in $(seq 0 $((N - 1))); do
        psql_admin "DROP DATABASE IF EXISTS adcp_gw$i"
        psql_admin "CREATE DATABASE adcp_gw$i TEMPLATE adcp_e2e_template"
        dc run -d --no-deps --name "${COMPOSE_PROJECT_NAME}-server-gw$i" \
            -e DATABASE_URL="$_admin/adcp_gw$i?sslmode=disable" adcp-server >/dev/null
        # Per-worker TLS sidecar (salesagent-tgzb). The DOTTED CONTAINER NAME is
        # the entire mechanism: `docker compose run` has no --network-alias, so
        # the container's own name is the only DNS label we control. Docker's
        # embedded DNS resolving a dotted container name is OBSERVED behaviour,
        # not a documented contract — if a future Docker release breaks it, the
        # handshake probe below is what turns it into a diagnosable failure
        # instead of a mystery. COMPOSE_PROJECT_NAME never contains a dot, so the
        # name is exactly one label under the leaf's `*.adcp.test` SAN.
        dc run -d --no-deps --name "${COMPOSE_PROJECT_NAME}-tls-gw$i.adcp.test" \
            -e TLS_UPSTREAM="${COMPOSE_PROJECT_NAME}-server-gw$i:8080" tls-proxy >/dev/null
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
    # TLS readiness — a REAL handshake at the dotted name, verified against the
    # generated CA, and a HARD FAILURE on timeout. Deliberately NOT the shape of
    # the plaintext probe above, which prints "NOT ready (continuing)" and carries
    # on: a TLS listener that half-starts and is skipped past is precisely the
    # vacuity salesagent-tgzb exists to remove — every https scenario would then
    # silently grade the http branch. (Making the plaintext probe fail too is a
    # separate, deliberate change, not a side effect of this one.)
    echo "  waiting for $N per-worker TLS sidecars to complete a verified handshake..."
    for i in $(seq 0 $((N - 1))); do
        name="${COMPOSE_PROJECT_NAME}-tls-gw$i.adcp.test"
        wd=$(( $(date +%s) + 120 )); ok=false
        while [ "$(date +%s)" -lt "$wd" ]; do
            docker exec "$name" curl -sf --cacert /app/.test-tls/ca.pem "https://$name:8443/health" >/dev/null 2>&1 && ok=true && break
            sleep 2
        done
        if [ "$ok" != true ]; then
            echo "ERROR: TLS sidecar $name never completed a verified handshake at https://$name:8443/health" >&2
            docker logs "$name" 2>&1 | tail -30 >&2 || true
            exit 1
        fi
        echo "    tls-gw$i ready ($name)"
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
# Best-effort backstop, NOT a guarantee: chmod only succeeds on paths this
# user (the launcher) already owns -- it silently no-ops (EPERM, swallowed by
# `|| true`) on anything a DIFFERENT uid created, e.g. files adcp-server (uid
# 1001) writes into this bind mount. That case needs the file recreated by
# us, not chmod'd -- see the logs/ block above for the pattern. This sweep
# still earns its keep for paths WE created with a too-strict umask.
chmod -R g+w . 2>/dev/null || true
chmod -R go-w .git 2>/dev/null || true

dc run --rm --use-aliases $E2E_ENV_ARGS tests tox -e "$SUITES" || RC=$?

# tox writes per-suite JSON into /app/.tox, which is the `tox_data` NAMED VOLUME
# (docker-compose.e2e.yml, restored along with the removal of the `tests.user`
# pin -- as root the daemon-created root:root mountpoint is writable again, so
# tox envs stay off the slow bind-mounted host tree). The HOST's ./.tox is
# therefore empty, and the reports must be extracted FROM THE VOLUME with a
# throwaway container before the cleanup trap's `down -v` destroys it.
#
# Do NOT "simplify" this back to `cp .tox/*.json` (Aug 2026): that host-side
# form belongs to the no-volume shape and, paired with the volume, copies
# NOTHING -- and if a previous run left stale JSON on the host it silently
# copies THOSE, producing a fresh, plausible, timestamped results directory
# holding an older run's green numbers. That exact substitution was observed
# live: two runs that executed ZERO suites still emitted full six-suite
# "passing" report directories. The pairing is load-bearing; the volume mount
# and this extraction have to move together.
echo "Extracting JSON reports from the tox_data volume..."
# Loud, not silent (Aug 2026): this used to be `2>/dev/null || true`, which
# once ate a real failure completely silently -- a full 23-minute run
# finished clean (exit 0, all 7 suites really passed, .tox/*.json all
# present and correct) but test-results/ never got populated, with zero
# trace of why. Re-mkdir defensively right before copying (idempotent, cheap
# insurance against $RESULTS_DIR having been removed or never created for
# any reason) and let a real failure actually say something instead of
# vanishing 23 minutes of work without a trace.
mkdir -p "$RESULTS_DIR"
if ! docker run --rm \
    -v "${COMPOSE_PROJECT_NAME}_tox_data:/t:ro" \
    -v "$(pwd)/${RESULTS_DIR}:/out" \
    alpine sh -c 'cp /t/*.json /out/'; then
    echo "WARNING: failed to extract JSON reports into $RESULTS_DIR/ -- see error above." >&2
    echo "         They are in the ${COMPOSE_PROJECT_NAME}_tox_data volume until this run's" >&2
    echo "         cleanup trap runs \`down -v\`; copy them out now if you need them." >&2
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
    # stderr is KEPT. It used to go to /dev/null, which made this step the only
    # one in the script that can fail the whole run while destroying the reason:
    # three consecutive in-network runs exited 1 here with a green suite and left
    # nothing to diagnose, and the same lockfile audits clean (243 deps, no
    # vulnerabilities) in every isolated reproduction — the VM host, the tests
    # image, and the supervisor image. Whatever differs is visible only in the
    # stream that was being discarded. A failure loud enough to fail the run must
    # be loud enough to explain itself.
    audit_log=$(mktemp)
    if ./scripts/security-audit.sh --no-check-uv-tool >"$audit_log" 2>&1; then
        echo "Security audit passed"
    else
        # FIRST statement in this branch: $? is still the audit's status here.
        # One echo earlier and it would report that echo instead — the same
        # class of self-erasing diagnostic this change exists to remove.
        audit_rc=$?
        echo "Security audit FAILED (exit $audit_rc) — run: ./scripts/security-audit.sh"
        echo "--- security audit output ---"
        cat "$audit_log"
        echo "--- end security audit output ---"
        [ "$RC" -eq 0 ] && RC=1
    fi
    rm -f "$audit_log"
fi

exit $RC
