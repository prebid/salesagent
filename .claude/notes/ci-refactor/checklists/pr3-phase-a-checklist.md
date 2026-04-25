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

[ ] 2. ci: add _pytest.yml reusable workflow
       File: .github/workflows/_pytest.yml (new; spec §Phase A Commit 2; postgres:17-alpine; permissions: {})
       Verify: grep -q 'workflow_call:' .github/workflows/_pytest.yml && \
               grep -qE '^permissions:\s*\{?\s*\}?' .github/workflows/_pytest.yml && \
               grep -q 'postgres:17-alpine' .github/workflows/_pytest.yml

[ ] 3. ci: add ci.yml orchestrator with 11 frozen check names
       File: .github/workflows/ci.yml (new; spec §Phase A Commit 3 verbatim — DO NOT alter check names)
       Verify: for name in 'CI / Quality Gate' 'CI / Type Check' 'CI / Schema Contract' 'CI / Unit Tests' \
                          'CI / Integration Tests' 'CI / E2E Tests' 'CI / Admin UI Tests' 'CI / BDD Tests' \
                          'CI / Migration Roundtrip' 'CI / Coverage' 'CI / Summary'; do
                 grep -qF "name: '$name'" .github/workflows/ci.yml || \
                   { echo "missing check name: $name"; exit 1; }
               done

[ ] 4. ci: add migration_roundtrip.sh script
       File: .github/scripts/migration_roundtrip.sh (new, executable; spec §Phase A Commit 4 verbatim)
       Verify: test -x .github/scripts/migration_roundtrip.sh && bash -n .github/scripts/migration_roundtrip.sh
       Local smoke (against agent-db Postgres):
         eval $(.claude/skills/agent-db/agent-db.sh up)
         DATABASE_URL=$DATABASE_URL bash .github/scripts/migration_roundtrip.sh

[ ] 5. ci: pin GitHub Actions in new workflows to SHAs
       Files: replace every <SHA> placeholder in commits 1-3 with actual 40-char SHA + # v<tag>
       Reuse the SHA-resolution loop from PR 1 commit 9 (it's in your shell history).
       Verify: [[ $(grep -hoE 'uses: [^ ]+@<SHA>' .github/workflows/ci.yml .github/workflows/_pytest.yml \
                    .github/actions/setup-env/action.yml | wc -l) == "0" ]] && \
               [[ $(grep -hoE 'uses: [^ ]+@[a-f0-9]{40}' .github/workflows/ci.yml .github/workflows/_pytest.yml \
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
- 11 frozen check names don't appear in `gh run view` after merge
- Coverage combine fails artifact merge
- migration_roundtrip.sh detects schema drift
File: .claude/notes/ci-refactor/escalations/pr3-phase-a-<topic>.md

Post-merge actions (operator + agent):
- ≥48h soak window observation (operator monitors `gh run list --workflow=ci.yml --branch=main`)
- Confirm new check names appear green on ≥2 real PRs before Phase B
- Update 00-MASTER-INDEX.md: Phase A status → "merged-soaking"
```
