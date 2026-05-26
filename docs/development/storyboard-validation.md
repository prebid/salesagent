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
specialism.

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

### 2. Latest SDK Drift

The same workflow runs a scheduled latest-SDK assessment with
`ADCP_SDK_VERSION=latest` and `STORYBOARD_SOFT_FAIL=1`. This job should surface
new failures quickly, but it does not block merges while the failure list is
being triaged.

Use it to answer: "What did the current storyboard suite start expecting?"

### 3. Release Gate

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
PROTOCOLS=mcp \
STORYBOARDS=capability_discovery,pagination_integrity_list_accounts,get_signals_pagination_integrity,signal_owned \
REPORT_DIR=.context/storyboard-smoke \
./scripts/storyboard-check.sh
```

To see the SDK's selected storyboard set for each advertised specialism:

```bash
npx -y @adcp/sdk@7.11.0 storyboard show --specialism sales-non-guaranteed
npx -y @adcp/sdk@7.11.0 storyboard show --specialism signal-owned
```

Pinned full-specialism assessment, useful for debt burn-down but not currently
green enough to make blocking:

```bash
AGENT_URL=http://localhost:8000 \
AGENT_TOKEN=ci-test-token \
ADCP_SDK_VERSION=7.11.0 \
ALLOW_HTTP=1 \
PROTOCOLS=mcp \
SPECIALISMS=sales-non-guaranteed,signal-owned \
EXCLUDED_STORYBOARDS=security_baseline \
STORYBOARD_SOFT_FAIL=1 \
REPORT_DIR=.context/storyboard-specialisms \
./scripts/storyboard-check.sh
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
REPORT_DIR=.context/storyboard-latest \
./scripts/storyboard-check.sh
```

## Good Enough Bar

Storyboard coverage is good enough when all of the following are true:

- The pinned PR gate is required and green.
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
