# End-to-end protocol tests

This directory holds the protocol pytest suite: tests that exercise the live
Docker stack over real HTTP through nginx. **[End-to-end
testing](../../docs/development/e2e-testing.md) documents how to run the
suite, what it starts, how it relates to the BDD `e2e_rest` transport, and
how to debug a failing run — read that first.** This file covers only what is
local to this directory.

## Stack lifecycle

The session fixture `docker_services_e2e` in `conftest.py` decides whether to
start a stack:

- With `ADCP_TESTING=true` in the environment (the runners set it, and
  `make test-stack-up` writes it to `.test-stack.env`) or with the
  `--skip-docker` pytest option, the fixture reuses the already-running
  services at the published ports (`ADCP_SALES_PORT`, `POSTGRES_PORT`,
  `ADCP_TLS_PORT`, `WEBHOOK_CAPTURE_PORT`).
- Otherwise, the fixture builds and starts its own stack on dynamically
  allocated ports and tears it down at session end.

Either way, the fixture seeds the server database through
`scripts/setup/init_database_ci.py` — the `ci-test` and `iso-test` tenants,
products, and the `ci-test-token` principal. It then yields the port map that
the `live_server` fixture turns into URLs. `live_server["tls"]` is present
only when the stack serves a verified TLS listener; tests that need HTTPS
request that key and fail with an explicit error when it is absent.

## Stack readiness

`docker_services_e2e` waits through `stack_readiness.wait_for_e2e_stack` with
required probe order `postgres → creative-agent → adcp /health` (both when
reusing an already-running stack via `ADCP_TESTING=true` / `--skip-docker`, and
after a self-`up`). On timeout it dumps compose logs (including
`creative-agent`) once and fails once. See `stack_readiness.py`.

## Testing hooks

The suite implements the AdCP testing hooks from
[AdCP PR #34](https://github.com/adcontextprotocol/adcp/pull/34) as request
headers: `X-Dry-Run`, `X-Mock-Time`, `X-Jump-To-Event`, `X-Test-Session-ID`,
and `X-Simulated-Spend`. See `utils.py` and `test_adcp_full_lifecycle.py` for
the client helpers that set them.

## Schema validation

`conftest_contract_validation.py` validates tool calls against the AdCP
schemas at collection time. The validator itself lives in
`tests/helpers/adcp_schema_validator.py` — it is shared with `tests/unit` and
`tests/integration`, so it cannot live under this directory without those
suites depending on `tests/e2e`. Schemas load from the installed `adcp` SDK
at the repo's pinned spec version (see `docs/adcp-spec-version.md`), never
from the live registry.

## Test an external server

`test_a2a_adcp_compliance.py` accepts `--server-url` and `--auth-token`
options for validating an external A2A endpoint instead of the local stack.
