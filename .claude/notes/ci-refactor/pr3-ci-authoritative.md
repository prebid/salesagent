# PR 3 — CI authoritative + reusable workflows (3-phase merge)

**Drift items closed:** PD10 (partial), PD11, PD15, PD16 (groundwork), plus #1233 D1, D2, D3, D4, D5, D6, D8, D10, D12, D14
**Estimated effort:** 3-4 days across 3 phases
**Depends on:** PR 1 (SHA-pinning, permissions baseline) and PR 2 (local mypy/black hooks) merged
**Blocks:** PR 4 (PR 4 deletes hooks whose work is absorbed into the `CI / Quality Gate` job introduced here)
**Decisions referenced:** D2, D3, D11, D15, D17

> **Precondition**: `pytest-xdist>=3.6` MUST be added to `pyproject.toml [dependency-groups].dev` before this PR's xdist commits land. Best location: PR 2 commit 4 or 5 (which already touches dependency groups). If PR 2 ships without it, this PR must lead with a one-line `[dependency-groups].dev` addition commit.

## Scope

Restructure `.github/workflows/` to make CI the authoritative enforcement layer. Replace the matrix-sharded integration tests with `pytest-xdist` (validated safe per `tests/conftest_db.py:323-348` UUID-per-test DB pattern). Add **composite actions** `setup-env` and `_pytest` (NOT reusable workflows — composites avoid the 3-segment rendered-name issue per Decision-4). Freeze the 14 required check names per D17 amended by D30 (which expanded the list with Smoke Tests, Security Audit, Quickstart). Add Migration Roundtrip and Coverage Combine jobs. Per D32, integration-tests bootstraps the creative-agent service with 10 env vars (ghcr.io/prebid/creative-agent:ca70dd1e2a6c).

This is the **only PR with a non-atomic merge.** It lands in 3 phases over ~1 week:

- **Phase A** — overlap: new workflows run alongside existing `test.yml`. Both green for ≥48h.
- **Phase B** — atomic flip: admin runs one `gh api -X PATCH` to swap required-checks list. ≤5 min window.
- **Phase C** — cleanup: delete legacy `test.yml`. Land ≥48h after Phase B is stable.

## Out of scope

- Pre-commit hook deletion / migration → PR 4
- Postgres unification across compose files → PR 5 (this PR unifies CI Postgres only)
- Python/uv version anchor consolidation → PR 5
- New required CI checks beyond the 14 frozen names per D17 (e.g., harden-runner) → PR 6 follow-up
- `merge_group:` triggers → never (D6)

## Architectural choices (corrections to issue #1234)

The issue says "reusable workflows `_setup-env.yml`, `_postgres.yml`, `_pytest.yml`." Three corrections (per Decision-4):

1. **`_setup-env` is a composite action**, not a reusable workflow. Composite actions can be called as a series of steps inside a job; reusable workflows can't. Path: `.github/actions/setup-env/action.yml`.

2. **`_pytest` is ALSO a composite action** (revised from earlier "reusable workflow" plan). Path: `.github/actions/_pytest/action.yml`. **Rationale:** reusable-workflow nesting renders status checks as 3-segment names (`CI / Unit Tests / pytest`) because the called workflow's job-id is appended. Branch protection's required-checks list uses 2-segment names (`CI / Unit Tests`); a reusable-workflow `_pytest.yml` would silently 422 the Phase B PATCH. Composites don't add path segments — the calling job's `name:` IS the rendered name. The structural guard `tests/unit/test_architecture_required_ci_checks_frozen.py` (drafted in `drafts/guards/`) enforces this at design time, not just at flip time. (Per Decision-4 in `RESUME-HERE.md`; see also D26 corollary.)

3. **`_postgres` does not exist as a separate file.** Postgres services live at the calling-job level in `ci.yml` (services are a workflow/job-level concern; composite actions cannot declare them). The integration/e2e/admin/bdd jobs each declare their own `services: postgres:` block at the job level, then call the `_pytest` composite for test execution. The duplication is ~15 lines per job — acceptable cost for the rendered-name win.

## Internal commit sequence

Phase A is one PR with ~10 commits. Phase B is admin action (no PR). Phase C is a small follow-up PR with 1-2 commits.

### Pre-flight (BEFORE Phase A merges)

> **Pre-flight 3a — xdist soak (HARD precondition).** Before flipping the matrix in Phase A, run `tox -e integration -- -n 4` against current main. If the suite fails or flakes, fix infrastructure first (likely candidates: `mcp_server` port TOCTOU, `factory.Sequence` collisions, module-global engine mutations, session-scoped fixtures racing on shared resources). Phase A does NOT proceed until xdist is green across ≥3 consecutive runs at `-n 4` AND ≥1 successful run at `-n auto`. Record a paste-able command + timing summary in the PR description.
>
> Rationale: switching from matrix-shard to xdist exposes any test-isolation defect that the legacy single-process matrix was masking. Discovering that defect during Phase A's 48h soak (post-merge) means rolling back; discovering it pre-merge is cheap.

### Phase A — Overlap (single PR)

> **Phase A operational note:**
> During the 48h overlap window, both `test.yml` (old) and `ci.yml` (new) run in parallel on every PR. If old `test.yml` passes but new `ci.yml` fails, the new one is INFORMATIONAL — branch protection still keys on the OLD names. Do NOT fix-forward into the new workflow until Phase A completes and Phase B flips required-checks. Maintainer judgment: if `ci.yml` failure indicates a real bug, fix in a follow-up PR; if it indicates `ci.yml` configuration drift, fix Phase A.

> **Commit ordering note (bisect cleanliness):** The numbered commits below are the LOGICAL grouping. The ACTUAL git-history order MUST place "Commit 6 — coverage baseline + tox.ini sync" BEFORE "Commit 3 — ci.yml orchestrator", because `ci.yml` references `.coverage-baseline` via `cat .coverage-baseline`. Required git order: 1, 2, 6, 3, 4, 4b, 4c, 5, 7, 8, 9, 10, 11. A bisect that lands on Commit 3 with `.coverage-baseline` absent would fail the Coverage job (file-not-found in the subshell). Commit 4c (filelock + worker-id gate around `migrate.py`) MUST follow 4b (template-clone optimization) because the gate operates on the conftest_db.py shape that 4b restructures. Reviewers reading the spec top-to-bottom should mentally re-thread the dependency.

#### Commit 1 — `ci: add setup-env composite action`

Files:
- `.github/actions/setup-env/action.yml` (new)

```yaml
name: 'Setup environment'
description: 'Checkout, setup uv, install deps, cache'
inputs:
  python-version-file:
    description: 'Path to .python-version'
    required: false
    default: '.python-version'
  uv-version:
    description: 'uv version to install'
    required: false
    default: '0.11.7'   # Per current uv anchor; bump in lock-step across setup-env, pyproject.toml, and pre-commit config
  groups:
    description: 'uv dependency groups to install (space-separated)'
    required: false
    default: 'dev'
  frozen:
    description: 'Pass --frozen to uv sync (CI default true — fail on lockfile drift)'
    required: false
    default: 'true'
  install-project:
    description: 'Install the salesagent package itself (false for non-pytest jobs like type-check, schema-contract)'
    required: false
    default: 'true'
runs:
  using: 'composite'
  steps:
    - uses: actions/checkout@<SHA>  # v4
    - uses: astral-sh/setup-uv@<SHA>  # v8.x
      with:
        version: ${{ inputs.uv-version }}
        python-version-file: ${{ inputs.python-version-file }}
        enable-cache: true
        # Per #1228 C5: hash both uv.lock AND pyproject.toml
        # so a `pyproject.toml` change without `uv lock` regen invalidates the cache. Otherwise
        # stale cache hits silently keep the old resolved deps even after the manifest changes.
        cache-dependency-glob: |
          uv.lock
          pyproject.toml
    - shell: bash
      run: |
        FLAGS=""
        for g in ${{ inputs.groups }}; do
          FLAGS="$FLAGS --group $g"
        done
        [[ "${{ inputs.frozen }}" == "true" ]] && FLAGS="$FLAGS --frozen"
        [[ "${{ inputs.install-project }}" == "false" ]] && FLAGS="$FLAGS --no-install-project"
        uv sync $FLAGS
```

Verification:
```bash
test -f .github/actions/setup-env/action.yml
yamllint -d relaxed .github/actions/setup-env/action.yml
grep -qE 'using: .composite.' .github/actions/setup-env/action.yml
```

#### Commit 2 — `ci: add _pytest composite action`

Files:
- `.github/actions/_pytest/action.yml` (new) — composite action, NOT reusable workflow

```yaml
name: 'Pytest run'
description: 'Run a tox env, upload coverage and report artifacts. Caller declares postgres services if needed.'
inputs:
  tox-env:
    description: 'tox env to run (e.g., unit, integration, e2e, admin, bdd)'
    required: true
  pytest-args:
    description: 'Extra pytest args (e.g., -n auto, file selection)'
    required: false
    default: ''
  xdist-workers:
    description: 'pytest-xdist worker count ("0" = no parallelism, "auto", "logical", or N)'
    required: false
    default: '0'
runs:
  using: 'composite'
  steps:
    - uses: ./.github/actions/setup-env
    - name: Run tox env
      shell: bash
      env:
        # ADCP_TESTING signals test-mode auth/config paths
        ADCP_TESTING: 'true'
        # Coverage data file (downloaded by Coverage job for combine)
        COVERAGE_FILE: .coverage.${{ inputs.tox-env }}
        # ENCRYPTION_KEY: integration tests at tests/integration/test_*encryption* + creative-agent
        # ↳ matches test.yml:232 today; mock value, not a real secret.
        ENCRYPTION_KEY: 'PEg0SNGQyvzi4Nft-ForSzK8AGXyhRtql1MgoUsfUHk='
        # DELIVERY_WEBHOOK_INTERVAL: tests/e2e/conftest.py:137-141 reads this; tox.ini:24 passes.
        # ↳ Default '5' matches local tox.ini.
        DELIVERY_WEBHOOK_INTERVAL: '5'
        # GEMINI_API_KEY: unconditional mock per D15 (no fork-PR secret leak surface).
        GEMINI_API_KEY: 'test_key_for_mocking'
        # SUPER_ADMIN_EMAILS: parity with test.yml today.
        SUPER_ADMIN_EMAILS: 'test@example.com'
        # DB_POOL_SIZE / DB_MAX_OVERFLOW: per D40 — reduce app's connection pool
        # in CI so 4 xdist workers × 12 conn = 48 stays well under Postgres default
        # max_connections=100. Local dev compose sees production defaults (no override).
        # NB: src/core/database/database_session.py must read these env vars; verify at PR-3
        # author time. If not wired, add a tiny PR before this composite lands.
        DB_POOL_SIZE: '4'
        DB_MAX_OVERFLOW: '8'
        # CREATIVE_AGENT_URL: integration job sets this at job level (NOT here in the composite).
        # The creative-agent runs as a docker-run script step on a custom Docker network;
        # see PR 3 commit 9 (per D32 + D39). Value at job level:
        # `CREATIVE_AGENT_URL: 'http://localhost:9999/api/creative-agent'` (port 9999 is the
        # host mapping; `/api/creative-agent` is the path prefix inside the adcp monolith).
      run: |
        XDIST_FLAG=""
        DIST_FLAG=""
        if [[ "${{ inputs.xdist-workers }}" != "" && "${{ inputs.xdist-workers }}" != "0" ]]; then
          XDIST_FLAG="-n ${{ inputs.xdist-workers }}"
          # --dist=loadscope keeps tests sharing session-scoped fixtures (DB, app) on the
          # same worker. Default --dist=load is random and would split UUID-per-test DB
          # fixtures across workers, causing IntegrityError. Per D33.
          # If any test uses @pytest.mark.xdist_group in the future, switch to loadgroup.
          DIST_FLAG="--dist=loadscope"
        fi
        uv run tox -e ${{ inputs.tox-env }} -- \
          -p no:cacheprovider \
          $XDIST_FLAG $DIST_FLAG \
          ${{ inputs.pytest-args }}
    - uses: actions/upload-artifact@<SHA>  # v4
      if: always()
      with:
        name: coverage-${{ inputs.tox-env }}
        # Literal path, not glob (R27 mitigation — prevents tar-bomb of .coverage.* in /tmp etc.)
        path: '.coverage.${{ inputs.tox-env }}'
        include-hidden-files: true
        if-no-files-found: error   # fail noisily; coverage data must be produced
        retention-days: 7   # Matches Scorecard pattern. GH default is 90d (~5MB × N envs × every PR run = quota burn).
    - uses: actions/upload-artifact@<SHA>  # v4
      if: always()
      with:
        name: pytest-report-${{ inputs.tox-env }}
        # tox.ini writes pytest-json-report to {toxworkdir}/<env>.json
        # (e.g., .tox/unit.json), NOT test-results/. The earlier `path: test-results/` was a
        # silent-empty-artifact. Glob both candidate paths so this works whether tox.ini is
        # updated to emit into test-results/ (preferred follow-up) or stays at the
        # {toxworkdir}-relative default.
        path: |
          test-results/
          .tox/${{ inputs.tox-env }}.json
          .tox/${{ inputs.tox-env }}-*.json
        if-no-files-found: ignore
        retention-days: 7   # Matches the coverage-artifact retention.
```

**Why composite, not reusable workflow** (Decision-4): rendering. A reusable-workflow `_pytest.yml`
called from `ci.yml`'s `unit-tests` job renders as `CI / Unit Tests / pytest` (3 segments —
GitHub appends the called workflow's job-id). The Phase B branch-protection PATCH uses
2-segment names. Reusable-workflow nesting always adds segments; composite actions don't.

**Postgres services** live at the calling-job level in `ci.yml`. Non-DB jobs (schema-contract,
unit-tests) skip the services block. DB jobs (integration-tests, e2e-tests, admin-tests,
bdd-tests, migration-roundtrip) declare it per-job. The duplication is ~15 lines per job —
acceptable cost. Future DRY: extract to a job-level YAML anchor or a second composite if
the duplication becomes painful.

**`timeout-minutes`** also moves to the calling-job level (composite-action steps don't accept
job-level `timeout-minutes`).

Verification:
```bash
test -f .github/actions/_pytest/action.yml
yamllint -d relaxed .github/actions/_pytest/action.yml
grep -qE 'using:\s+["\x27]?composite' .github/actions/_pytest/action.yml
# Negative: no reusable-workflow form (would re-introduce 3-segment rendering)
! test -f .github/workflows/_pytest.yml
```

#### Commit 3 — `ci: add ci.yml orchestrator with 14 frozen check names`

Files:
- `.github/workflows/ci.yml` (new)

**Naming convention (per D26):** Workflow `name: CI` (top-level) + job `name: 'Quality Gate'` (bare). GitHub renders the concatenation `CI / Quality Gate` as the check-run name. The PATCH body in `flip-branch-protection.sh` uses the rendered form. The structural-guard test `test_architecture_required_ci_checks_frozen.py` parses the bare YAML form and reconstructs the rendered form for comparison. `verify-pr3.sh` greps the bare form (matches what's actually in `ci.yml`). Three valid representations exist in this corpus: bare YAML (`name: 'Quality Gate'`), bare unquoted (`name: Quality Gate`), and rendered (`CI / Quality Gate`). The bare-vs-rendered split is intentional — the YAML stores bare; the GitHub API receives rendered.

The 14 frozen check names per D17 amended by D30:

```yaml
name: CI

on:
  pull_request:
    branches: [main, develop]
  push:
    branches: [main, develop]
  workflow_dispatch:   # Per D37 — preserve manual-run capability matching test.yml:8 today.
  # Note: `develop` is included to maintain parity with the existing test.yml trigger model.
  # Formal deprecation of `develop` is deferred to a post-#1234 follow-up; until then,
  # PR 3 must support both branches so contributor PRs targeting `develop` continue to gate.
  # `workflow_dispatch` is required by Phase B Step 1b (rendered-name capture) and Phase B
  # Step 2.5 (in-flight PR drain). R28 ensures cancel-in-progress does NOT cancel dispatch runs.

permissions: {}

# YAML anchors deduplicate the postgres service block + DATABASE_URL across the 5 DB-needing
# jobs (integration, e2e, admin, bdd, migration-roundtrip). GitHub Actions YAML parsing
# supports anchors (baseline YAML 1.2). Verify with `actionlint ci.yml` before commit.
# Fallback (if anchors fail to parse on first run): emit ci.yml from a small Python template
# script — preserves single-source-of-truth without runtime anchor dependency.
x-postgres-service: &postgres-service
  # Per D40: pool_size=10 + max_overflow=20 = 30 conn/worker × 4 xdist workers
  # under -n auto = 120 conn at peak; default max_connections=100 → "too many clients" flake.
  # GHA `services:` block does NOT support a `command:` field (GitHub schema only allows
  # image/env/ports/options/credentials/volumes), so we cannot pass `-c max_connections=200`
  # directly to postgres CLI. Two workarounds available; chosen approach is (b):
  #
  #   (a) Build a custom postgres image with config in postgresql.conf — adds a build step
  #       to ci.yml and a Dockerfile to maintain. Rejected: too much surface for one knob.
  #   (b) Post-start tuning step inside integration-tests / e2e-tests / admin-tests / bdd-tests
  #       jobs that runs `docker exec <postgres-container> psql -c "ALTER SYSTEM SET ...;"`
  #       followed by `SELECT pg_reload_conf();`. Reload (NOT restart) works because
  #       `max_connections` requires restart but we can ALTER SYSTEM + restart-in-place via
  #       `pg_ctl restart` if needed. **For the per-PR-test scope, the simpler route is to
  #       lower the app pool size in CI**: set env `DB_POOL_SIZE=4` and `DB_MAX_OVERFLOW=8`
  #       (4 workers × 12 = 48 conn at peak, comfortably under default 100).
  #   (c) Move salesagent postgres out of `services:` into a docker-run step like
  #       creative-postgres. Most invasive but most consistent. P1 follow-up if (b)
  #       doesn't suffice.
  #
  # PR 3 ships with workaround (b) + R31 tripwire monitoring. If `tox -e integration -- -n auto`
  # post-Phase-A-soak shows "too many clients" failures, escalate to (a) or (c).
  # NB: `src/core/database/database_session.py:124-125` reads `DB_POOL_SIZE` /
  # `DB_MAX_OVERFLOW` env vars (verify at PR-3 author time; if not, add a pre-flight ADR
  # to wire the env-var override).
  image: postgres:17-alpine
  env:
    POSTGRES_USER: adcp_user
    POSTGRES_PASSWORD: test_password
    POSTGRES_DB: adcp_test
  options: >-
    --health-cmd "pg_isready -U adcp_user"
    --health-interval 5s
    --health-retries 10
  ports:
    - 5432:5432

x-database-url: &database-url
  postgresql://adcp_user:test_password@localhost:5432/adcp_test

concurrency:
  # Per concurrency-key best practice (corroborates ruff/uv pattern): include PR-number/SHA in the group key
  # so two distinct runs at different commits on the same branch don't collide. Without the
  # third segment, branch-rebase or force-push workflows lose the in-flight cancel.
  group: ${{ github.workflow }}-${{ github.ref }}-${{ github.event.pull_request.number || github.sha }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}
  # FYI / Round-9 trade-off note: this expression cancels in-progress runs on PR pushes
  # but NEVER cancels main-branch runs OR workflow_dispatch (R28 + D37). If main receives
  # rapid pushes (merge train), multiple ci.yml runs queue serially and can dominate
  # runner-minute budget. A future follow-up may switch to:
  #   cancel-in-progress: ${{ !startsWith(github.ref, 'refs/tags/') }}
  # which cancels rapid main pushes while preserving release-tag runs. Defer until
  # main-push cadence is measured (e.g., 4-week telemetry window post-Phase B).

jobs:
  quality-gate:
    name: 'Quality Gate'
    runs-on: ubuntu-latest
    timeout-minutes: 10   # Per #1228 A5: was inheriting GHA 360-min default; bound for hung pre-commit hooks
    permissions:
      contents: read
    steps:
      - uses: ./.github/actions/setup-env
        with:
          install-project: 'false'   # quality-gate runs hooks/scripts, not pytest
      - run: uv run pre-commit run --all-files --show-diff-on-failure
      # Migrated CI-only invocations (PR 4 will activate these as the deleted-hook replacements):
      - run: uv run python .pre-commit-hooks/check-gam-auth-support.py
      - run: uv run python scripts/hooks/check_response_attribute_access.py
      - run: uv run python .pre-commit-hooks/check_roundtrip_tests.py
      - run: uv run python .pre-commit-hooks/check_code_duplication.py

  type-check:
    name: 'Type Check'
    runs-on: ubuntu-latest
    timeout-minutes: 10   # Per #1228 A5: bound mypy at warm-time + 5 min headroom
    permissions:
      contents: read
    steps:
      - uses: ./.github/actions/setup-env
        with:
          install-project: 'false'   # mypy reads source via path, no install needed
      - run: uv run mypy src/ --config-file=mypy.ini

  schema-contract:
    name: 'Schema Contract'
    runs-on: ubuntu-latest
    timeout-minutes: 10
    permissions:
      contents: read
    services:
      postgres: *postgres-service   # D38: integration test needs DB; tox -e unit unsets DATABASE_URL.
    env:
      DATABASE_URL: *database-url
    steps:
      # schema-contract runs pytest against unit + integration contract tests.
      # tests/integration/test_mcp_contract_validation.py loads the MCP tool registry
      # from the DB, so the integration env (DATABASE_URL set) is required.
      # tox -e unit unsets DATABASE_URL at tox.ini:38; running this under unit would
      # silently fail the DB-dependent assertions or error on connect. Per D38.
      - run: uv run python scripts/ops/migrate.py
      - uses: ./.github/actions/_pytest
        with:
          tox-env: integration
          pytest-args: 'tests/unit/test_adcp_contract.py tests/integration/test_mcp_contract_validation.py -v'

  # ── Per D30: Security Audit, Quickstart, Smoke Tests ──
  # These three jobs preserve regression coverage from `test.yml` (which Phase C deletes).
  # Without them the equivalent capability silently disappears post-rollout.

  security-audit:
    name: 'Security Audit'
    runs-on: ubuntu-latest
    timeout-minutes: 5
    permissions:
      contents: read
    steps:
      - uses: ./.github/actions/setup-env
        with:
          install-project: 'false'
      # Preserves test.yml:15-32 behavior — uvx uv-secure scan with the two existing
      # CVE allowlists (verify the GHSA IDs are still active at PR 3 author time;
      # if a fix has shipped, drop the allowlist entry):
      #   GHSA-7gcm-g887-7qv7  — vulnerable PyPI package transitive (re-verify)
      #   GHSA-5239-wwwm-4pmq  — Pygments AdlLexer ReDoS (CVE-2026-4539); local-only;
      #                          ADL lexer not invoked by this app
      # The pip-audit job in security.yml (PR 1) is complementary, not a replacement —
      # uv-secure consults a different (smaller, uv-curated) advisory set.
      - run: uvx uv-secure --ignore-vulns GHSA-7gcm-g887-7qv7,GHSA-5239-wwwm-4pmq

  quickstart:
    name: 'Quickstart'
    runs-on: ubuntu-latest
    timeout-minutes: 10
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@<SHA>  # v4
        with:
          persist-credentials: false
      # Preserves test.yml:239-285 behavior — full docker-compose stack health check.
      # This is the only CI job that exercises the actual `docker compose up -d` flow that
      # contributors and operators use. Catches docker-compose drift no other job sees.
      - name: Bring up the stack
        run: docker compose up -d --wait
        timeout-minutes: 8
      - name: Verify migration logs
        run: docker compose logs db-init | grep -E "Running migration|migration complete"
      - name: Verify /health endpoint
        run: |
          for i in $(seq 1 30); do
            if curl -fsS http://localhost:8000/health; then exit 0; fi
            sleep 2
          done
          echo "Health check failed after 60s"; docker compose logs; exit 1
      - name: Verify core tables exist
        run: |
          docker compose exec -T postgres psql -U adcp_user -d adcp -c "\dt" | \
            grep -E "media_buys|tenants|products" || { docker compose logs; exit 1; }
      - name: Cleanup (always)
        if: always()
        run: docker compose down -v

  smoke-tests:
    name: 'Smoke Tests'
    runs-on: ubuntu-latest
    timeout-minutes: 5
    permissions:
      contents: read
    steps:
      - uses: ./.github/actions/setup-env
      # Preserves test.yml:34-75 behavior — pytest tests/smoke/ for fast import/migration/startup.
      # No Docker needed; ~30s warm. Fail-fast layer that runs cheap before paying for
      # heavier suites. Includes the @pytest.mark.skip grep gate (test.yml:69-75).
      - name: Run smoke tests
        env:
          ADCP_TESTING: 'true'
          GEMINI_API_KEY: 'test_key_for_mocking'
          PYTEST_CURRENT_TEST: 'true'
        run: uv run pytest tests/smoke/ -v --tb=short
      - name: Skip-decorator hygiene gate
        # This gate must run somewhere CI-side after Phase C deletes test.yml.
        # `repo-invariants` pre-commit hook also covers it, but contributors who skip pre-commit
        # would otherwise ship `@pytest.mark.skip` decorators undetected.
        run: |
          if grep -r "@pytest.mark.skip" tests/ --include="test_*.py" | grep -v "skip_ci"; then
            echo "❌ Found @pytest.mark.skip without skip_ci label"
            exit 1
          fi
          echo "✅ No skip decorators found"

  unit-tests:
    name: 'Unit Tests'
    runs-on: ubuntu-latest
    timeout-minutes: 15
    permissions:
      contents: read
    steps:
      - uses: ./.github/actions/_pytest
        with:
          tox-env: unit

  integration-tests:
    name: 'Integration Tests'
    runs-on: ubuntu-latest
    timeout-minutes: 30
    permissions:
      contents: read
    services:
      # Only the salesagent's own Postgres lives as a `services:` container. Creative-agent
      # and its Postgres CANNOT be `services:` because GitHub Actions service containers
      # cannot resolve each other by hostname (they're each on their own bridge network
      # with the runner host). They're started as script steps below using
      # `docker network create + docker run` — matching the existing `test.yml:180-223`
      # pattern. (Per D39; revoked an earlier services-block draft.)
      postgres: *postgres-service
    env:
      DATABASE_URL: *database-url
      # CREATIVE_AGENT_URL value comes from the docker-run step below; default points at
      # the host-port-mapped creative-agent container (port 9999 host → 8080 container,
      # matching test.yml:200). Path is `/api/creative-agent` because creative-agent is
      # one route in the adcp monolith (per `test.yml:215`).
      CREATIVE_AGENT_URL: 'http://localhost:9999/api/creative-agent'
    steps:
      - uses: ./.github/actions/setup-env
      - run: uv run python scripts/ops/migrate.py

      # Per D32 + D39 — start the creative-agent stack.
      # Pinned commit `ca70dd1e2a6c` per test.yml:186; bump and re-verify at PR-3 author
      # time per D32 tripwire and pre-flight A23. The reference creative agent
      # is part of the adcp monolith repo — built from a source tarball, not pulled from
      # GHCR. NODE_ENV=production matches test.yml:201 (production code path with mock
      # WorkOS via the documented test-mode env vars).
      - name: Build creative-agent image (from pinned source tarball)
        run: |
          mkdir -p /tmp/adcp-server
          curl -sL https://github.com/adcontextprotocol/adcp/archive/ca70dd1e2a6c.tar.gz \
            | tar xz -C /tmp/adcp-server --strip-components=1
          docker build -t adcp-creative-agent /tmp/adcp-server

      - name: Create creative-agent docker network
        run: docker network create creative-net

      - name: Start creative-agent's Postgres
        run: |
          docker run -d --network creative-net --name adcp-postgres \
            -e POSTGRES_DB=adcp_registry \
            -e POSTGRES_USER=adcp \
            -e POSTGRES_PASSWORD=localdev \
            postgres:16-alpine
          # Wait for adcp-postgres to be ready (container's pg_isready)
          for i in $(seq 1 30); do
            if docker exec adcp-postgres pg_isready -U adcp > /dev/null 2>&1; then
              echo "adcp-postgres ready"; break
            fi
            sleep 2
          done

      - name: Start creative-agent
        run: |
          docker run -d --network creative-net --name creative-agent \
            -p 9999:8080 \
            -e NODE_ENV=production \
            -e PORT=8080 \
            -e DATABASE_URL=postgresql://adcp:localdev@adcp-postgres:5432/adcp_registry \
            -e RUN_MIGRATIONS=true \
            -e ALLOW_INSECURE_COOKIES=true \
            -e DEV_USER_EMAIL=ci@test.com \
            -e DEV_USER_ID=ci-user \
            -e AGENT_TOKEN_ENCRYPTION_SECRET='local-ci-encryption-key-32chars!!' \
            -e WORKOS_API_KEY=sk_test_dummy \
            -e WORKOS_CLIENT_ID=client_dummy \
            adcp-creative-agent

      - name: Wait for creative-agent health
        # Health probe runs on the runner host (where curl is preinstalled), NOT inside
        # the creative-agent container — per D39 avoids the "curl missing
        # in Node image" silent-timeout. 60×2s = 120s budget, matches test.yml:213-223.
        run: |
          for i in $(seq 1 60); do
            if curl -sf http://localhost:9999/api/creative-agent/health > /dev/null 2>&1; then
              echo "Creative agent healthy on :9999"
              exit 0
            fi
            sleep 2
          done
          echo "Creative agent failed to start"
          docker logs creative-agent | tail -50
          docker logs adcp-postgres | tail -20
          exit 1

      - uses: ./.github/actions/_pytest
        with:
          tox-env: integration
          xdist-workers: 'auto'

      - name: Cleanup creative-agent stack
        if: always()
        run: |
          docker rm -f creative-agent adcp-postgres 2>/dev/null || true
          docker network rm creative-net 2>/dev/null || true

  e2e-tests:
    name: 'E2E Tests'
    runs-on: ubuntu-latest
    timeout-minutes: 25
    permissions:
      contents: read
    services:
      postgres: *postgres-service
    env:
      DATABASE_URL: *database-url
    steps:
      - run: uv run python scripts/ops/migrate.py
      - uses: ./.github/actions/_pytest
        with:
          tox-env: e2e

  admin-tests:
    name: 'Admin UI Tests'
    runs-on: ubuntu-latest
    timeout-minutes: 20
    permissions:
      contents: read
    services:
      postgres: *postgres-service
    env:
      DATABASE_URL: *database-url
    steps:
      - run: uv run python scripts/ops/migrate.py
      - uses: ./.github/actions/_pytest
        with:
          tox-env: admin

  bdd-tests:
    name: 'BDD Tests'
    runs-on: ubuntu-latest
    timeout-minutes: 20
    permissions:
      contents: read
    services:
      postgres: *postgres-service
    env:
      DATABASE_URL: *database-url
    steps:
      - run: uv run python scripts/ops/migrate.py
      - uses: ./.github/actions/_pytest
        with:
          tox-env: bdd

  migration-roundtrip:
    name: 'Migration Roundtrip'
    runs-on: ubuntu-latest
    timeout-minutes: 10   # Per #1228 A5: alembic upgrade→downgrade→upgrade against fresh PG; ~3-5 min typical
    permissions:
      contents: read
    services:
      # Anchor merge with override: roundtrip job uses a distinct DB name (`roundtrip`).
      # YAML merge-key (`<<`) replaces only `env.POSTGRES_DB`, inheriting image/options/ports.
      postgres:
        <<: *postgres-service
        env:
          POSTGRES_USER: adcp_user
          POSTGRES_PASSWORD: test_password
          POSTGRES_DB: roundtrip
    steps:
      - uses: ./.github/actions/setup-env
      - env:
          # Note: Migration-roundtrip uses a literal DATABASE_URL with database name `roundtrip`
          # rather than the YAML anchor's default `adcp_test`. The merge-key pattern
          # (`<<: *postgres-service`) replaces `env.POSTGRES_DB` at the service level, but the
          # step's DATABASE_URL must point to the renamed database. Intentional divergence —
          # both forms valid, neither contradicts the anchor pattern.
          DATABASE_URL: postgresql://adcp_user:test_password@localhost:5432/roundtrip
        run: bash .github/scripts/migration_roundtrip.sh

  coverage:
    name: 'Coverage'
    runs-on: ubuntu-latest
    timeout-minutes: 10   # Per #1228 A5: combine + report; serial after parallel test suites
    needs: [unit-tests, integration-tests, e2e-tests, admin-tests, bdd-tests]
    permissions:
      contents: read
      pull-requests: write   # for coverage comment
    steps:
      - uses: ./.github/actions/setup-env
      - uses: actions/download-artifact@<SHA>  # v4
        with:
          pattern: 'coverage-*'
          merge-multiple: true
          path: .coverage-data/
      - run: |
          uv run coverage combine .coverage-data/.coverage.*
          uv run coverage report --fail-under=$(cat .coverage-baseline)
          uv run coverage json -o coverage.json
          uv run coverage html
      - uses: actions/upload-artifact@<SHA>  # v4
        with:
          name: coverage-html
          path: htmlcov/
      - if: github.event_name == 'pull_request'
        uses: py-cov-action/python-coverage-comment-action@<SHA>  # v3
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          MINIMUM_GREEN: 80
          MINIMUM_ORANGE: 60
          ANNOTATE_MISSING_LINES: true
        # Per D11: hard-gate from PR 3 day 1 at 53.5%.
        # `--fail-under=$(cat .coverage-baseline)` above blocks merge on regression.
        # Ratchet upward only when measured-stable across 4+ consecutive PRs.
        # MINIMUM_GREEN/ORANGE drive the comment's badge color (visual only — does not gate).
        # ANNOTATE_MISSING_LINES adds inline PR annotations to uncovered changed lines.

  summary:
    name: 'Summary'
    runs-on: ubuntu-latest
    timeout-minutes: 5    # Per #1228 A5: aggregation only; should complete in seconds
    needs:
      - quality-gate
      - type-check
      - schema-contract
      - security-audit       # Per D30
      - quickstart           # Per D30
      - smoke-tests          # Per D30
      - unit-tests
      - integration-tests
      - e2e-tests
      - admin-tests
      - bdd-tests
      - migration-roundtrip
      - coverage
    if: always()
    permissions: {}
    steps:
      - name: Aggregate job results
        run: |
          echo "All required CI jobs reported above."
          # If any required job failed, this job fails too via needs:
          # GitHub auto-fails this if a needed job failed.
```

Verification:
```bash
test -f .github/workflows/ci.yml
yamllint -d relaxed .github/workflows/ci.yml
# YAML anchor parsing — actionlint validates GHA-specific schema (anchors are baseline
# YAML 1.2 but actionlint catches semantic mismatches under merge-keys)
actionlint .github/workflows/ci.yml
# Workflow header is `name: CI` (D26) — GitHub auto-prefixes job names
grep -qE '^name:\s+CI\s*$' .github/workflows/ci.yml
# All 14 frozen BARE job names present (D26: bare, no 'CI /' prefix; D30 expanded 11→14)
for name in 'Quality Gate' 'Type Check' 'Schema Contract' 'Security Audit' 'Quickstart' 'Smoke Tests' 'Unit Tests' 'Integration Tests' 'E2E Tests' 'Admin UI Tests' 'BDD Tests' 'Migration Roundtrip' 'Coverage' 'Summary'; do
  grep -qF "name: '$name'" .github/workflows/ci.yml || \
    grep -qF "name: \"$name\"" .github/workflows/ci.yml || \
    grep -qE "^\s+name:\s+$name\s*$" .github/workflows/ci.yml
done
# No 'CI /' prefix in job names (the D26 bug — would render as 'CI / CI / X')
! grep -qE "name:\s+['\"]CI / " .github/workflows/ci.yml
# concurrency gated to pull_request only (R28: cancel-in-progress must NOT cancel push/dispatch)
grep -qE '^permissions:\s*\{?\s*\}?' .github/workflows/ci.yml
grep -qE 'concurrency:' .github/workflows/ci.yml
```

#### Commit 4 — `ci: add migration_roundtrip.sh script`

Files:
- `.github/scripts/migration_roundtrip.sh` (new, executable)

```bash
#!/usr/bin/env bash
# Migration Roundtrip Test
# Verifies: empty -> upgrade head -> downgrade base -> upgrade head produces identical schema
set -euo pipefail

: "${DATABASE_URL:?DATABASE_URL must be set}"

psql "$DATABASE_URL" -c 'DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;' >/dev/null

# 1. Upgrade head from empty
uv run alembic -c alembic.ini upgrade head
SCHEMA_AFTER_UP=$(pg_dump --schema-only --no-owner "$DATABASE_URL" | grep -v '^--' | sha256sum)

# 2. Downgrade base
uv run alembic -c alembic.ini downgrade base
TABLES=$(psql "$DATABASE_URL" -At -c \
  "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_name != 'alembic_version'")
[[ "$TABLES" == "0" ]] || { echo "downgrade leaked $TABLES tables"; exit 1; }

# 3. Re-upgrade head
uv run alembic -c alembic.ini upgrade head
SCHEMA_AFTER_REUP=$(pg_dump --schema-only --no-owner "$DATABASE_URL" | grep -v '^--' | sha256sum)
[[ "$SCHEMA_AFTER_UP" == "$SCHEMA_AFTER_REUP" ]] || \
  { echo "schema drift across roundtrip"; exit 1; }

# 4. Single head
HEADS=$(uv run alembic heads | wc -l)
[[ "$HEADS" == "1" ]] || { echo "alembic has $HEADS heads, expected 1"; exit 1; }

echo "Migration roundtrip OK"
```

Verification:
```bash
test -x .github/scripts/migration_roundtrip.sh
bash -n .github/scripts/migration_roundtrip.sh   # syntax check
```

#### Commit 4b — `test: integration_db template-clone optimization`

Today's `integration_db` fixture (`tests/conftest_db.py:323-528`) does per-test `CREATE DATABASE` + `Base.metadata.create_all` (~30 tables) costing ~400-900ms per test. With 600 integration tests under xdist, this saturates Postgres connection limits and dominates suite wall-clock.

Convert `tests/conftest_db.py` to:

1. **Session-scoped fixture** creates ONE `template_db_<worker_id>` with `Base.metadata.create_all` (uses Postgres template-DB feature; one template per xdist worker to avoid cross-worker contention).
2. **Function-scoped `integration_db`** clones via `CREATE DATABASE foo TEMPLATE template_db_<worker_id>` (~10-50× faster than full schema creation — Postgres copies the template's data files instead of replaying DDL).
3. **Cleanup**: per-test `DROP DATABASE foo` (template stays). Worker-end finalizer drops the template.

Files:
- `tests/conftest_db.py` (modify lines ~323-528)

Acceptance: pre/post timing on integration suite recorded in PR description. Target: ≥3× speedup on suite wall-clock under `pytest -n 8`.

Verification:
```bash
# Before this commit (record baseline)
time tox -e integration -- -n auto
# After this commit (expect ≥3× faster)
time tox -e integration -- -n auto
# Both timings recorded in the PR description; reviewer confirms threshold met.
```

**Sub-fix — worker-id-suffix tox json-report paths.** `tox.ini:42, 52, 62, 71, 83, 92` all write json-report to a fixed path; under xdist, N workers race on the same file and only one's output survives (or the file ends up corrupt). Update each affected env's pytest invocation:

```ini
# Before (one of six identical lines):
--json-report-file={toxworkdir}/<env>.json

# After:
--json-report-file={toxworkdir}/<env>-{env:PYTEST_XDIST_WORKER:gw0}.json
```

The `{env:PYTEST_XDIST_WORKER:gw0}` substitution gives each xdist worker its own file (`gw0`, `gw1`, ...); the `:gw0` default keeps non-xdist runs valid. Coverage-combine and JSON-aggregator steps must adapt to glob `*-gw*.json`.

**Note:** the filelock + worker-id gate around `migrate.py` is its own commit (4c, below) — promoted from prose-only embedded "sub-fix" to a reviewable standalone change.

#### Commit 4c — `test: filelock + worker-id gate around migrate.py for xdist safety`

The session-scoped fixture at `tests/conftest_db.py:79-81` runs `subprocess.run([..., "scripts/ops/migrate.py"])` on every worker startup. Under xdist with N workers (`-n auto` per commit 4b's xdist-workers='auto'), that's N concurrent `alembic upgrade head` invocations racing on the same DB. Symptoms include `IntegrityError: duplicate key` on alembic_version, partial schema, or first-worker-wins schema with later workers running against incomplete state.

Gate so only `gw0` runs migrations; other workers wait via filelock and find the schema at head when the lock releases (alembic upgrade head is idempotent — re-running on already-migrated schema is a no-op).

Files:
- `tests/conftest_db.py` (modify lines ~79-81)

```python
# tests/conftest_db.py — replace the unconditional migrate.py call
import os
import subprocess
from filelock import FileLock

def _run_migrations_once(template_dsn):
    worker = os.environ.get("PYTEST_XDIST_WORKER", "gw0")
    # Lock file under root_tmp_dir is per-test-session; pgid keeps separate sessions isolated.
    lock_path = f"/tmp/salesagent-migrate-{os.getpgid(0)}.lock"
    with FileLock(lock_path, timeout=120):
        # gw0 runs migrations under the lock; other workers acquire after gw0 releases
        # and skip (idempotency check below). alembic upgrade head is already idempotent
        # — running it twice on a fully-migrated schema is a no-op — but we skip the
        # subprocess overhead for the common case.
        if worker == "gw0":
            subprocess.run(["uv", "run", "python", "scripts/ops/migrate.py"], check=True)
```

`filelock` is already a main dependency at `pyproject.toml:48` (`filelock>=3.20.3`); no additional add needed for this commit. (PR 2 commit 4.5 adds `pytest-xdist` and `pytest-randomly` to the dev group per D33; filelock stays where it is.)

**Why a standalone commit:** this race-condition fix was previously embedded in commit 4b's prose. Without a standalone commit, the change either (a) ships unreviewed inside commit 4b's diff, or (b) is silently dropped during executor handoff. Promoted here for explicit reviewability.

Verification:
```bash
# Filelock import present in conftest_db.py
grep -q 'from filelock import FileLock' tests/conftest_db.py
# Worker-id gate present
grep -q 'PYTEST_XDIST_WORKER' tests/conftest_db.py
# Run xdist soak — must be green for ≥3 consecutive runs at -n 4 AND ≥1 run at -n auto
DATABASE_URL="postgresql://adcp_user:test_password@localhost:5432/adcp_test" \
  tox -e integration -- -n 4
```

#### Commit 5 — Resolve SHA placeholders by consuming `.github/.action-shas.txt`

**What:** PR 1 commit 9 produced `.github/.action-shas.txt` with format `<ref>\t<sha>\t<tag>` per line. PR 3 commits 1-3 introduced new `<SHA>` placeholders for any new actions in `ci.yml` and the composite actions. Commit 5 reads `.action-shas.txt` and substitutes each `<SHA>` placeholder with its resolved value.

**Why:** Re-running the SHA-resolution loop wastes GitHub API quota AND creates a race window where a freshly-resolved SHA differs from PR 1's. Consuming the artifact preserves PR 1's frozen SHAs.

**How:**
```bash
# For each <SHA> placeholder in any new workflow/composite, look up in artifact:
for placeholder_file in .github/workflows/ci.yml .github/actions/_pytest/action.yml .github/actions/setup-env/action.yml; do
  while IFS= read -r line; do
    if [[ "$line" == *@\<SHA\>* ]]; then
      # Extract action ref, look up in .action-shas.txt
      ref=$(echo "$line" | sed -E 's|.*uses: ([^@]+).*|\1|')
      sha=$(awk -v ref="$ref" '$1 == ref {print $2}' .github/.action-shas.txt)
      [[ -n "$sha" ]] || { echo "ERROR: no SHA for $ref in .action-shas.txt"; exit 1; }
      sed -i.bak "s|${ref}@<SHA>|${ref}@${sha}|g" "$placeholder_file"
    fi
  done < "$placeholder_file"
  rm "${placeholder_file}.bak"
done
```

**Verification:** `! grep -rE 'uses: [^@]+@<SHA>' .github/` (no remaining placeholders) AND every new SHA must appear in `.github/.action-shas.txt`.

```bash
# No <SHA> placeholders remain anywhere in .github/
! grep -rE 'uses: [^@]+@<SHA>' .github/
# Every resolved SHA in PR 3's new files appears in .action-shas.txt (single source of truth)
for sha in $(grep -hoE 'uses: [^ ]+@[a-f0-9]{40}' .github/workflows/ci.yml .github/actions/_pytest/action.yml .github/actions/setup-env/action.yml \
              | sed -E 's|.*@([a-f0-9]{40})|\1|' | sort -u); do
  grep -q "$sha" .github/.action-shas.txt || { echo "ERROR: SHA $sha not in .action-shas.txt"; exit 1; }
done
[[ $(grep -hoE 'uses: [^ ]+@[a-f0-9]{40}' .github/workflows/ci.yml .github/actions/_pytest/action.yml .github/actions/setup-env/action.yml | wc -l) -ge "5" ]]
```

#### Commit 6 — `ci: add coverage baseline + sync tox.ini coverage gate`

Files:
- `.coverage-baseline` (new, contents: `53.5`)
- `tox.ini:106` (modify)

Per D11, hard-gate from PR 3 day 1. Set to current measured (55.56% from pre-flight A7) minus 2pp safety margin. Coverage job uses `--fail-under=$(cat .coverage-baseline)`. Ratchet upward only when measured-stable across 4+ consecutive PRs. The earlier "advisory for 4 weeks" framing was contradicted by the actual implementation (`--fail-under` is a hard gate); aligning the framing with the implementation eliminates ambiguity.

`tox.ini:106` currently reads `coverage report --fail-under=30`, drifting from the CI baseline of 53.5. Sync the local-tox path to read from the same source-of-truth file:

```ini
[testenv:coverage]
...
commands =
    coverage combine {toxworkdir}
    bash -c 'coverage report --fail-under=$(cat {toxinidir}/.coverage-baseline 2>/dev/null || echo 30)'
    coverage html -d {toxinidir}/htmlcov
    coverage json -o {toxinidir}/coverage.json
```

The subshell form keeps `.coverage-baseline` as the single source of truth for both CI and local `tox -e coverage` runs. The `|| echo 30` fallback preserves the historical floor if the file is absent (e.g., on a stale checkout).

Verification:
```bash
test -f .coverage-baseline
[[ "$(cat .coverage-baseline)" == "53.5" ]]
# tox.ini gate references .coverage-baseline (not a literal 30 anymore)
grep -qE '\.coverage-baseline' tox.ini
! grep -qE 'coverage report --fail-under=30\b' tox.ini
```

#### Commit 7 — `ci: remove || true and continue-on-error from ruff invocations`

Files:
- `.github/workflows/test.yml` (modify lines 382-387)

Closes #1233 D6. Remove the 4 lines:

```yaml
# Before:
      - run: uv run ruff check . --config pyproject.toml || true
        continue-on-error: true
      - run: uv run ruff format . --check || true
        continue-on-error: true

# After (or these are removed entirely if they live in ci.yml's quality-gate now):
      - run: uv run ruff check . --config pyproject.toml
      - run: uv run ruff format . --check
```

Verification:
```bash
[[ $(grep -E '\|\| true|continue-on-error' .github/workflows/test.yml | grep -E 'ruff|lint' | wc -l) == "0" ]]
```

#### Commit 8 — `ci: dynamic ADCP_SALES_PORT in e2e`

Files:
- `.github/workflows/test.yml:347` (modify)

Closes #1233 D5, D14. Remove `ADCP_SALES_PORT=8080` hardcode; conftest already picks a port dynamically.

```yaml
# Before:
        env:
          ADCP_SALES_PORT: 8080
          # ... other env

# After:
        env:
          # ADCP_SALES_PORT picked dynamically by tests/e2e/conftest.py
          # ... other env (without the hardcoded port)
```

Verification:
```bash
! grep -q 'ADCP_SALES_PORT: 8080' .github/workflows/test.yml
```

#### Commit 9 — `ci: unconditional creative agent in integration; permissions blocks (D32 full bootstrap)`

Closes #1233 D12, PD15. **Per D32 + D39:** spec content matches disk truth (`test.yml:180-223`); creative-agent runs as docker-run script-steps on a custom `creative-net` Docker network (NOT as `services:` blocks — GHA service containers can't resolve each other by hostname).

Files:
- `.github/workflows/ci.yml` `integration-tests` job (already authored in commit 3 — this commit is the bootstrap that makes the integration job actually run, NOT a separate workflow change)
- All workflows missing top-level `permissions:` block (PR 1 commit 9 should have done this; verify)

The integration-tests job in commit 3 already declares the docker-network + script-step bootstrap (matches `test.yml:180-223` verbatim with corrections for `xdist-workers: 'auto'` from commit 4b and 14-name frozen list). This commit's role is to:

1. **Re-verify the pinned source-tarball SHA at PR-3 author time.** Per D32, the pinned commit is `ca70dd1e2a6c` per `test.yml:186` (the adcp monolith repo source tarball, NOT a Docker image — creative-agent is one route in the full app and is built locally via `docker build`). If a stable release has been tagged upstream since 2026-04-26, prefer the release SHA. Update both the curl URL and the verification grep.

2. **Verify environment variables match the upstream creative-agent's expected schema.** The upstream may have added required env vars since the original audit. Inspect the pinned source:
   ```bash
   curl -sL https://github.com/adcontextprotocol/adcp/archive/ca70dd1e2a6c.tar.gz \
     | tar xz -C /tmp/adcp-pinned --strip-components=1
   grep -RhE 'process\.env\.[A-Z_]+' /tmp/adcp-pinned/src/ 2>/dev/null \
     | sort -u | head -30
   ```
   Confirm the 10 env vars (table below) are still authoritative; flag any new required vars to the user before authoring.

3. **Confirm the health-check endpoint** is still `/api/creative-agent/health` (NOT `/health` — the creative agent is one route in the adcp monolith; the adcp app prefixes `/api/creative-agent`). If the upstream relocated the route, update both the curl URL and the `CREATIVE_AGENT_URL` env value.

4. **Audit `permissions:` blocks** across all workflows (`test.yml` is being deleted in Phase C, but until then both old + new workflows run — verify both have explicit `permissions: {}` at top-level).

The 10 env vars (per D32 + D39 — values match `test.yml:201-211` verbatim):

| Var | Value | Purpose |
|---|---|---|
| `NODE_ENV` | `production` | Production code path; mock identity provided via `DEV_USER_*` + `WORKOS_*` test-mode flags below |
| `PORT` | `8080` | Container internal port; mapped to host `9999` for `CREATIVE_AGENT_URL` reach |
| `DATABASE_URL` | `postgresql://adcp:localdev@adcp-postgres:5432/adcp_registry` | adcp monolith's own DB (separate from salesagent's `adcp_test`); resolves via `creative-net` Docker network |
| `RUN_MIGRATIONS` | `true` | Auto-migrate on startup |
| `ALLOW_INSECURE_COOKIES` | `true` | HTTP-only test mode (no HTTPS in CI) |
| `DEV_USER_EMAIL` | `ci@test.com` | Mock identity bound to test JWT |
| `DEV_USER_ID` | `ci-user` | Mock identity bound to test JWT |
| `AGENT_TOKEN_ENCRYPTION_SECRET` | `local-ci-encryption-key-32chars!!` | Test-only mock; 32-char string required for AES-256-GCM init in adcp |
| `WORKOS_API_KEY` | `sk_test_dummy` | Test-mode bypass; adcp's WorkOS client doesn't validate against real WorkOS for `sk_test_*` keys |
| `WORKOS_CLIENT_ID` | `client_dummy` | Test-mode bypass paired with `sk_test_dummy` |

**No real secrets in CI.** All 10 values are test-mode mocks that match `test.yml:201-211` verbatim. The mock-identity path is exercised by adcp's `NODE_ENV=production` plus `WORKOS_API_KEY=sk_test_*` heuristic. The `AGENT_TOKEN_ENCRYPTION_SECRET` is intentionally a literal string published in the repo — its only role is satisfying the AES-256-GCM init's 32-byte requirement during integration tests; production secret rotation is out of scope.

Verification:
```bash
# Workflow has the integration-tests job with the docker-run step pattern (NOT services blocks)
! grep -qE '^\s+creative-agent:' .github/workflows/ci.yml   # negative: no services-block creative-agent
! grep -qE '^\s+creative-postgres:' .github/workflows/ci.yml # negative: no services-block creative-postgres
grep -q 'docker network create creative-net' .github/workflows/ci.yml
grep -q 'docker run -d --network creative-net --name creative-agent' .github/workflows/ci.yml
grep -q 'docker run -d --network creative-net --name adcp-postgres' .github/workflows/ci.yml
grep -q 'ca70dd1e2a6c' .github/workflows/ci.yml

# All 10 env vars enumerated in the docker run step
for var in NODE_ENV PORT DATABASE_URL RUN_MIGRATIONS ALLOW_INSECURE_COOKIES \
           DEV_USER_EMAIL DEV_USER_ID AGENT_TOKEN_ENCRYPTION_SECRET \
           WORKOS_API_KEY WORKOS_CLIENT_ID; do
  grep -q "${var}=" .github/workflows/ci.yml || { echo "missing creative-agent env: $var"; exit 1; }
done

# CREATIVE_AGENT_URL exported on integration-tests job, with /api/creative-agent path
grep -qE "CREATIVE_AGENT_URL: 'http://localhost:9999/api/creative-agent'" .github/workflows/ci.yml

# Health probe runs on the host (curl available there), NOT inside the container (curl missing in node images)
grep -A 5 'Wait for creative-agent health' .github/workflows/ci.yml | grep -q 'curl -sf http://localhost:9999/api/creative-agent/health'

# Cleanup-on-always step exists
grep -q 'docker rm -f creative-agent adcp-postgres' .github/workflows/ci.yml

# All workflows have explicit top-level permissions:
for f in .github/workflows/*.yml; do
  grep -qE '^permissions:' "$f" || { echo "missing perms: $f"; exit 1; }
done
```

#### Commit 10 — `ci: schema-alignment fail-hard on network errors`

Closes #1233 D10. Find the `pytest.skip` on network error pattern and replace with hard failure.

Files:
- The test file (likely `tests/integration/test_schema_alignment.py` or similar; locate via `grep -rn 'pytest.skip.*network'`)

Verification:
```bash
! grep -rn 'pytest.skip.*network\|pytest.skip.*connection' tests/integration/ tests/unit/
```

#### Commit 11 — `ci: gate Gemini key behind unconditional mock` (moved from PR 1)

Closes PD24, per D15. **MOVED from PR 1 commit 10** — PR 3 rewrites
`test.yml` wholesale into `ci.yml` + `_pytest` composite, so applying the Gemini fix on the
old `test.yml` in PR 1 just to have PR 3 rewrite the same region is wasted work. The fix
lands here on the new structure.

Files:
- `.github/actions/_pytest/action.yml` (modify the `env:` block in the `Run tox env` step)

Replace any reference to `${{ secrets.GEMINI_API_KEY || 'test_key_for_mocking' }}` with
unconditional `GEMINI_API_KEY: test_key_for_mocking`. The composite's env block:

```yaml
# Before (if migrated literally from test.yml):
      env:
        ADCP_TESTING: 'true'
        COVERAGE_FILE: .coverage.${{ inputs.tox-env }}
        GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY || 'test_key_for_mocking' }}

# After (D15: unconditional mock):
      env:
        ADCP_TESTING: 'true'
        COVERAGE_FILE: .coverage.${{ inputs.tox-env }}
        GEMINI_API_KEY: test_key_for_mocking
```

Rationale (per D15): no e2e test invokes a live Gemini client; `src/core/config.py:141`
documents the key as optional; `src/core/tools/creatives/_processing.py:177-182` handles
no-key with a clear error. Unconditional mock removes the silent secret dependency.

Verification:
```bash
! grep -RnE 'secrets\.GEMINI_API_KEY' .github/
grep -RnE 'GEMINI_API_KEY:\s+test_key_for_mocking' .github/actions/_pytest/action.yml
# Confirm test.yml is gone (PR 3 deleted it in Phase C; or the original env block is gone if Phase C hasn't run)
[[ ! -f .github/workflows/test.yml ]] || ! grep -q 'secrets.GEMINI_API_KEY' .github/workflows/test.yml
```

### Phase B — Atomic flip (admin action only, no PR)

After Phase A has been on main for ≥48 hours and the new check names appear green on at least 2-3 PRs:

#### Step 1 — Verify pre-flip snapshot exists

```bash
test -f .claude/notes/ci-refactor/branch-protection-snapshot.json   # from pre-flight A1
```

#### Step 1b — Capture the **actually-rendered** check names from the latest Phase A PR

GitHub renders status checks as `<workflow.name> / <job.name>` and the branch-protection `context` field requires an exact-string match. Reusable workflow calls can produce 3-segment names (e.g., `CI / Unit Tests / pytest`) instead of the expected 2-segment names. Verify before flipping.

```bash
# Resolve the SHA to probe. Prefer an explicit argument (avoids fragile string-search
# on PR title); fall back to the most recent merged PR title-search if not provided.
PR_SHA="${PR_SHA:-$(gh pr list --state merged --limit 1 --search "phase a" --json headRefOid --jq '.[0].headRefOid')}"
[[ -n "$PR_SHA" ]] || { echo "ERROR: could not resolve PR_SHA — set explicitly: PR_SHA=<sha>" >&2; exit 2; }

# Capture every check-run name GitHub published for that SHA. --paginate is mandatory:
# nested workflows + retries can produce >30 check-runs (default page size).
gh api "repos/prebid/salesagent/commits/${PR_SHA}/check-runs" --paginate \
  --jq '.check_runs[].name' | sort -u > /tmp/rendered-names.txt

# Compare to the 14 expected names (D17 amended by D30) — they must match the Step 2 PATCH body exactly
cat <<'EOF' | sort -u > /tmp/expected-names.txt
CI / Quality Gate
CI / Type Check
CI / Schema Contract
CI / Security Audit
CI / Quickstart
CI / Smoke Tests
CI / Unit Tests
CI / Integration Tests
CI / E2E Tests
CI / Admin UI Tests
CI / BDD Tests
CI / Migration Roundtrip
CI / Coverage
CI / Summary
EOF

# These must each appear as a substring/exact match in /tmp/rendered-names.txt
diff /tmp/expected-names.txt <(grep -F -f /tmp/expected-names.txt /tmp/rendered-names.txt | sort -u) \
  || { echo "Rendered names diverge from expected. Inspect /tmp/rendered-names.txt and update PATCH body in Step 2 to match exact strings before flipping."; exit 1; }
```

If the diff fails AFTER applying Decision-4 (composite migration), the failure indicates a regression — a reusable workflow has been re-introduced somewhere. The structural guard `test_architecture_required_ci_checks_frozen.py` should have caught this before the soak; if it didn't, audit `ci.yml` for `uses: ./.github/workflows/_*.yml` and convert to composite. Do NOT flip Phase B until rendered names are 2-segment for all 14 checks.

Pre-Decision-4 historical note: the original plan used a reusable workflow `_pytest.yml` and accepted the 3-segment rendered name as a runtime concern (flatten on detection). Decision-4 eliminates this class of bug at design time by mandating composite actions.

#### Step 2.5 — Capture in-flight PRs

Before flipping, snapshot every open non-draft PR so post-flip drain has a definitive list:

```bash
gh pr list --state open --search "draft:false" --json number,headRefOid \
  > /tmp/inflight-prs.json
```

After Step 2 (the atomic flip), each PR in `/tmp/inflight-prs.json` is in one of two states:
- (a) Has a CI run completed against the OLD check list. Status checks evaluated against new required-checks list will report "expected — waiting for status to be reported" until the PR's next push.
- (b) Has CI in-flight. Will complete and report under the OLD job names; new required names won't appear without a re-run.

Choose ONE drain strategy per PR (document in the Phase B coordination notes):

```bash
# Option A — kick a fresh ci.yml run on each in-flight PR (preferred for active PRs):
jq -r '.[] | "\(.number) \(.headRefOid)"' /tmp/inflight-prs.json | while read num sha; do
  gh workflow run ci.yml --ref "refs/pull/$num/head"
done

# Option B — accept "expected — waiting for status to be reported" until PR's next push
# (no action needed; PR authors push or rebase as normal).
```

Option A wins for high-traffic windows (≥5 open PRs); Option B is acceptable when stale PRs exist that should be rebased anyway.

#### Step 2 — Atomic flip via `gh api`

```bash
gh api -X PATCH \
  /repos/prebid/salesagent/branches/main/protection/required_status_checks \
  -H "Accept: application/vnd.github+json" \
  --input - <<'EOF'
{
  "strict": true,
  "checks": [
    {"context": "CI / Quality Gate"},
    {"context": "CI / Type Check"},
    {"context": "CI / Schema Contract"},
    {"context": "CI / Security Audit"},
    {"context": "CI / Quickstart"},
    {"context": "CI / Smoke Tests"},
    {"context": "CI / Unit Tests"},
    {"context": "CI / Integration Tests"},
    {"context": "CI / E2E Tests"},
    {"context": "CI / Admin UI Tests"},
    {"context": "CI / BDD Tests"},
    {"context": "CI / Migration Roundtrip"},
    {"context": "CI / Coverage"},
    {"context": "CI / Summary"}
  ]
}
EOF
```

`app_id` is intentionally omitted — allows any GitHub App (incl. GitHub Actions) to satisfy each check.

#### Step 3 — Verify

```bash
gh api repos/prebid/salesagent/branches/main/protection/required_status_checks \
  --jq '.checks[].context' | sort > /tmp/protected   # NB: `.checks[].context` is the modern field; `.contexts[]` is deprecated
cat <<'EOF' | sort > /tmp/expected
CI / Quality Gate
CI / Type Check
CI / Schema Contract
CI / Security Audit
CI / Quickstart
CI / Smoke Tests
CI / Unit Tests
CI / Integration Tests
CI / E2E Tests
CI / Admin UI Tests
CI / BDD Tests
CI / Migration Roundtrip
CI / Coverage
CI / Summary
EOF
diff /tmp/protected /tmp/expected
```

Expected diff: empty.

#### Step 4 — Open a test PR

Open a trivial PR (e.g., add a comment) and confirm all 14 new check names show as required. If any fail unexpectedly, see rollback.

### Phase C — Cleanup (small follow-up PR)

After Phase B has been stable for ≥48 hours:

#### Commit 1 — `chore(ci): delete legacy test.yml workflow`

Files:
- `.github/workflows/test.yml` (delete)

Verification:
```bash
! test -f .github/workflows/test.yml
gh run list --workflow=ci.yml --branch=main --limit=3 --json conclusion --jq '[.[].conclusion] | all(. == "success")'
```

#### Commit 2 — `docs: update ci-pipeline.md to reflect ci.yml authoritative`

Files:
- `docs/development/ci-pipeline.md` (PR 4 will rewrite this; this commit only updates the "current state" section to point to `ci.yml`)

## Acceptance criteria

From issue #1234 §Acceptance criteria, scoped to PR 3:

- [ ] Composite action `.github/actions/setup-env/action.yml` exists
- [ ] Composite action `_pytest` exists at `.github/actions/_pytest/action.yml` with `using: composite` (Decision-4 — NOT a reusable workflow)
- [ ] No `.github/workflows/_pytest.yml` file (confirms composite migration; reusable form would re-introduce 3-segment rendering)
- [ ] Required check names match the 14 frozen names per D17 amended by D30
- [ ] Every workflow has top-level `permissions: {}` (or restrictive equivalent)
- [ ] `grep -E '\|\| true|continue-on-error' .github/workflows/*.yml` returns zero hits in lint-related contexts
- [ ] Coverage-combine job posts single combined number on PRs
- [ ] #1233 D1, D3, D4, D5, D6, D8, D10, D12, D14 are resolved
- [ ] `concurrency:` cancel-in-progress on PR triggers (Fortune-50 pattern, found via OSS validation)

Plus agent-derived:

- [ ] All actions in new workflows are SHA-pinned (no tag refs)
- [ ] `.coverage-baseline` exists, contains `53.5` per D11
- [ ] `migration_roundtrip.sh` is executable and runs locally against a Postgres instance
- [ ] Phase A: both old and new workflows green on main for ≥48h before Phase B
- [ ] Phase B: branch protection updated atomically; verified via `gh api` diff
- [ ] Phase C: `test.yml` deleted; 3 consecutive `ci.yml` runs green on main

## Verification (full PR-level)

Phase A:
```bash
bash .claude/notes/ci-refactor/scripts/verify-pr3-phase-a.sh
```

Phase B (admin runs after Phase A merged):
```bash
bash .claude/notes/ci-refactor/scripts/flip-branch-protection.sh
```

Phase C:
```bash
bash .claude/notes/ci-refactor/scripts/verify-pr3-phase-c.sh
```

## Risks (scoped to PR 3)

- **R1 — Branch-protection flip locks out merging**: Phase A's 48h soak + atomic flip body + pre-flight snapshot for inverse call. Documented inverse `gh api -X PATCH` body in this spec.
- **R8 — Coverage drops > 2%**: D11 hard-gate from PR 3 day 1; `.coverage-baseline` set conservatively at 53.5 (current 55.56% minus 2pp safety margin). A single PR dropping >2pp triggers immediate failure.
- **R6 — v2.0 phase PR overlap on `test.yml`**: highest conflict surface in the rollout. Coordinate before opening Phase A.

## Rollback plan

### Phase A rollback (PR not yet merged)

Standard PR close. New files removed; main is unchanged.

### Phase A rollback (merged, pre-flip)

```bash
git revert -m 1 <PR3-merge-sha>
# admin: pushes via UI; agent does NOT run this command
```

Both old and new workflows stop running. Branch protection still references old check names; no merge breakage.

### Phase B rollback (post-flip)

Inverse atomic call:

```bash
gh api -X PATCH \
  /repos/prebid/salesagent/branches/main/protection/required_status_checks \
  -H "Accept: application/vnd.github+json" \
  --input .claude/notes/ci-refactor/branch-protection-snapshot-required-checks.json
```

Where `branch-protection-snapshot-required-checks.json` was extracted in pre-flight A1:
```bash
gh api repos/prebid/salesagent/branches/main/protection/required_status_checks \
  > .claude/notes/ci-refactor/branch-protection-snapshot-required-checks.json
```

Recovery: < 5 minutes.

### Phase C rollback (after test.yml deleted)

```bash
git revert -m 1 <PR3.1-merge-sha>
```

Restores `test.yml`. Old workflow runs again; new workflow continues running. Both green = OK.

## Merge tolerance

- **PR #1217 (adcp 3.12)**: tolerated. PR 3 doesn't reference `adcp` directly.
- **v2.0 phase PR landing on `test.yml`**: high conflict surface. Coordinate before opening Phase A.
- **v2.0 phase PR landing on `.github/`** other workflows: tolerated; this PR only modifies `test.yml` and adds new files.
- **PR 4 opening before Phase C lands**: blocked. PR 4 deletes pre-commit hooks whose enforcement moves to `CI / Quality Gate`. If `ci.yml` doesn't exist or isn't required yet, PR 4 creates a coverage gap.

## Coordination notes for the maintainer

1. **Before opening Phase A**: confirm pre-flight A1 (branch-protection snapshot) is saved.
2. **Before Phase B**: verify Phase A is on main for ≥48 hours and ≥2 PRs have shown the new check names green.
3. **Phase B is admin-only**: agents do not run `gh api -X PATCH` on branch protection per `feedback_user_owns_git_push.md`. Run the call yourself.
4. **Immediately after Phase B**: open a trivial PR to validate. If anything breaks, run the inverse `gh api -X PATCH` to roll back.
5. **Before Phase C**: verify Phase B is stable for ≥48 hours.
6. **After Phase C**: PR 4 can begin authoring.
7. **At end of Week 4 (D11 tripwire)**: D11 is hard-gate from PR 3 day 1, NOT a flip step. The Week-4 tripwire is: review `.coverage-baseline` and decide if it can be RATCHETED UPWARD (e.g., 53.5 → 54.5) given measured stability across PRs in the window. No "flip to gating" step — the gate is on from day 1.
8. **CodeQL gating flip (D10) ownership**: D10 schedules a Week-5 flip removing `continue-on-error: true` from `codeql.yml` and adding `CodeQL / analyze (python)` to required checks. PR 3 does NOT touch `codeql.yml`. The flip lands as the **final commit of PR 4** (Week 5). PR 4's final commit will: (1) remove `continue-on-error: true` from `codeql.yml`; (2) document the admin step `gh api -X PATCH branches/main/protection/...` adding `CodeQL / analyze (python)` to required checks. <!-- TODO: confirm CodeQL flip lands in PR 4 final commit; if PR 4 scope shifts, carve a tiny standalone `pr-codeql-flip` Week-5 PR -->
