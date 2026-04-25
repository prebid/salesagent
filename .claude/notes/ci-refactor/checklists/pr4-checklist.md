# PR 4 — Hook relocation + structural guards

## Checklist

```
[ ] Pre-flight TTL guard
[ ] Verify PR 3 fully merged: ls .github/workflows/ci.yml && ! test -f .github/workflows/test.yml
[ ] git checkout -b refactor/ci-refactor-pr4-hook-relocation

Commits in order:

[ ] 1. test: extend _architecture_helpers.py + verify @pytest.mark.arch_guard marker
       Files: tests/unit/_architecture_helpers.py (extend with parse_module, iter_function_defs,
              iter_call_expressions, src_python_files, repo_root per spec)
       Verify: uv run python -c "from tests.unit._architecture_helpers import parse_module, \
                 iter_function_defs, iter_call_expressions, src_python_files, repo_root; print('OK')"

[ ] 2. test: backfill @pytest.mark.arch_guard on existing 27 guards
       Files: 23 tests/unit/test_architecture_*.py + 3 transport-boundary guards
       Mechanical: prepend @pytest.mark.arch_guard to every def test_* line.
       Verify: for f in tests/unit/test_architecture_*.py tests/unit/test_no_toolerror_in_impl.py \
                       tests/unit/test_transport_agnostic_impl.py tests/unit/test_impl_resolved_identity.py; do
                 [[ -f "$f" ]] || continue
                 t=$(grep -c '^def test_\|^    def test_' "$f")
                 m=$(grep -B1 'def test_' "$f" | grep -c '@pytest.mark.arch_guard')
                 [[ "$t" == "$m" ]] || { echo "marker missing in $f: $m/$t"; exit 1; }
               done
               uv run pytest tests/unit/ -m arch_guard -v 2>&1 | tail -3

[ ] 3. test: add 5 new structural guards (4 new + 1 extension)
       Files: tests/unit/test_architecture_no_tenant_config.py (new; spec §Commit 3)
              tests/unit/test_architecture_jsontype_columns.py (new)
              tests/unit/test_architecture_no_defensive_rootmodel.py (new — note ALLOWED_FILES list for a2a-sdk)
              tests/unit/test_architecture_import_usage.py (new — port 243-LOC pre-commit hook to AST)
              tests/unit/test_architecture_query_type_safety.py (extend with test_no_legacy_session_query
                + test_models_use_mapped_not_column)
       Verify: uv run pytest tests/unit/test_architecture_no_tenant_config.py \
                            tests/unit/test_architecture_jsontype_columns.py \
                            tests/unit/test_architecture_no_defensive_rootmodel.py \
                            tests/unit/test_architecture_import_usage.py \
                            tests/unit/test_architecture_query_type_safety.py -v -x
       Each individual guard < 2s; profile if heavier.

[ ] 4. chore(pre-commit): add coverage map for hook deletions
       File: .pre-commit-coverage-map.yml (new, ~30 lines mapping every deleted hook → replacement)
       Verify: yamllint -d relaxed .pre-commit-coverage-map.yml
               python -c "import yaml; m = yaml.safe_load(open('.pre-commit-coverage-map.yml')); \
                 [(_:=target['enforced_by'] in {'guard','guard-existing','ci-step','pre-push','pre-push + ci','deleted'}) \
                  or (_ for _ in (1/0,)) for hook,target in m.items()]; print(f'{len(m)} entries')"

[ ] 5. refactor(pre-commit): move 10 medium-cost hooks to pre-push stage (D27 revised Round 8)
       File: .pre-commit-config.yaml — add `stages: [pre-push]` to all 10:
         check-docs-links, check-route-conflicts, type-ignore-no-regression,
         adcp-contract-tests, mcp-contract-validation,
         mcp-schema-alignment, check-tenant-context-order, ast-grep-bdd-guards, check-migration-completeness,
         mypy (the 10th, added per D3 — was at commit-stage during PR 2's migration window for invocation parity; CI's `CI / Type Check` is authoritative)
       Verify: for h in check-docs-links check-route-conflicts type-ignore-no-regression \
                        adcp-contract-tests mcp-contract-validation \
                        mcp-schema-alignment check-tenant-context-order ast-grep-bdd-guards check-migration-completeness mypy; do
                 yq ".repos[].hooks[] | select(.id == \"$h\") | .stages" .pre-commit-config.yaml | grep -q pre-push
               done
       Pre-flight (Round 8 disk-truth re-verification — zero-headroom warning):
         BASELINE_HOOKS=$(grep -c "^\s*- id:" .pre-commit-config.yaml)
         MANUAL_HOOKS=$(grep -c "stages: \[manual\]" .pre-commit-config.yaml)
         [[ $((BASELINE_HOOKS - MANUAL_HOOKS)) -le 36 ]] || { echo "baseline drifted >36; v2.0 may have added hooks; identify 11th move"; exit 1; }

[ ] 6. refactor(pre-commit): consolidate grep one-liners into check_repo_invariants.py
       Files: .pre-commit-hooks/check_repo_invariants.py (new ~80 lines per spec);
              .pre-commit-config.yaml (replace no-skip-tests + no-fn-calls sh -c hooks with single repo-invariants Python hook)
       Verify: uv run python -m py_compile .pre-commit-hooks/check_repo_invariants.py
               ! grep -qE '^\s+- id: no-skip-tests$' .pre-commit-config.yaml
               ! grep -qE '^\s+- id: no-fn-calls$' .pre-commit-config.yaml
               yq '.repos[].hooks[] | select(.id == "repo-invariants") | .id' .pre-commit-config.yaml | grep -qx repo-invariants
               uv run pre-commit run repo-invariants --all-files

[ ] 7. refactor(pre-commit): delete migrated and dead hooks
       File: .pre-commit-config.yaml — delete the 15 hooks listed in Briefing §"Hooks DELETED" (13 commit-stage + 2 already-manual stubs: pytest-unit, mcp-endpoint-tests). v2.0 phase PR may have already deleted test-migrations — verify post-rebase.
       Verify: for h in no-tenant-config enforce-jsontype check-rootmodel-access enforce-sqlalchemy-2-0 \
                        check-import-usage check-gam-auth-support check-response-attribute-access \
                        check-roundtrip-tests check-code-duplication check-parameter-alignment \
                        pytest-unit mcp-endpoint-tests suggest-test-factories no-skip-integration-v2 \
                        check-migration-heads; do
                 ! grep -qE "^\s+- id: $h$" .pre-commit-config.yaml || { echo "still exists: $h"; exit 1; }
               done
               # Hook count ≤12 at commit stage
               python -c "import yaml; cfg=yaml.safe_load(open('.pre-commit-config.yaml')); \
                 default=cfg.get('default_stages',['pre-commit','commit']); \
                 n=sum(1 for r in cfg['repos'] for h in r['hooks'] \
                       if 'pre-commit' in (h.get('stages') or default) or 'commit' in (h.get('stages') or default)); \
                 import sys; sys.exit(0 if n <= 12 else 1); print(n)"

       RED-TEAM (mandatory; document in PR description):
       For each migrated invariant — on a scratch branch, inject a violation, run the guard, confirm fails.
       Table from spec §Red-team: 7 entries. Take screenshots/output of each.

[ ] 8. chore: latency baseline post-PR-4
       Verify: pre-commit clean
               pre-commit run --all-files >/dev/null 2>&1 || true   # warm
               { time pre-commit run --all-files >/dev/null; } 2>&1 | tee .pre-commit-latency-after.txt
               # Acceptance: warm < 5s
       If >5s: profile, fix or escalate before commit 9.

[ ] 9. docs: update CLAUDE.md guards table — DEFERRED scope per D18 (revised Round 8)
       File: CLAUDE.md
       Per D18 (revised 2026-04-25 Round 8), the full ~81-row table audit DEFERS to a
       post-v2.0-rebase commit. PR 4 commit 9 has minimal scope:
       - Add the 4 PR-4 rows (no_tenant_config, jsontype_columns, no_defensive_rootmodel, import_usage)
       - Add the **1 residual missing row** (`test_architecture_production_session_add`).
         **Do NOT add `test_architecture_no_silent_except`** — v2.0 phase PR DELETES it.
       - Do NOT update guard-count text to a final number (~81 only after v2.0 lands)
       - Do NOT remove rows whose tests don't exist (none exist; the "phantom" framing was wrong)
       Verify: each of the 4+1 new rows present in CLAUDE.md table; no_silent_except is NOT added.
               # Full audit (every test file has a row + every row has a file) deferred.

[ ] 10. docs: rewrite ci-pipeline.md and extend structural-guards.md
        Files: docs/development/ci-pipeline.md (full rewrite per 12-section outline in spec §Commit 10)
               docs/development/structural-guards.md (append PR 2 + PR 4 + helper module sections)
        Verify: [[ $(wc -l < docs/development/ci-pipeline.md) -ge 100 ]]
                grep -qE 'Layer 1.*pre-commit' docs/development/ci-pipeline.md
                grep -qE 'pre_commit_no_additional_deps' docs/development/structural-guards.md

After all commits:
[ ] bash .claude/notes/ci-refactor/scripts/verify-pr4.sh  (8 sections; spec §Verification)
[ ] make quality + ./run_all_tests.sh

Stop conditions:
- Any guard takes >2s individually
- Hook deletion (commit 7) regresses make quality on main
- Warm latency post-deletion >5s
- Red-team test doesn't fire on injected violation
File: .claude/notes/ci-refactor/escalations/pr4-<topic>.md

Post-merge:
- Update CLAUDE.md guards table again when v2.0 phase PRs land their 31 architecture tests + 9 baseline JSONs (final ~73 per D18 revised P0 sweep)
- Monitor first contributor PR's `pre-commit run --all-files` time; expect ~1.5-2s warm
```
