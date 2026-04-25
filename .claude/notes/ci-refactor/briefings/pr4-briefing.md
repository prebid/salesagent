# PR 4 — Hook relocation + structural guards

## Briefing
**Where we are.** Week 4 → Week 5. PR 3 fully merged; `CI / Quality Gate` is required. Calendar: refactoring pre-commit for the layered model.

**What this PR does.** Per-hook reassignment per the 5-layer architecture. Drops warm pre-commit latency from ~23s baseline (A8) to <5s target. Migrates 5 grep hooks → AST-based structural guards. Moves **9 medium hooks to `pre-push` stage** (D27 P0 sweep — was 5; the 4 added: `mcp-schema-alignment`, `check-tenant-context-order`, `ast-grep-bdd-guards`, `check-migration-completeness`). Migrates 4 expensive hooks to CI-only (the `CI / Quality Gate` job introduced in PR 3 already runs them — this PR just deletes the redundant pre-commit invocation). Deletes 6 dead/advisory hooks. **ORDER IS LOAD-BEARING**: all new structural guards must pass on main BEFORE any hook is deleted. **Real hook math (D27 revised P0 sweep): 33 effective commit-stage − 13 commit-stage deletions − 9 moves to pre-push − 1 consolidation = 10** (under ≤12 ceiling, with 2-hook headroom).

**Drift closed.** PD16-PD22.

**You can rely on.** PR 3's `ci.yml` runs `check-gam-auth-support.py`, `check_response_attribute_access.py`, `check_roundtrip_tests.py`, `check_code_duplication.py` in `quality-gate`. PR 2's `_architecture_helpers.py` exists; you extend it. `@pytest.mark.architecture` marker registered (PR 2 commit 8) — backfill onto existing 27 guards happens here.

**You CANNOT do.** Delete a hook before its replacement guard passes on main. Add new CI checks beyond `CI / Quality Gate` work absorption (PR 3 owns workflow). Touch `.guard-baselines/` (v2.0 territory). Re-litigate D7 (prek). Edit `src/` outside fixing test failures from new guards.

**Concurrent activity.** v2.0 phase PRs landing on `.pre-commit-config.yaml` are HIGHEST conflict; coordinate. v2.0 contributes **31 new architecture tests + 9 baseline JSONs** (verified disk-truth audit). Per D18 (revised P0 sweep), post-v2.0-rebase guard count is **~73** (was 41/42). PR 4's CLAUDE.md table audit DEFERS to a post-v2.0-rebase commit; PR 4 commit 9 adds only the residual 2 missing rows.

**Files (heat map).**
- Heavy: `.pre-commit-config.yaml` (delete 13 commit-stage hooks + 2 already-manual stubs = 15 total deletions, move 9 to `pre-push`, add `repo-invariants` consolidation).
- Medium: `CLAUDE.md` (guards table audit DEFERRED to post-v2.0-rebase per D18 P0 sweep; PR 4 commit 9 adds only residual 2 missing rows — final ~73 post-rebase, NOT 32); `docs/development/ci-pipeline.md` (rewrite to 5-layer model); `docs/development/structural-guards.md` (extend).
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
2. CLAUDE.md table on disk (revised P0 sweep): **22 rows with 0 phantoms + 5 missing**. PR 4 commit 9 adds ONLY the residual 2 missing rows (`test_architecture_no_silent_except.py`, `test_architecture_production_session_add.py`); v2.0 phase PR will land the other 3 (`bdd_obligation_sync`, `bdd_no_direct_call_impl`, `test_marker_coverage`). The full ~73-row table audit DEFERS to a post-v2.0-rebase commit.
3. Guard count math (D18 revised P0 sweep): 27 baseline + 1 (PR 2) + 4 (PR 4) + 1 (PR 5) + 8 (PR 1/3/6 governance) + 31 (v2.0 architecture tests) + 9 (v2.0 baseline JSONs) = **~73 final post-v2.0-rebase**.
4. `.pre-commit-coverage-map.yml` is documentation, not enforcement. It maps each deleted/moved hook to where its invariant now lives — used in PR description and verification.
5. Hooks DELETED (15 total: 13 commit-stage + 2 already-manual stubs): no-tenant-config, enforce-jsontype, check-rootmodel-access, enforce-sqlalchemy-2-0, check-import-usage (5 → guards); check-gam-auth-support, check-response-attribute-access, check-roundtrip-tests, check-code-duplication (4 → CI / Quality Gate); check-parameter-alignment, suggest-test-factories, no-skip-integration-v2, check-migration-heads (4 → dead/redundant); pytest-unit, mcp-endpoint-tests (2 → already manual; deletion is dead-stub cleanup, doesn't reduce commit-stage count).
6. Hooks MOVED to `pre-push` (9 — D27 P0 sweep): check-docs-links, check-route-conflicts, type-ignore-no-regression, adcp-contract-tests, mcp-contract-validation, mcp-schema-alignment, check-tenant-context-order, ast-grep-bdd-guards, check-migration-completeness.
7. Final commit-stage hook count target: ≤12 (real: 10; 2-hook headroom).
