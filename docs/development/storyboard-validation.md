# Storyboard Validation

Storyboards are a black-box contract signal. They are not a replacement for
unit and integration tests: every storyboard failure that requires a product
fix should be reduced to a local regression test after the root cause is known.

## Validation Lanes

### 1. Pinned PR Gate

Pull requests run `.github/workflows/storyboard.yml` against the local Docker
stack using a pinned `@adcp/sdk` version. The blocking gate is intentionally
deterministic: it runs the currently green storyboard set that covers the core
contract, account pagination, signal pagination, and the advertised owned-signal
specialism on both MCP and A2A.

Current blocking set:

- `capability_discovery`
- `pagination_integrity_list_accounts`
- `get_signals_pagination_integrity`
- `signal_owned`

Current advertised specialisms:

- `sales-non-guaranteed`
- `signal-owned`

This is not the final bar for those specialisms. With `@adcp/sdk@7.11.0`,
`sales-non-guaranteed` and `signal-owned` resolve to a much larger set of
universal, media-buy, and signals storyboards. That full set is still a debt
burn-down lane until the known media-buy failures are fixed.

### 2. Pinned Sales Non-Guaranteed Assessment

Pull requests and pushes also run a pinned, non-blocking
`sales-non-guaranteed` assessment. This is the burn-down lane for the full
non-guaranteed specialism: it uses the same pinned SDK as the blocking gate,
resolves the storyboard set from `sales-non-guaranteed`, excludes only
`security_baseline` for the local Docker auth reasons described below, and
uploads both storyboard reports and compose logs. CI runs this lane on both
MCP and A2A and resets the compose stack between protocol runs so each
transport starts from clean seeded state. The storyboard workflow explicitly
sets `SEED_DEMO_AUTO_APPROVE=1` so the local seed exercises auto-approval paths;
ordinary compose runs preserve the tenant's manual-review setting.

This job remaps storyboard assertion failures (`exit 1`) to a non-blocking
GitHub result, while reset-hook and infrastructure failures still fail the job.
A red storyboard report should create or update the burn-down list, not block
unrelated PRs until the lane is green enough to promote.

Known local/CI limitation: `security_baseline` remains part of the release gate,
where publishable auth metadata and HTTPS-style test-kit credentials are
available. Treating it as a Docker-local smoke makes the required check fail for
environment reasons rather than product regressions.

### 3. Latest SDK Drift

The same workflow runs a scheduled latest-SDK assessment with
`ADCP_SDK_VERSION=latest`. This job should surface new failures quickly, but it
does not block merges while the failure list is being triaged.

Use it to answer: "What did the current storyboard suite start expecting?"

### 4. Release Gate

Before promoting a deployed agent, run the full storyboard suite against a
clean staging environment with the exact tenant configuration and tokens that
production will use. This catches configuration and state issues that local CI
cannot see: auth setup, reverse proxy paths, idempotency cache state, seeded
tenant data, external creative agents, and deployment SHA drift.

## Local Commands

Pinned local smoke against a running compose stack:

```bash
AGENT_URL=http://localhost:8000 \
AGENT_TOKEN=ci-test-token \
ADCP_SDK_VERSION=7.11.0 \
ALLOW_HTTP=1 \
PROTOCOLS=mcp,a2a \
STORYBOARDS=capability_discovery,pagination_integrity_list_accounts,get_signals_pagination_integrity,signal_owned \
REPORT_DIR=.context/storyboard-smoke \
./scripts/storyboard-check.sh
```

To see the SDK's selected storyboard set for each advertised specialism:

```bash
npx -y @adcp/sdk@7.11.0 storyboard show --specialism sales-non-guaranteed
npx -y @adcp/sdk@7.11.0 storyboard show --specialism signal-owned
```

Pinned `sales-non-guaranteed` assessment. Start or restart the compose stack
with `SEED_DEMO_AUTO_APPROVE=1` first so the initial MCP pass sees the same
auto-approval seed state as CI; the between-protocol reset hook applies that
same seed state before A2A. `STORYBOARD_SOFT_FAIL=1` is only for local
iteration so the command writes reports without failing the shell:

```bash
SEED_DEMO_AUTO_APPROVE=1 CONDUCTOR_PORT=8000 make compose-up
```

```bash
AGENT_URL=http://localhost:8000 \
AGENT_TOKEN=ci-test-token \
ADCP_SDK_VERSION=7.11.0 \
SEED_DEMO_AUTO_APPROVE=1 \
ALLOW_HTTP=1 \
PROTOCOLS=mcp,a2a \
SPECIALISMS=sales-non-guaranteed \
EXCLUDED_STORYBOARDS=security_baseline \
STORYBOARD_SOFT_FAIL=1 \
BETWEEN_PROTOCOLS_HOOK=./scripts/storyboard-reset-compose.sh \
REPORT_DIR=.context/storyboard-non-guaranteed \
./scripts/storyboard-check.sh
```

Equivalent Make target:

```bash
make storyboard-non-guaranteed
```

Webhook storyboards require an SDK-hosted receiver. The wrapper exposes the
SDK flags but leaves them off by default because Docker/remote agents need a
callback URL they can actually reach:

```bash
# Host-run agent.
WEBHOOK_RECEIVER=loopback make storyboard-non-guaranteed

# Docker compose on a local machine. Use the host-gateway name from
# docker-compose.yml so the container can call back to the SDK receiver.
WEBHOOK_RECEIVER=proxy \
WEBHOOK_RECEIVER_PORT=58123 \
WEBHOOK_RECEIVER_PUBLIC_URL=http://host.docker.internal:58123 \
make storyboard-non-guaranteed

# Remote agent.
WEBHOOK_RECEIVER_AUTO_TUNNEL=1 make storyboard-non-guaranteed
WEBHOOK_RECEIVER=proxy \
WEBHOOK_RECEIVER_PUBLIC_URL=https://receiver.example.test \
make storyboard-non-guaranteed
```

Latest-SDK full assessment without blocking on known failures:

```bash
AGENT_URL=http://localhost:8000 \
AGENT_TOKEN=ci-test-token \
ADCP_SDK_VERSION=latest \
ALLOW_HTTP=1 \
PROTOCOLS=mcp,a2a \
STORYBOARD= \
STORYBOARD_SOFT_FAIL=1 \
BETWEEN_PROTOCOLS_HOOK=./scripts/storyboard-reset-compose.sh \
REPORT_DIR=.context/storyboard-latest \
./scripts/storyboard-check.sh
```

## Good Enough Bar

Storyboard coverage is good enough when all of the following are true:

- The pinned PR gate is required and green.
- The pinned PR gate exercises both MCP and A2A.
- The pinned full-specialism assessment has no untriaged failures.
- The latest-SDK scheduled run has no untriaged failures.
- Every advertised tool has local tests for pagination, auth scoping, request
  validation, response shape, and repeated-run state.
- Every fixed storyboard failure has a minimal local regression test.
- The release checklist includes a full staging storyboard run on clean seeded
  state.

Do not add broad latest-SDK storyboards as required PR checks until the current
failure list is burned down. Required checks should be deterministic and
actionable by the author of the PR that fails them.
