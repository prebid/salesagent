# PR 3 — CI authoritative + reusable workflows (3-phase merge)

**Drift items closed:** PD10 (partial), PD11, PD15, PD16 (groundwork), plus #1233 D1, D2, D3, D4, D5, D6, D8, D10, D12, D14
**Estimated effort:** 3-4 days across 3 phases
**Depends on:** PR 1 (SHA-pinning, permissions baseline) and PR 2 (local mypy/black hooks) merged
**Blocks:** PR 4 (PR 4 deletes hooks whose work is absorbed into the `CI / Quality Gate` job introduced here)
**Decisions referenced:** D2, D3, D11, D15, D17

> **Precondition**: `pytest-xdist>=3.6` MUST be added to `pyproject.toml [dependency-groups].dev` before this PR's xdist commits land. Best location: PR 2 commit 4 or 5 (which already touches dependency groups). If PR 2 ships without it, this PR must lead with a one-line `[dependency-groups].dev` addition commit.

## Scope

Restructure `.github/workflows/` to make CI the authoritative enforcement layer. Replace the matrix-sharded integration tests with `pytest-xdist` (validated safe per `tests/conftest_db.py:323-348` UUID-per-test DB pattern). Add **composite actions** `setup-env` and `_pytest` (NOT reusable workflows — composites avoid the 3-segment rendered-name issue per Decision-4). Freeze the 11 required check names per D17. Add Migration Roundtrip and Coverage Combine jobs.

This is the **only PR with a non-atomic merge.** It lands in 3 phases over ~1 week:

- **Phase A** — overlap: new workflows run alongside existing `test.yml`. Both green for ≥48h.
- **Phase B** — atomic flip: admin runs one `gh api -X PATCH` to swap required-checks list. ≤5 min window.
- **Phase C** — cleanup: delete legacy `test.yml`. Land ≥48h after Phase B is stable.

## Out of scope

- Pre-commit hook deletion / migration → PR 4
- Postgres unification across compose files → PR 5 (this PR unifies CI Postgres only)
- Python/uv version anchor consolidation → PR 5
- New required CI checks beyond the 11 frozen names per D17 (e.g., harden-runner) → PR 6 follow-up
- `merge_group:` triggers → never (D6)

## Architectural choices (corrections to issue #1234)

The issue says "reusable workflows `_setup-env.yml`, `_postgres.yml`, `_pytest.yml`." Three corrections (last revised 2026-04-25 P0 sweep — Decision-4):

1. **`_setup-env` is a composite action**, not a reusable workflow. Composite actions can be called as a series of steps inside a job; reusable workflows can't. Path: `.github/actions/setup-env/action.yml`.

2. **`_pytest` is ALSO a composite action** (revised 2026-04-25 from earlier "reusable workflow" plan). Path: `.github/actions/_pytest/action.yml`. **Rationale:** reusable-workflow nesting renders status checks as 3-segment names (`CI / Unit Tests / pytest`) because the called workflow's job-id is appended. Branch protection's required-checks list uses 2-segment names (`CI / Unit Tests`); a reusable-workflow `_pytest.yml` would silently 422 the Phase B PATCH. Composites don't add path segments — the calling job's `name:` IS the rendered name. The structural guard `tests/unit/test_architecture_required_ci_checks_frozen.py` (drafted in `drafts/guards/`) enforces this at design time, not just at flip time. (Decision-4 in `RESUME-HERE.md` §"P0 sweep applied"; see also D26 corollary.)

3. **`_postgres` does not exist as a separate file.** Postgres services live at the calling-job level in `ci.yml` (services are a workflow/job-level concern; composite actions cannot declare them). The integration/e2e/admin/bdd jobs each declare their own `services: postgres:` block at the job level, then call the `_pytest` composite for test execution. The duplication is ~15 lines per job — acceptable cost for the rendered-name win.

## Internal commit sequence

Phase A is one PR with ~10 commits. Phase B is admin action (no PR). Phase C is a small follow-up PR with 1-2 commits.

### Pre-flight (BEFORE Phase A merges)

> **Pre-flight 3a — xdist soak (HARD precondition).** Before flipping the matrix in Phase A, run `tox -e integration -- -n 4` against current main. If the suite fails or flakes, fix infrastructure first (likely candidates: `mcp_server` port TOCTOU, `factory.Sequence` collisions, module-global engine mutations, session-scoped fixtures racing on shared resources). Phase A does NOT proceed until xdist is green across ≥3 consecutive runs at `-n 4` AND ≥1 successful run at `-n auto`. Record a paste-able command + timing summary in the PR description.
>
> Rationale: switching from matrix-shard to xdist exposes any test-isolation defect that the legacy single-process matrix was masking. Discovering that defect during Phase A's 48h soak (post-merge) means rolling back; discovering it pre-merge is cheap.

### Phase A — Overlap (single PR)

> **Commit ordering note (2026-04-25 P0 sweep — bisect cleanliness):** The numbered commits below are the LOGICAL grouping. The ACTUAL git-history order MUST place "Commit 6 — coverage baseline + tox.ini sync" BEFORE "Commit 3 — ci.yml orchestrator", because `ci.yml` references `.coverage-baseline` via `cat .coverage-baseline`. Required git order: 1, 2, 6, 3, 4, 4b, 5, 7, 8, 9, 10, 11. A bisect that lands on Commit 3 with `.coverage-baseline` absent would fail the Coverage job (file-not-found in the subshell). Reviewers reading the spec top-to-bottom should mentally re-thread the dependency.

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
    default: '0.11.6'
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
    - uses: astral-sh/setup-uv@<SHA>  # v4
      with:
        version: ${{ inputs.uv-version }}
        python-version-file: ${{ inputs.python-version-file }}
        enable-cache: true
        cache-dependency-glob: 'uv.lock'
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
        ADCP_TESTING: 'true'
        COVERAGE_FILE: .coverage.${{ inputs.tox-env }}
      run: |
        XDIST_FLAG=""
        if [[ "${{ inputs.xdist-workers }}" != "" && "${{ inputs.xdist-workers }}" != "0" ]]; then
          XDIST_FLAG="-n ${{ inputs.xdist-workers }}"
        fi
        uv run tox -e ${{ inputs.tox-env }} -- \
          -p no:cacheprovider \
          $XDIST_FLAG \
          ${{ inputs.pytest-args }}
    - uses: actions/upload-artifact@<SHA>  # v4
      if: always()
      with:
        name: coverage-${{ inputs.tox-env }}
        # Literal path, not glob (R27 mitigation — prevents tar-bomb of .coverage.* in /tmp etc.)
        path: '.coverage.${{ inputs.tox-env }}'
        include-hidden-files: true
        if-no-files-found: error   # fail noisily; coverage data must be produced
    - uses: actions/upload-artifact@<SHA>  # v4
      if: always()
      with:
        name: pytest-report-${{ inputs.tox-env }}
        path: test-results/
        if-no-files-found: ignore
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

#### Commit 3 — `ci: add ci.yml orchestrator with 11 frozen check names`

Files:
- `.github/workflows/ci.yml` (new)

The 11 frozen check names per D17:

```yaml
name: CI

on:
  pull_request:
    branches: [main, develop]
  push:
    branches: [main, develop]
  # Note: `develop` is included to maintain parity with the existing test.yml trigger model.
  # Formal deprecation of `develop` is deferred to a post-#1234 follow-up; until then,
  # PR 3 must support both branches so contributor PRs targeting `develop` continue to gate.

permissions: {}

# YAML anchors deduplicate the postgres service block + DATABASE_URL across the 5 DB-needing
# jobs (integration, e2e, admin, bdd, migration-roundtrip). GitHub Actions YAML parsing
# supports anchors (baseline YAML 1.2). Verify with `actionlint ci.yml` before commit.
# Fallback (if anchors fail to parse on first run): emit ci.yml from a small Python template
# script — preserves single-source-of-truth without runtime anchor dependency.
x-postgres-service: &postgres-service
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
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}
  # FYI / Round-9 trade-off note: this expression cancels in-progress runs on PR pushes
  # but NEVER cancels main-branch runs. If main receives rapid pushes (merge train),
  # multiple ci.yml runs queue serially and can dominate runner-minute budget. A future
  # follow-up may switch to:
  #   cancel-in-progress: ${{ !startsWith(github.ref, 'refs/tags/') }}
  # which cancels rapid main pushes while preserving release-tag runs. Defer the change
  # until main-push cadence is measured (e.g., 4-week telemetry window post-Phase B).

jobs:
  quality-gate:
    name: 'Quality Gate'
    runs-on: ubuntu-latest
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
    steps:
      # schema-contract runs pytest against unit + integration contract tests;
      # pytest needs the package installed to import `src.*`. Project IS installed
      # (default install-project: 'true' is propagated via _pytest -> setup-env).
      - uses: ./.github/actions/_pytest
        with:
          tox-env: unit
          pytest-args: 'tests/unit/test_adcp_contract.py tests/integration/test_mcp_contract_validation.py -v'

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
      postgres: *postgres-service
    env:
      DATABASE_URL: *database-url
    steps:
      - run: uv run python scripts/ops/migrate.py
      - uses: ./.github/actions/_pytest
        with:
          tox-env: integration
          xdist-workers: 'auto'

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
          DATABASE_URL: postgresql://adcp_user:test_password@localhost:5432/roundtrip
        run: bash .github/scripts/migration_roundtrip.sh

  coverage:
    name: 'Coverage'
    runs-on: ubuntu-latest
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
        # Per D11 (revised 2026-04-25 P0 sweep): hard-gate from PR 3 day 1 at 53.5%.
        # `--fail-under=$(cat .coverage-baseline)` above blocks merge on regression.
        # Ratchet upward only when measured-stable across 4+ consecutive PRs.
        # MINIMUM_GREEN/ORANGE drive the comment's badge color (visual only — does not gate).
        # ANNOTATE_MISSING_LINES adds inline PR annotations to uncovered changed lines.

  summary:
    name: 'Summary'
    runs-on: ubuntu-latest
    needs:
      - quality-gate
      - type-check
      - schema-contract
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
# All 11 frozen BARE job names present (D26: bare, no 'CI /' prefix)
for name in 'Quality Gate' 'Type Check' 'Schema Contract' 'Unit Tests' 'Integration Tests' 'E2E Tests' 'Admin UI Tests' 'BDD Tests' 'Migration Roundtrip' 'Coverage' 'Summary'; do
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

**Sub-fix — filelock + worker-id gate around `migrate.py`.** The session-scoped fixture at `tests/conftest_db.py:79-81` runs migrations on every worker startup; under xdist with N workers, that's N concurrent `alembic upgrade head` invocations racing on the same DB. Gate so only `gw0` runs migrations; other workers wait via filelock until the lockfile is released:

```python
# tests/conftest_db.py — replace the unconditional migrate.py call
import os
from filelock import FileLock

def _run_migrations_once(template_dsn):
    worker = os.environ.get("PYTEST_XDIST_WORKER", "gw0")
    lock_path = f"/tmp/salesagent-migrate-{os.getpid()}.lock"  # or per-suite root_tmp_dir
    with FileLock(lock_path, timeout=120):
        # gw0 runs migrations; other workers acquire the lock after gw0 releases
        # and find the schema already at head — alembic upgrade head is idempotent.
        if worker == "gw0":
            subprocess.run(["uv", "run", "python", "scripts/ops/migrate.py"], check=True)
```

Add `filelock>=3.13` to `pyproject.toml [dependency-groups].dev` (verify it's not already pulled in transitively; if `filelock` is already a transitive dependency of e.g. `virtualenv` or `pytest`, no addition needed). Alternative: use `flock(1)` system tool inline in CI's pre-step rather than Python. The Python form is preferred for parity with local `tox -e integration -- -n auto` runs.

#### Commit 5 — `ci: pin GitHub Actions in new workflows to SHAs`

For every `<SHA>` placeholder in commits 1-3, replace with the actual SHA + `# v<tag>` comment. Reuse the SHA-resolution loop from PR 1 commit 9.

Verification:
```bash
[[ $(grep -hoE 'uses: [^ ]+@<SHA>' .github/workflows/ci.yml .github/actions/_pytest/action.yml .github/actions/setup-env/action.yml | wc -l) == "0" ]]
[[ $(grep -hoE 'uses: [^ ]+@[a-f0-9]{40}' .github/workflows/ci.yml .github/actions/_pytest/action.yml .github/actions/setup-env/action.yml | wc -l) -ge "5" ]]
```

#### Commit 6 — `ci: add coverage baseline + sync tox.ini coverage gate`

Files:
- `.coverage-baseline` (new, contents: `53.5`)
- `tox.ini:106` (modify)

Per D11 (revised 2026-04-25 P0 sweep), hard-gate from PR 3 day 1. Set to current measured (55.56% from pre-flight A7) minus 2pp safety margin. Coverage job uses `--fail-under=$(cat .coverage-baseline)`. Ratchet upward only when measured-stable across 4+ consecutive PRs. The earlier "advisory for 4 weeks" framing was contradicted by the actual implementation (`--fail-under` is a hard gate); aligning the framing with the implementation eliminates ambiguity.

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

#### Commit 9 — `ci: unconditional creative agent in integration; permissions blocks`

Files:
- `.github/workflows/test.yml:181, etc.` (modify)
- All workflows missing top-level `permissions:` block (PR 1 commit 9 should have done this; verify)

Closes #1233 D12, PD15.

Verification:
```bash
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

#### Commit 11 — `ci: gate Gemini key behind unconditional mock` (moved from PR 1 in 2026-04-25 P0 sweep)

Closes PD24, per D15. **MOVED from PR 1 commit 10** in the 2026-04-25 P0 sweep — PR 3 rewrites
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

# Compare to the 11 expected names — they must match the Step 2 PATCH body exactly
cat <<'EOF' | sort -u > /tmp/expected-names.txt
CI / Quality Gate
CI / Type Check
CI / Schema Contract
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

If the diff fails AFTER applying Decision-4 (composite migration), the failure indicates a regression — a reusable workflow has been re-introduced somewhere. The structural guard `test_architecture_required_ci_checks_frozen.py` should have caught this before the soak; if it didn't, audit `ci.yml` for `uses: ./.github/workflows/_*.yml` and convert to composite. Do NOT flip Phase B until rendered names are 2-segment for all 11 checks.

Pre-Decision-4 historical note: the original plan used a reusable workflow `_pytest.yml` and accepted the 3-segment rendered name as a runtime concern (flatten on detection). Decision-4 (2026-04-25 P0 sweep) eliminates this class of bug at design time by mandating composite actions.

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

Open a trivial PR (e.g., add a comment) and confirm all 11 new check names show as required. If any fail unexpectedly, see rollback.

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
- [ ] Required check names match the 11 frozen names per D17
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
- **R8 — Coverage drops > 2%**: D11 hard-gate from PR 3 day 1 (revised 2026-04-25 P0 sweep); `.coverage-baseline` set conservatively at 53.5 (current 55.56% minus 2pp safety margin). A single PR dropping >2pp triggers immediate failure.
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
7. **At end of Week 4 (D11 tripwire — revised 2026-04-25 P0 sweep)**: D11 is now hard-gate from PR 3 day 1, NOT a flip step. The Week-4 tripwire is now: review `.coverage-baseline` and decide if it can be RATCHETED UPWARD (e.g., 53.5 → 54.5) given measured stability across PRs in the window. No "flip to gating" step — the gate is on from day 1.
8. **CodeQL gating flip (D10) ownership**: D10 schedules a Week-5 flip removing `continue-on-error: true` from `codeql.yml` and adding `CodeQL / analyze (python)` to required checks. PR 3 does NOT touch `codeql.yml`. The flip lands as the **final commit of PR 4** (Week 5) — Option A in Round-9 verification. PR 4's final commit will: (1) remove `continue-on-error: true` from `codeql.yml`; (2) document the admin step `gh api -X PATCH branches/main/protection/...` adding `CodeQL / analyze (python)` to required checks. <!-- TODO: confirm CodeQL flip lands in PR 4 final commit; if PR 4 scope shifts, carve a tiny standalone `pr-codeql-flip` Week-5 PR -->
