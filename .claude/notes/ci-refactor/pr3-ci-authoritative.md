# PR 3 — CI authoritative + reusable workflows (3-phase merge)

**Drift items closed:** PD10 (partial), PD11, PD15, PD16 (groundwork), plus #1233 D1, D2, D3, D4, D5, D6, D8, D10, D12, D14
**Estimated effort:** 3-4 days across 3 phases
**Depends on:** PR 1 (SHA-pinning, permissions baseline) and PR 2 (local mypy/black hooks) merged
**Blocks:** PR 4 (PR 4 deletes hooks whose work is absorbed into the `CI / Quality Gate` job introduced here)
**Decisions referenced:** D2, D3, D11, D15, D17

## Scope

Restructure `.github/workflows/` to make CI the authoritative enforcement layer. Replace the matrix-sharded integration tests with `pytest-xdist` (validated safe per `tests/conftest_db.py:323-348` UUID-per-test DB pattern). Add reusable workflow `_pytest.yml` and composite action `setup-env`. Freeze the 11 required check names per D17. Add Migration Roundtrip and Coverage Combine jobs.

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

The issue says "reusable workflows `_setup-env.yml`, `_postgres.yml`, `_pytest.yml`." Two corrections from the round-1 deep-dive agent:

1. **`_setup-env` is a composite action**, not a reusable workflow. Composite actions can be called as a series of steps inside a job; reusable workflows can't. Path: `.github/actions/setup-env/action.yml`.

2. **`_postgres.yml` collapses into `_pytest.yml`.** Services can't live in composite actions, and splitting service from runner forces a confusing job-graph. Single reusable workflow `_pytest.yml` declares Postgres services conditionally based on a `needs-postgres` input.

## Internal commit sequence

Phase A is one PR with ~10 commits. Phase B is admin action (no PR). Phase C is a small follow-up PR with 1-2 commits.

### Phase A — Overlap (single PR)

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
    description: 'Dependency groups to sync (space-separated)'
    required: false
    default: 'dev'
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
        for g in ${{ inputs.groups }}; do
          uv sync --group "$g"
        done
```

Verification:
```bash
test -f .github/actions/setup-env/action.yml
yamllint -d relaxed .github/actions/setup-env/action.yml
grep -qE 'using: .composite.' .github/actions/setup-env/action.yml
```

#### Commit 2 — `ci: add _pytest.yml reusable workflow`

Files:
- `.github/workflows/_pytest.yml` (new)

```yaml
name: Pytest Suite
on:
  workflow_call:
    inputs:
      tox-env:
        type: string
        required: true
      needs-postgres:
        type: boolean
        required: false
        default: false
      timeout-minutes:
        type: number
        required: false
        default: 15
      pytest-args:
        type: string
        required: false
        default: ''
      xdist-workers:
        type: string
        required: false
        default: 'auto'

permissions: {}

jobs:
  pytest:
    runs-on: ubuntu-latest
    timeout-minutes: ${{ inputs.timeout-minutes }}
    permissions:
      contents: read
    services:
      postgres:
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
    steps:
      - uses: ./.github/actions/setup-env
      - if: ${{ inputs.needs-postgres }}
        env:
          DATABASE_URL: postgresql://adcp_user:test_password@localhost:5432/adcp_test
        run: uv run python scripts/ops/migrate.py
      - name: Run tox env
        env:
          DATABASE_URL: postgresql://adcp_user:test_password@localhost:5432/adcp_test
          ADCP_TESTING: 'true'
          COVERAGE_FILE: .coverage.${{ inputs.tox-env }}
        run: |
          uv run tox -e ${{ inputs.tox-env }} -- \
            -p no:cacheprovider \
            ${{ inputs.pytest-args }}
      - uses: actions/upload-artifact@<SHA>  # v4
        if: always()
        with:
          name: coverage-${{ inputs.tox-env }}
          path: .coverage.${{ inputs.tox-env }}
          include-hidden-files: true   # coverage data files are dotfiles
      - uses: actions/upload-artifact@<SHA>  # v4
        if: always()
        with:
          name: pytest-report-${{ inputs.tox-env }}
          path: test-results/
          if-no-files-found: ignore
```

Note: Postgres service is declared unconditionally. Non-DB tests ignore it (trivial overhead). GitHub Actions does not support conditional `services:` blocks.

Verification:
```bash
test -f .github/workflows/_pytest.yml
yamllint -d relaxed .github/workflows/_pytest.yml
grep -q 'workflow_call:' .github/workflows/_pytest.yml
grep -qE '^permissions:\s*\{?\s*\}?' .github/workflows/_pytest.yml
```

#### Commit 3 — `ci: add ci.yml orchestrator with 11 frozen check names`

Files:
- `.github/workflows/ci.yml` (new)

The 11 frozen check names per D17:

```yaml
name: CI

on:
  pull_request:
  push:
    branches: [main]

permissions: {}

concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}

jobs:
  quality-gate:
    name: 'CI / Quality Gate'
    runs-on: ubuntu-latest
    permissions:
      contents: read
    steps:
      - uses: ./.github/actions/setup-env
      - run: uv run pre-commit run --all-files --show-diff-on-failure
      # Migrated CI-only invocations (PR 4 will activate these as the deleted-hook replacements):
      - run: uv run python .pre-commit-hooks/check-gam-auth-support.py
      - run: uv run python scripts/hooks/check_response_attribute_access.py
      - run: uv run python .pre-commit-hooks/check_roundtrip_tests.py
      - run: uv run python .pre-commit-hooks/check_code_duplication.py

  type-check:
    name: 'CI / Type Check'
    runs-on: ubuntu-latest
    permissions:
      contents: read
    steps:
      - uses: ./.github/actions/setup-env
      - run: uv run mypy src/ --config-file=mypy.ini

  schema-contract:
    name: 'CI / Schema Contract'
    uses: ./.github/workflows/_pytest.yml
    with:
      tox-env: unit
      pytest-args: 'tests/unit/test_adcp_contract.py tests/integration/test_mcp_contract_validation.py -v'
      timeout-minutes: 10

  unit-tests:
    name: 'CI / Unit Tests'
    uses: ./.github/workflows/_pytest.yml
    with:
      tox-env: unit
      timeout-minutes: 15

  integration-tests:
    name: 'CI / Integration Tests'
    uses: ./.github/workflows/_pytest.yml
    with:
      tox-env: integration
      needs-postgres: true
      pytest-args: '-n auto'
      timeout-minutes: 30

  e2e-tests:
    name: 'CI / E2E Tests'
    uses: ./.github/workflows/_pytest.yml
    with:
      tox-env: e2e
      needs-postgres: true
      timeout-minutes: 25

  admin-tests:
    name: 'CI / Admin UI Tests'
    uses: ./.github/workflows/_pytest.yml
    with:
      tox-env: admin
      needs-postgres: true
      timeout-minutes: 20

  bdd-tests:
    name: 'CI / BDD Tests'
    uses: ./.github/workflows/_pytest.yml
    with:
      tox-env: bdd
      needs-postgres: true
      timeout-minutes: 20

  migration-roundtrip:
    name: 'CI / Migration Roundtrip'
    runs-on: ubuntu-latest
    permissions:
      contents: read
    services:
      postgres:
        image: postgres:17-alpine
        env:
          POSTGRES_USER: adcp_user
          POSTGRES_PASSWORD: test_password
          POSTGRES_DB: roundtrip
        options: >-
          --health-cmd "pg_isready -U adcp_user"
          --health-interval 5s
          --health-retries 10
        ports:
          - 5432:5432
    steps:
      - uses: ./.github/actions/setup-env
      - env:
          DATABASE_URL: postgresql://adcp_user:test_password@localhost:5432/roundtrip
        run: bash .github/scripts/migration_roundtrip.sh

  coverage:
    name: 'CI / Coverage'
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
        # Per D11, advisory for first 4 weeks; --fail-under above is set to 53.5

  summary:
    name: 'CI / Summary'
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
# All 11 frozen names present:
for name in 'CI / Quality Gate' 'CI / Type Check' 'CI / Schema Contract' 'CI / Unit Tests' 'CI / Integration Tests' 'CI / E2E Tests' 'CI / Admin UI Tests' 'CI / BDD Tests' 'CI / Migration Roundtrip' 'CI / Coverage' 'CI / Summary'; do
  grep -qF "name: '$name'" .github/workflows/ci.yml || \
    grep -qF "name: \"$name\"" .github/workflows/ci.yml
done
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

#### Commit 5 — `ci: pin GitHub Actions in new workflows to SHAs`

For every `<SHA>` placeholder in commits 1-3, replace with the actual SHA + `# v<tag>` comment. Reuse the SHA-resolution loop from PR 1 commit 9.

Verification:
```bash
[[ $(grep -hoE 'uses: [^ ]+@<SHA>' .github/workflows/ci.yml .github/workflows/_pytest.yml .github/actions/setup-env/action.yml | wc -l) == "0" ]]
[[ $(grep -hoE 'uses: [^ ]+@[a-f0-9]{40}' .github/workflows/ci.yml .github/workflows/_pytest.yml .github/actions/setup-env/action.yml | wc -l) -ge "5" ]]
```

#### Commit 6 — `ci: add coverage baseline`

Files:
- `.coverage-baseline` (new, contents: `53.5`)

Per D11, advisory for 4 weeks. Set to current measured (55.56% from pre-flight A7) minus 2pp.

Verification:
```bash
test -f .coverage-baseline
[[ "$(cat .coverage-baseline)" == "53.5" ]]
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

### Phase B — Atomic flip (admin action only, no PR)

After Phase A has been on main for ≥48 hours and the new check names appear green on at least 2-3 PRs:

#### Step 1 — Verify pre-flip snapshot exists

```bash
test -f .claude/notes/ci-refactor/branch-protection-snapshot.json   # from pre-flight A1
```

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
  --jq '.contexts[]' | sort > /tmp/protected
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
- [ ] Reusable workflow `_pytest.yml` exists with `workflow_call:` trigger
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
- **R8 — Coverage drops > 2%**: D11 advisory window; `.coverage-baseline` set conservatively.
- **R6 — v2.0 phase PR overlap on `test.yml`**: highest conflict surface in the rollout. Coordinate before opening Phase A.

## Rollback plan

### Phase A rollback (PR not yet merged)

Standard PR close. New files removed; main is unchanged.

### Phase A rollback (merged, pre-flip)

```bash
git revert -m 1 <PR3-merge-sha>
git push origin main   # USER ACTION
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
7. **At end of Week 4 (D11 tripwire)**: review `.coverage-baseline` and decide if it can flip to gating (remove `--fail-under` from the coverage job's advisory pass, make it strict).
