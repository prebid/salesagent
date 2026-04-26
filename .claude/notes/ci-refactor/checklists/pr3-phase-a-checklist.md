# PR 3 Phase A — Overlap (new workflows alongside test.yml)

## Checklist

```
[ ] Pre-flight TTL guard with PR-3 lines uncommented (snapshot + required-checks + coverage.json)
[ ] git checkout -b ci/ci-refactor-pr3-phase-a-overlap

Commits in order:

[ ] 1. ci: add setup-env composite action
       File: .github/actions/setup-env/action.yml (new; spec §Phase A Commit 1)
       Verify: grep -qE 'using: .composite.' .github/actions/setup-env/action.yml && \
               yamllint -d relaxed .github/actions/setup-env/action.yml

[ ] 2. ci: add _pytest composite action (Decision-4 — NOT a reusable workflow)
       File: .github/actions/_pytest/action.yml (new; spec §Phase A Commit 2; using: composite)
       Note: postgres:17-alpine services live at the calling-job level in ci.yml (composites can't declare services)
       Verify: grep -qE 'using:\s+["\x27]?composite' .github/actions/_pytest/action.yml && \
               yamllint -d relaxed .github/actions/_pytest/action.yml && \
               ! test -f .github/workflows/_pytest.yml   # reusable form forbidden post-Decision-4

[ ] 3. ci: add ci.yml orchestrator with 14 frozen BARE job names (D26)
       File: .github/workflows/ci.yml (new; spec §Phase A Commit 3 verbatim — DO NOT alter check names)
       D26: workflow `name: CI` + jobs use BARE names (NOT 'CI / X' — that produces 'CI / CI / X' rendered)
       Verify the workflow header and bare job names:
         grep -qE '^name:\s+CI\s*$' .github/workflows/ci.yml
         for name in 'Quality Gate' 'Type Check' 'Schema Contract' 'Unit Tests' \
                    'Integration Tests' 'E2E Tests' 'Admin UI Tests' 'BDD Tests' \
                    'Migration Roundtrip' 'Coverage' 'Summary'; do
           grep -qF "name: '$name'" .github/workflows/ci.yml || \
             grep -qF "name: \"$name\"" .github/workflows/ci.yml || \
             grep -qE "^\s+name:\s+${name}\s*$" .github/workflows/ci.yml || \
             { echo "missing bare job name: $name"; exit 1; }
         done
         # No 'CI /' prefix in job names (the D26 bug)
         ! grep -qE "name:\s+['\"]CI / " .github/workflows/ci.yml
         # develop branch trigger (P0 sweep)
         grep -qE 'branches:\s+\[main,\s*develop\]' .github/workflows/ci.yml

[ ] 4. ci: add migration_roundtrip.sh script
       File: .github/scripts/migration_roundtrip.sh (new, executable; spec §Phase A Commit 4 verbatim)
       Verify: test -x .github/scripts/migration_roundtrip.sh && bash -n .github/scripts/migration_roundtrip.sh
       Local smoke (against agent-db Postgres):
         eval $(.claude/skills/agent-db/agent-db.sh up)
         DATABASE_URL=$DATABASE_URL bash .github/scripts/migration_roundtrip.sh

[ ] 5. ci: pin GitHub Actions in new workflows to SHAs
       Files: replace every <SHA> placeholder in commits 1-3 with actual 40-char SHA + # v<tag>
       Reuse `.github/.action-shas.txt` from PR 1 commit 9.
       Verify: [[ $(grep -hoE 'uses: [^ ]+@<SHA>' .github/workflows/ci.yml .github/actions/_pytest/action.yml \
                    .github/actions/setup-env/action.yml | wc -l) == "0" ]] && \
               [[ $(grep -hoE 'uses: [^ ]+@[a-f0-9]{40}' .github/workflows/ci.yml .github/actions/_pytest/action.yml \
                    .github/actions/setup-env/action.yml | wc -l) -ge "5" ]]

[ ] 6. ci: add coverage baseline
       File: .coverage-baseline (new; contents: exactly "53.5\n")
       Verify: [[ "$(cat .coverage-baseline)" == "53.5" ]]

[ ] 7. ci: remove || true and continue-on-error from ruff invocations (closes #1233 D6)
       File: .github/workflows/test.yml (lines 382-387 area)
       Verify: [[ $(grep -E '\|\| true|continue-on-error' .github/workflows/test.yml | grep -E 'ruff|lint' | wc -l) == "0" ]]

[ ] 8. ci: dynamic ADCP_SALES_PORT in e2e (closes #1233 D5, D14)
       File: .github/workflows/test.yml:347 area (remove ADCP_SALES_PORT: 8080)
       Verify: ! grep -q 'ADCP_SALES_PORT: 8080' .github/workflows/test.yml

[ ] 9. ci: unconditional creative agent in integration; verify permissions blocks (closes #1233 D12, PD15)
       Files: .github/workflows/test.yml (line 181 area + verify all top-level permissions blocks present)
       Verify: for f in .github/workflows/*.yml; do grep -qE '^permissions:' "$f"; done

[ ] 10. ci: schema-alignment fail-hard on network errors (closes #1233 D10)
        Locate: grep -rn 'pytest.skip.*network\|pytest.skip.*connection' tests/integration/
        Replace pytest.skip with hard failure (raise or pytest.fail).
        Verify: ! grep -rn 'pytest.skip.*network\|pytest.skip.*connection' tests/integration/ tests/unit/

After all commits:
[ ] bash .claude/notes/ci-refactor/scripts/verify-pr3-phase-a.sh
[ ] make quality + ./run_all_tests.sh

Stop conditions:
- 14 frozen check names don't appear in `gh run view` after merge
- Coverage combine fails artifact merge
- migration_roundtrip.sh detects schema drift
File: .claude/notes/ci-refactor/escalations/pr3-phase-a-<topic>.md

Post-merge actions (operator + agent):
- ≥48h soak window observation (operator monitors `gh run list --workflow=ci.yml --branch=main`)
- Confirm new check names appear green on ≥2 real PRs before Phase B
- Update 00-MASTER-INDEX.md: Phase A status → "merged-soaking"
```
