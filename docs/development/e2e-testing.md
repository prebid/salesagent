# End-to-end testing

This guide explains how this repository tests against a live, containerized
server. It covers what the stack is, which suites drive it, how they share and
protect its database, and how to debug a failing run. Start with the first
section: the most common e2e mistake here is to run the wrong suite, then draw
a conclusion from a passing result that never tested your change.

## Two suites named "e2e"

"E2E" names two different things in this repository. They share the Docker
stack but verify different contracts, run under different tox environments,
and fail for different reasons.

**`tests/e2e/` — the protocol pytest suite.**
The `test_*.py` modules under `tests/e2e/` are ordinary pytest modules that
exercise the live server over real HTTP through nginx: full Ad Context
Protocol (AdCP) lifecycle flows, A2A protocol compliance, delivery webhooks,
tenant isolation, and schema compliance. Runs as `tox -e e2e`.

**`e2e_rest` — a wire transport for the behavior-driven development (BDD)
suite.**
The BDD suite (`tests/bdd/`) runs every generated scenario across the wire
transports `a2a`, `mcp`, and `rest` in-process. When `BDD_E2E_ENABLED=true`,
`pytest_generate_tests` in `tests/bdd/conftest.py` appends a fourth
parametrization, `e2e_rest`. That parametrization replays the same scenario as
real HTTP against the live server. Runs as `tox -e bdd_e2e` (which runs
`pytest tests/bdd/ -k e2e_rest`); on a full run the in-process transports run as
the shards `bdd-inprocess-s1` to `bdd-inprocess-s4`, published as one
`bdd_inprocess` report.

The distinction matters because a passing result only certifies what ran.
`tox -e e2e` passing says nothing about BDD scenario conformance over the
wire. The in-process shards passing says nothing about the live server,
because those environments set `BDD_E2E_ENABLED=false`. If you changed server
behavior that a BDD scenario covers, the suite that must pass is `bdd_e2e`.

## The stack

Both suites talk to the same compose stack, defined in
`docker-compose.e2e.yml`:

| Service | Role | Why it exists |
|---------|------|---------------|
| `postgres` | One Postgres instance holding every database the run uses | Single DB service; isolation comes from database names, not containers |
| `adcp-server` | The application under test (FastAPI, all protocols on one port) | The system under test |
| `adcp-server-storyboard` | A second copy of the same image, `ENVIRONMENT: production`, waiting on `adcp-server` to be healthy | The conformance storyboards grade a production-mode deployment; sequencing on health keeps two boots from running `alembic upgrade` concurrently |
| `proxy` | nginx in front of `adcp-server`, plaintext `:8000` | Tests go through the same reverse proxy production does |
| `tls-proxy` | A second nginx listener terminating TLS on `:8443` for the `*.adcp.test` origins | Serves real HTTPS from a generated private certificate authority (CA), so https behavior is true rather than asserted |
| `webhook-capture` | Long-lived webhook receiver (`tests/e2e/webhook_capture_service.py`) | Captures the server's outbound webhook deliveries; delivery arrives over TLS, readback is a plain-HTTP test control plane |
| `creative-agent` + `creative-pg` | A pinned in-network creative agent with its own registry DB | The live public agent drifts and rate-limits; `adcp-server`'s `extra_hosts` blackholes it so a regression fails instantly |
| `tests` | The in-network test runner (profile `runner`) | Runs tox inside the compose network so suites reach services by name |

The following diagram shows an in-network run: every arrow is traffic on the
stack's own bridge network, addressed by service name or dotted alias — no
host ports exist. The `tls-proxy` service fronts every HTTPS origin on one
listener — four dotted aliases — and routes by Server Name Indication (SNI).
That is what makes the server's outbound calls real HTTPS. The SNI map is
`config/nginx/nginx-tls-test.conf.template:39-44`; read the alias list there
rather than from this diagram.

```mermaid
flowchart LR
    subgraph network["Compose bridge network — one per project, no published host ports"]
        Runner["tests runner\nruns tox"]
        Proxy["proxy\nnginx :8000, plaintext"]
        TLSP["tls-proxy\nnginx :8443, TLS\naliases: proxy.adcp.test,\ncreative-agent.adcp.test,\nwebhooks.adcp.test,\nstoryboard.adcp.test"]
        Server["adcp-server\napp under test :8080"]
        WH["webhook-capture\n:8080"]
        CA["creative-agent\n:8080"]
        CPG["creative-pg\nadcp_registry"]
        PG["postgres\ndatabases adcp + adcp_test"]
    end

    Runner -->|"E2E_BASE_URL\nhttp://proxy:8000"| Proxy
    Runner -->|"E2E_TLS_BASE_URL\nhttps://proxy.adcp.test:8443"| TLSP
    Runner -->|"postgres:5432\nboth databases"| PG
    Runner -->|"readback, plain HTTP\nwebhook-capture:8080"| WH
    Proxy -->|"adcp-server:8080"| Server
    Server -->|"database adcp"| PG
    Server -->|"outbound HTTPS\ncreative-agent.adcp.test:8443\nwebhooks.adcp.test:8443"| TLSP
    TLSP -->|"SNI proxy.adcp.test"| Server
    TLSP -->|"SNI creative-agent.adcp.test"| CA
    TLSP -->|"SNI webhooks.adcp.test"| WH
    CA --> CPG
```

Three properties of this stack determine everything else in this guide.

**The base compose file publishes no host ports.** You reach every service by
service name (`postgres:5432`, `proxy:8000`) on the stack's own bridge
network. Each `docker compose -p <project>` invocation gets an isolated
network, so any number of stacks can run concurrently with zero port
coordination. The paths that run pytest on the host instead overlay
`docker-compose.e2e.ports.yml`, which publishes loopback ports
(`POSTGRES_PORT`, `ADCP_SALES_PORT`, `ADCP_TLS_PORT`,
`WEBHOOK_CAPTURE_PORT`). Never add that overlay to the in-network runner —
adding it reintroduces the cross-stack port collisions that the in-network
design eliminates.

The following diagram contrasts the two ways a test process reaches the same
stack. The in-network runner needs no published ports; the host paths publish
loopback ports through the overlay.

```mermaid
flowchart LR
    subgraph innet["In-network path — ./run_all_tests.sh"]
        Runner2["tests runner container\ninside the stack's network"]
    end
    subgraph hostpath["Host paths — make test-stack-up, quick, ci target"]
        HostPy["pytest / tox on the host"]
        Overlay["docker-compose.e2e.ports.yml\npublishes 127.0.0.1 ports:\nPOSTGRES_PORT, ADCP_SALES_PORT,\nADCP_TLS_PORT, WEBHOOK_CAPTURE_PORT"]
    end
    Stack["Compose stack\ndocker-compose.e2e.yml:\npostgres, adcp-server, proxy,\ntls-proxy, webhook-capture,\ncreative-agent"]

    Runner2 -->|"service names\nproxy:8000, postgres:5432"| Stack
    HostPy --> Overlay
    Overlay -->|"published loopback ports"| Stack
```

**The stack serves real TLS.** `scripts/dev/ensure-test-tls.sh` generates a
private CA and a leaf certificate covering `*.adcp.test` into `.test-tls/`
before any `up`. `tls-proxy` then serves every HTTPS origin the stack connects
to (`proxy.adcp.test`, `creative-agent.adcp.test`, `webhooks.adcp.test`,
`storyboard.adcp.test`)
through dotted network aliases on one service. Startup performs a verified
handshake against the CA, so a listener that only partially starts is a hard
failure rather than a silent fallback to plaintext. For the egress reasoning
behind this design — why the stack serves every outbound origin over TLS, and
what the outbound HTTP layer refuses — read
[Outbound egress](../security/outbound-egress.md); don't re-derive it in this
guide.

**The stack enables the private-address override.** Every origin on a compose
network resolves to a private bridge address, so `adcp-server` runs with
`ADCP_OUTBOUND_ALLOW_PRIVATE=true`. The consequence: no e2e or `bdd_e2e`
scenario in this stack can verify a private-range egress refusal. That
verification stays in-process, in `tests/integration/test_outbound_http.py`
and the BDD refusal scenarios, which disable the override in each case. Scheme
refusals and the supplement-range refusals remain testable against the live
stack. For the full posture, read
[Outbound egress](../security/outbound-egress.md).

## Two databases, one Postgres

The `postgres` service holds two databases with different owners:

- `/adcp` — the **server's** database. `adcp-server` reads and writes it; it
  is the state behind every HTTP response an e2e test sees.
- `/adcp_test` — the **suite** database. Integration, in-process BDD, and
  admin tests provision and query it directly.

The suites that drive the live server must look at the server's database, not
the suite one. The `[testenv:e2e]` block in `tox.ini` does this with a
per-suite override:

```ini
DATABASE_URL = {env:E2E_DATABASE_URL:{env:DATABASE_URL:}}
```

In the in-network runner, `docker-compose.e2e.yml` exports
`E2E_DATABASE_URL=postgresql://...@postgres:5432/adcp`, so the e2e and ui
environments see `/adcp`. The integration, bdd, and admin environments share
the same runner container and keep the runner's `DATABASE_URL` of
`/adcp_test`. On the host path, `E2E_DATABASE_URL` starts unset.
`tests/e2e/conftest.py` then derives it from the stack's published Postgres
port (`localhost:<POSTGRES_PORT>/adcp`) with `setdefault`. A stale
`DATABASE_URL` inherited from another harness therefore cannot silently point
the direct-database helpers at a database the server never reads.

The following diagram shows which suite touches which database. The suites on
the left drive the live server and read or mutate its `/adcp` database. The
suites on the right never touch the live server and use `/adcp_test`.

```mermaid
flowchart TD
    subgraph liveSuites["Suites that drive the live server"]
        E2E["tox -e e2e\ntests/e2e/ protocol pytest"]
        UI["tox -e ui"]
        BDDE["tox -e bdd_e2e\nBDD e2e_rest transport"]
    end
    subgraph inprocSuites["Suites that never touch the live server"]
        BDDI["tox -e bdd-inprocess-s1..s4\ntransports a2a, mcp, rest"]
        INT["tox -e integration / admin"]
    end
    Server2["Live adcp-server\nreal HTTP via proxy"]
    ADCP["Database adcp\nthe server's database"]
    ADCPT["Database adcp_test\nthe suite database"]

    E2E --> Server2
    UI --> Server2
    BDDE --> Server2
    Server2 --> ADCP
    E2E -->|"E2E_DATABASE_URL\nseeds ci-test / iso-test"| ADCP
    UI -->|"E2E_DATABASE_URL\nseeds default tenant"| ADCP
    BDDE -->|"E2E_POSTGRES_URL\ntruncates every table,\nwrites Given-step data"| ADCP
    BDDI --> ADCPT
    INT --> ADCPT
```

Three suites mutate the server database, and they must not overlap:

- `e2e` seeds the `ci-test`/`iso-test` tenants
  (`scripts/setup/init_database_ci.py`).
- `ui` seeds the default tenant.
- `bdd_e2e` **truncates every table** before each `e2e_rest` scenario
  (`_reset_e2e_db` in `tests/bdd/conftest.py`).

`tox.ini` therefore chains them: the `ui` environment declares
`depends = e2e`, and the `bdd` and `bdd_e2e` environments declare
`depends = e2e, ui`. That serializes the server-DB group under `tox -p` while
the unit, integration, and admin environments still run fully parallel. The
ordering is required for correctness. If the group runs in parallel, the bdd
truncations land while e2e is still running and delete the tenants that e2e
seeded. The failure presents as `Tenant with subdomain 'ci-test' not found`,
and no targeted rerun reproduces it because the same tests pass alone. The
comment block above `[testenv:ui]` in `tox.ini` carries the same warning.

## The e2e_rest transport and its known-failures list

`e2e_rest` exists so that the exact scenarios that verify the in-process wire
transports also verify the deployed server — same Gherkin, same step
definitions, same assertions, dispatched as real HTTP. The `e2e_stack` fixture
in `tests/bdd/conftest.py` reads `E2E_BASE_URL` / `E2E_POSTGRES_URL` and
health-checks the server. For an `e2e_rest` test, a missing stack is a hard
error, never a skip: the transport is parametrized only when
`BDD_E2E_ENABLED=true`, so you asked for it and it must run.

Some scenarios don't pass over real HTTP.
`tests/bdd/e2e_rest_known_failures.txt` lists the node IDs that
`tests/bdd/conftest.py` marks `xfail(strict=False)` — genuine production gaps,
unseeded server-side state, or harness observability gaps. Each line names the
gap it represents, and `tests/unit/test_e2e_rest_ledger_state.py` enforces the
file's format.

**The only job that exercises the known-failures list is a bdd-in-network
run** — the "BDD In-Network (e2e_rest)" job in `.github/workflows/ci.yml`,
which runs `./run_all_tests.sh bdd_e2e`. In-process runs never run the
`e2e_rest` parametrizations, so they can neither confirm that a listed
scenario passes nor catch a regression there. When your change touches
behavior that a listed scenario covers, work through these steps:

1. Run `./run_all_tests.sh bdd_e2e`, or let the CI job run it.
2. If a listed scenario passes after your change, remove its line **in the
   same change**, following the
   [xpass graduation workflow](../../.claude/rules/workflows/xpass-graduation.md).
   Take one scenario at a time, with the inspection evidence that the workflow
   demands.
3. Treat a previously passing scenario that fails as a regression: the list
   only ever shrinks.

### A passing run that proves nothing: the xdist silent drop

This hazard earns its own section because its failure mode is a **passing**
suite.

`BDD_E2E_ENABLED=true` is incompatible with xdist. Under `-n>0`, the xdist
worker's collection never reaches the `pytest_generate_tests` state that
appends the `e2e_rest` transport, so every scenario silently collects with
only its in-process parametrizations. Nothing fails. Nothing skips. The suite
passes without ever having sent HTTP to the live server. A reader of that
passing run concludes that the deployed server conforms, when nothing ever
asked it.

`pytest_configure` in `tests/bdd/conftest.py` turns this into a hard
`pytest.UsageError`: `BDD_E2E_ENABLED=true` together with `-n>0` refuses to
start. Two configurations are exempt, each because the drop cannot go
unnoticed there:

- `E2E_PER_WORKER=1`: each xdist worker targets its **own** server container
  and database, as the "Worker counts are computed, not chosen" section
  describes, so parallel `e2e_rest` is legal. The `bdd_e2e` environment's
  `-k e2e_rest` also makes pytest exit 5 ("no tests collected") if the
  transport is ever dropped, so a silent drop cannot appear as a pass.
- `-n 0`: collection runs in-process, so the transport is parametrized
  normally. The in-network compose default sets `BDD_XDIST_N=0` for exactly
  this reason.

If you see the `UsageError`, the fix is one of those two configurations —
never unsetting the flag to silence the guard.

## Worker counts are computed, not chosen

`run_all_tests.sh` sizes every xdist worker count from the **Docker VM's**
memory (`docker info --format '{{.MemTotal}}'`) and the core count, not from
host RAM. On a Mac the two differ widely, and every worker imports the whole
application. The script defines three tiers and is the authority:

| Docker VM memory | `UNIT_XDIST_N` | `INTEGRATION_XDIST_N` | `E2E_WORKERS` |
|------------------|----------------|------------------------|---------------|
| ≥ 64 GB | min(cores, 16) | 8 | 8 |
| ≥ 32 GB | min(cores, 8) | 4 | 2 |
| below (default Docker Desktop) | cores / 2, floor 2 | 4 | 0 |

Every export uses the `${VAR:-computed}` pattern, which means **any value you
have exported silently wins, with no warning**. On a laptop-tier VM, an
exported worker count makes the VM's out-of-memory killer end suites with
`-9` (SIGKILL), which reads as unexplained test failures. If a full run exits
with code -9 or reports `collected N but reported 0`, check your environment
for stale `*_XDIST_N`/`E2E_WORKERS` exports before you suspect the tests. The
safe default is to never export these variables.

Two related settings are required for correctness:

- `docker-compose.e2e.yml` sets `PYTEST_XDIST_AUTO_NUM_WORKERS` to 1, so any
  `-n auto` inside the runner resolves to one worker instead of one
  application-loading process per CPU core, which exhausts the container's
  memory.
- `BDD_SHARD_XDIST_N` must **derive from** `E2E_WORKERS`; never set it directly.
  When `E2E_WORKERS > 0`, `run_all_tests.sh` swaps the serial `bdd`
  environment for the `bdd-inprocess-s*,bdd_e2e` split, exports
  `BDD_SHARD_XDIST_N` as `E2E_WORKERS` divided across the in-process shards, and
  provisions `E2E_WORKERS` isolated per-worker stacks — one `adcp-server`
  container plus one TLS sidecar per xdist worker, each on its own database
  (`adcp_gwN`, cloned from a migrated template) — then runs `bdd_e2e` with
  `E2E_PER_WORKER=1` and `BDD_E2E_XDIST_N=$E2E_WORKERS`. That provisioning is
  what makes parallel `e2e_rest` legal under the earlier guard.

## Choose a command

This table extends the decision tree in the root `CLAUDE.md` with the e2e
distinctions this page teaches.

| What you want to test | Command | What it starts |
|-----------------------|---------|----------------|
| Everything (the default full gate) | `./run_all_tests.sh` | Full stack in-network, all six suites via `tox -p`, auto-teardown |
| The protocol pytest suite only | `./run_all_tests.sh e2e` | Full stack in-network, `tox -e e2e` |
| BDD scenarios over real HTTP (the known-failures list) | `./run_all_tests.sh bdd_e2e` | Full stack in-network, `tox -e bdd_e2e` |
| BDD scenarios in-process only | `BDD_E2E_ENABLED=false tox -e bdd` | Nothing (needs a suite `DATABASE_URL`) |
| One e2e test, iterating | `make test-stack-up`, then `source .test-stack.env && tox -e e2e -- -k <name>` | Host-side stack with published ports; stays up between runs |
| One e2e test, no manual lifecycle | `scripts/run-test.sh tests/e2e/test_foo.py -x` | Auto-detects and starts the full stack |
| A targeted non-suite run | `./run_all_tests.sh ci <pytest-target> [args]` | Host-side stack (delegates to `run_all_tests_host.sh`) |

`run_all_tests.sh` with no arguments (or `ci`) is the in-network path: the
runner runs inside the compose network, no host ports exist, and concurrent
invocations cannot collide. `quick` and `ci <target>` delegate to
`run_all_tests_host.sh`, which runs pytest on the host against a
`scripts/test-stack.sh` stack with published loopback ports.

## Iterate on one failing e2e test

Rebuilding and tearing down the full stack for every attempt is slow. Use
this loop instead:

1. Start a host-side stack once: `make test-stack-up`. This builds the images,
   overlays the ports file, waits for Postgres, the server, and a **verified**
   TLS handshake, then writes the allocated ports and `DATABASE_URL` to
   `.test-stack.env`.
2. In every shell you test from, run `source .test-stack.env`.
3. Iterate: `tox -e e2e -- -k <test_name> -x`, or
   `uv run pytest tests/e2e/test_foo.py::test_bar -x -v`. Because
   `.test-stack.env` exports `ADCP_TESTING=true`, the session-scoped
   `docker_services_e2e` fixture reuses the running stack instead of starting
   its own.
4. Inspect the server while the test runs:
   `docker compose -f docker-compose.e2e.yml -f docker-compose.e2e.ports.yml -p <project> logs -f adcp-server`
   (the project name is in `.test-stack.env`), and the server database at
   `psql "postgresql://adcp_user:...@127.0.0.1:${POSTGRES_PORT}/adcp"`.
5. Tear down when done: `make test-stack-down`.

Without `ADCP_TESTING=true` (and without `--skip-docker`), `pytest
tests/e2e/...` takes the standalone branch of `docker_services_e2e`. That
branch builds and starts its own stack on dynamically allocated ports (server
20000–25000, Postgres 25000–30000, TLS 15000–20000, webhook capture
30000–32000), then tears it down at session end. GitHub CI's host-side
`pytest tests/e2e` path uses it. For local iteration it rebuilds the stack
every session, so prefer the preceding loop.

For an `e2e_rest` BDD scenario, the supported iteration path is the in-network
runner: `./run_all_tests.sh bdd_e2e`. It is slower per cycle than the pytest
loop, but it is the configuration that the known-failures list and the CI job
are defined against. A scenario's pass or fail result on any other
configuration is not evidence about the list.

## Read the results, not the scrollback

Every suite writes a pytest JSON report, and the runners copy the reports of
exactly the suites that ran:

- In-network runs: `test-results/innet_<ddmmyy_HHMM>/` (UTC timestamp), plus a
  `.suites` manifest naming which suites this invocation ran.
- Host runs: `test-results/<ddmmyy_HHmm>/` (local time); the last 10
  directories are kept.

The JSON is the authoritative record:

- A long run's terminal output can vanish — background processes exit, CLI
  connections drop. The reports persist.
- A suite that is stopped mid-run can truncate its report;
  `scripts/check_truncated_reports.py` fails the run when a report's
  collected/reported counts disagree, so a half-run cannot read as passing.
- A suite that ran but produced no report is an **error**, not an omission —
  it exited before writing one, and the runner exits nonzero for it.
- `.tox/` persists between runs, so both runners purge old reports first; a
  report exists only if this run produced it. Trust the directory plus its
  `.suites` manifest, never a lone JSON file's timestamp.

A useful corollary: when the process exit code is nonzero but every report in
the directory shows zero failures, the failure is outside the suites. The
runner prints a diagnosis for the common case — Docker's own 125/126/127
"could not run the container" range after all suites already finished.

## Failure modes and what they mean

The following table maps a symptom to its usual cause and to the place that
explains it.

| Symptom | What it usually means | Where to look |
|---------|----------------------|---------------|
| `Tenant with subdomain 'ci-test' not found` in e2e, though the seed ran | Another suite truncated the server DB mid-run, bypassing the serialization in `tox.ini` (for example, by running server-DB environments in parallel by hand) | The `depends` comment block above `[testenv:ui]` in `tox.ini` |
| `Server not ready after 60s (port N)` across all of e2e, or `the live E2E stack is unreachable` across all of `bdd_e2e` | The stack never came up; the test failures are downstream symptoms, not causes. One infrastructure fault, thousands of downstream errors | `docker compose ... logs adcp-server`; the runner's own health-wait output |
| `PermissionError: /app/logs/audit.log` at collection, or the server exiting during import | Bind-mount file-ownership race between `adcp-server` (uid 1001) and the runner. The runners pre-create world-writable log files to prevent it | The `logs/` block in `run_all_tests.sh` |
| Suite exits with code -9, or a report says `collected N but reported 0` | The Docker VM's out-of-memory killer. Almost always an exported worker-count override defeating the computed tiers | Your shell environment: `UNIT_XDIST_N`, `INTEGRATION_XDIST_N`, `E2E_WORKERS`, `BDD_XDIST_N` |
| `pytest.UsageError: BDD_E2E_ENABLED=true is incompatible with xdist` | The guard against the silent transport drop fired | The "A passing run that proves nothing" section in this guide |
| nginx 502, or connection refused on a host-path port | Cross-stack host-port collision (a time-of-check to time-of-use race in port allocation), or an abandoned stack still holding the port | `scripts/test-stack.sh` retries with fresh ports and reaps stacks older than 90 minutes; `docker ps --filter name=adcp-test-` |
| An https e2e test fails with a certificate error | Stale or missing `.test-tls/` material, or the process doesn't trust the generated CA | `scripts/dev/ensure-test-tls.sh`; `E2E_CA_BUNDLE`; `SSL_CERT_FILE` must point at the **combined** bundle, never the private CA alone |
| A report in `test-results/` shows failures for code that no longer exists | You are reading a stale report from a previous run | The directory's `.suites` manifest; rerun and read the fresh directory |
| Exit code 125/126/127 though every report shows zero failures | Docker's runner-level failure after the suites finished | The runner's closing diagnostic; the Docker daemon, not the tests |

## Related reading

- [Outbound egress](../security/outbound-egress.md) — TLS for outbound
  origins, the outbound HTTP enforcement layer, and why the stack's SSRF
  posture is what it is.
- [Xpass graduation workflow](../../.claude/rules/workflows/xpass-graduation.md)
  — the per-scenario protocol for retiring known-failure entries.
- [CI pipeline](ci-pipeline.md) — where these suites run in GitHub Actions.
- `tests/e2e/README.md` — directory-local notes for the protocol pytest
  suite.
