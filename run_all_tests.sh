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
# No TEST_UID/TEST_GID export, deliberately. docker-compose.e2e.yml sets no
# `user:` on the tests service: under the rootless daemon the run boxes use,
# container root already maps to the invoking user, so everything written into
# the bind-mounted repo is owned by whoever launched the run, with no uid
# plumbing at all. Exporting `id -u` here actively broke that -- rootless maps a
# non-zero container uid to a host SUBUID, which is what left /app/logs
# unwritable and killed adcp-server at import.
# The delivery-webhook scheduler runs on the SERVER (adcp-server), gated by this
# interval. docker-compose.e2e.yml defaults it empty (scheduler off); the host
# e2e path sets it to 5 via conftest. Mirror that so test_daily_delivery_webhook
# gets a report. Compose interpolates this into the adcp-server service env.
export DELIVERY_WEBHOOK_INTERVAL="${DELIVERY_WEBHOOK_INTERVAL:-5}"

# Parallelism defaults, sized against DOCKER'S memory rather than the host's.
#
# Every knob below feeds processes that each import the whole application, so the
# binding constraint is RAM inside the Docker VM -- not host cores and not host
# RAM. On a Mac those differ wildly: 48GB host / 14 cores, but Docker Desktop
# defaults its VM to ~16GB. Sizing from `nproc` alone is what produced this,
# measured on exactly that machine with E2E_WORKERS=4 + unit=14 + integration=4:
#
#     bdd_inprocess: FAIL code -9      <- SIGKILL, the VM OOM-killer
#     e2e:           FAIL code -9
#     unit.json:        collected 5894 but reported 0
#     integration.json: collected 2324 but reported 0
#
# (The truncation check further down is what surfaced that as a failure rather
# than a green run with two silently empty suites.)
#
# tox -p runs the suites CONCURRENTLY, so the peak is the SUM of every suite's
# workers plus the per-worker server stacks -- which is why each knob cannot be
# chosen in isolation. Tiers are keyed on the Docker VM's MemTotal, with the
# CI-box tier matching what is measured green in-network (16/16/8 at 86-196GB).
_docker_mem_gb="$(docker info --format '{{.MemTotal}}' 2>/dev/null || echo 0)"
# `{{.MemTotal}}` renders the literal string `<no value>` when the daemon does
# not report it. Under `set -euo pipefail` the arithmetic below then aborts the
# whole run before a single suite starts ("syntax error: operand expected"), so
# anything non-numeric falls back to the laptop tier rather than killing the
# run. Empty output is already safe (0 -> laptop tier), which is why the
# stubbed-docker test never caught this.
[[ $_docker_mem_gb =~ ^[0-9]+$ ]] || _docker_mem_gb=0
_docker_mem_gb=$(( _docker_mem_gb / 1073741824 ))
_cores="$( (nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4) )"

if [ "$_docker_mem_gb" -ge 64 ]; then
    # CI box. Per-worker e2e stacks are affordable; cores are the ceiling again.
    _unit=$(( _cores > 16 ? 16 : _cores )); _integration=8; _e2e_workers=8
elif [ "$_docker_mem_gb" -ge 32 ]; then
    _unit=$(( _cores > 8 ? 8 : _cores )); _integration=4; _e2e_workers=2
else
    # Developer laptop (a default Docker Desktop VM lands here). No per-worker
    # server stacks: four app-loading containers alongside the suites is what
    # OOM-killed the run above. Unit still parallelises -- it has no database and
    # is the largest suite -- but at half the cores, since it shares the VM with
    # every other suite tox -p starts at the same time.
    _unit=$(( _cores / 2 )); [ "$_unit" -lt 2 ] && _unit=2
    _integration=4; _e2e_workers=0
fi

export UNIT_XDIST_N="${UNIT_XDIST_N:-$_unit}"
# Integration is safe to parallelise at any tier: tests/conftest_db.py's
# `integration_db` fixture creates a uuid-named database PER TEST, so workers
# never share rows, and tox.ini runs this env with `--dist loadfile`.
export INTEGRATION_XDIST_N="${INTEGRATION_XDIST_N:-$_integration}"
# BDD parallelism comes from E2E_WORKERS, NOT from BDD_XDIST_N directly.
# docker-compose.e2e.yml sets BDD_E2E_ENABLED=true, and tests/bdd/conftest.py
# raises a UsageError for that together with -n>0 unless E2E_PER_WORKER=1 --
# because under xdist the e2e_rest transport is silently dropped at collection and
# the suite would go green without ever having run it. Setting E2E_WORKERS>0 takes
# the fast path below, which splits `bdd` into `bdd_inprocess` (e2e disabled,
# freely parallel) plus `bdd_e2e`, and provisions N per-worker server+DB stacks so
# e2e_rest can run in parallel legally.
export E2E_WORKERS="${E2E_WORKERS:-$_e2e_workers}"
# stderr, not stdout: RUN_ALL_TESTS_RESOLVE_ONLY makes stdout a machine-read
# contract (tests/unit/test_run_all_tests_contract.py parses it), so diagnostics
# must not land there.
echo "Parallelism: docker_mem=${_docker_mem_gb}GB cores=${_cores} -> unit=$UNIT_XDIST_N integration=$INTEGRATION_XDIST_N e2e_workers=$E2E_WORKERS" >&2
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
    #
    # A REAL NUMBER, not `auto`. `auto` resolves through
    # PYTEST_XDIST_AUTO_NUM_WORKERS, which docker-compose.e2e.yml pins to 1 for
    # BDD's own benefit -- so `auto` here meant ONE worker for every caller that
    # does not separately export that variable, and the ~23m->~3.5m in-process win
    # silently never landed. Matching E2E_WORKERS keeps the two halves of the split
    # in proportion; both are overridable.
    export BDD_XDIST_N="${BDD_XDIST_N:-$E2E_WORKERS}"
    echo "Fast bdd path: E2E_WORKERS=$E2E_WORKERS BDD_XDIST_N=$BDD_XDIST_N -> suites=$SUITES"
fi

# UTC, not local: the runner box and the machine reading the reports are in
# different zones, so a local-time directory name means each side computes a
# different one and the results cannot be attributed to the run that produced
# them ("no confirmed run identity to attribute local test-results/ to").
RESULTS_DIR="test-results/innet_$(date -u +%d%m%y_%H%M)"
mkdir -p "$RESULTS_DIR"

# Per-worker timing profile, ON by default and written INTO the results dir so it
# travels with the run's JSON reports and is fetched alongside them.
#
# It was originally opt-in via a bare PYTEST_WORKER_PROFILE, which does not work
# for the caller that matters: cassini forwards a hardcoded 9-variable
# FORWARD_ENV tuple to the box, and any variable outside it is silently dropped.
# Measured -- a sweep launched with PYTEST_WORKER_PROFILE set produced no profile
# directory at all on the remote. A knob that only works locally cannot answer a
# question about the CI box, which is the only place the worker counts are
# interesting.
#
# Cost is a handful of time.time() calls plus one small JSON write per worker at
# session end; the per-report hook is a single float addition. Cheap enough to be
# unconditional, and unconditional is what makes it there when a slow run needs
# explaining after the fact rather than only when someone predicted it.
export PYTEST_WORKER_PROFILE="${PYTEST_WORKER_PROFILE:-/app/$RESULTS_DIR/worker-profile}"

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

# Pre-create logs/ group-writable + setgid BEFORE anything else touches the
# bind mount: adcp-server bind-mounts .:/app and creates logs/audit.log at
# import time (uid 1001, its own baked umask) -- when that umask strips the
# group-write bit the tests container (a different uid, 1003 here) can create
# the dir but not write into it, and every suite dies at collection with
# `PermissionError: '/app/logs/audit.log'`. Owning it here first means
# adcp-server writes into an already-correct dir instead of racing to
# create it (confirmed live: sa-93d37d7c, sa-c9acaf66 both landed
# drwxr-sr-x -- not group-writable -- and are latent failures until fixed).
# This must stay ahead of the TLS step below too: that step's fallback runs a
# `tests` container, which would otherwise be the one to create logs/ first.
mkdir -p logs
# Guarded, not silent: chmod on a logs/ that already exists owned by ANOTHER
# uid fails with EPERM, and a bare `chmod` here would abort the whole script
# under `set -e` -- so tolerate the failure, but do NOT assume it worked. The
# verification below is what turns a still-broken state into a diagnosable
# error instead of a collection-time PermissionError 200 lines later.
chmod 2775 logs 2>/dev/null || true
# The setgid bit above fixes the GROUP of files created in here, but NOT their
# write bit -- that comes from the creating process's umask, and adcp-server's
# yields 0644. So a FRESH logs/ still ends up with `-rw-r--r-- ci:ci`
# audit.log, and the tests container (a different uid in the same `ci` group)
# dies at collection with `PermissionError: '/app/logs/audit.log'`. The
# `chmod -R g+w .` backstop further down cannot repair it either: it runs as
# the sync user, which does not OWN a ci-created file, so the chmod fails and
# is swallowed by its `2>/dev/null || true`. Pre-create the files ourselves so
# the server APPENDS to an already-group-writable file instead of creating one
# with its own umask. Runs before every `dc up`/`dc run` for the same reason
# the mkdir does. (Observed live: sa-067cc4a9 failed exactly this way on a
# fresh run dir, while sa-858f3b3a passed only because it inherited a stale
# 0664 logs/ from an earlier run -- i.e. this was always latent, and green
# runs were green by accident.)
# rm first, then recreate: on a REUSED run directory the existing files are
# owned by the server's uid, so `touch` (needs write) and `chmod` (needs
# ownership) both fail on them -- and under `set -e` that would abort the whole
# script. Unlinking works regardless of file ownership because it is the
# DIRECTORY's write bit that governs it, and we own the directory. These are
# per-run scratch logs, so discarding a previous run's copy costs nothing.
# The list is exactly what src/core/audit_logger.py opens: audit.log (its
# FileHandler), error.log (its error FileHandler), and the two append-mode
# sinks structured.jsonl and security.jsonl. security.jsonl only appears on a
# security event, so it is the one that hides longest before biting.
for _log in audit.log error.log structured.jsonl security.jsonl; do
    rm -f "logs/$_log" 2>/dev/null || true
    : >"logs/$_log" 2>/dev/null || true
    # 0666, not 0664. Group-write is NOT enough, and assuming it was is what
    # kept the e2e stack down: `adcp-server` runs as the image's own non-root
    # user, and that user shares no group with whoever created these files.
    # Measured on the box: the container is `app` uid=1001 gid=1001 groups=1001,
    # while the files land `sacirunner:sacirunner` (or `ci`) — so 0664 leaves the
    # server with OTHER permissions, r--, and it dies opening audit.log:
    #   PermissionError: [Errno 13] Permission denied: '/app/logs/audit.log'
    # The whole stack follows it down — nothing binds :8000, so every e2e test
    # errors "Server not ready after 60s (port 8000)" and every e2e_rest BDD
    # scenario errors "the live E2E stack is unreachable", with no hint that a
    # file mode is the cause. chown to 1001 would need root we do not have here;
    # these are per-run scratch logs, so world-writable is the honest fix.
    chmod 666 "logs/$_log" 2>/dev/null || true
    # Verify rather than hope. `-w` would test OUR access; what actually matters
    # is the OTHER write bit, since the server's uid is outside our groups. find
    # -perm is used over `stat` because stat's flags differ between the GNU
    # coreutils on the CI box and the BSD one on a macOS host, and this script
    # runs on both.
    if [ -z "$(find "logs/$_log" -perm -o+w 2>/dev/null)" ]; then
        echo "ERROR: logs/$_log is not writable by other; adcp-server runs as a" >&2
        echo "       non-root uid outside our groups and will die at startup with" >&2
        echo "       PermissionError, taking the whole e2e stack with it. State:" >&2
        ls -la "logs/$_log" logs/ >&2 || true
        exit 1
    fi
done

# TLS material for the tls-proxy service and the per-worker sidecars below. It
# must exist before `up`: the service bind-mounts .test-tls/, and an absent
# directory would materialise empty and nginx would refuse to start. The host
# wrapper needs a Python with `cryptography`; the in-network CI job has neither
# uv nor the project venv on the host, so fall back to the runner image (which
# writes through the same `.:/app` bind mount).
echo "Ensuring the test stack's TLS material..."
scripts/dev/ensure-test-tls.sh || dc run --rm --no-deps -T tests python scripts/dev/gen_test_tls.py

# Bring up Postgres + the app server + proxy + the TLS listener + the pinned
# creative-agent (and its own registry Postgres). None publish host ports —
# all reached by service name. tls-proxy is in this explicit list
# deliberately: it is a normal `up` service, and omitting it would leave both
# https origins it fronts (proxy.adcp.test, creative-agent.adcp.test) pointing
# at nothing while every scenario that depends on either reported green on the
# http branch instead (E2E_TLS_BASE_URL / CREATIVE_AGENT_URL, salesagent-amht.2).
dc up -d postgres adcp-server proxy tls-proxy creative-pg creative-agent

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
# $srv gets the SAME fail-fast treatment as $pg. It used to be computed, printed
# on success, and then never checked -- so a stack whose server never became
# healthy within the 360s deadline proceeded silently into every server-dependent
# suite. That is not a smaller failure than a dead Postgres, it is a louder one:
# bdd_e2e/e2e/ui then emit thousands of "live E2E stack is unreachable" /
# "Server not ready after 60s" / TargetClosedError errors, none of which names
# the actual cause. Measured on 2026-08-24: the wait spun its full 360s, said
# only "Postgres ready", and the suites then produced 2537 errors -- one root
# cause, zero of them a real defect. Infrastructure death must present as ONE
# infrastructure failure, reported once.
#
# Unconditional because THIS path just started the stack a few lines above: if we
# brought the server up and it never became healthy, that is a failure for every
# caller, not a caller-specific one. Symmetric with the Postgres guard by design;
# an asymmetry here is what hid the problem.
[ "$srv" = true ] || {
    echo "ERROR: adcp-server never became healthy within the 360s deadline — aborting" >&2
    echo "       (waited on http://localhost:8080/health inside adcp-server; every" >&2
    echo "        server-dependent suite would otherwise error en masse)" >&2
    dc logs --tail=120 adcp-server >&2
    exit 1
}

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
    # Two admin-SQL helpers, deliberately NOT one. The old single helper ended in
    # `|| true` and discarded both streams, so a failed CREATE DATABASE was
    # indistinguishable from a successful one. The failure then resurfaced four
    # steps later as the opaque "e2e template migration failed", whose real cause
    # (`database "adcp_e2e_template" does not exist`) was unrecoverable from any
    # log. A DROP that fails is tolerable; a CREATE that fails is not.
    _psql_admin() { dc exec -T postgres psql -U adcp_user -d postgres -v ON_ERROR_STOP=1 -c "$1" 2>&1; }
    # Tolerant: reports, then continues. For DROP ... IF EXISTS, where "it was
    # already gone" and "the server refused" are both survivable.
    psql_admin_try() {
        local out
        if ! out=$(_psql_admin "$1"); then
            echo "WARNING: admin SQL failed (continuing): $1" >&2
            echo "$out" >&2
        fi
    }
    # Fatal: a database the rest of this block depends on either exists or we stop.
    psql_admin() {
        local out
        if ! out=$(_psql_admin "$1"); then
            echo "ERROR: admin SQL failed: $1" >&2
            echo "$out" >&2
            exit 1
        fi
    }
    echo "Provisioning $N per-worker e2e server stacks..."
    # A crashed previous run leaves backends attached to the template, and
    # DROP DATABASE refuses while any connection remains. Evict them first so the
    # DROP below fails only for reasons worth reporting.
    psql_admin_try "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'adcp_e2e_template' AND pid <> pg_backend_pid()"
    psql_admin_try "DROP DATABASE IF EXISTS adcp_e2e_template"
    psql_admin "CREATE DATABASE adcp_e2e_template"
    # Fail fast: if the template migration fails, every per-worker DB below would
    # be cloned from an un-migrated template and the whole e2e_rest pass would
    # error confusingly. Surface it here instead -- WITH the migrator's own output,
    # which this block used to throw away.
    _migrate_log="$(mktemp)"
    if ! dc run --rm --no-deps -e DATABASE_URL="$_admin/adcp_e2e_template?sslmode=disable" \
            tests python scripts/ops/migrate.py >"$_migrate_log" 2>&1; then
        echo "ERROR: e2e template migration failed — aborting per-worker provisioning" >&2
        echo "--- migrate.py output ---" >&2
        cat "$_migrate_log" >&2
        rm -f "$_migrate_log"
        exit 1
    fi
    rm -f "$_migrate_log"
    for i in $(seq 0 $((N - 1))); do
        psql_admin_try "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'adcp_gw$i' AND pid <> pg_backend_pid()"
        psql_admin_try "DROP DATABASE IF EXISTS adcp_gw$i"
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
    # Same fail-fast reasoning as the template-migration check above: a worker
    # whose server never came up cannot run a single e2e_rest scenario, so
    # "(continuing)" only converts one infrastructure fault into a flood of
    # scenario errors attributed to the wrong layer. Collect all of them first
    # (one pass, so the operator sees every unhealthy worker rather than just
    # the first) and then abort with the count.
    _unhealthy=""
    for i in $(seq 0 $((N - 1))); do
        wd=$(( $(date +%s) + 120 )); ok=false
        while [ "$(date +%s)" -lt "$wd" ]; do
            docker exec "${COMPOSE_PROJECT_NAME}-server-gw$i" curl -sf http://localhost:8080/health >/dev/null 2>&1 && ok=true && break
            sleep 2
        done
        if [ "$ok" = true ]; then
            echo "    server-gw$i ready"
        else
            echo "    server-gw$i NOT ready"
            _unhealthy="$_unhealthy gw$i"
        fi
    done
    if [ -n "$_unhealthy" ]; then
        echo "ERROR: per-worker e2e server(s) never became healthy:$_unhealthy" >&2
        echo "       aborting — these workers' scenarios would error en masse and" >&2
        echo "       be misread as test failures rather than a stack failure." >&2
        for i in $_unhealthy; do
            echo "--- logs: ${COMPOSE_PROJECT_NAME}-server-$i ---" >&2
            docker logs --tail 40 "${COMPOSE_PROJECT_NAME}-server-$i" >&2 2>&1 || true
        done
        exit 1
    fi
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
# PARALLEL (`-p`): this was serial from 2026-06-18
# to 2026-08-16 over an OOM observed running all six suites concurrently in one
# container, where bdd's `-n auto` alone could spawn one worker per host CPU
# (~17), each loading the app. That OOM predates PYTEST_XDIST_AUTO_NUM_WORKERS
# (added the same day, in response) ever reaching this container correctly —
# a real, separate export-plumbing bug meant the cap this
# comment used to cite was, for some callers, never actually applied. With
# that bug fixed and the cap now confirmed to genuinely reach the container,
# a real, monitored, disposable-worktree run of the full 7-suite `-p` (unit,
# integration, bdd_inprocess, bdd_e2e, admin, e2e, ui) measured peak memory at
# ~35GB of the box's 86.4GB (40.5%), no OOM, pass/fail counts matching a
# serial baseline, measured on a full in-network run of all 7 suites.
echo "Running suites in-network (parallel): $SUITES"
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

# Delete every previous run's report BEFORE this run writes its own, and do it in
# exactly ONE place. `.tox/` is an ordinary persistent bind-mounted directory now
# (see the extraction note below), so a report left there outlives the run that
# wrote it. The copy below is per-suite -- it copies only the envs named in
# $SUITES -- which already stops an env this run never executed from being
# republished. What the copy cannot do is tell a report THIS run wrote from one a
# prior run left behind for a suite that DID run and died before writing its own.
# Purging first makes a stale report unrepresentable rather than merely
# detectable: after this line, a report exists only if this run produced it, so a
# suite that died reaches the missing-report arm below instead of quietly
# republishing its last PASS forever.
#
# Blanket (`.tox/*.json`), not a $SUITES-scoped loop: a scoped loop leaves exactly
# the not-run envs' reports sitting in `.tox/`, which is the class of staleness
# documented at the copy below (`storyboard.json` republished for three runs). It
# also bit `bdd`, which is swapped out for `bdd_inprocess,bdd_e2e` whenever
# E2E_WORKERS>0 (see above): a stale bdd.json from whenever `tox -e bdd` last ran
# kept being republished -- one was a full DAY older than its directory-mates and
# reported 6 failures against code that no longer existed, read as a live
# regression. Purging wholesale plus copying per-suite closes both directions.
rm -f .tox/*.json

dc run --rm --use-aliases $E2E_ENV_ARGS tests tox -p -e "$SUITES" || RC=$?

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
#
# Copy ONLY the suites THIS invocation ran, never `.tox/*.json` wholesale.
# `.tox` used to be the tox_data NAMED VOLUME, destroyed by the cleanup trap's
# `down -v` every run, so a wholesale copy could not pick up anything stale.
# Removing that volume (so the non-root runner could write `.tox/<env>`) made
# `.tox` an ordinary bind-mounted directory that PERSISTS between runs -- and
# silently turned the same wholesale copy into a stale-report generator: a suite
# this run never executed still contributes its last report, and downstream
# tooling renders it as freshly measured.
#
# That is not hypothetical. `storyboard` is an opt-in env (not in tox's
# env_list), so a bare `./run_all_tests.sh` never runs it -- yet three
# consecutive runs published a storyboard.json from hours earlier, and the
# numbers were read as this run's until an SDK version inside the report
# contradicted the SDK that was actually installed.
#
# A missing report for a suite that DID run is an error, not an omission: it
# means the suite died before writing one, which is exactly when its absence
# most needs to be loud.
mkdir -p "$RESULTS_DIR"
# Record WHICH suites this invocation ran, next to their reports. Consumers
# cannot infer it: report timestamps do not separate "stale" from "ran early in
# a long serial run" (measured: a genuine unit report was 16 min behind the
# newest, a stale storyboard one 28 min — overlapping bands, so any threshold
# misfires both ways). An explicit manifest is exact.
printf '%s\n' "$SUITES" > "$RESULTS_DIR/.suites"
_missing_reports=""
for _suite in ${SUITES//,/ }; do
    if [ -f ".tox/${_suite}.json" ]; then
        cp ".tox/${_suite}.json" "$RESULTS_DIR/" || _missing_reports="$_missing_reports $_suite(copy-failed)"
    else
        _missing_reports="$_missing_reports $_suite"
    fi
done
if [ -n "$_missing_reports" ]; then
    echo "ERROR: no JSON report for suite(s):$_missing_reports" >&2
    echo "       The suite ran but produced no report -- it died before writing one." >&2
    echo "       Reports are how this run is graded; a suite that produced none was not measured." >&2
    # The comment above already calls this "an error, not an omission". The code
    # said RC=${RC:-0}, which leaves the exit code untouched — so a dead suite
    # produced a GREEN run, and the file that publishes the numbers disagreed
    # with its own docstring.
    [ "$RC" -eq 0 ] && RC=1
fi
echo "Reports: $RESULTS_DIR/  (suites: $SUITES)"
ls -1 "$RESULTS_DIR"/*.json 2>/dev/null || echo "  (no JSON reports extracted)"

# The parallelism evidence, in the log beside the reports. The profile records
# were written on every run and nothing read them; this is the reader. It fails
# only when a suite's workers disagree on what they collected -- a
# self-contradiction under `--dist load`, where every worker collects the whole
# suite, and therefore a check that needs no baseline. It sets no threshold on
# startup or idle: a threshold is a ratchet and a ratchet needs a baseline.
if ! python3 scripts/ci/report_worker_profile.py "$RESULTS_DIR/worker-profile"; then
    [ "$RC" -eq 0 ] && RC=1
fi

# A truncated suite is a failed suite, and the whole point is that it must not
# be mistakable for a green one. The predicate is shared with
# run_all_tests_host.sh -- see scripts/check_truncated_reports.py for why it
# lives in its own file rather than inline here.
if ls "$RESULTS_DIR"/*.json >/dev/null 2>&1; then
    if ! python3 scripts/check_truncated_reports.py "$RESULTS_DIR"; then
        RC=1
    fi
fi

# Reconcile a non-zero exit against what the suites actually reported. This does
# NOT change the exit code -- masking a failure is how the real cause of a dead
# run became unknowable before (the `psql_admin() { ...; } || true` and the
# discarded migrate.py output). It only says WHICH layer failed, because
# "exit 125, no explanation, all suites OK" sends you hunting for a test bug
# that does not exist.
#
# Docker reserves 125/126/127 for "the runner itself failed" (125 = the daemon
# or CLI could not run/wait on the container) as distinct from any code the
# command inside returned. A long `-p` run that drops its CLI connection at the
# end lands here with every suite already finished and every report written.
if [ "$RC" -ne 0 ] && ls "$RESULTS_DIR"/*.json >/dev/null 2>&1; then
    if python3 - "$RESULTS_DIR" <<'PYEOF'
import glob, json, os, sys
bad = []
for f in sorted(glob.glob(os.path.join(sys.argv[1], "*.json"))):
    try:
        s = json.load(open(f)).get("summary", {})
    except Exception:
        bad.append(f"{os.path.basename(f)}: unreadable")
        continue
    if s.get("failed") or s.get("error"):
        bad.append(f"{os.path.basename(f)}: failed={s.get('failed', 0)} error={s.get('error', 0)}")
sys.exit(1 if bad else 0)
PYEOF
    then
        echo ""
        echo "NOTE: exit code is $RC but every suite report shows 0 failures and 0 errors."
        case "$RC" in
            125|126|127)
                echo "      $RC is Docker's own 'could not run/wait on the container' range, not a"
                echo "      test result -- the suites finished and wrote their reports first."
                echo "      Look at the runner (daemon connection, container lifetime), not the tests." ;;
            *)
                echo "      The failure is therefore OUTSIDE the suites: check the report-copy step"
                echo "      and the security audit above." ;;
        esac
        echo "      Exit code is left as-is on purpose: this is a diagnostic, not a downgrade."
        echo ""
    fi
fi

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
