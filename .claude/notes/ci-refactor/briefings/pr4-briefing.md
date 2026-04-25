# PR 4 — Hook relocation + structural guards

## Briefing
**Where we are.** Week 4 → Week 5. PR 3 fully merged; `CI / Quality Gate` is required. Calendar: refactoring pre-commit for the layered model.

**What this PR does.** Per-hook reassignment per the 5-layer architecture. Drops warm pre-commit latency from ~23s baseline (A8) to <5s target. Migrates 5 grep hooks → AST-based structural guards. Moves 5 medium hooks to `pre-push` stage. Migrates 4 expensive hooks to CI-only (the `CI / Quality Gate` job introduced in PR 3 already runs them — this PR just deletes the redundant pre-commit invocation). Deletes 6 dead/advisory hooks. **ORDER IS LOAD-BEARING**: all new structural guards must pass on main BEFORE any hook is deleted.

**Drift closed.** PD16-PD22.

**You can rely on.** PR 3's `ci.yml` runs `check-gam-auth-support.py`, `check_response_attribute_access.py`, `check_roundtrip_tests.py`, `check_code_duplication.py` in `quality-gate`. PR 2's `_architecture_helpers.py` exists; you extend it. `@pytest.mark.architecture` marker registered (PR 2 commit 8) — backfill onto existing 27 guards happens here.

**You CANNOT do.** Delete a hook before its replacement guard passes on main. Add new CI checks beyond `CI / Quality Gate` work absorption (PR 3 owns workflow). Touch `.guard-baselines/` (v2.0 territory). Re-litigate D7 (prek). Edit `src/` outside fixing test failures from new guards.

**Concurrent activity.** v2.0 phase PRs landing on `.pre-commit-config.yaml` are HIGHEST conflict; coordinate. v2.0's own 9 guards in `.guard-baselines/` get reserved space in CLAUDE.md table per D18 — projected post-rollout count is 41.

**Files (heat map).**
- Heavy: `.pre-commit-config.yaml` (delete 15 hooks, move 5 to `pre-push`, add `repo-invariants` consolidation).
- Medium: `CLAUDE.md` (guards table audit per D18 — fix 8 inaccuracies + add 4 PR-4 rows + 1 PR-2 row → final 32); `docs/development/ci-pipeline.md` (rewrite to 5-layer model); `docs/development/structural-guards.md` (extend).
- New: `tests/unit/test_architecture_no_tenant_config.py`, `…_jsontype_columns.py`, `…_no_defensive_rootmodel.py`, `…_import_usage.py`, extend `…_query_type_safety.py` with 2 new test functions; `.pre-commit-coverage-map.yml`; `.pre-commit-hooks/check_repo_invariants.py`.
- Backfill: 27 existing test files (`tests/unit/test_architecture_*.py` + 3 transport boundary files) — add `@pytest.mark.architecture` decorator to every test function.
- DO NOT touch: `.github/workflows/ci.yml` (PR 3), `pyproject.toml` version anchors (PR 5).

**Verification environment.** TTL guard. `pre-commit` warm latency baseline (A8). Each new guard must run < 2s individually; the heaviest is `test_architecture_import_usage.py` (ports 243-LOC `check_import_usage.py` to AST).

**Escalation triggers.**
- Commit 3: any new guard takes >2s individually → profile, optimize, escalate if unfixable.
- Commit 7: pre-commit hook deletion regresses `make quality` (a guard didn't catch what the hook did) → STOP; revert commit 7, fix the guard, re-delete.
- Commit 8: warm latency >5s → profile, escalate.
- Red-team test fails to fire on injected violation → guard is broken; fix before deletion.

**Key facts from prior rounds.**
1. **Internal commit ordering enforces "guards added before hook deleted."** Commit 1-3 add infrastructure + new guards; commit 4 documents coverage map; commit 5 moves hooks to `pre-push`; commit 6 consolidates grep one-liners; **commit 7 is the actual hook deletion** — guards already on main and passing by then.
2. CLAUDE.md table on disk has 8 inaccuracies (D18): 3 phantom rows (test files don't exist) + 5 missing rows (test files exist but no row). Audit and fix exhaustively.
3. Guard count math: existing on-disk = 27 (23 `test_architecture_*.py` + 3 transport guards + 1 duplication script). PR 2 adds 1. PR 4 adds 4 + extends 1. Total post-rollout EXCLUDING v2.0 = 32. v2.0's 9 from `.guard-baselines/` → final 41.
4. `.pre-commit-coverage-map.yml` is documentation, not enforcement. It maps each deleted/moved hook to where its invariant now lives — used in PR description and verification.
5. Hooks DELETED (15 total): no-tenant-config, enforce-jsontype, check-rootmodel-access, enforce-sqlalchemy-2-0, check-import-usage (5 → guards); check-gam-auth-support, check-response-attribute-access, check-roundtrip-tests, check-code-duplication (4 → CI / Quality Gate); check-parameter-alignment, pytest-unit, mcp-endpoint-tests, suggest-test-factories, no-skip-integration-v2, check-migration-heads (6 → dead/redundant).
6. Hooks MOVED to `pre-push` (5): check-docs-links, check-route-conflicts, type-ignore-no-regression, adcp-contract-tests, mcp-contract-validation.
7. Final commit-stage hook count target: ≤12.
