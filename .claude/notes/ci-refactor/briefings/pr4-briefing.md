# PR 4 — Hook relocation + structural guards

## Briefing
**Where we are.** Week 4 → Week 5. PR 3 fully merged; `CI / Quality Gate` is required. Calendar: refactoring pre-commit for the layered model.

**What this PR does.** Per-hook reassignment per the 5-layer architecture. Drops warm pre-commit latency from ~23s baseline (A8) to <5s target. Migrates 5 grep hooks → AST-based structural guards. Moves **10 medium hooks to `pre-push` stage** (per D27 — `mypy` is the 10th per D3). Migrates 4 expensive hooks to CI-only (the `CI / Quality Gate` job introduced in PR 3 already runs them — this PR just deletes the redundant pre-commit invocation). Deletes 6 dead/advisory hooks. **ORDER IS LOAD-BEARING**: all new structural guards must pass on main BEFORE any hook is deleted. **Real hook math (D27): 36 effective commit-stage − 13 commit-stage deletions − 10 moves to pre-push − 1 consolidation = 12** (exactly at ≤12 ceiling, ZERO headroom — re-verify disk count at PR 4 authoring time).

**Drift closed.** PD16-PD22.

**Prerequisite:** ensure `.claude/notes/ci-refactor/.hook-baseline.txt` was captured at PR 1 author time (P10). PR 4's verify script reads this file and fails if drift is detected from the expected `effective_commit_stage: 36`. If the file is missing, run `bash .claude/notes/ci-refactor/scripts/capture-hook-baseline.sh` before PR 4 commit 5.

**You can rely on.** PR 3's `ci.yml` runs `check-gam-auth-support.py`, `check_response_attribute_access.py`, `check_roundtrip_tests.py`, `check_code_duplication.py` in `quality-gate`. PR 2's `_architecture_helpers.py` exists; you extend it. `@pytest.mark.arch_guard` marker registered (PR 2 commit 8) — backfill onto existing 27 guards happens here.

**You CANNOT do.** Delete a hook before its replacement guard passes on main. Add new CI checks beyond `CI / Quality Gate` work absorption (PR 3 owns workflow). Touch `.guard-baselines/` (v2.0 territory). Re-litigate D7 (prek). Edit `src/` outside fixing test failures from new guards.

**Concurrent activity.** v2.0 phase PRs landing on `.pre-commit-config.yaml` are HIGHEST conflict; coordinate. v2.0 contributes **27 architecture tests + 4 top-level + 9 baseline JSONs** (drift-verified). Per D18, post-v2.0-rebase guard count is **~81**. PR 4's CLAUDE.md table audit DEFERS to a post-v2.0-rebase commit; PR 4 commit 9 adds only **1 residual row** (`production_session_add` — v2.0 deletes `no_silent_except`, removing it from the residual list).

**Files (heat map).**
- Heavy: `.pre-commit-config.yaml` (delete 13 commit-stage hooks + 3 already-manual stubs (`pytest-unit`, `mcp-endpoint-tests`, `test-migrations`) = 16 total deletions, **move 10 to `pre-push`** (per D27 — adds `mypy` as 10th per D3), add `repo-invariants` consolidation).
- Medium: `CLAUDE.md` (guards table audit DEFERRED to post-v2.0-rebase per D18; PR 4 commit 9 adds only residual 2 missing rows — final ~81 post-rebase per D18 Round 8 revision (was ~73; corrected after v2.0 architecture/ count was re-verified at 27, not 31)); `docs/development/ci-pipeline.md` (rewrite to 5-layer model); `docs/development/structural-guards.md` (extend).
- New: `tests/unit/test_architecture_no_tenant_config.py`, `…_jsontype_columns.py`, `…_no_defensive_rootmodel.py`, `…_import_usage.py`, extend `…_query_type_safety.py` with 2 new test functions; `.pre-commit-coverage-map.yml`; `.pre-commit-hooks/check_repo_invariants.py`.
- Backfill: 27 existing test files (`tests/unit/test_architecture_*.py` + 3 transport boundary files) — add `@pytest.mark.arch_guard` decorator to every test function.
- DO NOT touch: `.github/workflows/ci.yml` (PR 3), `pyproject.toml` version anchors (PR 5).

**Verification environment.** TTL guard. `pre-commit` warm latency baseline (A8). Each new guard must run < 2s individually; the heaviest is `test_architecture_import_usage.py` (ports 243-LOC `check_import_usage.py` to AST).

**Escalation triggers.**
- Commit 3: any new guard takes >2s individually → profile, optimize, escalate if unfixable.
- Commit 7: pre-commit hook deletion regresses `make quality` (a guard didn't catch what the hook did) → STOP; revert commit 7, fix the guard, re-delete.
- Commit 8: warm latency >5s → profile, escalate.
- Red-team test fails to fire on injected violation → guard is broken; fix before deletion.

**Key facts from prior rounds.**
1. **Internal commit ordering enforces "guards added before hook deleted."** Commit 1-3 add infrastructure + new guards; commit 4 documents coverage map; commit 5 moves hooks to `pre-push`; commit 6 consolidates grep one-liners; **commit 7 is the actual hook deletion** — guards already on main and passing by then.
2. CLAUDE.md table on disk: **22 rows with 0 phantoms + 5 missing**. PR 4 commit 9 adds ONLY **1 residual row** (`production_session_add`); v2.0 phase PR adds 3 more (`bdd_obligation_sync`, `bdd_no_direct_call_impl`, `test_marker_coverage`) AND DELETES `test_architecture_no_silent_except.py` (drift-verified). The full ~81-row table audit DEFERS to a post-v2.0-rebase commit.
3. Guard count math (per D18): 27 baseline + 1 (PR 2) + 4 (PR 4) + 1 (PR 5) + 8 (PR 1/3/6 governance) + 27 (v2.0 architecture/) + 4 (v2.0 top-level) + 9 (v2.0 baseline JSONs) = **~81 final post-v2.0-rebase**.
4. `.pre-commit-coverage-map.yml` is documentation, not enforcement. It maps each deleted/moved hook to where its invariant now lives — used in PR description and verification.
5. Hooks DELETED (16 total: 13 commit-stage + 3 already-manual stubs): no-tenant-config, enforce-jsontype, check-rootmodel-access, enforce-sqlalchemy-2-0, check-import-usage (5 → guards); check-gam-auth-support, check-response-attribute-access, check-roundtrip-tests, check-code-duplication (4 → CI / Quality Gate); check-parameter-alignment, suggest-test-factories, no-skip-integration-v2, check-migration-heads (4 → dead/redundant); pytest-unit, mcp-endpoint-tests, test-migrations (3 → already manual; deletion is dead-stub cleanup, doesn't reduce commit-stage count).
6. Hooks MOVED to `pre-push` (10 per D27): check-docs-links, check-route-conflicts, type-ignore-no-regression, adcp-contract-tests, mcp-contract-validation, mcp-schema-alignment, check-tenant-context-order, ast-grep-bdd-guards, check-migration-completeness, **mypy** (the 10th per D3; CI's `CI / Type Check` job is authoritative).
7. Final commit-stage hook count target: ≤12 (real: 12; **zero headroom**). Re-verify at authoring time.
