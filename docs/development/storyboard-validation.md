# Storyboard Validation

Storyboards are a black-box contract signal. They are not a replacement for
unit and integration tests: every storyboard failure that requires a product
fix should be reduced to a local regression test after the root cause is known.

## Validation Lanes

### 1. Pinned PR Smoke

Pull requests run `.github/workflows/storyboard.yml` against the local Docker
stack using a pinned `@adcp/sdk` version. This catches regressions in contracts
we already know we support without letting upstream storyboard drift randomly
break every PR.

Current smoke set:

- `pagination_integrity_list_accounts`

Expand this only when the new storyboard is green against the local stack and
the failure mode is actionable by a PR author.

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
STORYBOARDS=pagination_integrity_list_accounts \
REPORT_DIR=.context/storyboard-smoke \
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

- The pinned PR smoke is required and green.
- The latest-SDK scheduled run has no untriaged failures.
- Every advertised tool has local tests for pagination, auth scoping, request
  validation, response shape, and repeated-run state.
- Every fixed storyboard failure has a minimal local regression test.
- The release checklist includes a full staging storyboard run on clean seeded
  state.

Do not add broad latest-SDK storyboards as required PR checks until the current
failure list is burned down. Required checks should be deterministic and
actionable by the author of the PR that fails them.
