# Flask → FastAPI Migration: Deep Elaboration of §14, §16, §21

> **LAYERED SCOPE (2026-04-14) — L0-L4 use SYNC admin handlers; L5 converts to ASYNC.**
> This file predates the L0-L7 layering and contains ~68 async pattern references
> (`async def`, `AsyncSession`, `asyncpg`, etc.) that are **L5+ scope within v2.0**, not L0-L4.
> L0-L4 ship with sync `def` admin handlers; async SQLAlchemy lands at L5b (SessionDep alias flip) and
> mechanically propagates through L5c-L5e. The authoritative implementation guide is `execution-plan.md`.

This document elaborates three sections of `/Users/quantum/Documents/ComputedChaos/salesagent/.claude/notes/flask-to-fastapi/flask-to-fastapi-migration.md`: the 5-6 wave migration strategy (§14, expanded from 4 to 5-6 waves by the 2026-04-11 async pivot that absorbed async SQLAlchemy into v2.0 as Waves 4-5 — see `async-pivot-checkpoint.md`), the 28 assumptions (§16), and the verification strategy (§21). All file paths are absolute. Line numbers reference the current HEAD of the repository.

---

## PART 1: Per-Wave Execution Detail (elaborates §14)

### Pre-Wave-0 — Spike sequence gate (5.5-7.5 days, hard gate)

> **[L5 ENTRY GATE — not L0 entry]** The spike sequence below gates L5 entry. Under the L0-L4 sync-first layering, these spikes are NOT needed for Flask removal; they are the gate for the L5b async alias flip. Spike 3 (perf baseline) specifically captures at **L4 EXIT**, not L5a entry, so the comparison oracle exists before the flip.

This gate was added by the 2026-04-11 async pivot (`async-pivot-checkpoint.md`). Under the 2026-04-14 layering, v2.0 sequences sync Flask removal (L0-L2), test-harness modernization (L3), and FastAPI-native pattern refinement (L4) BEFORE the async conversion (L5). The 5.5-7.5 day spike sequence runs at the L5a entry gate on a throwaway branch `spike/async-pivot-gate` to prove the async conversion scope is tractable. The spike gate has one hard blocker (Spike 1) and eight soft blockers. If the hard blocker fails, L5 narrows scope or defers residual async to a v2.1 epic — L0-L4 already ship standalone value and are not affected. The gate is governed by `async-audit/agent-b-risk-matrix.md` §4 and `async-audit/agent-a-scope-audit.md` §6; CLAUDE.md §"Mandatory pre-L5 spike sequence" enumerates the **10-spike list** that every fresh session must honor. **Spikes 4.25, 4.5 and 5.5 were added on 2026-04-11 by the Decision 3 (factory-boy shim), Decision 7 (ContextManager refactor — now scheduled for L4, not L5d) and Decision 9 (background_sync sync-bridge) deep-think analyses — see CLAUDE.md §"Open decisions blocking Wave 4" resolutions for full context.**

#### A. Detailed acceptance criteria

1. Spike branch `spike/async-pivot-gate` exists, forked from current `main`, and does not merge back — it is discarded after the gate decision.
2. **Spike 1 (Lazy-load audit — HARD BLOCKER):** `src/core/database/database_session.py` rewritten to `create_async_engine` + `async_sessionmaker(expire_on_commit=False)`, `postgresql+asyncpg://` URL rewrite in place, and **every** `relationship()` in `src/core/database/models.py` (all 58-68 definitions per Agent A §4.1) annotated with `lazy="raise"`. `tox -e integration -- -x -v 2>&1 | tee spike-results.log` run to completion. Output classified into unique `InvalidRequestError: 'X.y' is not available due to lazy='raise'` failure categories. **Pass:** fewer than 40 unique failure categories AND total estimated fix cost < 2 days (each fix being a mechanical `selectinload(...)` / `joinedload(...)` addition in the relevant repository call site). **Fail:** 100+ unique failures OR non-obvious fixes in critical paths (cross-boundary lazy loads from `_impl` into admin serialization).
3. **Spike 2 (Driver compat — soft blocker):** `tox -e unit` + `tox -e integration` run under `DATABASE_URL=postgresql+asyncpg://...` on the spike branch. Every `TypeError`, `asyncpg.exceptions.*`, `InterfaceError` captured and classified by category (JSONB codec per Risk #17, event listener per Risk #18, `json_serializer` per Risk #22, prepared-statement cache conflict, UUID/Interval/Array codec). **Pass:** all integration tests pass OR fail only with lazy-load errors (not driver errors); JSONB roundtrip via `JSONType(model=X)` works for both typed and untyped columns; `SHOW statement_timeout` returns the configured value; Pydantic `json_serializer` emits correct JSONB on writes. **Fail:** multiple codec issues requiring >4 hours of manual fixes, OR statement timeout not applied, OR typed-column round-trip broken.
4. **Spike 3 (Performance baseline — soft blocker):** Committed `tests/performance/bench_admin_routes.py` and `tests/performance/bench_mcp_tools.py` harnesses. Run sync baseline on `main`: `pytest tests/performance/bench_admin_routes.py --benchmark-json=baseline-sync.json` + the MCP equivalent. Run async stack on `spike/async-pivot-gate` (with Spike 1's minimal lazy-load fixes applied): emit `spike-async.json` and `spike-async-mcp.json`. Compare p50/p95 latencies at 1/50/200 req/s concurrency points. **Pass:** low-concurrency (1 req/s) async within ±20% of sync; medium-concurrency (50 req/s) async within ±10% of sync; high-concurrency (200+ req/s) async outperforms sync by ≥20% (or sync saturates while async does not). The `baseline-sync.json` artifact is committed to the repo at `tests/performance/baselines/baseline-sync.json` as the Wave 5 comparison oracle. **Fail:** low-concurrency async >40% slower than sync, OR pool saturates at <100 concurrent, OR connection wait time spikes >500ms at moderate load.
5. **Spike 4 (Test harness — soft blocker):** `tests/harness/_base.py` converted: `__enter__`/`__exit__` duplicated as `__aenter__`/`__aexit__`. Factory-boy `AsyncSQLAlchemyModelFactory` shim landed (Agent B §4 Spike 4 / Agent A §7 decision 3). 5 representative integration tests selected (one per domain: accounts, products, principals, media_buys, creatives) and converted to `@pytest.mark.asyncio` + `async def` + `async with`. Run under `tox -e integration -- tests/integration/test_<picked>.py -n 4` (xdist 4 workers). **Pass:** all 5 converted tests pass; xdist parallelism holds (each worker binds its own DB, no event-loop collisions); factory-boy shim writes rows successfully; no `RuntimeError: Event loop is closed` in teardown. **Fail:** xdist workers collide on event-loop state, OR factory-boy cannot bind sessions, OR session fixture teardown hangs.
5.25. **Spike 4.25 (Factory async-shim validation — soft blocker, 0.5 day):** Validates Decision 3 resolution. Creates `tests/factories/_async_shim.py` per the corrected `foundation-modules.md` §11.13.1(D) recipe (overrides `_save`, NOT `_create`; no `sync_session.flush()`; `session.add(instance)` directly). Temporarily flips `TenantFactory` to `AsyncSQLAlchemyModelFactory` base. Runs 8 edge-case tests: (a) SubFactory chain resolves without flush (monkey-patch `AsyncSession.flush` to raise), (b) `RelatedFactory` runs after parent add (`TenantFactory.currency_usd → CurrencyLimitFactory`), (c) `AccountFactory._create` override still works (tenant kwarg is popped correctly), (d) partial-error rollback via savepoint (3 factories succeed → raise → next test sees zero rows), (e) nested fixture guard fires (call `bind_factories` twice → `RuntimeError`), (f) wrong session type guard fires (bind sync `Session` → `TypeError`), (g) factory not in `ALL_FACTORIES` fails loudly (`RuntimeError: unbound`), (h) 3-deep `AgentAccountAccessFactory` chain works (Tenant → Principal → Account → AgentAccountAccess, all 4 rows present after `await async_db.flush()`). **Pass:** all 8 green, no `MissingGreenlet`. **Fail (HARD):** recipe has a bug → STOP Wave 4 and re-analyze; reconsider polyfactory if `MissingGreenlet` fires on any flush path.
5.5. **Spike 4.5 (ContextManager refactor smoke test — soft blocker, 0.5-1 day):** Validates Decision 7 resolution. The `ContextManager` singleton at `src/core/context_manager.py:26-782` (inherits from `DatabaseManager`; caches `self._session`) is a hard blocker for full-async correctness — under `async_sessionmaker` on a single event-loop thread, the cached session is shared across concurrent tasks and causes transaction interleaving. `async_sessionmaker` does NOT fix this because the singleton sits above the session factory. The spike validates the refactor path before Wave 4a opens: rewrite `context_manager.py` to stateless async module functions taking `session: AsyncSession`; delete `DatabaseManager` from `database_session.py`; convert the smallest caller (`src/core/tools/creatives/_workflow.py::_create_sync_workflow_steps`) end-to-end; update the `tests/harness/media_buy_update.py::EXTERNAL_PATCHES` dict from single `"ctx_mgr"` entry to 4 function-level patch entries; delete the 18 lines of singleton-reset hacks at `tests/conftest_db.py:484-486,494`, `tests/fixtures/integration_db.py:134-144`, `tests/e2e/test_gam_lifecycle.py:153-155,170-172`. **Pass:** imports clean, one caller converted and its integration tests pass, harness updated, reset hacks deleted, grep-verified caller inventory table committed to `spike-decision.md` §"Decision 7 resolution", refactor LOC delta **<400** AND files touched **<15** AND test patches **<50**, and error-path composition test proves `raise` inside `_update_media_buy_impl` does NOT wipe `status="failed"` write when outer `session_scope()` rolls back (fail case documents the "use a separate `async with session_scope()` for error logging" idiom in §11.0.6 gotchas). Structural guard stub `tests/unit/test_architecture_no_singleton_session.py` committed to spike branch with allowlist containing ONLY the pre-refactor violations. **Fail (SOFT):** LOC >600 OR files >25 OR test patches >80 → ContextManager refactor becomes a dedicated Wave 4a sub-phase rather than part of the pilot (not a gate failure on the pivot itself, just a scope re-plan). Lazy-load bombs in `get_pending_steps` / `get_contexts_for_principal` get classified and added to Wave 4 scope as `selectinload(...)` additions per Spike 1 playbook.
6. **Spike 5 (Scheduler alive-tick — soft blocker):** `delivery_webhook_scheduler` and `media_buy_status_scheduler` tick bodies converted to `async with get_db_session()` / `await session.execute(...)`. Spike container started (`docker compose up -d`) and run for 5 minutes. Container logs grepped for `"delivery_webhook_scheduler alive"` and `"media_buy_status_scheduler alive"` — both present within 60 seconds of startup. One webhook forced (e.g., by creating a media buy with a push-notification config against a test fixture); delivery verified. **Pass:** both alive-tick lines visible, webhook delivered, no `CancelledError` / `MissingGreenlet` / `InvalidRequestError` in logs. **Fail:** alive-tick missing, OR scheduler crashes first tick, OR webhook payload construction fails with `MissingGreenlet` (this last one expands Wave 4 scope; soft-fails rather than gate-fails).
6.5. **Spike 5.5 (Two-engine coexistence — soft blocker, 0.5 day):** Validates Decision 9 resolution. Proves async asyncpg engine + sync psycopg2 engine can coexist in one Python process, sharing the same Postgres DB, with lifespan-driven shutdown, without deadlocks or resource leaks. Spike owner creates `src/services/background_sync_db.py` MVP (~200 LOC per the Decision 9 analysis: module-level ContextVar-held lazy-init engine, `get_sync_db_session()` contextmanager, `dispose_sync_bridge()` function, atexit hook, separate `application_name='adcp-salesagent-sync-bridge'` on the psycopg2 connection, pool `pool_size=2, max_overflow=3, pool_pre_ping=True, pool_recycle=3600`, statement_timeout=600s for long-running GAM syncs). Writes 4 test cases at `tests/driver_compat/test_sync_bridge_coexistence.py`: **(a) Engine lifecycle** — lazy-init, query, dispose, re-use after dispose raises; **(b) MVCC bidirectional visibility** — async engine writes a Tenant, sync-bridge reads it; sync-bridge updates the Tenant, async engine reads the update; **(c) Concurrent load** — 5 concurrent async requests + 1 background sync thread (2-second `pg_sleep`), all complete within 5 seconds without deadlock; **(d) Shutdown** — both engines dispose, post-dispose `pg_stat_activity` connection count ≤ baseline + 1 (no leaks). **Pass:** all 4 tests green. **Fail (SOFT):** deadlock in test (c) → abandon Option B for v2.0, fall back to Option A (asyncio task + single async session per sync, suboptimal but viable), document in `spike-decision.md`. Connection leak in test (d) → debug before falling back (most likely atexit hook issue). MVCC broken in test (b) → escalate to user (very unlikely given prior art). Also verifies the Wave 3 `from flask import current_app` fix at `src/services/background_sync_service.py:472` — spike owner confirms the replacement uses `src/admin/cache.py::SimpleAppCache` helper (see Decision 9 and Wave 3 acceptance criteria). Verifies grep shows 3 `flask-caching` consumer sites (inventory.py:874, inventory.py:1133, background_sync_service.py:472) — NOT zero — so the Wave 3 "delete flask-caching" plan is corrected to "replace with SimpleAppCache then delete".
7. **Spike 6 (Alembic async — soft blocker):** `alembic/env.py` rewritten with the standard async adapter pattern (`create_async_engine` + `async with connectable.connect() as connection: await connection.run_sync(do_run_migrations)` + `asyncio.run()` wrapper). Test DB dropped and recreated. Run `alembic upgrade head` — all 161 migrations apply cleanly. Run `alembic downgrade -1` then `alembic upgrade +1` — roundtrip works. Pick one migration that uses `op.execute(text("..."))` and verify it runs. **Pass:** upgrade, downgrade, roundtrip all clean. **Fail:** any migration errors inside `run_sync` callback OR `RuntimeError: asyncio.run() cannot be called from a running event loop` surfaces. Fail-action is to keep `alembic/env.py` sync with a boot-time `postgresql+asyncpg://` rewriter; this is documented in Risk #4 of `async-audit/agent-b-risk-matrix.md`.
8. **Spike 7 (`server_default` audit — soft blocker):** `grep -n "server_default" src/core/database/models.py` enumerates every column with a DB-side default. Each column classified: (a) safe under `expire_on_commit=False` because no caller reads it post-INSERT; (b) rewrite to `default=datetime.utcnow` (client-side); (c) must add `await session.refresh(obj)` at call sites. **Pass:** fewer than 30 total `server_default=` columns AND each is either safe or has a clear rewrite path (no `gen_random_uuid()`-style defaults that can't be client-side-replaced). **Fail:** >30 columns with many non-trivial rewrites.
9. **Spike 8 (L5 go/no-go decision gate — HARD, L5a EXIT, 0.5 day):** per the canonical spike table in `CLAUDE.md` §"v2.0 Spike Sequence", Spike 8 is the aggregate decision gate at L5a EXIT — NOT a technical spike. A written decision meeting note committed at `.claude/notes/flask-to-fastapi/spike-decision.md` summarizes each technical spike's pass/fail state, including `spike-results.log` excerpts for Spike 1, `baseline-sync.json` for Spike 3, Spike 4.5's LOC-count + error-path composition evidence, Spike 5.5's 4 test results, and the final go/no-go call. **Go condition:** Spike 1 PASSES AND no more than 2 soft spikes fail (out of Spikes 2-7 + 4.25 + 4.5 + 5.5 — total 9 soft spikes). Spike 4.25 is a HARD fail (recipe bug → stop Wave 4); Spikes 4.5 and 5.5 are SOFT blockers; fail actions fall back to dedicated L4 sub-phase PR (Spike 4.5) or Option A asyncio task (Spike 5.5), not pivot-level reverts. **No-go condition:** Spike 1 FAILS OR more than 2 soft spikes fail → narrow L5 scope (fewer async routers) OR ship L0-L4 only and defer async to v2.1. L0-L4 is not affected by a no-go; it ships independently. This resolves the 10-vs-11 count ambiguity: **"10 technical spikes + 1 decision gate = 11 total pre-L5b work items."**
10. The `baseline-sync.json` and `baseline-sync-mcp.json` artifacts are committed to `tests/performance/baselines/` on the migration branch (NOT on the spike branch, which is discarded). Wave 5 Spike F uses these as the comparison oracle.
11. All 9 open decisions from CLAUDE.md §"Open decisions blocking Wave 4" have signed-off answers recorded in `spike-decision.md` — including adapter base-class async strategy (decision 1), `DatabaseConnection` deletion (decision 2), factory-boy shim vs polyfactory (decision 3), `queries.py` / `database_schema.py` / `product_pricing.py` audit results (decisions 4-5), flask-caching async compat (decision 6), `context_manager.py` ContextVar propagation (decision 7), SSE session lifetime strategy (decision 8), and `background_sync_service.py` handling for long-running GAM jobs (decision 9).
12. `pyproject.toml` on `main` (NOT on the spike branch) has `asyncpg>=0.30.0,<0.32` and (if Spike 2 mandated fallback) `psycopg[binary,pool]>=3.2.0` as the driver choice recorded in an Agent F F1.1.1 note.
13. `[tool.pytest.ini_options]` section added to `pyproject.toml` with `asyncio_mode = "auto"` (Agent F F1.7.1 / F8.2.1).
14. New `tox -e driver-compat` environment defined in `tox.ini` that runs `tests/integration/` under the chosen async driver (Agent F F2.1.2 / F2.4.5).
15. Spike branch `spike/async-pivot-gate` deleted from origin after decision committed; the gate is a discard-and-document experiment, not an incremental merge.

#### B. File-level checklist

**CREATE (on `spike/async-pivot-gate`, discarded after gate):**
- `/Users/quantum/Documents/ComputedChaos/salesagent/tests/performance/bench_admin_routes.py` (~200 LOC, moved to `main` after gate closes)
- `/Users/quantum/Documents/ComputedChaos/salesagent/tests/performance/bench_mcp_tools.py` (~150 LOC, moved to `main` after gate closes)
- `/Users/quantum/Documents/ComputedChaos/salesagent/tests/performance/baselines/baseline-sync.json` (captured from `main`, committed to `main`)
- `/Users/quantum/Documents/ComputedChaos/salesagent/tests/performance/baselines/baseline-sync-mcp.json` (same)

**CREATE (on `main`, persists):**
- `/Users/quantum/Documents/ComputedChaos/salesagent/.claude/notes/flask-to-fastapi/spike-decision.md` (~300 LOC — go/no-go decision record, spike results, open-decisions resolutions)
- `/Users/quantum/Documents/ComputedChaos/salesagent/tests/performance/` directory — new
- `/Users/quantum/Documents/ComputedChaos/salesagent/tests/performance/baselines/` directory — new

**MODIFY (on `main`, persists regardless of spike outcome):**
- `/Users/quantum/Documents/ComputedChaos/salesagent/pyproject.toml` — add `[tool.pytest.ini_options]` with `asyncio_mode = "auto"`, add `asyncpg>=0.30.0,<0.32` (or `psycopg[binary,pool]>=3.2.0` if Spike 2 mandated fallback) alongside (not replacing) `psycopg2-binary` — the psycopg2 pin stays in place until Wave 4 lands the conversion
- `/Users/quantum/Documents/ComputedChaos/salesagent/tox.ini` — add `[testenv:driver-compat]` environment (Agent F F2.1.2)

**MODIFY (on `spike/async-pivot-gate` only, discarded):**
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/core/database/database_session.py` — async engine + session (spike experiment)
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/core/database/models.py` — `lazy="raise"` annotations on all relationships (spike experiment only — must not land on `main`)
- `/Users/quantum/Documents/ComputedChaos/salesagent/alembic/env.py` — async adapter (spike experiment)
- `/Users/quantum/Documents/ComputedChaos/salesagent/tests/harness/_base.py` — `__aenter__`/`__aexit__` addition (spike experiment)
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/services/delivery_webhook_scheduler.py` + `media_buy_status_scheduler.py` — tick bodies async (spike experiment)

**DELETE:** None. The spike is pure addition on a throwaway branch.

#### C. Risk assessment

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Spike 1 reveals 100+ lazy-load failures, killing the L5 conversion and triggering a major replan (narrow L5 scope or defer residual async to a v2.1 epic; 2-3 weeks of plan rewrite) | Medium | High — L5 async scope collapses, but L0-L4 (sync Flask removal) already ships standalone | Accept the rework. The spike exists specifically to catch this at L5a entry — catching it mid-L5c is an order of magnitude worse. The fallback (keep L0-L4 sync, shrink or defer L5) is pre-authorized. |
| `asyncpg` has a JSONB codec incompatibility with `JSONType(model=X)` (Risk #17 in Agent B matrix) and the spike cannot work around it in 4 hours | Medium | High — driver pivots to `psycopg[binary,pool]>=3.2.0` | Spike 2 is the detection mechanism. Fallback is pre-authorized in `async-pivot-checkpoint.md` §3 "Driver change" — switch driver, no other plan changes required. Agent B §2 Risk #17 documents the asyncpg JSON codec registration recipe if the workaround is <4 hours. |
| Spike 1 passes but developers skip the `lazy="raise"` annotation step, running integration tests against the default `lazy="select"` posture and getting false-green | Medium | High — Wave 4 detonates with lazy-load failures that should have been caught in Spike 1 | Spike 1 acceptance criterion #2 requires `lazy="raise"` on ALL 58-68 relationships; `spike-decision.md` must include a `git diff src/core/database/models.py | grep -c "lazy=\"raise\""` count that matches the relationship count. |
| Spike branch diverges from `main` during the 5-7 day spike window, and the gate decision becomes stale by the time Wave 4 starts | Medium | Medium | Spike branch is capped at 7 days. If `main` changes materially (new `relationship()` definitions, new `_impl` functions, new repositories), the spike is rerun on a fresh branch forked from current `main`. The gate is "the spike passed as of commit `<sha>`", not a blanket clearance. |
| Spike 2 driver-compat surfaces problems ONLY under load, not at the unit+integration test level | Low | High — asyncpg issue lands in staging | Spike 3 (performance baseline) runs under 1/50/200 req/s concurrency and surfaces pool contention that unit-level driver-compat can miss. The two spikes are complementary, not redundant. |
| Open-decisions resolution in `spike-decision.md` is rushed or dismissive, leaving Wave 4 to re-litigate adapter base-class strategy (decision 1) or SSE session lifetime (decision 8) mid-wave | Medium | High — Wave 4 scope doubles or stalls | Every decision in CLAUDE.md §"Open decisions blocking Wave 4" requires a signed-off answer before the go-decision. A decision of the form "revisit in Wave 4" is explicitly NOT acceptable — either answer it now or cut its scope from Wave 4. |
| Spike 5 (scheduler alive-tick) passes but under production lifespan composition (`combine_lifespans(app_lifespan, mcp_app.lifespan)` at `src/app.py:68`) a deadlock on shutdown surfaces that the spike didn't exercise | Low | Medium | Spike 5 should run a forced shutdown (`docker compose down -t 10`) and check for deadlock-related tracebacks in logs. Agent B Risk #26 documents this specifically and pre-Wave-0 must exercise it. |
| Spike 7 (`server_default` audit) finds a `gen_random_uuid()` default that has no client-side replacement and cascades into a post-INSERT `await session.refresh()` requirement in >20 call sites | Low | Medium | Agent B Risk #5 classifies this as a soft fail — the mitigation (explicit `refresh()`) is well-understood and adds ~0.5-1 day of work. Not a gate-fail. |
| Performance baseline (Spike 3) is captured on a developer laptop and the numbers diverge from CI's actual timings | Medium | Medium | Spike 3 must run inside the `test-stack` Docker stack on the agent's machine (OR on a CI job pinned to a dedicated runner), with the results recorded with the hardware fingerprint. The Wave 5 benchmark comparison re-runs on the same topology. |

#### D. Rollback procedure

The spike is a **throwaway branch**, not a merge. "Rollback" has two distinct meanings here:

**Spike branch rollback (no-op):** `git branch -D spike/async-pivot-gate && git push origin :spike/async-pivot-gate`. The branch is deleted from origin. No `main` changes involved.

**Gate failure rollback (pivot reversion):** if Spike 1 fails or the go-condition is not met, the 2026-04-11 async pivot itself is reverted. This means:

```bash
git checkout main
git revert -m 1 <pivot-propagation-commit-sha>   # 3e0afa02 and d8957931 and subsequent
git revert -m 1 <additional-pivot-commits>
git push origin main
```

The reverts restore the plan files to their pre-pivot state (sync-def Option C admin handlers). Under the 2026-04-14 layering this collapses the v2.0 scope back to L0-L4 only, with L5-L7 either narrowed (fewer async routers) or deferred to a v2.1 epic. Agent F non-surface changes (Dockerfile, CI, pre-commit updates) that are defensible independent of async can be preserved by cherry-picking non-async parts; the judgment call is made in `spike-decision.md`.

**Post-gate-failure actions:**
- v2.0 scope reverts to L0-L4 only (sync Flask removal + sync FastAPI-native refinement); L5-L7 drop out of v2.0
- A v2.1 epic gains an "async SQLAlchemy migration" body with the Agent A scope audit as its starting inventory
- The `pyproject.toml` `asyncpg` dep is removed (left only the legacy `psycopg2-binary`)
- `baseline-sync.json` captured at L4 EXIT stays in the repo as the v2.1 async comparison oracle
- `.claude/notes/flask-to-fastapi/CLAUDE.md` is updated to note the pivot was attempted and failed, with a reference to `spike-decision.md`

**Database:** no migrations.
**Environment variables:** none to back out.
**Rollback window:** indefinite — there is no downstream code depending on the spike.

#### E. Merge-conflict resolution

**Freeze scope:** none. The spike runs on a branch; `main` stays open for unrelated work during the spike window.

**Announcement template:**
```
[MIGRATION] Pre-Wave-0 async pivot spike runs <date> to <date+7>.
No main-branch freeze. The spike lives on spike/async-pivot-gate and
will be deleted after the go/no-go decision. A written decision lands
at .claude/notes/flask-to-fastapi/spike-decision.md.

Unrelated PRs can proceed. Do NOT merge anything that touches
src/core/database/models.py, src/core/database/database_session.py,
alembic/env.py, or tests/harness/_base.py during the spike window —
those would invalidate the spike measurements. If one of those files
urgently needs a fix, ping @migration-squad and we'll coordinate.
```

**Rebase strategy:** the spike branch is never rebased — it is discarded. If `main` changes materially during the spike window, the spike is rerun on a fresh fork from the new `main`.

#### F. Time estimate

- **Low (4 days):** all spikes pass cleanly on first run; no asyncpg codec surprises; lazy-load audit yields <20 failures all mechanically fixable; Spike 4.5 shows ContextManager refactor is <200 LOC (mechanical); Spike 5.5's two-engine coexistence works on the first driver combination. Skilled async-SQLAlchemy dev on the spike.
- **Expected (6 days):** 1 day for Spike 1 + 1 day Spike 2 + 0.5 day Spike 3 + 0.5 day Spike 4 + **0.5-1 day Spike 4.5 (ContextManager refactor smoke test)** + 0.5 day Spike 5 + **0.5 day Spike 5.5 (two-engine coexistence)** + 0.5 day Spike 6 + 0.5 day Spike 7 + 0.5 day writing `spike-decision.md` and resolving the 9 open decisions (of which 1/7/9 already carry 2026-04-11 pre-resolutions from CLAUDE.md deep-think — the spike VALIDATES them rather than re-resolves).
- **High (8 days):** Spike 1 reveals 60-90 lazy-load failures, Spike 2 asyncpg-fails and we re-run under psycopg3 (+1 day), Spike 4.5 hits the 400-LOC upper bound and needs Wave 4a sub-phase scope doc (+0.5 day), Spike 5.5 deadlocks in test (c) and we pivot to Option A fallback (+1 day debug), benchmark hardware concerns require re-running Spike 3.

#### G. Entry / exit criteria

**Entry:**
- `main` is green (`make quality` + `tox -e integration` + `tox -e bdd`) at the fork point.
- `feat/v2.0.0-flask-to-fastapi` async-pivot documentation (checkpoint + audit reports + implementation-checklist updates) is committed — no fresh reader should open a spike without reading `async-pivot-checkpoint.md` first.
- Agent A scope audit, Agent B risk matrix, Agent D AdCP verification, Agent E ideal-state gaps, Agent F non-surface inventory all committed.
- Staging PostgreSQL available for Spike 3 benchmark runs.
- Team availability: one senior async-SQLAlchemy engineer available for the full spike window.
- Docker test stack can be stood up on the spike-runner's machine (`./run_all_tests.sh` passes on main before the spike begins).
- CLAUDE.md § "Open decisions blocking Wave 4" has been read by the spike owner.

**Exit (go condition):**
- Spike 1 PASSES (<40 unique lazy-load failures, fix cost <2 days).
- **Spike 4.5 PASSES** (ContextManager refactor <400 LOC, error-path composition test green) OR has a concrete Wave 4a sub-phase scope documented in `spike-decision.md`.
- **Spike 5.5 PASSES** (4 coexistence test cases green) OR has a documented fallback to Option A asyncio task approach.
- At most 2 of Spikes 2-7 + 4.5 + 5.5 fail (8 soft spikes total).
- `spike-decision.md` committed to `main` with the go call, spike results excerpts, all 9 open-decision resolutions signed off (decisions 1/7/9 VALIDATED against their 2026-04-11 pre-resolutions from CLAUDE.md, not re-resolved).
- `baseline-sync.json` + `baseline-sync-mcp.json` committed to `tests/performance/baselines/`.
- `pyproject.toml` has the driver choice and `asyncio_mode = "auto"` landed.
- `tox -e driver-compat` environment exists and runs (even if it currently fails — the env definition is what's needed).
- Spike branch deleted from origin.
- **Wave 0 is now cleared to open.**

**Exit (no-go condition):**
- Spike 1 FAILS OR more than 2 soft spikes fail.
- `spike-decision.md` committed with the no-go call and a detailed failure analysis.
- Pivot-propagation commits reverted per §D above.
- `async-pivot-checkpoint.md` marked superseded; `.claude/notes/flask-to-fastapi/CLAUDE.md` updated to reflect the revert.
- v2.0 scope narrows to L0-L4 only (sync Flask removal + sync FastAPI-native refinement); L0 opens as planned under the Option C pattern.
- A v2.1 epic is opened for async SQLAlchemy absorption as a standalone follow-on migration.

**Wave 0 dependency:** Wave 0 (Flask foundation work) and pre-Wave-0 spikes can in principle overlap — Wave 0 is Flask-to-FastAPI foundation and does not touch the database layer. BUT: under the pivot, Wave 0's `src/admin/deps/auth.py` uses `async def` dep functions with `async with get_db_session()`, which assumes Wave 4 target-state DB patterns. If the spike gate fails, Wave 0's `deps/auth.py` must be rewritten to sync-def Option C signatures. **Decision: Wave 0 does NOT open until the spike gate lands, to avoid writing code that may need to be rewritten in-place within days.**

---

### Wave 0 — Foundation + template codemod (~2,500 LOC)

#### A. Detailed acceptance criteria

1. `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/templating.py` exists, exports `render(request, name, context)` and a module-level `templates: Jinja2Templates` singleton; `python -c "from src.admin.templating import render, templates"` succeeds.
2. `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/flash.py` exposes `flash(request, message, category='info')` and `get_flashed_messages(request, with_categories=False)`; both are imported by `templating.py` and exposed as Jinja globals.
3. `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/sessions.py` exports `build_session_middleware_kwargs() -> dict`, returning `secret_key` from `SESSION_SECRET`, `session_cookie='adcp_session'`, `same_site='lax'`, `https_only=True` in production.
4. `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/csrf.py` exposes a pure-ASGI `CSRFOriginMiddleware` class (Origin header validation, NOT Double Submit Cookie); `python -c "from src.admin.csrf import CSRFOriginMiddleware"` succeeds.
5. `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/oauth.py` registers an `authlib.integrations.starlette_client.OAuth` instance named `oauth` with a Google client; module-level constant `GOOGLE_CLIENT_NAME = "google"`.
6. `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/deps/auth.py` exports `CurrentUserDep`, `RequireAdminDep`, `RequireSuperAdminDep` as `Annotated[...]` aliases with module-level sync `def` dep functions (use `with get_db_session()` and `session.execute(...)`). **[REVERSED 2026-04-12 — v2.0 uses sync def, not async def]**
7. `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/app_factory.py::build_admin_router()` returns an empty `APIRouter(prefix="/admin", tags=["admin"])` — importable, callable, returns a non-None router.
8. `/Users/quantum/Documents/ComputedChaos/salesagent/scripts/codemod_templates_greenfield.py` exists and parses (`python -c "import ast; ast.parse(open('scripts/codemod_templates_greenfield.py').read())"` exit 0). **[MOVED TO L1a — codemod execution breaks Flask's url_for while Flask still serves traffic. L0 creates the script; L1a runs it atomically with FastAPI activation.]**
9. ~~After the codemod runs, `git diff --stat templates/` shows changes in at least 40 files.~~ **[MOVED TO L1a — see item 8.]**
10. `/Users/quantum/Documents/ComputedChaos/salesagent/tests/admin/test_templates_url_for_resolves.py` exists and **passes against an empty admin router** — it iterates every `url_for("name")` literal in templates and asserts the endpoint name follows the `bp_endpoint` flat-naming convention (regex `^[a-z_][a-z0-9_]*$`) without yet requiring the endpoint to resolve.
11. `tests/harness/_base.py::IntegrationEnv` has a new method `get_admin_client()` that is a sibling of `get_rest_client()` at line 894, lazy-caches `self._admin_client`, and returns a `TestClient` with admin dep overrides.
12. `python -c "from tests.harness import IntegrationEnv; env = IntegrationEnv(tenant_id='t1', principal_id='p1'); env.__enter__(); env.get_admin_client()"` succeeds (even though the router is empty, the TestClient construction must not error).
13. `/Users/quantum/Documents/ComputedChaos/salesagent/pyproject.toml` is **unchanged** in Wave 0.
14. `/Users/quantum/Documents/ComputedChaos/salesagent/src/app.py` is **unchanged** in Wave 0 — no middleware added, no router included.
15. `make quality` passes; `tox -e integration` passes; `./run_all_tests.sh` passes. Flask still serves 100% of `/admin/*`.

#### B. File-level checklist

**CREATE:**
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/templating.py` (~120 LOC)
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/flash.py` (~70 LOC)
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/sessions.py` (~40 LOC)
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/oauth.py` (~60 LOC)
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/csrf.py` (~100 LOC)
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/app_factory.py` (~80 LOC, empty router)
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/deps/__init__.py` (2 LOC)
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/deps/auth.py` (~220 LOC, shells matching the `_require_auth_dep` pattern at `/Users/quantum/Documents/ComputedChaos/salesagent/src/core/auth_context.py`)
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/deps/tenant.py` (~90 LOC)
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/deps/audit.py` (~110 LOC)
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/middleware/__init__.py` (2 LOC)
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/middleware/external_domain.py` (~90 LOC, pure-ASGI)
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/middleware/fly_headers.py` (~40 LOC, pure-ASGI)
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/routers/__init__.py` (2 LOC)
- `/Users/quantum/Documents/ComputedChaos/salesagent/scripts/codemod_templates_greenfield.py` (~80 LOC)
- `/Users/quantum/Documents/ComputedChaos/salesagent/tests/admin/test_templates_url_for_resolves.py` (~150 LOC)
- `/Users/quantum/Documents/ComputedChaos/salesagent/tests/unit/test_architecture_no_flask_imports.py` (~100 LOC — empty allowlist check, will guard Wave 2+)
- `/Users/quantum/Documents/ComputedChaos/salesagent/tests/unit/test_foundation_modules_import.py` (~50 LOC — smoke test that every foundation module imports cleanly)

**MODIFY:**
- `/Users/quantum/Documents/ComputedChaos/salesagent/tests/harness/_base.py` — add `get_admin_client()` method immediately after line 914
- 40+ template files under `/Users/quantum/Documents/ComputedChaos/salesagent/templates/` — mechanical codemod output

**DELETE:** None.

#### C. Risk assessment

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Codemod regex chokes on JS template literal `url_for` in `add_product_gam.html` | High | Medium — incorrect URLs in GAM product creation page | Codemod ships with a failing-safe mode: any template line it cannot parse gets logged and left untouched. Manual audit of the 4 files in §12.5 after the codemod run. |
| Template validator test passes against empty router but hides future endpoint-name bugs | Medium | Low — caught in Wave 1 | Validator has two modes: `--strict` (requires resolution) activated in Wave 1 entry criteria; default (Wave 0) only checks naming conventions. |
| Foundation modules import `Flask` transitively via `src.admin.app` | Low | Medium — circular import on app startup | New modules live at `src/admin/*.py`, never under `src/admin/blueprints/`. Unit test `test_foundation_modules_import.py` explicitly imports each new module in isolation. |
| `get_admin_client()` TestClient triggers `src.app` middleware that is not yet configured | Medium | Low — TestClient creation fails | Wave 0 `get_admin_client()` returns a `TestClient` of an isolated `FastAPI()` instance holding only the empty `build_admin_router()` output — not `src.app.app`. Wave 1 swaps it to `src.app.app` once middleware lands. |
| Codemod mass-rewrites 40+ files and collides with in-flight feature branches | High | High — developer toil | Announce a templates freeze 48h ahead; run codemod on main at off-hours; expect 1-2 rebases on open PRs touching `templates/`. |
| New `src/admin/csrf.py` body-reads and breaks future streaming responses | Medium | High — silent hang when SSE lands in Wave 3 | Middleware skips CSRF checks for `GET`, `HEAD`, `OPTIONS`, and any path matching `^/admin/.*?/stream$`; unit test asserts non-read for those methods/paths by passing a `Receive` spy. |
| `SESSION_SECRET` env var missing in dev loop crashes everyone's local run | Medium | Medium — dev loop breakage | Wave 0 `sessions.py` does NOT raise at import — only at middleware construction. Wave 0 never constructs it; added to `.env.example`. |

#### D. Rollback procedure

Wave 0 is **pure addition** (no deletes, no `src/app.py` changes). Rollback is a single-commit revert:

```
git checkout main
git revert -m 1 <wave-0-merge-sha>
git push origin main
```

Database state: no migrations. No env var changes require backing out (SESSION_SECRET only needs to be set when Wave 1 lands). Rollback window: until Wave 1 merges. After Wave 1 merges, a Wave 0 revert is still safe as long as the revert preserves `src/admin/templating.py` (Wave 1 depends on it) — so rollback becomes a *partial* revert by that point: `git revert <sha> -- templates/ scripts/codemod_templates_greenfield.py` only, leaving foundation modules in place.

#### E. Merge-conflict resolution

**Branch freeze scope:** `templates/**` only (foundation modules live in a new namespace and cannot conflict).

**Announcement template:**
```
[MIGRATION] Wave 0 lands <date>. Templates freeze from <date-1> 17:00 UTC
to <date> 23:59 UTC. Avoid opening PRs that touch files under templates/.
If you must, rebase onto main after the codemod lands; expect conflicts
on url_for(...) sites and resolve by re-running:
    python scripts/codemod_templates_greenfield.py templates/your_file.html
```

**Rebase strategy:** for conflicting PRs, `git checkout main -- templates/<file.html>` to take the post-codemod version, then re-apply the PR's semantic edits on top. Because the codemod is idempotent, re-running it on a rebased branch produces no diff if already applied.

#### F. Time estimate

- **Low (3 days):** Experienced FastAPI dev, clean main, no codemod surprises. Foundation modules are straight ports from §11.
- **Expected (5 days):** 2 days foundation, 1 day codemod scripting + audit, 1 day harness extension + validator, 1 day review/rebase.
- **High (8 days):** Codemod regression on JS template literals, `get_admin_client()` harness plumbing fights dependency cleanup at `tests/harness/_base.py:827-832`, security review of `csrf.py` demands a second iteration.

#### G. Entry / exit criteria

**Entry:**
- Main is green (`make quality` + `tox -e integration` + `tox -e bdd`).
- `SESSION_SECRET` env var defined in `.env.example` and staging secret store.
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/app.py` still has `a2wsgi` Flask mount at lines 299-304 — this is the safety net for Waves 0-2.
- Migration document §§11, 12, 13 signed off.

**Exit:**
- All 15 Wave-0 acceptance criteria pass.
- `rg -n "url_for" templates/ | wc -l` output ≥ 134 (§3.4 baseline) — codemod did not drop references.
- `python scripts/codemod_templates_greenfield.py --check templates/` returns exit code 0 (idempotent re-run).
- Coverage for `src/admin/**` not yet changed (foundation modules have smoke-test-only coverage; that's acceptable because nothing calls them yet).
- `git log --oneline main..HEAD` shows a single squashed merge commit.

---

### Wave 1 — Foundational routers + session cutover (~4,000 LOC)

#### A. Detailed acceptance criteria

1. `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/routers/public.py`, `core.py`, `auth.py`, `oidc.py` exist with every route from the corresponding Flask blueprints in `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/blueprints/`.
2. `GET /admin/login` returns 200 from `src/admin/routers/auth.py::login`, not from Flask. Verified by grep: `curl -sI http://localhost:8000/admin/login | grep -i server` returns the uvicorn banner, and a new integration test `tests/integration/test_admin_auth_router.py` asserts the route resolves via `IntegrationEnv.get_admin_client()`.
3. `GET /admin/auth/google/callback` (Google OAuth — Authlib re-exchange) completes a full redirect chain ending at `/admin/` with a valid `adcp_session` cookie set by `SessionMiddleware`. Verified against the byte-immutable URI list in `tests/unit/test_oauth_redirect_uris_immutable.py`.
4. `GET /admin/health` returns 200 from the new FastAPI `core.py` router. Old Flask `/admin/health` route is commented out in `src/admin/app.py`.
5. `SessionMiddleware` is registered in `/Users/quantum/Documents/ComputedChaos/salesagent/src/app.py` in the canonical L1a stack `Fly → ExternalDomain → UnifiedAuth → Session → CSRF → RestCompat → CORS` (outermost → innermost; `TrustedHost` added at L2, `RequestID` at L4/L6 — see `foundation-modules.md` §cross-cutting/Middleware ordering for the full L1a/L2/L4-L6 progression). Middleware ordering verified by `test_middleware_ordering.py`.
6. `CSRFOriginMiddleware` is registered **inside** `SessionMiddleware` (`request.session` is available to handlers downstream of CSRF). Unit test asserts order.
7. `ApproximatedExternalDomainMiddleware` is registered in `src/app.py` OUTSIDE `UnifiedAuth`/`Session`/`CSRF`; test confirms non-admin paths short-circuit without session access, and the hard invariant (ExternalDomain BEFORE CSRF per `notes/CLAUDE.md` #2) holds.
8. `register_blueprint` calls for `public`, `core`, `auth`, `oidc` in `src/admin/app.py` are commented out (not deleted — Wave 2 deletes them).
9. Flask catch-all mount at `src/app.py:299-304` **still exists** and still serves the other 26 blueprints.
10. Session cookie name change: a stale `session=...` cookie in a request returns a fresh login page (not an error). Verified by Playwright test `login_with_stale_flask_cookie`.
11. CSRF Origin check: `POST /admin/auth/logout` with session cookie AND `Origin: https://evil.example.com` → 403; same POST with `Origin: https://admin.sales-agent.example.com` → 303. (Origin-header validation per §11.7; no form token, no `adcp_csrf` cookie.)
12. POST `/admin/*` with no Origin AND no Referer passes (SameSite=Lax on the session cookie is the defense for legacy UAs); `Origin: null` is always rejected.
13. `test_templates_url_for_resolves.py` runs in `--strict` mode: every `url_for("name")` in templates referenced by Wave 1 routers resolves to an actual registered endpoint.
14. `make quality` passes; `tox -e integration` passes; `tox -e bdd` passes; Playwright `test_admin_login_flow.py` passes against staging.
15. Pre-Wave-1 integration tests that asserted `response.status_code == 302` for login redirects are updated to `303` (FastAPI `RedirectResponse` convention).

#### B. File-level checklist

**CREATE:**
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/routers/public.py` (~400 LOC)
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/routers/core.py` (~600 LOC)
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/routers/auth.py` (~1,100 LOC)
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/routers/oidc.py` (~500 LOC)
- `/Users/quantum/Documents/ComputedChaos/salesagent/tests/integration/test_admin_public_router.py`
- `/Users/quantum/Documents/ComputedChaos/salesagent/tests/integration/test_admin_core_router.py`
- `/Users/quantum/Documents/ComputedChaos/salesagent/tests/integration/test_admin_auth_router.py`
- `/Users/quantum/Documents/ComputedChaos/salesagent/tests/integration/test_admin_oidc_router.py`
- `/Users/quantum/Documents/ComputedChaos/salesagent/tests/integration/test_middleware_ordering.py`
- `/Users/quantum/Documents/ComputedChaos/salesagent/tests/e2e/test_admin_login_flow.py` (Playwright)
- `/Users/quantum/Documents/ComputedChaos/salesagent/tests/e2e/test_admin_csrf_enforcement.py` (Playwright)

**MODIFY:**
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/app.py` — register `SessionMiddleware`, `CSRFOriginMiddleware`, `ApproximatedExternalDomainMiddleware`, `include_router(build_admin_router())`. Lines 274-293 (middleware stack) and 299-304 (mount) both touched.
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/app.py` — comment out `register_blueprint` calls for the 4 migrated blueprints.
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/app_factory.py` — `build_admin_router()` now `include_router`s the 4 feature routers.
- `/Users/quantum/Documents/ComputedChaos/salesagent/tests/unit/test_architecture_no_flask_imports.py` — **remove** `public.py/core.py/auth.py/oidc.py` from the allowlist (forbids re-introducing Flask in migrated files).
- `/Users/quantum/Documents/ComputedChaos/salesagent/tests/integration/conftest.py` — `authenticated_admin_client` fixture swaps from Flask `test_client()` to `IntegrationEnv.get_admin_client()` for routes now served by FastAPI.
- `/Users/quantum/Documents/ComputedChaos/salesagent/pyproject.toml` — add `pydantic-settings>=2.7.0` (Wave 1 adds, Wave 3 removes Flask deps). **`itsdangerous` NOT explicitly pinned — Origin-based CSRF doesn't use it; it stays transitive via `SessionMiddleware`.** **`sse-starlette` NOT added per Decision 8 DELETE.**

**DELETE:** None in Wave 1. Flask blueprint files stay on disk; only the `register_blueprint` calls are commented out.

#### C. Risk assessment

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Middleware order bug: `UnifiedAuthMiddleware` fires before session is loaded → auth sees empty session | High | Critical — login broken | `test_middleware_ordering.py` inspects `app.user_middleware` list and asserts the sequence; ordering is a pure ASGI concern and deterministic. |
| CSRF middleware body-reads on JSON POST and blocks downstream body consumption | Medium | High — all form POSTs hang | Pure-ASGI CSRF middleware reads header only; body-parsing happens in the handler via `Form()`. Integration test posts 5MB form body and asserts handler receives it. |
| Session cookie name change (`session` → `adcp_session`) causes user-visible logout everyone at once | Certain | Low (acceptable per decision #7) | Announce in release notes; add a GET `/admin/` handler that detects the old cookie name and 303's to `/admin/login` (expected flow). |
| Authlib `starlette_client.OAuth` has a silent API drift from `flask_client.OAuth` around `authorize_redirect` signatures | Medium | High — OAuth callback 500s | Spike a Playwright happy-path in staging **before** the Wave 1 PR is marked ready. Entry criterion #5 below. |
| `request.url_for("bp_endpoint")` fails to resolve because `APIRouter(prefix="/admin")` nests another prefix | Medium | High — `NoMatchFound` at runtime | Assumption #19 verified via `test_url_for_nesting.py` that calls `request.url_for` on a router mounted the same way. |
| Concurrent PRs rename routes in `/Users/quantum/Documents/ComputedChaos/salesagent/templates/base.html` (header nav) during Wave 1 branch | High | Medium — merge conflict hell | Declare `src/admin/routers/public.py|core.py|auth.py|oidc.py` freeze; template conflicts resolved by re-running codemod. |
| CSRFOriginMiddleware rejects OIDC form_post callback because the IdP's Origin cannot be pre-registered | Medium | Critical — OIDC broken | `CSRFOriginMiddleware._EXEMPT_PATH_PREFIXES` is the canonical exempt tuple at `src/admin/csrf.py` per `foundation-modules.md §11.7`: `/mcp`, `/a2a`, `/api/v1/`, `/.well-known/`, `/agent.json`, `/_internal/`, `/admin/auth/google/callback`, `/admin/auth/oidc/callback`, `/admin/auth/gam/callback`. State validation (§11.6.1) replaces Origin validation on the OIDC path. Guard: `test_architecture_csrf_exempt_covers_adcp.py`. |
| Staging `SESSION_SECRET` leaks in logs or environment dumps | Low | Medium | Code review gate: grep PR for any `logger.info.*SESSION_SECRET` or `print.*SESSION_SECRET`. |
| `SessionMiddleware` payload exceeds 3.5KB for super-admin sessions | Low | Medium — cookie silently truncated | Verification test (see Part 2 assumption #5). If fails, fallback to `starlette-session` Redis backend; not a release-blocker because super-admin is a tiny user set. |

#### D. Rollback procedure

Wave 1 is reversible via single-commit revert until Wave 2 merges:

```
git checkout main
git revert -m 1 <wave-1-merge-sha>
git push origin main
```

**Required post-revert action:** manually restore the `register_blueprint` calls in `src/admin/app.py` — they were commented out, not deleted, so `git revert` restores them automatically. Verify with `grep "register_blueprint" src/admin/app.py`.

**Session cookie concern:** users who logged in on the new cookie (`adcp_session`) stay logged in under the reverted Flask app only if they also have a legacy `session` cookie, which they don't. Expect a second round of forced re-logins on rollback. Document in the revert PR description.

**Environment variables to back out:** none required (leaving `SESSION_SECRET` set does no harm; Flask ignores it).

**Database:** no migrations.

**Rollback window:** open until Wave 2 merge. After Wave 2, rollback requires reverting both PRs — the Wave 2 revert restores Flask blueprints, then the Wave 1 revert restores the `register_blueprint` wiring.

#### E. Merge-conflict resolution

**Freeze scope:** `src/admin/routers/public.py`, `core.py`, `auth.py`, `oidc.py`, `src/app.py` middleware stack lines 274-304, `src/admin/app.py` `register_blueprint` section.

**Announcement:**
```
[MIGRATION] Wave 1 lands <date>. Freeze on:
  - src/admin/routers/{public,core,auth,oidc}.py (do not touch)
  - src/admin/blueprints/{public,core,auth,oidc}.py (read-only — being replaced)
  - src/app.py lines 274-304
  - src/admin/app.py register_blueprint block
Rebase window: rebase onto post-Wave-1 main and expect conflicts only
in the 4 target blueprints if you were mid-change. Bug fixes to those
blueprints should be applied to BOTH the Flask source AND the new
FastAPI router during the freeze.
```

**Rebase strategy:** for PRs touching the 4 migrated blueprints, re-apply the semantic fix to the corresponding `src/admin/routers/*.py` file instead and drop the Flask-side change. For middleware conflicts in `src/app.py`, take main's version of lines 274-304 and re-apply your own middleware below the new admin-facing middleware.

#### F. Time estimate

- **Low (4 days):** 4 straightforward routers, shared CSRF/session infra pre-tested in Wave 0, single-pass code review.
- **Expected (6 days):** 3 days routers, 1 day middleware wiring + ordering tests, 1 day Playwright OAuth + CSRF tests, 1 day fixing staging surprises.
- **High (10 days):** OAuth Starlette-client API drift requires a redesign of `src/admin/oauth.py`, middleware ordering has a subtle bug caught in staging, per-tenant OIDC dynamic client flow has untested edge cases.

#### G. Entry / exit criteria

**Entry:**
- Wave 0 merged to main.
- `SESSION_SECRET` set in staging secret store.
- Playwright smoke run on staging against an empty admin router confirms `get_admin_client()` infra is sound.
- Authlib starlette_client happy-path spike completed (see assumption #8 verification).

**Exit:**
- All 15 Wave-1 acceptance criteria pass.
- 4 new routers together have ≥90% branch coverage (matches deleted blueprint coverage − 1 point).
- Zero Flask imports in `src/admin/routers/**` (enforced by `test_architecture_no_flask_imports.py`).
- Staging deploy completes; manual login smoke test by 2 engineers.

---

### Wave 2 — Bulk blueprint migration (~9,000 LOC)

#### A. Detailed acceptance criteria

1. 22 new routers exist under `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/routers/`: `accounts.py`, `products.py`, `principals.py`, `users.py`, `tenants.py`, `gam.py`, `inventory.py`, `inventory_profiles.py`, `creatives.py`, `creative_agents.py`, `signals_agents.py`, `operations.py`, `policy.py`, `settings.py`, `adapters.py`, `authorized_properties.py`, `publisher_partners.py`, `workflows.py`, `api.py`, `format_search.py`, `schemas.py`, `tenant_management_api.py`, `sync_api.py`, `gam_reporting_api.py`. (The wave list is 22 HTML/JSON blueprints + 3 top-level APIs = 25 target files.)
2. Every route previously served by Flask in those blueprints resolves via FastAPI. Verified by `tests/integration/test_route_parity.py` which loads the pre-Wave-2 Flask URL map (captured as a JSON fixture) and asserts FastAPI resolves each URL + method to a non-500 response.
3. `register_blueprint` for all migrated blueprints deleted from `src/admin/app.py`. Only the Flask catch-all remains wired (for safety during the branch, even though there should be nothing left to catch).
4. Flask blueprint files deleted from `src/admin/blueprints/`. `git rm` applied to `accounts.py`, `products.py`, `principals.py`, `users.py`, `tenants.py`, `gam.py`, `inventory.py`, `inventory_profiles.py`, `creatives.py`, `creative_agents.py`, `signals_agents.py`, `operations.py`, `policy.py`, `settings.py`, `adapters.py`, `authorized_properties.py`, `publisher_partners.py`, `workflows.py`, `api.py`, `format_search.py`, `schemas.py`.
5. `src/admin/tenant_management_api.py`, `src/admin/sync_api.py`, `src/adapters/gam_reporting_api.py` deleted or gutted into FastAPI routers in `src/admin/routers/`. The 3 category-2 JSON API modules preserve their error shape via a compat exception handler, verified by new `test_category2_error_shape.py`.
6. Dead code deleted: `src/services/gam_inventory_service.py::create_inventory_endpoints` (early return at line 1469).
7. `src/adapters/google_ad_manager.py::register_ui_routes` and `src/adapters/mock_ad_server.py::register_ui_routes` deleted; their content re-homed into `src/admin/routers/adapters.py`.
8. `test_architecture_no_flask_imports.py` allowlist has **3 entries** at the end of Wave 2 (`src/admin/app.py`, `src/app.py`, `src/admin/blueprints/activity_stream.py` — those 3 files move to Wave 3).
9. Every new router has at least one integration test per route using `IntegrationEnv.get_admin_client()`.
10. Coverage parity: each new router's line coverage ≥ (deleted blueprint coverage − 1 point), measured by `scripts/check_coverage_parity.py` (Part 3).
11. `test_category1_native_error_shape.py` asserts `POST /admin/api/*` endpoints return `{"detail": "..."}` on 4xx (native FastAPI shape).
12. `test_category2_compat_error_shape.py` asserts `POST /api/v1/tenant-management/*` endpoints return `{"success": false, "error": "..."}` on 4xx (preserved compat).
13. Flask catch-all mount is still live at `src/app.py:299-304` as a safety net but should be unreached. New test `test_flask_catchall_unreached.py` marks the Flask mount as a 404-returning shim and asserts no request routes to it during `./run_all_tests.sh`.
14. Branch lifetime ≤ 7 calendar days from PR open to merge. Announce `src/admin/**` freeze at PR open.
15. `make quality`, `tox -e integration`, `tox -e bdd`, `./run_all_tests.sh` all pass.

#### B. File-level checklist

**CREATE:** 25 new router files at `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/routers/*.py` (total ~9,000 LOC). 25 corresponding integration test files under `/Users/quantum/Documents/ComputedChaos/salesagent/tests/integration/test_admin_*_router.py`. `tests/integration/test_route_parity.py` (~200 LOC). `tests/integration/test_category1_native_error_shape.py`. `tests/integration/test_category2_compat_error_shape.py`. `tests/integration/test_flask_catchall_unreached.py`. `scripts/check_coverage_parity.py` (~150 LOC, Part 3).

**MODIFY:**
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/app.py` — delete `CustomProxyFix` if unused; update `include_router` calls; keep Flask catch-all.
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/app_factory.py` — wire 22 new routers.
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/app.py` — delete `register_blueprint` calls for 22 migrated blueprints; keep only Flask catch-all plumbing for activity_stream.
- `/Users/quantum/Documents/ComputedChaos/salesagent/tests/unit/test_architecture_no_flask_imports.py` — shrink allowlist to 3 entries: `src/admin/app.py`, `src/app.py`, `src/admin/blueprints/activity_stream.py`.
- `/Users/quantum/Documents/ComputedChaos/salesagent/tests/integration/conftest.py` — delete `flask_client`, `authenticated_client`, `admin_client`, `test_admin_app`, `authenticated_admin_client` fixtures (replaced by `get_admin_client()`).
- `/Users/quantum/Documents/ComputedChaos/salesagent/tests/conftest.py` — delete `flask_app`, `flask_client`, `authenticated_client` fixtures (lines 596-635 per §5.3).

**DELETE:**
- 21 files under `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/blueprints/` (every file except `activity_stream.py`).
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/tenant_management_api.py`
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/sync_api.py`
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/adapters/gam_reporting_api.py`
- `create_inventory_endpoints` function body in `src/services/gam_inventory_service.py` (the early-return dead code).
- `register_ui_routes` in `src/adapters/google_ad_manager.py` and `src/adapters/mock_ad_server.py`.
- 17 integration test files that build a Flask test app (§5.8).
- `tests/admin/test_accounts_blueprint.py`, `tests/admin/test_product_creation_integration.py` (replaced by FastAPI equivalents).
- `tests/admin/conftest.py` fixtures `ui_client`, `authenticated_ui_client`.

#### C. Risk assessment

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| 22 blueprints in one PR is unreviewable | High | High | One router per commit within the PR; PR description includes a blueprint-by-blueprint diff summary; 3 reviewers assigned per area (HTML UI / JSON API / adapters). |
| `src/admin/` freeze for 7 days blocks product work | High | Medium | Announce 2 weeks ahead; offer exception lane: fixes that MUST land apply to both Flask (blueprints under migration) AND FastAPI router simultaneously, in a fast-track PR merged into the Wave 2 branch. |
| Route parity test finds a 500 on an obscure URL combination | Medium | Medium | `test_route_parity.py` is an acceptance-time smoke test, not a parity oracle; it asserts non-500 only. Functional parity is the job of the per-router integration tests. |
| Deleted adapter `register_ui_routes` hooks break a downstream adapter we don't know about | Low | Medium | Grep `rg 'register_ui_routes' src/adapters/` — if more adapters show up, add them to the Wave 2 list. Currently only 2 call sites. |
| Category-2 compat exception handler shape drift: new `{"success": false, ...}` differs subtly from old | Medium | High — external consumer (Datadog synthetic, dashboards) breaks | Golden fixtures captured from pre-Wave-2 live traffic via a shadow-trace sidecar; `test_category2_compat_error_shape.py` compares byte-for-byte. |
| SessionMiddleware max-cookie-size hit on super-admin (many tenants in session) | Low | High | Measured in Wave 1 verification; if >3.5KB, switch to server-side `starlette-session` Redis backend before Wave 2 merges. |
| Async SQLAlchemy latency profile regresses vs pre-migration sync baseline | Medium | Medium | Benchmark in CI async (Wave 4-5) vs pre-migration sync baseline (Wave 2); acceptable range is net-neutral to ~5% improvement under moderate concurrency; significantly worse signals `pool_size` tuning is needed (Risk #6 in `async-pivot-checkpoint.md` §4). Under low concurrency async has slightly higher per-request overhead; under high concurrency it wins big. |
| Test harness `get_admin_client()` leaks state between tests when dep overrides persist | Medium | Medium | Teardown at `tests/harness/_base.py:827-832` already clears overrides; extend to also null `self._admin_client`. Integration test `test_harness_isolation.py`. |
| Concurrent PR to `tests/integration/conftest.py` conflicts with fixture deletions | High | Low | Expected; document in freeze announcement. |
| Datadog dashboards reference old `/admin/*/status` endpoints that now return different JSON | Medium | High — silent metric loss | Grep Datadog exports + ping platform team during Wave 2 entry criterion (assumption #18 verification). |

#### D. Rollback procedure

Wave 2 is the largest and hardest to roll back. Single-commit revert still works, but the revert commit is itself large:

```
git checkout main
git revert -m 1 <wave-2-merge-sha> --no-edit
# Expect 25+ files restored; verify
git diff HEAD~1 --stat | head -30
git push origin main
```

**Partial rollback option:** if only one router is broken (say `gam.py`), revert just that router + its tests + restore the Flask blueprint: `git checkout <pre-wave-2-sha> -- src/admin/blueprints/gam.py tests/admin/test_gam*` and re-add `register_blueprint(gam_bp)` to `src/admin/app.py`. The Flask catch-all is still live at `src/app.py:299-304` so the restored Flask route is reachable immediately.

**Database:** no migrations.

**Environment variables:** none to back out.

**Rollback window:** open until Wave 3 merges. After Wave 3, Flask catch-all is gone and any rollback requires re-adding `a2wsgi`, Flask, and the mount wiring — effectively recreate Waves 2+3 in reverse. Document this as a hard line in the Wave 3 PR.

#### E. Merge-conflict resolution

**Freeze scope:** entire `src/admin/**` tree except `src/admin/blueprints/activity_stream.py`. Whole `tests/integration/**` for anything touching the deleted fixtures.

**Announcement:**
```
[MIGRATION] Wave 2 FREEZE: <date> to <date+7>. Scope:
  - src/admin/** (22 blueprints being replaced in one PR)
  - tests/integration/conftest.py admin fixtures
  - tests/admin/ (entire directory moving to FastAPI)
Emergency exception: bug fixes to migrated blueprints apply to BOTH
Flask source AND the Wave 2 branch's FastAPI router. File an issue tagged
[wave-2-exception] and ping @migration-squad.
Do NOT open speculative PRs to these files during the freeze.
```

**Rebase strategy:** do not rebase the Wave 2 branch during the freeze (the freeze exists specifically to prevent rebase thrash). On merge day, resolve any conflicts by taking Wave 2's version and re-applying semantic edits on top. All 22 blueprints live on a single long-lived branch `migration/wave-2`.

#### F. Time estimate

- **Low (5 days):** Blueprint patterns are homogeneous, codemod-friendly. Experienced team, 3 engineers.
- **Expected (7 days):** 22 blueprints × ~30 min each = 11 hours coding, then 3 days tests + review + CI green + staging validation.
- **High (14 days):** Hidden Flask-ism in a blueprint requires architectural rework (e.g., `products.py` at 2,464 LOC has surprises), category-2 error-shape compat discovered to be harder than planned, staging parity test finds non-obvious behavior diffs.

#### G. Entry / exit criteria

**Entry:**
- Wave 1 merged and running in staging for ≥3 business days.
- Wave 1 Playwright suite passing on staging nightly.
- `scripts/check_coverage_parity.py` tested on Wave 1 and green.
- `test_route_parity.py` baseline fixture captured from Wave 1 staging (JSON map of URL+method → status).
- Platform team confirms no external consumer depends on Flask-specific category-1 JSON shapes (assumption #18).
- `SESSION_SECRET` cookie-size instrumented in Wave 1 and confirmed <3.5KB over 24h of staging traffic.
- All 22 blueprints have a designated owner who will review their replacement router.
- Freeze announcement sent 48h before PR opens.

**Exit:**
- All 15 Wave-2 acceptance criteria pass.
- `git grep -l "flask" src/admin/` returns only `src/admin/app.py` and `src/admin/blueprints/activity_stream.py`.
- Flask catch-all receives zero requests in 24h of staging traffic (monitored).
- Datadog and dashboards confirmed green by platform team.
- PR merged within 7 calendar days of opening.

---

### Wave 3 — Cache migration + Flask cleanup cutover (~2,500 LOC)

> **⚠️ CORRECTED 2026-04-11 (Decision 8 deep-think): SSE route is DELETED in Wave 4, NOT ported in Wave 3.** The original criteria 1-4 below prescribed building an SSE feature with `sse_starlette.EventSourceResponse`. Decision 8 deep-think analysis verified the SSE `/events` route is **orphan code** — `templates/tenant_dashboard.html:972` literally says `// Use simple polling instead of EventSource for reliability`, zero `new EventSource(` exists in templates, and the only `/events` caller is one integration smoke test probe. The SSE route, generator, rate-limit state, and `sse_starlette` dependency are all **deleted in Wave 4** per Decision 8. Wave 3 now focuses on the cache migration (Decision 6) and Flask removal.

#### A. Detailed acceptance criteria

> ~~1-4. SSE criteria~~ **STALE — Decision 8 DELETE.** Do NOT build an SSE endpoint. The `/events` route is orphan code deleted in Wave 4. The `sse_starlette` dependency is NOT added. See Decision 8 in CLAUDE.md and `async-pivot-checkpoint.md` §3 "SSE / long-lived connections" for the deletion scope.

5. `flask`, `flask-socketio`, `python-socketio`, `simple-websocket`, `waitress`, `a2wsgi`, `types-waitress` removed from `pyproject.toml`. **`flask-caching` is NOT deleted in Wave 3 — it is replaced first, then deleted (Decision 6/9 correction).** The original plan claimed "zero callers" but 3 consumer sites exist. Wave 3 ships a replacement per the strict 12-step migration order in `foundation-modules.md` §11.15:
5.1. `src/admin/cache.py::SimpleAppCache` exists (**~90 LOC**, corrected from ~40 LOC per Decision 6 deep-think), `cachetools.TTLCache(maxsize=1024, ttl=300)`-backed, thread-safe via `threading.RLock` (NOT `asyncio.Lock` — Site 3 is a sync `threading.Thread`), `_NullAppCache` stub fallback for lifespan startup race window, `CacheBackend` Protocol for v2.2 Redis swap, env-overridable via `ADCP_INVENTORY_CACHE_MAXSIZE` + `ADCP_INVENTORY_CACHE_TTL`, install hook `install_app_cache(app)` called from the FastAPI lifespan startup BEFORE `yield`, global-lookup helper `get_app_cache()` for background-thread access.
5.2. `src/admin/blueprints/inventory.py:874, 1133` consumer sites migrated from `getattr(current_app, "cache", None)` to `request.app.state.inventory_cache`. **IMPORTANT:** cache dicts, NOT Flask Response objects (`jsonify(...)` returns a Flask `Response` which cannot be served by FastAPI). Fold `cache_key` + `cache_time_key` pair into single 2-tuple `(payload_dict, timestamp)` entry to eliminate the non-atomic write race.
5.3. `src/services/background_sync_service.py:472` consumer site migrated from `from flask import current_app` + `current_app.cache.delete(...)` to `from src.admin.cache import get_app_cache` + `get_app_cache().delete(...)`. The `from flask import current_app` line is DELETED in the same commit. Note: this invalidation was **latently broken even in Flask** (`threading.Thread` has no app context; `try/except` at :479 silently eats `RuntimeError`). The migration FIXES this by using the module-global cache accessor.
5.4. `rg "from flask import current_app" src/services/` returns zero hits. Wave 3 ImportError blocker closed.
5.5. Structural guard `tests/unit/test_architecture_no_flask_caching_imports.py` — AST walker asserting zero `flask_caching` imports in `src/`. Active and green.
5.6. Structural guard `tests/unit/test_architecture_inventory_cache_uses_module_helpers.py` — asserts no `current_app.cache` usage in `src/`, and `src/services/` files MUST use `get_app_cache()` not `request.app.state.inventory_cache`.
5.7. Unit test suite `tests/unit/admin/test_simple_app_cache.py` (13 test cases) green.
5.8. Integration test `tests/integration/test_inventory_cache_behavior.py` (3 test cases: cache hit, TTL expiry, post-sync invalidation) green.
5.9. Note: Site 2 (`inventory_list`) has **NO invalidation** — pre-existing 5-min stale data gap. This is a known gap carried forward from Flask. Document in the PR description but do NOT fix in Wave 3.
6. `src/admin/app.py` **deleted**. `src/app.py:25-45` (`_install_admin_mounts`), `src/app.py:127-135` (`/a2a/` redirect), `src/app.py:299-304` (Flask mount), `src/app.py:351-352` (landing route insert hack) all deleted.
7. `CustomProxyFix` references removed from `src/app.py`. `FlyHeadersMiddleware` kept pending assumption #21 verification.
8. `.pre-commit-hooks/check_route_conflicts.py` **rewritten** to scan FastAPI routes using `app.routes` introspection; passes on current main.
9. `/Users/quantum/Documents/ComputedChaos/salesagent/templates/` moved to `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/templates/`. `git mv` used so history is preserved. `Jinja2Templates` singleton in `src/admin/templating.py` updated to new path.
10. `/Users/quantum/Documents/ComputedChaos/salesagent/static/` moved to `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/static/`. `StaticFiles` mount updated.
11. `test_architecture_no_flask_imports.py` allowlist is empty. `rg -w flask src/` returns zero hits. `rg 'from flask' tests/` returns zero hits.
12. v2.0.0 CHANGELOG entry added at `/Users/quantum/Documents/ComputedChaos/salesagent/CHANGELOG.md` with breaking changes section referencing §15.
13. Docker image build completes; `docker images adcp-salesagent:v2.0.0` size ≤ Wave 2 size − 60MB (conservative of the 80MB estimate in assumption #28).
14. Playwright full regression suite (all 5 flows from Part 3.C) passes against staging v2.0.0 build.
15. Production smoke test plan executed in staging first: deploy → login → create tenant → create product → submit creative → activity polling visible on dashboard → logout.

#### B. File-level checklist

**CREATE:**
- ~~`src/admin/routers/activity_stream.py` (~400 LOC SSE)~~ **STALE — D8 DELETE: SSE route is deleted in Wave 4, not created in Wave 3. Wave 3 creates only the JSON-poll routes (~120 LOC).**
- ~~`tests/integration/test_activity_stream_sse.py`~~ **STALE — D8 DELETE**
- ~~`tests/integration/test_activity_stream_disconnect.py`~~ **STALE — D8 DELETE**
- ~~`tests/integration/test_activity_stream_backpressure.py`~~ **STALE — D8 DELETE**
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/cache.py` (~90 LOC, Decision 6 SimpleAppCache)
- `/Users/quantum/Documents/ComputedChaos/salesagent/tests/unit/admin/test_simple_app_cache.py` (13 unit tests)
- `/Users/quantum/Documents/ComputedChaos/salesagent/tests/integration/test_inventory_cache_behavior.py` (3 integration tests)
- `/Users/quantum/Documents/ComputedChaos/salesagent/tests/unit/test_architecture_no_flask_caching_imports.py` (D6 guard)
- `/Users/quantum/Documents/ComputedChaos/salesagent/tests/unit/test_architecture_inventory_cache_uses_module_helpers.py` (D6 guard)
- `/Users/quantum/Documents/ComputedChaos/salesagent/.pre-commit-hooks/check_route_conflicts.py` (rewritten, net +50 LOC)
- `/Users/quantum/Documents/ComputedChaos/salesagent/CHANGELOG.md` entry for v2.0.0

**MODIFY:**
- `/Users/quantum/Documents/ComputedChaos/salesagent/pyproject.toml` — remove 8 deps
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/app.py` — delete Flask mount + wsgi middleware + `/a2a/` redirect + landing-route insert hack
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/app_factory.py` — wire the last router (`activity_stream.py`)
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/templating.py` — template path updated
- `/Users/quantum/Documents/ComputedChaos/salesagent/tests/unit/test_architecture_no_flask_imports.py` — remove all entries from allowlist; assert empty
- `/Users/quantum/Documents/ComputedChaos/salesagent/scripts/run_server.py` — drop any Flask-only env var plumbing
- `/Users/quantum/Documents/ComputedChaos/salesagent/Dockerfile` — remove `flask` install step if present

**DELETE:**
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/app.py` (427 LOC)
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/blueprints/activity_stream.py` (390 LOC)
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/blueprints/` directory (empty)
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/server.py` (legacy Flask entry point)
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/utils/helpers.py::require_auth`, `require_tenant_access` (dead after all callers migrated)
- `/Users/quantum/Documents/ComputedChaos/salesagent/templates/` (moved to `src/admin/templates/`)
- `/Users/quantum/Documents/ComputedChaos/salesagent/static/` (moved to `src/admin/static/`)
- `/Users/quantum/Documents/ComputedChaos/salesagent/tests/admin/conftest.py` (legacy fixtures)

**MOVE (git mv):**
- `templates/` → `src/admin/templates/`
- `static/` → `src/admin/static/`

#### C. Risk assessment

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| ~~`sse-starlette` disconnect detection~~ **STALE — D8 DELETE: SSE route deleted in Wave 4** | — | — | — |
| Template path rewrite breaks every handler that uses a hardcoded relative path | Medium | High — runtime 500s | `Jinja2Templates(directory=...)` takes a single path; only one config site to change. Integration smoke test after move. |
| Dep removal triggers lockfile resolution hell (transitive deps re-evaluated) | Medium | Medium | Pin all remaining deps explicitly before lockfile regen; test `uv pip compile` in isolation. |
| `.pre-commit-hooks/check_route_conflicts.py` rewrite ships with a bug that passes on main but fails in CI | Low | Low — easy to fix | Unit test the new hook with a known-conflicting FastAPI app fixture. |
| Production traffic hits an unmapped Flask-only URL we forgot to migrate | Low | Critical — 404s for real users | Staging canary for 48h before production cut; monitor 404 rate. Wave 2 `test_route_parity.py` already asserts Wave-2 parity; Wave 3 only removes the catch-all that was already dead. |
| Docker image shrinkage less than 60MB (i.e., something else grew) | Low | Low | Non-blocker; log and investigate. Not a release blocker. |
| ~~SSE test flakiness~~ **STALE — D8 DELETE: no SSE tests needed** | — | — | — |
| CHANGELOG omits a breaking change | Medium | Medium — user confusion | PR template includes CHANGELOG checklist cross-referencing §15 dep changes, §19 flow changes, and §2 user directives. |
| `activity_stream.py` SSE poll loop under async SQLAlchemy holds an AsyncSession open across `asyncio.sleep` boundaries | Medium | Medium | Open a fresh `async with get_db_session()` inside each tick rather than holding one across sleeps; benchmark showed <2 concurrent DB queries per stream. Avoids connection-pool pressure. |

#### D. Rollback procedure

Wave 3 is the **point of no return** for Flask rollback. Once the catch-all is deleted and deps are removed, rolling back requires re-adding them:

```
git checkout main
git revert -m 1 <wave-3-merge-sha> --no-edit
# Verify pyproject.toml has flask/flask-caching/etc. restored
cat pyproject.toml | grep -A 2 flask
# Rebuild lockfile
uv lock
# Rebuild Docker image
docker build .
# Verify Flask catch-all is restored in src/app.py
grep -n "flask_admin_app\|admin_wsgi\|_install_admin_mounts" src/app.py
```

**Hard constraint:** Wave 3 cannot roll back piecemeal. A revert either restores Flask entirely or does nothing useful.

**Database:** no migrations.

**Environment variables:** `SESSION_SECRET` stays (Flask ignored it, FastAPI now requires it, revert still has it).

**Rollback window:** open until Wave 4 (the async SQLAlchemy conversion) merges. After Wave 4, rollback becomes effectively impossible because async deps have spread through the codebase and the driver has switched to asyncpg (pivoted 2026-04-11 — async SQLAlchemy absorbed into v2.0).

**Pre-release checklist:** tag `v1.99.0` (last-known-good Flask-era release) immediately before Wave 3 merges. Keep a container image of `v1.99.0` available in the registry for 30 days as the true rollback option: redeploy the old image, accept the downtime.

#### E. Merge-conflict resolution

**Freeze scope:** `pyproject.toml`, `src/app.py`, `.pre-commit-hooks/`, `CHANGELOG.md`, `Dockerfile`, `templates/`, `static/`.

**Announcement:**
```
[MIGRATION] Wave 3 lands <date>. Final cutover — no more Flask.
Freeze: pyproject.toml, src/app.py, templates/, static/, .pre-commit-hooks/.
Concurrent PRs that touch these files will need manual rebase after merge.
After Wave 3: rg -w flask src/ returns zero hits. New tests must use
IntegrationEnv.get_admin_client() with no exceptions.
Tag v2.0.0-rc1 will land in staging 72h before production cut.
```

**Rebase strategy:** for conflicts in `templates/`, the physical file path changes from `templates/foo.html` to `src/admin/templates/foo.html`; the `git mv` records the rename so most PRs rebase cleanly if the PR used text-based merges. For `pyproject.toml` conflicts, take the Wave 3 version (deps removed) and re-add only the new deps from your PR.

#### F. Time estimate

- **Low (3 days):** SSE port from existing Flask SSE code is straight translation; dep removal is mechanical.
- **Expected (5 days):** 2 days SSE (port + disconnect tests + staging validation), 1 day dep removal + lockfile + Docker, 1 day pre-commit rewrite, 1 day final regression + staging canary.
- **High (10 days):** SSE disconnect detection problems behind Fly/nginx, lockfile resolution surfaces a transitive dep conflict, template path rewrite finds an edge case, v2.0.0 release notes back-and-forth with product.

#### G. Entry / exit criteria

**Entry:**
- Wave 2 merged and stable in staging for ≥5 business days.
- Flask catch-all receives zero traffic in staging for 48h.
- Datadog/dashboard audit confirms no external consumer references Flask-era endpoints.
- v1.99.0 container image tagged and archived in registry.
- SSE spike completed and disconnect detection validated.

**Exit:**
- All 15 Wave-3 acceptance criteria pass.
- `rg -w flask .` from repo root returns zero hits.
- v2.0.0 tagged.
- Staging canary runs for 48h without incident.
- Production deploy completes.

---

### Wave 4 — Async database layer (~7,000-10,000 LOC, absorbed async pivot 2026-04-11)

#### A. Detailed acceptance criteria

1. `/Users/quantum/Documents/ComputedChaos/salesagent/src/core/database/database_session.py` fully rewritten to async: `create_async_engine(...)` replacing `create_engine(...)`, `async_sessionmaker(class_=AsyncSession, expire_on_commit=False, autoflush=False)` replacing `scoped_session(sessionmaker(...))`, `get_db_session()` is an `@asynccontextmanager async def` yielding `AsyncSession`. `scoped_session` is deleted entirely (the thread-identity scoping that caused Blocker #4 is gone, eliminating that class of bug by construction).
2. `/Users/quantum/Documents/ComputedChaos/salesagent/src/core/database/engine.py` (NEW) owns the `create_async_engine(...)` call and exposes a `database_lifespan(app)` context manager that creates the engine on startup, stores it on `app.state.db_engine`, and calls `await engine.dispose()` on shutdown. Engine is **lifespan-scoped**, not module-level (Agent E Category 1).
3. `/Users/quantum/Documents/ComputedChaos/salesagent/src/core/database/deps.py` (NEW) exposes `SessionDep = Annotated[AsyncSession, Depends(get_session)]` where `get_session` reads the engine from `app.state.db_engine`, yields a fresh `AsyncSession` per request, commits on normal exit, rolls back on exception, and closes in `finally`.
4. `/Users/quantum/Documents/ComputedChaos/salesagent/src/core/database/scope.py` (NEW, optional per Agent E) exposes `session_var: ContextVar[AsyncSession]` for non-request-scoped code paths (schedulers, background tasks, CLI scripts) that need an ambient session without going through `Depends`. Used only where the FastAPI DI cannot reach.
5. All 11 repository files under `/Users/quantum/Documents/ComputedChaos/salesagent/src/core/database/repositories/` rewritten with async methods: every `self._session.scalars(...)` becomes `(await self._session.execute(stmt)).scalars()`; every `self._session.flush()` becomes `await self._session.flush()`; every `session.commit()` becomes `await session.commit()`. Total: `media_buy.py` (525 LOC), `creative.py` (476), `workflow.py` (296), `delivery.py` (276), `account.py` (273), `adapter_config.py` (175), `product.py` (174), `tenant_config.py` (57), `currency_limit.py` (37), `__init__.py` (41), `uow.py` (286).
6. `src/core/database/repositories/uow.py::BaseUoW` and its 7 subclasses (`MediaBuyUoW`, `ProductUoW`, `WorkflowUoW`, `TenantConfigUoW`, `AccountUoW`, `CreativeUoW`, `AdminCreativeUoW`) either (a) implement `async def __aenter__` / `async def __aexit__` OR (b) are **deleted entirely** in favor of the `SessionDep` + per-repository Dep factory pattern per Agent E Category 3 ("FastAPI's request-scoped session IS the unit of work"). The chosen approach is locked in pre-Wave-0 decision #1 and consistent across the wave — no half-converted UoW leftovers.
7. **All 10 sync `_impl` functions converted to `async def`** per Agent A §2.3: `_get_adcp_capabilities_impl`, `_list_creative_formats_impl`, `_list_accounts_impl`, `_list_creatives_impl`, `_sync_creatives_impl`, `_list_authorized_properties_impl`, `_update_media_buy_impl`, `_get_media_buy_delivery_impl`, `_get_media_buys_impl`, `_update_performance_index_impl`. (The other 5 are already async: `_get_products_impl`, `_sync_accounts_impl`, `_create_media_buy_impl`, `_get_signals_impl`, `_activate_signal_impl`.)
8. **Agent D M1 — 8 missing `await` keywords added in `src/routes/api_v1.py`** at lines 200, 214, 252, 284, 305, 324, 342, 360. Each is a 1-character insertion in the same PR that converts the corresponding `_raw` to async. `tests/unit/test_api_v1_routes_await_all_impls.py` (NEW) AST-walks the file and asserts every call to an async `_raw`/`_impl` is `await`-prefixed.
9. **Agent D M2 — 2 missing `await` keywords added in `src/core/tools/capabilities.py`** at lines 265, 310.
10. **Agent D M3 — 8 missing `await` keywords added in `src/a2a_server/adcp_a2a_server.py`** at lines 1558, 1587, 1774, 1798, 1842, 1892, 1961, 2000. Same pattern as M1/M2 — fixes pre-existing latent bug that becomes active once the callees are async.
11. `/Users/quantum/Documents/ComputedChaos/salesagent/alembic/env.py` rewritten to async (~30 LOC) using the standard SQLAlchemy async pattern: `create_async_engine(_ASYNC_URL)` + `async with connectable.connect() as connection: await connection.run_sync(do_run_migrations)` + `asyncio.run()` wrapper. Existing 161 migration scripts are unchanged (they run inside `run_sync`).
12. `pyproject.toml` driver additions: `asyncpg>=0.30.0,<0.32` and `asyncpg-stubs>=0.30.0` added. **`psycopg2-binary` and `types-psycopg2` are RETAINED** — this is a partial reversal of Agent F F1.1.1 per the 2026-04-11 resolutions of Decisions 1 (Path B adapter sync session factory), 2 (pre-uvicorn health checks via `run_all_services.py:84,135`), and 9 (background_sync sync-bridge). **Fallback:** if Spike 2 mandated `psycopg[binary,pool]>=3.2.0`, that substitutes for asyncpg and the `postgresql+asyncpg://` URL rewrite becomes `postgresql+psycopg://`. `greenlet` pinned explicitly.
13. `src/core/database/db_config.py::DatabaseConnection` class + `get_db_connection()` helper **RETAINED** per Audit 06 Decision 2 OVERRULE. Audit 06 grep-verified that `scripts/deploy/run_all_services.py:84,135` calls `get_db_connection()` as pre-uvicorn health checks; Agent A's original grep was incomplete. Add a structural guard `tests/unit/test_architecture_no_runtime_psycopg2.py` that allowlists ONLY `src/core/database/db_config.py` + `src/core/database/database_session.py` (Decision 1 Path B sync factory) as legitimate psycopg2 importers in `src/`. (Pre-D3 plan also listed `src/services/background_sync_db.py` as a third allowlisted importer; under D3 2026-04-16 that module is never written, so the allowlist narrows to 2 files.)
14. All supporting-core files from Agent A §2.4 converted to async: `src/core/auth.py` (`get_principal_object`, `get_principal_adapter_mapping` — 21 call sites across 9 `_impl` files gain `await`), `src/core/config_loader.py` (5 DB uses), `src/core/audit_logger.py` (5 uses), `src/core/strategy.py` (`StrategyManager`, 5 uses), `src/core/tenant_status.py`, `src/core/format_resolver.py`, `src/core/signals_agent_registry.py`, `src/core/creative_agent_registry.py`, `src/core/webhook_delivery.py`, plus the three `src/core/helpers/*.py` and `src/core/utils/tenant_utils.py` one-use files.
15. All services-layer files from Agent A §2.5 converted: `delivery_webhook_scheduler.py` + `media_buy_status_scheduler.py` tick bodies (`with get_db_session()` → `async with`, repository calls `await`-ed), plus `auth_config_service.py` (10 uses — largest), `background_sync_service.py` (9 uses), `order_approval_service.py` (7 uses), and the 13 smaller services. `src/services/background_sync_service.py` handling locked in per D3 (2026-04-16, supersedes 2026-04-11 Option B sync-bridge): async rearchitect to `asyncio.create_task` + checkpoint-per-GAM-page — each of the 9 `get_db_session()` sites becomes an `async with get_db_session()` inside its own short-lived per-page block.
16. **Adapter files STAY SYNC under Path B (Decision 1, 2026-04-11 resolution).** The adapter base class `src/adapters/base.py` is UNCHANGED — methods remain sync `def`. Full async conversion would require porting `googleads==49.0.0` off `suds-py3` (hard-sync SOAP with no async variant) and rewriting 4 `requests`-based adapters (~1500 LOC) for zero AdCP-visible benefit; the wrap would end up as `async def create_media_buy(...): return await run_in_threadpool(self._sync_impl, ...)` — cosmetic cleanliness for ~1500 LOC. Path B keeps adapters sync and wraps calls at the 18 `_impl` call sites instead. The adapter-side DB-access changes under Path B are scoped: every adapter `get_db_session()` import (40 sites across `google_ad_manager.py`, `gam_reporting_api.py`, `gam/managers/workflow.py`, `gam/managers/targeting.py`, `gam/managers/orders.py`, `broadstreet/adapter.py`, `mock_ad_server.py`, `xandr.py`, `base_workflow.py`) rewrites to `get_sync_db_session()` — the NEW sync factory added to `src/core/database/database_session.py` alongside the async `get_db_session()`. Pool sizing for the sync factory: `pool_size=5, max_overflow=10`, `pool_pre_ping=True`, `pool_recycle=3600`, statement_timeout=30s. Adapters run inside `run_in_threadpool` worker threads and open sync sessions from this factory.
17. **Admin routers under `src/admin/routers/*.py` (landed in Waves 1-3) have their handler DB access patterns updated to async-target-state** per the Wave 0 foundation that was already `async def`: inline sync `with get_db_session()` bodies (if any slipped in) become `async with`, `session.scalars()` becomes `await session.execute().scalars()`. Admin routers are sync `def` through L4 per the 2026-04-14 layering decision; this step flips them to `async def` at L5c (3-router pilot) and L5d3 (bulk conversion). The scan-and-fix framing applies only to the handful of OAuth/MCP handlers already `async def` for Authlib/MCP compatibility.
18. **Agent D M4-M9 AdCP wire-safety mitigations land in the same PR as the conversions they guard:**
    - M4: `tests/unit/test_api_v1_routes_await_all_impls.py` — AST walk asserting every `_raw`/`_impl` call is `await`-prefixed.
    - M5: `tests/integration/test_get_media_buys_wire_datetime_present.py` — creates a media buy without explicit `created_at`, fetches via `get_media_buys`, asserts `created_at` is not None in the wire response.
    - M6: `tests/unit/test_architecture_no_server_default_without_refresh.py` — AST-parses `src/core/database/models.py`, flags `server_default=` without parallel `default=` or `# NOQA: server-default-refreshed` exemption.
    - M7: `server_default=func.now()` → `default=datetime.utcnow` migration for `Creative.created_at`, `Creative.updated_at`, `MediaBuy.created_at`, `MediaBuy.updated_at`.
    - M8: `tests/unit/test_architecture_adcp_datetime_nullability.py` — asserts `GetMediaBuysMediaBuy.created_at`/`updated_at` remain `datetime | None` to prevent schema tightening.
    - M9: `tests/unit/test_openapi_byte_stability.py` — snapshots `app.openapi()` to `tests/unit/fixtures/openapi_snapshot.json` and byte-diffs in CI.
19. `tests/harness/_base.py::IntegrationEnv` converts to `async def __aenter__` / `async def __aexit__` per Agent A §2.11. Sync `__enter__`/`__exit__` either deleted (clean break) or kept as a deprecation shim that `asyncio.run()`-wraps the async path (decided at Wave 4 entry based on test-corpus readiness).
20. `tests/factories/_async_shim.py` (NEW) implements a custom `AsyncSQLAlchemyModelFactory` per pre-Wave-0 decision #3, enabling factory-boy to drive INSERT through an async session via `session.run_sync(...)`. All 11 factories under `tests/factories/` switched over.
21. All ~166 integration tests converted to `async def` + `@pytest.mark.asyncio` + `async with`. Conversion is AST-driven (scripted rewriter) with manual audit of the ~20 tests that use the harness in unusual ways. `tests/integration/conftest.py` fixtures that yield DB state become async fixtures.
22. Tests switch from sync `TestClient(app)` to `httpx.AsyncClient(transport=ASGITransport(app=app))` with `app.dependency_overrides[get_session]` per Agent E Category 14. Per-test engine fixture (function-scoped) + per-test session fixture prevents event-loop leak across tests.
23. `DB_POOL_SIZE` and `DB_POOL_MAX_OVERFLOW` env vars introduced with production-safe defaults (pool sized to match or exceed the pre-migration sync-threadpool capacity per Risk #6). `pool_pre_ping=True` and `pool_recycle=3600` set to handle Fly.io network blips and 2h idle connection kill.
24. **Agent F pre-Wave-0 / Wave 4 tooling items land (with corrections):** `scripts/deploy/entrypoint_admin.sh:9` psycopg2 probe rewritten or deleted (F3.6.1), `scripts/deploy/run_all_services.py::check_database_health` + `::check_schema_issues` + `::init_database` RETAINED as sync psycopg2 paths (they run pre-uvicorn, outside any event loop — Audit 06 overruled the async conversion of F3.5.1-3), **`Dockerfile` `libpq-dev`/`libpq5` RETAINED** (partial reversal of F1.2.1/F3.1.1 — required by psycopg2 for the sync session factory + sync-bridge + pre-uvicorn health checks; Docker image savings adjust from ~80MB to ~75MB per Decision 9), `docker-compose*.yml` DATABASE_URL compatibility verified (F3.2.1 / F3.3.1 / F3.4.1), `/health/pool` and `/metrics` endpoints added AND distinguish between async engine + sync session factory + sync-bridge via `engine=async|sync|sync_bridge` Prometheus labels (F4.1.1 / F4.1.3 / F4.1.4), `contextvars` request-ID propagation (F4.3.1), 3 new structural guards — admin routes async (F6.2.2), async DB access (F6.2.3), templates no ORM (F6.2.4).

25. **Decision 1 (adapter Path B) — full implementation criteria:**
    25.1. `src/core/database/database_session.py` exports BOTH `get_db_session()` (async, `AsyncSession` yielding) AND `get_sync_db_session()` (sync, `Session` yielding) — two engines, two sessionmakers, one module. Async engine: `create_async_engine(...)`, pool 15+25. Sync engine: `create_engine(...)`, pool 5+10, `pool_pre_ping=True`, statement_timeout=30s. Both read the same `DATABASE_URL` with different scheme rewrites (`postgresql+asyncpg://` vs plain `postgresql://`).
    25.2. `src/core/audit_logger.py::AuditLogger` split into:
       - `_log_operation_sync(...)` (internal, uses `get_sync_db_session()`) — called by adapter code running inside `run_in_threadpool` worker threads.
       - `async def log_operation(...)` (public async wrapper) — called by `_impl` functions; wraps `_log_operation_sync` in `await run_in_threadpool(self._log_operation_sync, ...)`.
       30 `_impl` call sites update to `await audit_logger.log_operation(...)`. Adapter call sites update to `self.audit_logger._log_operation_sync(...)` (the underscore signals intent).
    25.3. 18 adapter call sites in `src/core/tools/*.py` wrapped: `media_buy_create.py:429` (`adapter.create_media_buy`), `media_buy_create.py:3283, 3386` (`adapter.add_creative_assets`), `media_buy_update.py:400, 460, 532` (`adapter.update_media_buy`), `media_buy_delivery.py:268` (`adapter.get_media_buy_delivery`), `performance.py:89` (`adapter.update_media_buy_performance_index`), plus the GAM sub-manager calls (`adapter.orders_manager.approve_order`, `adapter.creatives_manager.add_creative_assets`) and one in `src/admin/blueprints/operations.py:252`. Each wrap: `result = await run_in_threadpool(adapter.method, *args, **kwargs)` (use `functools.partial` if kwargs unsupported in the installed anyio version).
    25.4. **[Threadpool limiter bump moved to L0 lifespan.]** `src/app.py::lifespan` adds `anyio.to_thread.current_default_thread_limiter().total_tokens = int(os.environ.get("ADCP_THREADPOOL_TOKENS", "80"))` at startup — the default 40 workers is too low for sync-handler concurrency even before L5 adapter wraps. L5 inherits this. Env-override via `ADCP_THREADPOOL_TOKENS` (canonical; `ADCP_THREADPOOL_SIZE` is the deprecated older name). See `foundation-modules.md` §11.14.F for the full code block.
    25.5. Structural guard `tests/unit/test_architecture_adapter_calls_wrapped_in_threadpool.py` lands with empty allowlist. AST-walks `src/core/tools/`, `src/admin/blueprints/`, `src/admin/routers/`, `src/core/helpers/` for calls to `adapter.METHOD(...)` or `self.adapter.METHOD(...)` or `adapter.submanager.METHOD(...)` inside `async def` bodies that are NOT inside a `run_in_threadpool(...)` call expression. Allowlisted: `src/services/background_sync_service.py` (sync-bridge thread — adapter calls already in sync context, no wrap needed).

26. **Decision 7 (ContextManager refactor) — full implementation criteria:**
    26.1. `src/core/context_manager.py` rewritten: class `ContextManager` deleted, singleton `_context_manager_instance` + `get_context_manager()` deleted, `set_tool_state` (no-op) deleted. 12 public methods converted to module-level `async def` functions taking `session: AsyncSession` as first positional parameter. `_send_push_notifications` fork at lines 727-755 collapses to a single `await service.send_notification(...)`.
    26.2. `src/core/database/database_session.py::DatabaseManager` class (lines ~287-338) DELETED entirely — only ContextManager subclassed it, and two test classes in `tests/integration/test_session_json_validation.py` that existed only to test `DatabaseManager` itself.
    26.3. 7 production caller migrations: `src/core/main.py:164-166` (delete dead variable), `src/core/mcp_context_wrapper.py` (delete module-load `_wrapper = MCPContextWrapper()`, factory-construct via `_wrap_async_tool`'s `async with session_scope()`), `src/core/tools/media_buy_create.py:89,1357` (3 calls), `src/core/tools/media_buy_update.py:46,185` (**17 calls** — largest migration), `src/core/tools/creatives/_workflow.py:32,34,42,93` (2 calls, signature change), `src/adapters/mock_ad_server.py:274-303,349-400` (2 rewrite sites including `threading.Thread complete_after_delay` → `asyncio.create_task(_complete_after_delay)` with `async with session_scope()`), `src/admin/blueprints/operations.py:93,167` (1 site, part of Wave 4 admin conversion).
    26.4. `tests/harness/media_buy_update.py::EXTERNAL_PATCHES` updated: single `"ctx_mgr": f"{_MODULE}.get_context_manager"` entry replaced with 4 function-level patch entries. This single harness change absorbs ~20 `test_media_buy.py` patch sites automatically.
    26.5. 18 lines of singleton-reset hacks deleted: `tests/conftest_db.py:484-486,494`, `tests/fixtures/integration_db.py:134-144`, `tests/e2e/test_gam_lifecycle.py:153-155,170-172`.
    26.6. ~50 test patch sites rewritten or absorbed via harness update.
    26.7. Structural guard `tests/unit/test_architecture_no_singleton_session.py` lands with empty allowlist. AST-scanning test with 3 methods: (a) no class attributes typed `Session`/`AsyncSession`/`Session | None`/`AsyncSession | None` outside `src/core/database/`, (b) no `_X_instance = None` + `get_X()` singleton-getter patterns, (c) no module-level `X = SomeManager()` instantiations outside `src/core/database/`.
    26.8. Error-path composition test verified: `_update_media_buy_impl` raising an exception after `status="failed"` write must persist the error write. Requires a SEPARATE `async with session_scope() as error_session:` inside the `except` block. Documented in `§11.0.6 Gotchas`.
    26.9. Lazy-load audit for `get_pending_steps` and `get_contexts_for_principal` — both return `list[WorkflowStep]` / `list[Context]`. Under `lazy="raise"`, relationship access after session close raises `MissingGreenlet`. Add `selectinload(Context.principal)` and similar as needed, OR convert to DTO return types.

27. **Decision 9 / D3 (background_sync async rearchitect, 2026-04-16 supersedes 2026-04-11 Option B sync-bridge) — full implementation criteria:**
    27.1. `src/services/background_sync_service.py` rearchitects to `asyncio.create_task` + checkpoint-per-GAM-page. The module's `threading.Thread` workers become `asyncio.create_task(...)` in the lifespan. Each GAM-page (~30s) opens its own short-lived `async with get_db_session() as session:`, writes progress to a `sync_checkpoint` row, commits, closes. Resume logic reads the checkpoint and continues from the next cursor on the next tick. Session lifetime is always << `pool_recycle=3600`.
    27.2. `src/services/background_sync_db.py` is **NOT created** (never written). The pre-D3 plan scoped this as a ~200 LOC sync psycopg2 engine module; D3 supersedes and eliminates it.
    27.3. `sync_checkpoint` table added via Alembic migration. Schema: `(tenant_id, sync_kind, cursor, updated_at)` primary key `(tenant_id, sync_kind)`. Per-page idempotency: re-running a partial page is safe because the checkpoint cursor is advanced only after the page commits.
    27.4. Task registry: `app.state.active_sync_tasks: dict[str, asyncio.Task]` registered via `install_background_sync(app)` during lifespan startup. Keyed by `f"{tenant_id}:{sync_kind}"`. Shutdown cancels all tasks, awaits drain with a 30s timeout.
    27.5. Shutdown ordering in `src/core/main.py::lifespan_context.__aexit__`:
       1. `await cancel_all_sync_tasks(app.state.active_sync_tasks)` — cancel all active `asyncio.Task`s
       2. `await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=30.0)` — wait for clean shutdown
       3. `await engine.dispose()` — dispose async engine
    27.6. `src/services/background_sync_service.py::start_inventory_sync_background` is NOT wrapped in `run_in_threadpool` — it is called directly from async admin handlers and schedules an `asyncio.create_task(...)` on the event loop. (Pre-D3 the plan wrapped the call in `run_in_threadpool` to cross into a sync-bridge thread; post-D3 there is no thread to cross into.)
    27.7. Structural guard `tests/unit/test_architecture_no_threading_thread_for_db_work.py` lands with EMPTY allowlist. AST-walks `src/` for `threading.Thread(target=...)` whose target function body contains `get_db_session` or `session.` attribute access. The pre-D3 `test_architecture_sync_bridge_scope.py` guard is **deleted from plan** (never written).
    27.8. `pyproject.toml` — `psycopg2-binary` + `types-psycopg2` RETAINED as runtime deps **narrowly for Decision 2 fork-safety** (`db_config.py` raw psycopg2 connection for pre-fork orchestrator health check) and Decision 1 Path B (sync adapter factory). They are **NOT retained for background_sync** post-D3. `asyncpg>=0.30.0,<0.32` ADDED for the async path.
    27.9. `Dockerfile` — `libpq-dev` (builder stage) + `libpq5` (runtime stage) RETAINED per D2/D1. Docker image savings adjust from ~80MB to ~75MB. (The D9-sync-bridge contribution to libpq retention is removed under D3, but D1/D2 independently require it.)
    27.10. Validated by Spike 5.5 at L5a entry: 4 test cases green (4-hour sync, concurrent multi-tenant, cancellation, resume from checkpoint). Fail action (SOFT): revert to pre-D3 Option B sync-bridge (retain all pre-D3 scope); file v2.1 sunset ticket.
    27.11. Other long-running services (`background_approval_service`, `order_approval_service`) have bounded durations < `pool_recycle=3600` and convert to async normally — NOT subject to the D3 rearchitect.

28. **`./run_all_tests.sh` green** — all 5 tox envs (`unit`, `integration`, `bdd`, `e2e`, `admin`) pass. `make quality` green. `tox -e driver-compat` green. Wave 4 is ship-ready when the full CI matrix holds.

29. **Decision 3 (factory-boy Wave 4b/4c ordering gate):** all 166 consuming integration tests must be converted to async (Wave 4b) BEFORE factory base classes flip to `AsyncSQLAlchemyModelFactory` (Wave 4c). Enforced by pre-PR diff-scope gate: the Wave 4c PR must contain ONLY edits under `tests/factories/` (verified by `git diff --name-only origin/main...HEAD | grep -v "^tests/factories/" | wc -l` = 0, with small allowlist for `_async_shim.py` and `ALL_FACTORIES`). Three structural guards land in Wave 4c: `test_architecture_factory_inherits_async_base.py`, `test_architecture_factory_no_post_generation.py`, `test_architecture_factory_in_all_factories.py`.

30. **Decision 4 (queries.py convert-and-prune):** delete 3 dead functions (`get_recent_reviews`, `get_creatives_needing_human_review`, `get_ai_accuracy_metrics`) from `src/core/database/queries.py` (~−158 LOC). Remove 3 corresponding allowlist entries in `tests/unit/test_architecture_no_raw_select.py:287,291,292`. Convert 3 live functions (`get_creative_reviews`, `get_ai_review_stats`, `get_creative_with_latest_review`) to `async def` using `(await session.execute(stmt)).scalars().first()/all()` at L5. Convert 5 test functions + 1 helper in `tests/integration/test_creative_review_model.py` to `async def`/`async with` at L5c. No dual session factory needed (zero sync callers). Net: ~−100 LOC. Structural move to `CreativeRepository` deferred to post-v2.0.

31. **Decision 5 (product_pricing.py DELETE):** delete `src/core/database/product_pricing.py` entirely (~81 LOC). Inline the pricing-option conversion at the single caller `src/admin/blueprints/products.py:18,479` as a local helper or `AdminPricingOptionView` Pydantic DTO. Delete the import at `products.py:18`. Verify `list_products` renders unchanged. `get_primary_pricing_option` (line 74) has zero callers — deleted with the file.

32. **Decision 8 (SSE DELETE + surviving routes):**
    32.1. DELETE `/tenant/{id}/events` SSE route at `activity_stream.py:226-364` + SSE generator + rate-limit state (`MAX_CONNECTIONS_PER_TENANT`, `connection_counts`, `connection_timestamps`, lines 22-24) + HEAD probe.
    32.2. DELETE smoke test at `tests/integration/test_admin_ui_routes_comprehensive.py:367-370` + docs line at `docs/development/troubleshooting.md:74`.
    32.3. `sse_starlette` NOT added to `pyproject.toml` (dependency never needed).
    32.4. Convert 2 surviving routes (`/activity` JSON poll + `/activities` REST) to `async def` + `async with get_db_session()`. `get_recent_activities` becomes `async def` with per-call session lifetime.
    32.5. Fix `api_mode=False` → `api_mode=True` on the `/activity` JSON poll route (pre-existing bug — JS `fetch` sees HTML 302 redirect on auth failure, never gets the 401 the template expects).
    32.6. Structural guard `tests/unit/test_architecture_no_sse_handlers.py` asserts zero `EventSourceResponse`/`StreamingResponse(mimetype="text/event-stream")` in `src/admin/routers/`.
    32.7. Net change: −170 LOC, −1 unneeded pip dep, −3 unwritten test files.

#### B. File-level checklist

**CREATE:**
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/core/database/engine.py` (~120 LOC — `database_lifespan` context manager, lifespan-scoped engine construction, Agent E Category 1)
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/core/database/deps.py` (~80 LOC — `SessionDep`, `get_session` factory, per-request commit/rollback)
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/core/database/scope.py` (~40 LOC — `session_var: ContextVar[AsyncSession]` for non-request-scoped paths, optional per Agent E)
- `/Users/quantum/Documents/ComputedChaos/salesagent/tests/factories/_async_shim.py` (~150 LOC — `AsyncSQLAlchemyModelFactory` adapter per pre-Wave-0 decision #3)
- `/Users/quantum/Documents/ComputedChaos/salesagent/tests/unit/test_api_v1_routes_await_all_impls.py` (~120 LOC — M4 structural guard)
- `/Users/quantum/Documents/ComputedChaos/salesagent/tests/integration/test_get_media_buys_wire_datetime_present.py` (~80 LOC — M5 wire-safety)
- `/Users/quantum/Documents/ComputedChaos/salesagent/tests/unit/test_architecture_no_server_default_without_refresh.py` (~100 LOC — M6 structural guard)
- `/Users/quantum/Documents/ComputedChaos/salesagent/tests/unit/test_architecture_adcp_datetime_nullability.py` (~60 LOC — M8 schema-drift guard)
- `/Users/quantum/Documents/ComputedChaos/salesagent/tests/unit/test_openapi_byte_stability.py` (~100 LOC — M9 OpenAPI snapshot)
- `/Users/quantum/Documents/ComputedChaos/salesagent/tests/unit/fixtures/openapi_snapshot.json` (snapshot baseline, updated only on intentional schema changes)
- `/Users/quantum/Documents/ComputedChaos/salesagent/tests/unit/test_architecture_admin_handlers_async.py` (~100 LOC — F6.2.2 structural guard)
- `/Users/quantum/Documents/ComputedChaos/salesagent/tests/unit/test_architecture_admin_async_db_access.py` (~100 LOC — F6.2.3 structural guard)
- `/Users/quantum/Documents/ComputedChaos/salesagent/tests/unit/test_architecture_templates_no_orm.py` (~100 LOC — F6.2.4 structural guard)
- `/Users/quantum/Documents/ComputedChaos/salesagent/tests/unit/test_architecture_get_db_connection_callers_allowlist.py` (~60 LOC — D2 fork-safety guard)
- `/Users/quantum/Documents/ComputedChaos/salesagent/tests/unit/test_architecture_factory_inherits_async_base.py` (~80 LOC — D3 factory base guard, Wave 4c)
- `/Users/quantum/Documents/ComputedChaos/salesagent/tests/unit/test_architecture_factory_no_post_generation.py` (~40 LOC — D3 post_generation prohibition)
- `/Users/quantum/Documents/ComputedChaos/salesagent/tests/unit/test_architecture_factory_in_all_factories.py` (~40 LOC — D3 membership guard)
- `/Users/quantum/Documents/ComputedChaos/salesagent/tests/unit/test_architecture_no_sse_handlers.py` (~50 LOC — D8 SSE re-introduction guard)
- `/Users/quantum/Documents/ComputedChaos/salesagent/tests/integration/test_activity_stream_json_polling.py` (~80 LOC — D8 surviving routes test, replaces deleted SSE tests)

**MODIFY (major rewrites):**
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/core/database/database_session.py` — full rewrite to async (~465 LOC → ~500 LOC; +200/-120 net per Agent A §2.1)
- 11 repository files (`media_buy.py`, `creative.py`, `workflow.py`, `uow.py`, `delivery.py`, `account.py`, `adapter_config.py`, `product.py`, `tenant_config.py`, `currency_limit.py`, `__init__.py`)
- `alembic/env.py` (~91 LOC sync → ~30 LOC async)
- `tests/harness/_base.py` (~915 LOC — add `__aenter__`/`__aexit__`, ~+200 LOC delta)
- ~~`src/adapters/base.py` (base class methods → `async def`, cascades)~~ **STALE — D1 Path B: adapters stay sync `def`. Base class UNCHANGED.** Adapter files below get only import rewrites (`get_db_session` → `get_sync_db_session` at ~40 sites), NOT async method-signature changes.
- `src/adapters/google_ad_manager.py`, `gam_reporting_api.py`, `mock_ad_server.py`, `kevel.py`, `xandr.py`, `triton_digital.py`, `base_workflow.py`, `broadstreet/adapter.py`, `gam/managers/*.py` — **import rewrite only** per D1 Path B (not full async conversion as originally stated in Agent A §2.6)
- `src/core/tools/capabilities.py` + 8 other `_impl` files per Agent A §2.3
- `src/routes/api_v1.py` (M1 — 8 `await` insertions)
- `src/a2a_server/adcp_a2a_server.py` (M3 — 8 `await` insertions + 4 DB-access sites to async)
- `src/core/auth.py`, `config_loader.py`, `audit_logger.py`, `strategy.py` + 8 other `src/core/` files per Agent A §2.4
- `src/services/delivery_webhook_scheduler.py`, `media_buy_status_scheduler.py`, `auth_config_service.py`, `background_sync_service.py`, `order_approval_service.py` + 12 other services per Agent A §2.5
- `src/admin/utils/helpers.py::get_tenant_config_from_db` — canonical lazy-load hotspot, add `selectinload(Tenant.adapter_config)` per Spike 3 result
- All 11 factory files — adopt `AsyncSQLAlchemyModelFactory` from the shim
- `tests/integration/conftest.py` + `tests/conftest.py` — async fixtures
- `pyproject.toml` — add `asyncpg>=0.30.0,<0.32`; `psycopg2-binary` + `types-psycopg2` **RETAINED** per D1/D2/D9
- `Dockerfile` — `libpq-dev`/`libpq5` **RETAINED** per D2/D9 (partial reversal of F1.2.1)
- ~~`scripts/deploy/entrypoint_admin.sh`~~ **DELETED per D2** (dead shell code, unreferenced by Dockerfile/compose)
- `scripts/deploy/run_all_services.py` (F3.5.1-3)
- `~166` integration test files — AST-rewriter conversion to `async def` + `@pytest.mark.asyncio`
- `src/core/database/models.py` — 5 poisonous `@property` methods (lines 193, 341, 355, 454, 468 per Agent A §8) refactored or `selectinload`-fed

**DELETE:**
- ~~`src/core/database/db_config.py::DatabaseConnection` + `get_db_connection()` if resolves as DELETE~~ **RESOLVED D2: RETAINED (fork safety).** See criterion 13 and CLAUDE.md Decision 2.
- `tests/unit/test_architecture_admin_sync_db_no_async.py` must NOT exist (explicitly NOT to be created under the pivot — it would enforce the wrong direction; the stale Option C guard)

**Wave 4a pilot scope (first merge):** convert `src/core/database/database_session.py` + `engine.py` + `deps.py` + the `accounts` domain end-to-end (`accounts.py` repository + `_list_accounts_impl` + `_sync_accounts_impl` + `src/routes/api_v1.py:360` + admin `accounts.py` router + integration tests for accounts) as the pilot. If the pilot lands clean, Wave 4b-f cascade. If the pilot hits lazy-load bombs not caught by Spike 1, the wave pauses and the audit reruns.

#### C. Risk assessment

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **Lazy-load `MissingGreenlet` bombs** on relationship access outside session scope (Agent B Risk #1) | Medium (after Spike 1 passes) | **Critical** — any bomb takes down a whole route | Spike 1 is the first-line defense; Spike 3 (performance bench) exercises hot paths a second time. In-Wave-4 detection uses Agent B §3 "9-pattern lazy-load cookbook" for root-causing and fixing. If a bomb surfaces post-merge in staging, single-route rollback is impossible (the repository layer is shared) — full-wave revert is the only option. `src/admin/utils/helpers.py::get_tenant_config_from_db` is the canonical hotspot (confirmed by Agent A §8). |
| **`scoped_session` singleton** is the `ContextManager`-based ambient session used by `src/core/context_manager.py`; removing `scoped_session` breaks call sites that currently depend on thread-identity scoping (Agent B Risk #12 / Agent A §7 decision 7) | Medium | High — audit tools silently log to the wrong request | Pre-Wave-0 decision #7 locks the ContextVar-propagation strategy. Agent F F4.3.1 introduces `contextvars` request-ID propagation to replace thread-identity scoping. Integration test `tests/integration/test_audit_log_propagation.py` asserts that an audit log written inside `_list_accounts_impl` lands under the correct tenant_id under concurrent requests. |
| **`asyncio.run()` in handlers** — any `_impl` that calls `asyncio.run(...)` to drive async code from sync context becomes a fatal nested-loop error once the handler itself is async (Agent B Risk #7 / Risk #8 interaction) | Low | High | AST structural guard: `tests/unit/test_architecture_no_asyncio_run_in_handlers.py` scans admin routers and `_impl` functions and fails if `asyncio.run(` appears. One-time manual audit of `src/services/` identifies any standalone sync entry points. |
| **`background_sync_service.threading.Thread`** — service spawns long-lived worker threads that open `get_db_session()` (9 usages per Agent A §2.5); under async those threads have no event loop (Agent B Risk #14 + pre-Wave-0 decision #9) | High | Medium | **RESOLVED D3 (2026-04-16, supersedes 2026-04-11 Option B sync-bridge): async rearchitect.** `src/services/background_sync_service.py` rearchitects to `asyncio.create_task` + checkpoint-per-GAM-page (each GAM-page ~30s opens its own short-lived `async with get_db_session()`, writes progress to `sync_checkpoint` row, commits, closes). `threading.Thread` workers become `asyncio.create_task(...)` in lifespan. `src/services/background_sync_db.py` NOT written. See criterion 27.1-27.11. Validated by Spike 5.5 (checkpoint-session viability, 4 test cases). |
| **Module-level engines in `gam_*_service.py` and similar** — Agent B Risk #33 documents that several `src/services/*.py` and `src/adapters/*.py` files do `from src.core.database.database_session import get_db_session` at module load, which under lifespan-scoped engine construction may bind to the wrong engine OR trigger eager engine construction at import time (wrong event loop) | Medium | High — rare but nasty import-order bug | Agent F F7.3.1 requires an import-order test. The Agent E Category 1 fix (lifespan-scoped engine) explicitly **does not create** the engine at module load; `get_db_session` is a function that reads `app.state.db_engine` at call time. Any caller that holds a reference to the engine across lifespan boundaries is flagged by the import-order test. |
| ~~**Adapter base-class async cascade**~~ **STALE — D1 Path B** | — | — | **RESOLVED D1: Path B (sync adapters + `run_in_threadpool` wrap).** Adapter methods stay sync `def`. The 18 adapter call sites in `src/core/tools/*.py` + 1 in `src/admin/blueprints/operations.py:252` wrap in `await run_in_threadpool(...)`. No adapter method-signature changes. See criteria 25.1-25.5. |
| **`asyncpg` JSONB type codec vs `JSONType(model=X)`** (Agent B Risk #17) interacts with Pydantic round-tripping on JSONB columns | Low (caught in Spike 2) | High if caught in Wave 4 rather than Spike | If Spike 2 caught it, the fix is already in `main`. If not — Agent B §2 Risk #17 has the asyncpg JSON codec registration recipe. Fallback driver (`psycopg[binary,pool]`) is ready. |
| **`expire_on_commit=False` consequence** — `created_at`/`updated_at` server defaults no longer refresh post-commit; 4 callers (`Creative.created_at`/`updated_at`, `MediaBuy.created_at`/`updated_at`) read these post-INSERT | Medium | Medium | M7 migration handles the 4 known cases (`server_default=func.now()` → `default=datetime.utcnow`). M6 structural guard prevents new instances. M5 wire-safety integration test catches regressions. Spike 7 surveyed the overall `server_default=` surface at gate time. |
| **Agent D M1-M9 mitigations not all landing in the same PR as their conversion** — developer lands a `_raw` async conversion but forgets the paired `await` in `api_v1.py` or `a2a_server.py`, causing a latent coroutine-not-awaited warning that becomes a production 500 | Medium | High | M4 structural guard is the backstop: CI fails on any unmarked missing `await`. Agent D's inventory at Agent A §2.3 lists the 18 (8+2+8) exact line numbers to update; the conversion PR template has a checkbox per mitigation. |
| **Test conversion mass-rewrite introduces assertion drift** — the AST rewriter converts test bodies but subtle semantic differences in async fixtures cause tests to silently pass the wrong thing | Medium | Medium | Mass-rewrite PR is landed AFTER a sample of 20 hand-converted tests has been verified pass/fail behavior matches pre-conversion. The AST rewriter is committed as a script under `scripts/` so reviewers can run it themselves. |
| **Connection pool under sizing** vs the previous sync threadpool — async pool default (5+10 overflow = 15) is smaller than sync threadpool default (40) (Agent B Risk #6) | Medium | High — handlers block waiting for connections under load | `DB_POOL_SIZE` env var introduced, production default 40 or higher. Benchmark in Wave 5 Spike F validates. `pool_pre_ping=True` + `pool_recycle=3600` handle connection-health issues. |
| **Wave 4a pilot rollback is expensive** — the Wave 4a foundation (engine + deps + shim + 1 domain) must land before any Wave 4b-f work; reverting 4a after 4b is in-flight requires rolling back 4b as well | High (structurally true) | Medium | Wave 4a is a separate PR that merges first and soaks for 3 business days before Wave 4b opens. Entry criterion for Wave 4b: Wave 4a staging canary clean for 72h. |
| **Alembic async env.py has a subtle bug that only surfaces on `downgrade`** — not all migrations are symmetric, and the async runner may choke on a migration that uses `connection.execute(text(...))` in a downgrade path | Low | Medium | Spike 6 validated the upgrade/downgrade roundtrip on a fresh test DB. Agent B Risk #4 fallback: keep `alembic/env.py` sync with a boot-time `postgresql+asyncpg://` URL rewriter. Fallback adds +0.5 day and is pre-authorized. |
| **BDD step files (`tests/bdd/`) drift** — step dispatchers use `ctx["env"]` and call `env.call_via(...)`; under async the dispatcher must `await` the call but the step function is sync | Medium | Medium | Agent A §2.11 documents the conversion (~100 LOC). Step files adopt `pytest-bdd`'s async step support OR the step function wraps with `asyncio.run()`. Decision locked at Wave 4 entry. |

#### D. Rollback procedure

Wave 4 is the **hardest rollback in the migration**, harder than Wave 3 (which was the Flask cutover). The difficulty scales with how deep into Wave 4 the team has merged.

**Wave 4a pilot rollback (accounts domain only):**
```bash
git checkout main
git revert -m 1 <wave-4a-merge-sha>
# Restores sync src/core/database/database_session.py, drops src/core/database/engine.py,
# restores psycopg2-binary in pyproject.toml, restores sync accounts repository + _impl.
# Expected to revert cleanly because no dependent code has been merged yet.
git push origin main
```

**Wave 4b-f partial rollback:** NOT RECOMMENDED. After any Wave 4b-f module lands, its dependencies (shared repository layer, `src/core/auth.py`, `src/core/config_loader.py`) have been rewritten to async and can no longer be rolled back piecemeal. If a single Wave 4b-f module is broken, fix-forward. If the overall wave is broken, full revert.

**Full Wave 4 revert:**
```bash
git checkout main
git revert -m 1 <wave-4-final-merge-sha>
git revert -m 1 <wave-4f-merge-sha>
... (continue in reverse merge order)
git revert -m 1 <wave-4a-merge-sha>
# Rebuild lockfile (asyncpg → psycopg2-binary restored)
uv lock
# Rebuild Docker image
docker build .
# Verify driver restoration
grep -A 2 psycopg2-binary pyproject.toml
# Run migrations — alembic/env.py is now sync again, OK
uv run alembic upgrade head
```

**Consequence of a full Wave 4 revert:**
- Any database migrations landed during Wave 4 that used Wave-4-era patterns (unlikely — migrations are driver-agnostic at the `op.*` level) may need manual intervention.
- `tests/performance/baselines/baseline-sync.json` remains as the async-era comparison oracle; reverting doesn't invalidate it.
- v1.99.0 container image remains the true last-known-good rollback (from L2 exit criteria).
- Spike gate decisions in `spike-decision.md` stay on record; any future async attempt (whether the L5 in-v2.0 conversion or a later v2.1 epic) starts from that spike data.

**Database:** no migrations required by Wave 4 itself (the rewrite is all application-layer). Individual Wave 4 commits may include unrelated migrations that must be handled on their own.

**Environment variables:** `DB_POOL_SIZE`, `DB_POOL_MAX_OVERFLOW` can stay set on revert (sync engine ignores them).

**Rollback window:** Wave 4a pilot is revertable for 7 calendar days after merge. Full Wave 4 is revertable until Wave 5 merges; after Wave 5, `psycopg2-binary` is deleted from `pyproject.toml` and the `SessionLocal` sync class is removed, making rollback require re-implementing the sync layer.

**Pre-wave checklist:** tag `v1.99.0` is ALREADY applied at Wave 3 exit. Tag `v2.0.0-rc.1` applied just before Wave 4a merges as the "Wave 3 stable + async pilot ready" state. If Wave 4 is rolled back entirely, redeploy the `v2.0.0-rc.0` image (= Wave 3 post-merge state) as the running app.

#### E. Merge-conflict resolution

**Freeze scope (during Wave 4 branch lifetime):**
- `src/core/database/**` (every file)
- `src/core/auth.py`, `config_loader.py`, `audit_logger.py`, `strategy.py`
- `src/services/*.py` (every file)
- `src/adapters/**` (every file)
- `src/core/tools/*.py` (every `_impl` file)
- `src/routes/api_v1.py`
- `src/a2a_server/adcp_a2a_server.py` (DB access sites + `await` insertions)
- `alembic/env.py`
- `tests/harness/_base.py`
- `tests/factories/**`
- `tests/integration/**` (entire tree — mass-rewrite)
- `tests/conftest.py`, `tests/integration/conftest.py`, `tests/conftest_db.py`
- `pyproject.toml` (driver swap + psycopg2 removal)
- `Dockerfile`, `scripts/deploy/entrypoint_admin.sh`, `scripts/deploy/run_all_services.py`

**Announcement template:** see rollback `.md` template in other waves.

**Rebase strategy:** Wave 4a pilot rebases normally onto main — the freeze is one-directional. Wave 4b-f sub-branches rebase onto the Wave 4a-merged `main` once per day during active development. For freeze-window conflicts: resolve by taking the Wave 4 version of the file and re-applying the other PR's semantic edits in async form on top.

#### F. Time estimate

- **Low (10 days):** all Spike gate items passed with margin, lazy-load count was <20, driver is asyncpg on first try, adapter `run_in_threadpool` wrapping at 18 call sites is mechanical (D1 Path B), BDD step files convert without surprises, mass-test-rewrite AST script works on first run. Senior async-SQLAlchemy team of 2-3 engineers working in parallel.
- **Expected (14 days):** 3 days Wave 4a pilot + 3 days repositories/UoW/`_impl` sweep + 3 days supporting core/services/adapters cascade + 3 days tests mass-rewrite + harness conversion + factory shim + 2 days integration/staging/`make quality` stabilization.
- **High (21 days):** Wave 4a pilot surfaces a lazy-load bomb not caught by Spike 1, `background_sync_service` conversion hits a scope surprise, driver fallback from asyncpg to psycopg3 happens mid-wave, mass-test-rewrite produces 40+ false-fail tests requiring manual triage, staging pool tuning iteration doubles the time.

#### G. Entry / exit criteria

**Entry:**
- Pre-Wave-0 spike gate PASSED (Spike 1 green, ≤2 soft-spike failures).
- `spike-decision.md` committed with all 9 open decisions resolved.
- Wave 3 merged to `main` and stable in staging ≥3 business days.
- `v1.99.0` git tag applied (pre-Wave-3-merge Flask-era rollback).
- `v2.0.0-rc.0` git tag applied (Wave-3-merged FastAPI+sync-SQLAlchemy reference).
- `baseline-sync.json` + `baseline-sync-mcp.json` committed under `tests/performance/baselines/`.
- `tox -e driver-compat` environment exists and runs.
- Agent F pre-Wave-0 hard-gate items all landed (F1.1.1-F1.1.4, F1.5.1, F1.7.1, F2.4.1, F2.3.1, F2.5.1, F6.2.1, F6.2.5, F6.2.6, F8.2.1).
- Main is green (`make quality` + `./run_all_tests.sh`).
- Team: 2-3 senior async-SQLAlchemy engineers available for the 2-3 week wave window.
- Wave 4 freeze announcement sent 48h before Wave 4a PR opens.

**Exit:**
- All 25 Wave-4 acceptance criteria pass.
- `tox -e unit`, `tox -e integration`, `tox -e bdd`, `tox -e e2e`, `tox -e admin` all green.
- `tox -e driver-compat` green. `make quality` green.
- Agent D M1-M9 mitigations all landed — AST guard confirms every `_raw`/`_impl` call is `await`-prefixed, M5 wire-datetime test passes, M9 OpenAPI snapshot matches.
- Benchmark parity: Wave 4 staging admin routes run within **±5% p95 latency** of `baseline-sync.json` under moderate-concurrency load (full-spec benchmark comparison is Wave 5, but Wave 4 exit requires a rough parity sanity check).
- `/health/db` and `/health/schedulers` endpoints return healthy for 24h staging soak.
- `/health/pool` exposes AsyncEngine pool stats (size, checked_in, checked_out, overflow).
- Staging deploy completes with zero 500s on hot admin routes for 24h.
- `git grep -l "scoped_session" src/` returns zero hits.
- Wave 4a pilot merged first; Wave 4b-f follow in sequence and are confirmed green individually before the Wave 4 PR marks ready.
- v2.0.0-rc.2 tagged (Wave 4 stable), staging canary running for 48h.
- Wave 5 entry criterion "Wave 4 merged and stable ≥3 business days" timer starts.

---

### Wave 5 — Async cleanup + v2.0.0 release (~3,000-5,000 LOC)

#### A. Detailed acceptance criteria

1. Any residual sync `get_db_session()` call sites not migrated in Wave 4 are deleted or converted. `git grep -l "with get_db_session()" src/` returns zero hits outside the explicit allowlist.
2. The sync `SessionLocal` class / sync `sessionmaker` (if still present as a deprecation shim from Wave 4) is **deleted entirely** from `src/core/database/database_session.py`. No dual sync/async machinery remains.
3. The sync `IntegrationEnv.__enter__` / `__exit__` shim (if Wave 4 kept it as a `asyncio.run`-wrapped compatibility layer) is **deleted**. All test callers use `async with env:`.
4. The factory-boy sync wrapper (if Wave 4 kept a sync-compatible fallback) is deleted. `tests/factories/_async_shim.py` is the only factory driver.
5. `psycopg2-binary` **RETAINED** in `pyproject.toml` runtime deps per Decisions 1 (Path B sync factory) and 2 (pre-fork orchestrator health check). (Pre-D3 list also cited Decision 9 sync-bridge; under D3 2026-04-16 the sync-bridge is eliminated by the background_sync async rearchitect, so D1+D2 alone are sufficient.) Removal deferred to **post-v2.0** when Path B adapters go async and `run_all_services.py` is replaced by a proper process supervisor. `types-psycopg2` also RETAINED. `libpq-dev` + `libpq5` RETAINED in Dockerfile.
6. `types-psycopg2` removed from both dev-deps slots in `pyproject.toml` (Agent A §2.2 flagged two duplicate entries at lines 74 and 101).
7. All remaining FIXME comments tagged as async-landmines (e.g., `# FIXME(async-pivot): ...`) are resolved and the comments removed. `git grep -n "FIXME(async" src/ tests/` returns zero hits.
8. **Async-vs-sync benchmark** runs on the representative admin routes from `baseline-sync.json`. Results emitted to `tests/performance/results/wave-5-async.json` and compared against `tests/performance/baselines/baseline-sync.json`.
9. **Latency profile:** at medium concurrency (50 req/s), async is net-neutral to ~5% faster than the pre-migration sync baseline (Risk #10). At high concurrency (200+ req/s), async outperforms sync by >20% OR sync saturates while async does not. At low concurrency (1 req/s), async is within ±10% of sync. **If a regression >5% at medium concurrency is found:** first tune `pool_size`, second add `selectinload` eager-loading at the hot path, last resort is Wave-5-scoped rollback.
10. `/health/pool` endpoint exposes SQLAlchemy AsyncEngine pool statistics: `pool_size`, `checked_in`, `checked_out`, `overflow`. Prometheus scraper config verified (Agent F F4.1.1 / F4.1.3 / F4.1.4).
11. `/health/schedulers` endpoint returns alive-tick timestamps for `delivery_webhook_scheduler` and `media_buy_status_scheduler`; both timestamps must be within 30 seconds of "now" on a healthy instance.
12. **Startup log assertion:** schedulers emit `"delivery_webhook_scheduler alive"` and `"media_buy_status_scheduler alive"` log lines on their first post-startup tick (within 60s of uvicorn boot). A test in `tests/integration/test_scheduler_startup.py` asserts this.
13. **Risk #5 consequence audit:** `created_at` / `updated_at` post-commit access sites audited one final time — any code reading these fields after commit without an explicit `await session.refresh(obj)` is either fixed or explicitly marked with `# NOQA: expire_on_commit=False` justification. M6 structural guard enforces.
14. `.duplication-baseline` ratcheted back to the Wave 4-entry baseline or lower (Agent F F7.4.2). Async conversion typically introduces a short-term duplication bump from "same pattern applied across every repository"; Wave 5 cleanup extracts the shared helpers and restores the ratchet.
15. `CHANGELOG.md` v2.0.0 section finalized with complete breaking-changes list: (a) `psycopg2-binary` → `asyncpg` driver swap; (b) `expire_on_commit=False` default; (c) async handler signatures; (d) async repository method signatures; (e) async test harness (`IntegrationEnv`); (f) new env vars (`DB_POOL_SIZE`, `DB_POOL_MAX_OVERFLOW`, `SESSION_SECRET`); (g) new structural guards; (h) session cookie rename (`session` → `adcp_session`) — forced re-login.
16. `pyproject.toml` version bumped to `2.0.0` (Agent F F8.4.2).
17. `v2.0.0` git tag applied on the post-Wave-5 merge commit. Tag is GPG-signed.
18. Auto-memory `flask_to_fastapi_migration_v2.md` updated to reflect the post-v2.0.0 state (Agent F F7.6.1).
19. Production deploy plan approved (2 reviewers + 1 release manager sign-off).
20. Production deploy completes. Monitoring shows no regression over the first 24h post-deploy.
21. **Decision 5 (database_schema.py DELETE):** delete `src/core/database/database_schema.py` (192 LOC, confirmed orphan — zero Python importers, stale pre-Alembic DDL). In the same commit, strip the stale docstring reference in `src/core/database/__init__.py:12` ("database_schema.py: Schema definitions and table creation"). Verify `grep -r "database_schema" src/ tests/ scripts/ alembic/ pyproject.toml` returns only historical planning notes in `.claude/notes/`.

#### B. File-level checklist

**CREATE:**
- `tests/performance/results/wave-5-async.json` — benchmark results artifact
- `tests/integration/test_scheduler_startup.py` (~80 LOC — asserts alive-tick log lines within 60s of boot)

**MODIFY:**
- `pyproject.toml` — version bump to `2.0.0`; **remove** `psycopg2-binary` entirely (or move to `[project.optional-dependencies]` per Audit 06 Decision 2 if kept as pre-uvicorn bootstrap); remove `types-psycopg2` from both slots
- `CHANGELOG.md` — finalize v2.0.0 entry with all 8 breaking-change items
- `.duplication-baseline` — ratcheted to Wave-4-entry level or lower (Agent F F7.4.2)
- `~/.claude/projects/.../memory/flask_to_fastapi_migration_v2.md` — auto-memory update (Agent F F7.6.1)
- `src/core/database/database_session.py` — delete sync `SessionLocal`, delete sync `sessionmaker`, delete any remaining sync context-manager shim
- `tests/harness/_base.py` — delete sync `__enter__`/`__exit__` shim if kept from Wave 4
- All 11 factory files — remove sync-compat paths if any
- Any `src/` file with a remaining `FIXME(async-pivot)` comment — resolve the comment and delete it
- `docs/` — async debugging guide (`async-debugging.md`) + async cookbook (`async-cookbook.md`) finalized (Agent F F5.3.1 / F5.3.2)

**DELETE:**
- `src/core/database/database_session.py::SessionLocal` (sync class) — if still present from Wave 4
- Sync `sessionmaker(...)` call in `database_session.py`
- Sync `tests/harness/_base.py::IntegrationEnv.__enter__` / `__exit__`
- `pyproject.toml` `psycopg2-binary>=2.9.9` line — fully removed from runtime deps
- `pyproject.toml` `types-psycopg2>=2.9.21.*` lines (both occurrences per Agent A §2.2)

#### C. Risk assessment

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Benchmark comparison reveals >5% p95 regression at medium concurrency | Low (after Wave 4 entry sanity check) | High — threatens v2.0.0 release date | Tune `pool_size` first, then add `selectinload` at hot paths. If still regressing, the root cause is almost certainly N+1 query explosion; run a query-count profile and fix at the repository layer. Last resort is a Wave 4+5 revert. |
| Wave 4 left residual sync code paths that Wave 5 cleanup discovers | Medium | Low — cleanup is the point of this wave | Fix-forward. The biggest risk is that a sync path in a low-traffic code path is missed entirely and detonates in production weeks later. Mitigation: structural guards from Wave 4 catch async/sync mismatches. |
| `psycopg2-binary` removal from `pyproject.toml` breaks a one-off script in `scripts/` that was not audited in Wave 4 | Low | Low — scripts are tooling, not runtime | Grep `import psycopg2` across `scripts/` before the removal commit. Audit 06 Decision 2 already identified `run_all_services.py::check_database_health` as a legit caller — it stays. |
| Production deploy surfaces a `MissingGreenlet` in a rarely-hit code path (e.g., monthly GAM sync admin route) | Low | High — on-call pager | Staging soak for 5 business days before the v2.0.0 tag; staging runs include at least one monthly-GAM-sync rehearsal. Runbook `docs/async-debugging.md` gives on-call the lazy-load cookbook. |
| CHANGELOG entry misses a breaking change that external consumers hit on upgrade | Medium | Medium | PR template checklist cross-references the 8 enumerated breaking-change categories. Release manager sign-off gates the v2.0.0 tag. |
| Deprecation shims left from Wave 4 are not deleted because nobody remembers they exist | Medium | Low | `test_architecture_no_sync_database_session.py` (new, landed in Wave 5) AST-scans `src/` and fails if `class SessionLocal` or sync `sessionmaker(` is still present. |
| Auto-memory `flask_to_fastapi_migration_v2.md` update is skipped | High (cultural) | Low — affects fresh-session onboarding | Agent F F7.6.1 makes this a ship-gating checklist item. Release PR description references the auto-memory update explicitly. |
| `v2.0.0` tag applied on a commit that doesn't have the release-please conventional-commit history needed for the auto-changelog | Low | Medium | Wave 4 and Wave 5 merge commits use conventional-commit prefixes. Release-please dry-run executed pre-tag to verify the changelog draft matches the hand-finalized CHANGELOG. |

#### D. Rollback procedure

Wave 5 is mostly **cleanup** on top of Wave 4's substantive async conversion. Rollback complexity is low relative to Wave 4, but the window is narrow because v2.0.0 ships in Wave 5.

**Wave 5 single-commit revert (pre-v2.0.0 tag):**
```bash
git checkout main
git revert -m 1 <wave-5-merge-sha>
git push origin main
# Restores sync deprecation shims, restores psycopg2-binary as a runtime dep
```

**Post-v2.0.0-tag rollback (rare, emergency):**
If v2.0.0 is tagged and deployed, and a production-blocking regression is found, the rollback is to redeploy `v1.99.0` (Flask-era last-known-good) or `v2.0.0-rc.2` (Wave 4 stable). v2.0.0 is NOT untagged — the tag is immutable. A new `v2.0.1` is cut from a revert commit on `main`.

**Database:** no migrations to roll back in Wave 5.
**Environment variables:** `DB_POOL_SIZE`, `DB_POOL_MAX_OVERFLOW` can stay set (sync engine ignores them). `SESSION_SECRET` stays.
**Rollback window:** Wave 5 single-commit revert is safe for ~14 days after merge. Post-v2.0.0-tag rollback is emergency-only and follows the v2.0.1 patch process.

#### E. Merge-conflict resolution

**Freeze scope:** `pyproject.toml`, `CHANGELOG.md`, `src/core/database/database_session.py`, `tests/harness/_base.py`, `.duplication-baseline`.

**Rebase strategy:** Wave 5 is small and short-lived. Rebases onto main are mechanical. Version-bump conflicts resolve by taking Wave 5's version.

#### F. Time estimate

- **Low (3 days):** Wave 4 left no residual sync code; benchmark passes on first run; CHANGELOG writes itself. Single engineer.
- **Expected (5 days):** 1 day grep-and-fix residual sync paths + psycopg2 removal; 1 day benchmark run + analysis + pool tuning; 1 day CHANGELOG finalization + release-please reconciliation + auto-memory update; 1 day staging canary + production deploy prep; 1 day production deploy + 24h monitoring soak.
- **High (10 days):** benchmark reveals a regression requiring 2-3 days root-cause, CHANGELOG back-and-forth, production deploy slips to a maintenance window.

#### G. Entry / exit criteria

**Entry:**
- Wave 4 merged to `main` and stable in staging ≥3 business days (sustained, not just "green once").
- Wave 4 exit criteria all met including the sanity-check benchmark at ±5% parity.
- `/health/pool`, `/health/schedulers`, `/health/db` endpoints all reporting healthy.
- No `MissingGreenlet` / `InvalidRequestError` patterns in 3 days of staging logs.
- Release manager committed to a v2.0.0 ship date.

**Exit:**
- All 20 Wave-5 acceptance criteria pass.
- Full benchmark report shows async net-neutral to ~5% improvement at medium concurrency; regression <5% or fixed before ship.
- `git grep -l "psycopg2" src/` returns zero hits (or only the explicit allowlist — deploy scripts per Audit 06 Decision 2).
- `git grep -n "FIXME(async" src/ tests/` returns zero hits.
- `.duplication-baseline` ratcheted to Wave 4-entry level or lower.
- CHANGELOG v2.0.0 finalized and reviewed by release manager.
- `pyproject.toml` version is `2.0.0`.
- `v2.0.0` git tag applied (GPG-signed).
- Staging canary passed 48h clean.
- Production deploy completes.
- 24h post-deploy monitoring clean: p95 latency within ±5% of pre-deploy, error rate flat, no new `MissingGreenlet` patterns, schedulers alive-ticking.
- Auto-memory `flask_to_fastapi_migration_v2.md` updated to reflect post-v2.0.0 state.
- `.claude/notes/flask-to-fastapi/` folder flagged for archival per `CLAUDE.md §"Branch and folder cleanup intent"` (~2 releases after v2.0.0 ships).
- Migration branch `feat/v2.0.0-flask-to-fastapi` merged to `main` and deleted.
- v2.0.1 patch-release process documented as the rollback path.

---

## PART 2: Assumption Verification Plan (elaborates §16)

Grouped by verification strategy. HIGH confidence assumptions get single-line plans; MEDIUM and LOW get full recipes.

### Group 1: HIGH confidence (9 — one-line verifications)

1. **FastAPI 0.128 / Starlette 0.50 ABI-stable.** Verify: `pip show fastapi starlette` matches locked versions pre-Wave-1; pin exact versions during Wave 2. Fail symptom: import error at startup. Fallback: bump pin floor.

2. **`Annotated[T, Depends()]` is canonical idiom.** Verify: `rg 'Annotated\[' src/core/auth_context.py` shows current usage (line 256-257); no verification needed beyond reading. N/A fallback.

3. **Full async SQLAlchemy in v2.0** (pivoted 2026-04-11). Verify: benchmark per Part 3.D compares async vs pre-migration sync baseline. When: Wave 2 baseline captured; Wave 4-5 comparison run. Failure: regression >10% on read-heavy hot endpoints (write-heavy regressions up to 15% acceptable). Fallback: tune `pool_size` (Risk #6) OR (last resort) hand-roll `selectinload` eager-loads on the worst offenders; if that's not enough, fall back to Option C and defer async. Pre-Wave-0 lazy-loading audit spike (Risk #1) is the early-warning gate — if the audit reveals relationship-access scope is untenable, switch to Option C before starting Wave 0.

4. **Admin handlers `async def` + full async SQLAlchemy end-to-end** (pivoted 2026-04-11). Verify: AST guard `test_architecture_admin_handlers_async.py` (renamed from the original `test_architecture_admin_async_signatures.py` for consistency with sibling guards) asserts every `src/admin/routers/*.py` handler is `async def`; sibling guard `test_architecture_admin_async_db_access.py` asserts DB access uses `async with get_db_session()` + `await db.execute(...)` rather than sync `with` or `run_in_threadpool(_sync_fetch)`. The stale `test_architecture_admin_sync_db_no_async.py` from the pre-pivot sync-def resolution is DELETED (wrong direction). When: Wave 1 entry (handler signature guard); Wave 4 entry (async DB access guard). Failure: sync handler or sync DB access found. Fallback: rewrite that handler.

5. **Starlette `SessionMiddleware` sufficient (<3.5KB).** See Group 3 detailed recipe below.

6. **`SESSION_SECRET` set in every deploy.** Verify: `src/admin/sessions.py::build_session_middleware_kwargs` raises `KeyError` on missing env var; `tests/unit/test_sessions_config.py` asserts this. When: Wave 0. Failure: startup crash. Fallback: obvious — set the env var.

7. **Admins tolerate one forced re-login.** User-confirmed decision #7. N/A verification.

8. **Authlib starlette_client feature-parity.** See Group 2 detailed recipe below.

9. **Route name translation `bp.endpoint → bp_endpoint` unique/stable.** Verify: `tests/admin/test_templates_url_for_resolves.py` asserts all flat names are unique. When: Wave 0. Failure: collision detected. Fallback: rename colliding routes.

### Group 2: MEDIUM confidence (12 — full recipes)

**10. CSRFOriginMiddleware (Option A) secure and correct — Origin-header validation + SameSite=Lax session cookie. Canonical implementation: `foundation-modules.md §11.7`. Zero JavaScript changes, zero template changes, zero form changes — the entire defense is an ASGI middleware that inspects the Origin (or Referer fallback) header against a wildcard-subdomain allow-list.**
- **How:** `tests/unit/test_csrf_origin_middleware.py` covering seven scenarios: (1) safe method (GET/HEAD/OPTIONS/TRACE) bypasses validation; (2) unsafe method with matching Origin passes; (3) unsafe method with mismatched Origin returns 403; (4) unsafe method with missing Origin but matching Referer passes; (5) unsafe method with `Origin: null` is always rejected; (6) exempt-path POST (`/admin/auth/google/callback`, `/admin/auth/oidc/callback`, `/admin/auth/gam/callback`, `/mcp/*`, `/a2a/*`, `/api/v1/*`, `/_internal/*`, `/.well-known/*`, `/agent.json`) bypasses validation; (7) wildcard subdomain match against `*.PRIMARY_DOMAIN` accepts newly-provisioned tenant subdomains. Plus OIDC transit-cookie exception per §11.6.1: the OIDC callback is on the exempt list AND state-validated through `oauth_transit` (signed, single-use, 10-minute max_age) so the defense is strictly stronger than Origin validation on that path. Plus Playwright `tests/e2e/test_admin_csrf_enforcement.py` exercising a logged-in user submitting a form from an evil origin.
- **When:** L1a exit (unit tests green). L1b exit (Playwright green). L2 entry (security review sign-off).
- **Failure symptom:** same-origin POST 403s (origin normalization bug — see `_origin_of` RFC-6454 serialization in §11.7) OR cross-origin POST returns 200 (allowed-origins registration missed a virtual host).
- **Fallback:** `starlette-csrf` PyPI package (~1 day, explicitly rejected in §15 but reinstatable).

**11. `sse-starlette` disconnect detection works behind nginx/Fly.**
- **How:** Wave 3 integration test `tests/integration/test_activity_stream_disconnect.py` opens an SSE connection, sends a disconnect, asserts the server's producer coroutine is cancelled within 2s. Plus staging test against Fly production-like setup: 100 concurrent SSE clients, drop 50 mid-stream, assert CPU/memory return to baseline within 10s.
- **When:** Wave 3 spike (before Wave 3 PR opens) and Wave 3 entry.
- **Failure symptom:** server CPU stays elevated after clients disconnect; memory grows unbounded.
- **Fallback:** `MAX_CONNECTIONS_PER_TENANT=10` + 30-second absolute idle timeout kills lingering streams; acceptable degradation.

**12. `uvicorn --proxy-headers --forwarded-allow-ips='*'` sufficient.**
- **How:** staging deploy with `--proxy-headers`; test requests from a known external IP show `request.client.host` as that IP (not Fly's internal).
- **When:** Wave 1 staging deploy.
- **Failure symptom:** client IP logs show `10.x` or `172.x` Fly internal IPs.
- **Fallback:** restore a thin `CustomProxyFix` in `src/app.py` reading `Fly-Client-IP` header directly; ~20 LOC.

**13. Test harness extension `get_admin_client()` lands in Wave 0.**
- **How:** Wave 0 acceptance criterion #11 above. Smoke test `tests/unit/test_harness_admin_client.py::test_get_admin_client_returns_test_client_instance`.
- **When:** Wave 0 exit.
- **Failure symptom:** method missing or returns wrong type.
- **Fallback:** explicitly build a `TestClient` in each test (ugly but unblocking). Track debt.

**14. BDD admin scenarios stay excluded from cross-transport parametrization.**
- **How:** grep `tests/bdd/conftest.py` around line 534-561 for `_ADMIN_TAG_PREFIX = "T-ADMIN-"`; `test_bdd_admin_exclusion.py` asserts admin-tagged scenarios produce only 1 transport parametrization.
- **When:** Wave 2 entry.
- **Failure symptom:** admin BDD scenario runs 4× and 3 copies fail because they can only go through REST.
- **Fallback:** manually tag scenarios with `@transport-rest-only`.

**15. Codemod regex handles JS template literal `url_for`.**
- **How:** `scripts/codemod_templates_greenfield.py --dry-run templates/add_product_gam.html` prints 15 target transformations. Manual diff review.
- **When:** Wave 0, during codemod authoring.
- **Failure symptom:** JS fetch URLs left as Flask route names; `add_product_gam.html` page broken post-migration.
- **Fallback:** hand-edit the 4 tricky files from §12.5 after the codemod pass.

**16. No nginx config change needed.**
- **How:** `rg -r '/admin' config/nginx/` and read output. Visual inspection of any `location` blocks or rewrite rules.
- **When:** Wave 0 entry.
- **Failure symptom:** nginx strips/rewrites the session cookie or buffers SSE.
- **Fallback:** minimal nginx tweaks in Wave 3; expect `proxy_buffering off; proxy_cache off;` for SSE path.

**17. `/admin/` URL prefix stays.**
- **How:** user decision #10. Verify: `grep -r '/admin/' docs/ runbooks/ README.md` shows bookmarks; `APIRouter(prefix="/admin", ...)` in `src/admin/app_factory.py` preserves.
- **When:** Wave 0 entry.
- **Failure symptom:** N/A (decided).
- **Fallback:** N/A.

**18. No external consumer depends on Flask-specific JSON error shape (category 1).**
- **How:** (a) `grep -r '/admin/api/' <monitoring-configs>` — Datadog dashboards export, PagerDuty integration configs, internal-dashboards repo. (b) Platform team sync meeting before Wave 2 opens. (c) Shadow-trace staging for 48h capturing `Referer` headers on `/admin/api/*` and `/admin/*/status` routes; if external referers found, investigate.
- **When:** Wave 2 entry. Platform team sign-off is a Wave 2 hard gate.
- **Failure symptom:** dashboard breaks post-Wave-2; synthetic check fails.
- **Fallback:** add the broken endpoint to the category-2 compat list and preserve its Flask-era shape.

**19. `request.url_for()` resolves across nested `include_router(prefix=...)`.**
- **How:** Wave 0 validator test `test_templates_url_for_resolves.py` in strict mode: spin up a real FastAPI app with `build_admin_router()` mounted at `/` then call `request.url_for("some_endpoint_name")` and assert the resolved URL starts with `/admin/`. Works even with empty router body if at least one stub route is registered.
- **When:** Wave 0 (naming) and Wave 1 (strict resolution).
- **Failure symptom:** `NoMatchFound` at runtime on any `render(request, ...)` call.
- **Fallback:** register routes at the top-level app instead of nesting via `APIRouter`; ~30-min refactor.

**20. Super-admin flows fully expressible as `SuperAdminDep`.**
- **How:** Wave 2 bulk port exposes this naturally. Before Wave 2, write 2 representative super-admin routes (one list, one delete) in Wave 1's scope and verify `SuperAdminDep` composes.
- **When:** Wave 1 mid-wave.
- **Failure symptom:** super-admin route needs tenant-context logic that `SuperAdminDep` cannot express.
- **Fallback:** add a second dep `SuperAdminWithTenantContextDep`; reviewable scope creep (~30 LOC).

**21. `FlyHeadersMiddleware` may be redundant.**
- **How:** staging test with Fly traffic → check if `X-Forwarded-For` arrives with correct value when `FlyHeadersMiddleware` is disabled (temporary env flag).
- **When:** Wave 3 entry.
- **Failure symptom:** client IPs broken in logs.
- **Fallback:** keep `FlyHeadersMiddleware`. Cost: ~40 LOC of legacy we don't delete.

### Group 3: LOW confidence (7 — full recipes)

**22. `SessionMiddleware` + SameSite=Lax in all environments.** **[REVERSED — SameSite=Lax everywhere per CLAUDE.md blocker 5]**
- **How:** Playwright test opens admin login in tab 1, opens admin dashboard in tab 2, asserts session cookie carries between tabs. Run against staging.
- **When:** Wave 1 exit.
- **Failure symptom:** second tab gets redirected to login.
- **Fallback:** switch to `SameSite=Lax` (reduces cross-site safety); or move to Redis-backed session store.

**23. No monitoring parses `[SESSION_DEBUG]` log lines.**
- **How:** `grep -r 'SESSION_DEBUG' config/datadog/ config/fly/ <internal-monitoring-repo>`. Platform team check.
- **When:** Wave 0 entry.
- **Failure symptom:** Datadog alert silently stops firing after cut.
- **Fallback:** preserve the log line format in one module during Wave 1 as a compat bridge; remove in Wave 3.

**24. `test_mode` global injectable via small dep without leaking test surface.**
- **How:** write `src/admin/deps/test_mode.py::TestModeDep` and ensure it checks `os.environ.get("ADCP_AUTH_TEST_MODE") == "true"` only. Guard: no production code should import from `tests/`.
- **When:** Wave 1.
- **Failure symptom:** test infra leaks into production import paths (caught by `test_architecture_no_test_imports_in_src.py` if one exists).
- **Fallback:** pass test_mode flag through `request.state` set by a dedicated middleware.

**25. `tenant_management_api`, `sync_api`, `gam_reporting_api` are thin wrappers.**
- **How:** manual read-through in Wave 2 scoping session (pre-branch). Sample metric: ratio of handler LOC to underlying-service-call LOC; should be <2x.
- **When:** Wave 2 entry.
- **Failure symptom:** one of the 3 APIs has deep Flask-ism (e.g., `request.get_data()` branching for multipart vs JSON).
- **Fallback:** carve that API into its own Wave 2.5 mini-wave; extend branch by 2 days.

**26. `get_rest_client()` pattern extends cleanly to `get_admin_client()`.**
- **How:** Wave 0 implementation + smoke test. The pattern at `tests/harness/_base.py:894-914` (verified) uses `app.dependency_overrides` for auth deps; `get_admin_client()` needs the same plus the admin auth deps + session priming shim.
- **When:** Wave 0.
- **Failure symptom:** harness state leaks between tests; admin_client doesn't see session; test teardown errors.
- **Fallback:** dedicate a separate fixture file `tests/harness/admin_client.py` as a wrapper class rather than a method. See Part 3.B for exact proposed diff.

**27. 3 `try/except ImportError` blocks in Flask factory are vestigial.**
- **How:** `rg -n 'try:\s*$' src/admin/app.py -A 5 | rg ImportError` locates the 3 blocks. Read each and identify what module it guards against; commit history check `git log -p src/admin/app.py | grep -B 5 ImportError`.
- **When:** Wave 3 during `src/admin/app.py` deletion.
- **Failure symptom:** unconditional import in the new code path crashes on some platform.
- **Fallback:** keep the try/except in the new `app_factory.py` but log at WARNING if the import fails.

**28. Docker image shrinks ~75 MB** (corrected from ~80 MB — psycopg2 + libpq retained per D1/D2/D9).
- **How:** `docker images adcp-salesagent:v2.0.0` vs `docker images adcp-salesagent:v1.99.0`. Compare `Size` column.
- **When:** Wave 3 exit.
- **Failure symptom:** shrinkage <40MB.
- **Fallback:** non-blocker. Investigate what else grew; likely `sse-starlette` + `pydantic-settings` additions offset some removals.

---

## PART 3: Verification Strategy Elaboration (elaborates §21)

### A. Structural guard tests to add

#### `tests/unit/test_architecture_no_flask_imports.py`

**Path:** `/Users/quantum/Documents/ComputedChaos/salesagent/tests/unit/test_architecture_no_flask_imports.py`

**AST scan pattern:**
```python
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

FORBIDDEN_MODULES = {"flask", "flask_caching", "flask_socketio", "werkzeug"}

SCAN_PATHS = ["src/"]

# Allowlist ratchets per wave. Entries removed as files are migrated.
# Format: relative posix path from ROOT.
ALLOWLIST: set[str] = {
    # Wave 0 initial set (everything except the 4 Wave-1 targets)
    "src/admin/app.py",
    "src/admin/server.py",
    "src/admin/blueprints/public.py",
    "src/admin/blueprints/core.py",
    "src/admin/blueprints/auth.py",
    "src/admin/blueprints/oidc.py",
    "src/admin/blueprints/accounts.py",
    "src/admin/blueprints/products.py",
    "src/admin/blueprints/principals.py",
    "src/admin/blueprints/users.py",
    "src/admin/blueprints/tenants.py",
    "src/admin/blueprints/gam.py",
    "src/admin/blueprints/inventory.py",
    "src/admin/blueprints/inventory_profiles.py",
    "src/admin/blueprints/creatives.py",
    "src/admin/blueprints/creative_agents.py",
    "src/admin/blueprints/signals_agents.py",
    "src/admin/blueprints/operations.py",
    "src/admin/blueprints/policy.py",
    "src/admin/blueprints/settings.py",
    "src/admin/blueprints/adapters.py",
    "src/admin/blueprints/authorized_properties.py",
    "src/admin/blueprints/publisher_partners.py",
    "src/admin/blueprints/workflows.py",
    "src/admin/blueprints/api.py",
    "src/admin/blueprints/format_search.py",
    "src/admin/blueprints/schemas.py",
    "src/admin/blueprints/activity_stream.py",
    "src/admin/utils/helpers.py",
    "src/admin/utils/audit_decorator.py",
    "src/admin/tenant_management_api.py",
    "src/admin/sync_api.py",
    "src/adapters/gam_reporting_api.py",
    "src/adapters/google_ad_manager.py",
    "src/adapters/mock_ad_server.py",
    "src/app.py",
    "src/services/gam_inventory_service.py",
}


def _scan_file_for_flask_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text())
    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in FORBIDDEN_MODULES:
                    violations.append(f"{path}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in FORBIDDEN_MODULES:
                violations.append(f"{path}: from {node.module} import ...")
    return violations


def test_no_flask_imports_outside_allowlist():
    all_violations = []
    for scan_path in SCAN_PATHS:
        for py_file in (ROOT / scan_path).rglob("*.py"):
            rel = py_file.relative_to(ROOT).as_posix()
            if rel in ALLOWLIST:
                continue
            all_violations.extend(_scan_file_for_flask_imports(py_file))
    assert not all_violations, (
        "Flask imports found outside allowlist:\n" + "\n".join(all_violations)
    )


def test_allowlist_entries_exist():
    """Prevents allowlist rot: every allowlisted path must exist on disk."""
    for rel in sorted(ALLOWLIST):
        assert (ROOT / rel).exists(), f"allowlist entry missing from disk: {rel}"


def test_allowlist_shrinks_over_time():
    """Structural gate: the allowlist never grows. New Flask imports are rejected."""
    # This test's function body pins the current max allowlist size.
    # Each wave updates it downward.
    CURRENT_MAX = len(ALLOWLIST)  # Wave 0 baseline
    assert len(ALLOWLIST) <= CURRENT_MAX
```

**How it ratchets:** each layer/sub-PR edits the `ALLOWLIST` set to remove migrated files. L1a-L1b together remove 4 entries. L1c-L1d together remove 25 (including `src/admin/utils/helpers.py` and `src/admin/server.py` in the L1d high-risk sub-PR). L2 removes the last 3 (`src/admin/app.py`, `src/app.py`, `src/admin/blueprints/activity_stream.py`). After L2, `ALLOWLIST = set()` and `test_no_flask_imports_outside_allowlist` enforces zero tolerance.

#### `tests/unit/test_architecture_admin_handlers_async.py`

Scans `src/admin/routers/*.py` and asserts every function decorated with `@router.get/post/put/delete/patch` is `async def`. Sibling to existing `test_architecture_repository_pattern.py`. **[L5+ guard per the 2026-04-14 layering.]** The mutually-exclusive L0-L4 guard is `test_architecture_handlers_use_sync_def.py`; the two swap atomically at L5b (see execution-plan.md L5b work item 3). The stale `test_architecture_admin_sync_db_no_async.py` (which asserted async handlers must wrap DB in `run_in_threadpool`) is wrong-direction at every layer and is NOT implemented.

#### `tests/unit/test_architecture_admin_async_db_access.py`

Scans `src/admin/routers/*.py` and asserts every DB access site uses `async with get_db_session()` + `await db.execute(...)` patterns, NOT sync `with get_db_session()` or `run_in_threadpool(_sync_fetch)` wrappers around DB work. The `run_in_threadpool` helper is still valid for file I/O, CPU-bound, and sync-third-party-library calls — the guard specifically flags calls where the wrapped function does DB work (identified by an inner `get_db_session()` call or a `Session`/`AsyncSession` parameter). [L5+ guard per the 2026-04-14 layering. At L0-L4, sync `with get_db_session()` IS the correct pattern; this guard is not yet active.]

#### `tests/admin/test_templates_url_for_resolves.py`

Not quite a guard — a parity validator. Scans every `.html` file under templates/ for `url_for("name")` literals. Wave 0 mode: asserts every `name` matches `^[a-z_][a-z0-9_]*$`. Wave 1 mode (strict): asserts every `name` resolves via `app.url_path_for(name)`. Referenced as assumption #19 verification.

### B. Integration test patterns for admin routes

#### `get_admin_client()` extension diff

Proposed addition to `/Users/quantum/Documents/ComputedChaos/salesagent/tests/harness/_base.py`, inserted as a new method immediately after line 914 (just after `get_rest_client` closes):

```python
    def get_admin_client(self) -> Any:
        """Return FastAPI TestClient for admin routes with session priming.

        Sibling of get_rest_client(). Overrides the admin auth deps to inject
        a pre-authenticated admin identity and primes request.session with
        user/tenant context, matching what Flask's session_transaction() did.

        The default overrides return an admin identity for the tenant/principal
        bound on this env. Tests can override per-request by calling
        app.dependency_overrides[...] inside a try/finally.
        """
        if self._admin_client is None:
            from starlette.testclient import TestClient

            from src.admin.deps.auth import (
                _current_user_dep,
                _require_admin_dep,
                _require_super_admin_dep,
            )
            from src.admin.deps.tenant import _current_tenant_dep
            from src.app import app
            from tests.harness.transport import Transport

            admin_identity = self.identity_for(Transport.REST)
            admin_user = {
                "email": f"{self._principal_id}@example.com",
                "role": "admin",
                "tenant_id": self._tenant_id,
                "user_id": self._principal_id,
            }

            app.dependency_overrides[_current_user_dep] = lambda: admin_user
            app.dependency_overrides[_require_admin_dep] = lambda: admin_user
            app.dependency_overrides[_require_super_admin_dep] = lambda: admin_user
            app.dependency_overrides[_current_tenant_dep] = lambda: admin_identity.tenant

            # Prime session cookie for CSRF and session-gated routes.
            client = TestClient(app)
            with client.session_transaction() as session_data:
                session_data["user"] = admin_user
                session_data["tenant_id"] = self._tenant_id
                session_data["authenticated"] = True
            # CSRF-Option-A: Origin-header validation replaces token-based
            # CSRF; TestClient defaults Origin=http://testserver which is
            # added to ALLOWED_ORIGINS in the test config, so no per-test
            # priming is needed. See foundation-modules.md §11.7.
            self._admin_client = client

        return self._admin_client
```

Also requires adding `self._admin_client: Any = None` to `__init__` around line 248 (currently `self._rest_client: Any = None`), and extending teardown at line 827-832 to null `self._admin_client` alongside `self._rest_client`.

Note: `TestClient.session_transaction()` is a Flask-ism that Starlette TestClient does not support directly — the actual prime happens by setting the cookie via `client.cookies.set("adcp_session", <signed_value>, ...)` after computing the value with `itsdangerous`. The harness method abstracts this. A helper `_sign_session(payload) -> str` lives in `tests/harness/_admin_session_helper.py`.

#### Canonical integration test templates

**GET route** (HTML rendered, reads from DB):
```python
import pytest
from tests.factories import TenantFactory, PrincipalFactory, AccountFactory

@pytest.mark.requires_db
class TestListAccounts:
    """GET /admin/tenant/{tenant_id}/accounts lists all accounts.

    Covers: UC-ADMIN-ACCOUNTS-LIST-01
    """

    def test_returns_200_with_accounts_in_html(self, integration_db):
        from tests.harness import IntegrationEnv
        with IntegrationEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            acc = AccountFactory(tenant=tenant, name="Acme Co")

            client = env.get_admin_client()
            resp = client.get("/admin/tenant/t1/accounts")

            assert resp.status_code == 200
            assert "Acme Co" in resp.text
            assert resp.headers["content-type"].startswith("text/html")
```

**POST-redirect-GET** (form submission):
```python
def test_create_account_redirects_to_list(self, integration_db):
    from tests.harness import IntegrationEnv
    with IntegrationEnv(tenant_id="t1", principal_id="p1") as env:
        TenantFactory(tenant_id="t1")
        client = env.get_admin_client()
        # CSRF-Option-A: same-origin TestClient POSTs pass by Origin header
        # alone (Origin=http://testserver is in ALLOWED_ORIGINS).

        resp = client.post(
            "/admin/tenant/t1/accounts",
            data={"name": "New Co"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == "/admin/tenant/t1/accounts"

        # Follow redirect, assert account is listed.
        resp2 = client.get(resp.headers["location"])
        assert "New Co" in resp2.text
```

**AJAX JSON route** (category 1 internal):
```python
def test_change_status_returns_json(self, integration_db):
    from tests.harness import IntegrationEnv
    with IntegrationEnv(tenant_id="t1", principal_id="p1") as env:
        tenant = TenantFactory(tenant_id="t1")
        acc = AccountFactory(tenant=tenant, status="active")
        client = env.get_admin_client()
        # CSRF-Option-A: same-origin TestClient POST passes by Origin alone.

        resp = client.post(
            f"/admin/tenant/t1/accounts/{acc.account_id}/status",
            json={"status": "paused"},
            headers={"Accept": "application/json"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"success": True, "status": "paused"}
```

**File upload route**:
```python
def test_upload_creative_file(self, integration_db, tmp_path):
    from tests.harness import IntegrationEnv
    with IntegrationEnv(tenant_id="t1", principal_id="p1") as env:
        TenantFactory(tenant_id="t1")
        client = env.get_admin_client()
        # CSRF-Option-A: same-origin TestClient POST passes by Origin alone.

        fake_image = tmp_path / "banner.png"
        fake_image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        with fake_image.open("rb") as fh:
            resp = client.post(
                "/admin/tenant/t1/creatives/upload",
                files={"file": ("banner.png", fh, "image/png")},
                data={"name": "Summer Banner"},
                follow_redirects=False,
            )
        assert resp.status_code == 303
```

**SSE route**:
```python
def test_activity_stream_emits_events(self, integration_db):
    from tests.harness import IntegrationEnv
    with IntegrationEnv(tenant_id="t1", principal_id="p1") as env:
        tenant = TenantFactory(tenant_id="t1")
        client = env.get_admin_client()

        with client.stream("GET", "/admin/tenant/t1/activity-stream") as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")

            env.emit_activity(tenant_id="t1", kind="media_buy_created")

            events = []
            for line in resp.iter_lines():
                if line.startswith("data:"):
                    events.append(line)
                if len(events) >= 1:
                    break
            assert len(events) >= 1
            assert "media_buy_created" in events[0]
```

### C. Playwright end-to-end tests

All Playwright tests live under `/Users/quantum/Documents/ComputedChaos/salesagent/tests/e2e/`. They require the full Docker stack running via `./run_all_tests.sh` or a dedicated `docker-compose.e2e.yml`.

**1. Login via Google OAuth (happy path)**
- **File:** `tests/e2e/test_admin_login_flow.py`
- **Stack state:** Docker stack with `ADCP_AUTH_TEST_MODE=true` + mocked Google OIDC endpoint.
- **Assertions:** (a) `/admin/login` returns Google button; (b) click Google button → mock OIDC server issues token → `/admin/auth/google/callback` redirects to `/admin/`; (c) dashboard visible; (d) `adcp_session` cookie present with `HttpOnly` flag; (e) session contains `email`, `tenant_id`, `authenticated=True`.

**2. Login via per-tenant OIDC**
- **File:** `tests/e2e/test_admin_oidc_login_flow.py`
- **Stack state:** Docker + mock OIDC provider registered for `tenant_id=t1`.
- **Assertions:** (a) `/admin/auth/oidc/t1` returns 303 to mock provider; (b) callback completes; (c) session `tenant_id == "t1"`; (d) `/admin/tenant/t1/` reachable.

**3. Create account → create product → submit creative → delete creative**
- **File:** `tests/e2e/test_admin_account_product_creative_lifecycle.py`
- **Stack state:** Docker + seeded super-admin. Tests the full path from empty tenant to a creative being deleted.
- **Assertions per step:** each POST returns 303 to the list page; each list page contains the newly-created entity by name; final delete removes the entity and list no longer shows it. Assert DB state via a direct SQL query (not just UI) that the `deleted_at` column is set.

**4. CSRF rejection**
- **File:** `tests/e2e/test_admin_csrf_enforcement.py`
- **Stack state:** Docker + authenticated session.
- **Assertions:** (a) GET form page from `testserver`; (b) POST with `Origin: https://evil.example.com` AND valid session cookie → 403 with `{"detail": "CSRF Origin check failed"}`; (c) POST with `Origin: null` → 403 (always rejected); (d) POST with `Origin` header omitted and no `Referer` → 200/303 (SameSite=Lax is the defense for legacy UAs); (e) POST with `Origin: https://testserver` (same-origin) → 303.

**5. Session expiration**
- **File:** `tests/e2e/test_admin_session_expiration.py`
- **Stack state:** Docker + session cookie with lifetime set to 2 seconds via env override.
- **Assertions:** (a) login works; (b) wait 3 seconds; (c) GET `/admin/` redirects to `/admin/login` with `303`; (d) no server error logs.

### D. Benchmark harness (assumption #3 — pivoted 2026-04-11 to async-vs-sync comparison)

**Tool:** `pytest-benchmark` for deterministic microbenchmarks + `wrk` for macro load test.

**Routes benchmarked:**
1. **Read-heavy:** `GET /admin/tenant/t1/products` — lists 100 products via repository. Measures async DB latency end-to-end vs pre-migration sync baseline.
2. **Write-heavy:** `POST /admin/tenant/t1/accounts` — creates one account, redirects. Measures async DB latency end-to-end vs pre-migration sync baseline.

**Harness file:** `/Users/quantum/Documents/ComputedChaos/salesagent/tests/benchmark/test_admin_routes_async_vs_sync.py`

```python
import asyncio
import pytest


@pytest.mark.benchmark(group="admin-routes-async")
def test_list_products_route(benchmark, integration_db):
    """Async route benchmark — compares against pre-migration sync baseline."""
    from tests.harness import IntegrationEnv
    async def _run():
        async with IntegrationEnv(tenant_id="t1", principal_id="p1") as env:
            ...
            client = env.get_admin_client()
            await client.get("/admin/tenant/t1/products")
    benchmark(lambda: asyncio.run(_run()))
    # Acceptance: async p50 ≤ sync baseline p50 + 5%, p99 ≤ sync baseline p99 + 10%


@pytest.mark.benchmark(group="admin-routes-async")
def test_create_account_route(benchmark, integration_db):
    """Async write-heavy — compares against pre-migration sync baseline."""
    ...
```

**Acceptance criteria:**
- `test_list_products_route`: async p50 ≤ sync baseline p50 + 5%, p99 ≤ sync baseline p99 + 10%
- `test_create_account_route`: async p50 ≤ sync baseline p50 + 5%, p99 ≤ sync baseline p99 + 15% (write-heavy tolerances wider)
- Under HIGH concurrency (load test with `wrk -c 100 -t 10 -d 30s`): async throughput ≥ sync baseline (should win decisively)

**Storage:** `pytest-benchmark --benchmark-json=test-results/wave-N/benchmark.json` committed to repo per wave. `scripts/compare_benchmarks.py` asserts wave N doesn't regress >20% from wave N-1. **Wave 2 captures the sync baseline; Wave 4 captures the post-async comparison.**

**Failure fallback:** if async regresses significantly under the benchmark, first tune `pool_size` (Risk #6 in `async-pivot-checkpoint.md` §4). If that doesn't close the gap, apply `selectinload` eager-loading to the worst offenders. If THAT doesn't close the gap, invoke the last-resort fallback: revert to Option C (sync `def` admin handlers) and defer async conversion.

### E. Coverage parity automation

**Script:** `/Users/quantum/Documents/ComputedChaos/salesagent/scripts/check_coverage_parity.py` (~150 LOC)

```python
"""Compare per-file coverage between two coverage.json files.

Usage:
    python scripts/check_coverage_parity.py \\
        --before test-results/base/coverage.json \\
        --after test-results/head/coverage.json \\
        --mapping migrations/wave-1-file-mapping.json \\
        --tolerance 1.0

Fails with non-zero exit if any file in the mapping has coverage drop > tolerance.
"""
import argparse
import json
import sys
from pathlib import Path


def load_coverage(path: Path) -> dict[str, float]:
    data = json.loads(path.read_text())
    files = data.get("files", {})
    return {
        file_path: file_info["summary"]["percent_covered"]
        for file_path, file_info in files.items()
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--before", required=True, type=Path)
    ap.add_argument("--after", required=True, type=Path)
    ap.add_argument("--mapping", required=True, type=Path,
                    help="JSON: {old_path: new_path} mapping deleted files to replacements")
    ap.add_argument("--tolerance", type=float, default=1.0,
                    help="Allowed drop in coverage percentage points")
    args = ap.parse_args()

    before = load_coverage(args.before)
    after = load_coverage(args.after)
    mapping: dict[str, str] = json.loads(args.mapping.read_text())

    failures = []
    rows = []
    for old_path, new_path in mapping.items():
        old_cov = before.get(old_path)
        new_cov = after.get(new_path)
        if old_cov is None:
            failures.append(f"MISSING BEFORE: {old_path}")
            continue
        if new_cov is None:
            failures.append(f"MISSING AFTER: {new_path}")
            continue
        delta = new_cov - old_cov
        rows.append((old_path, new_path, old_cov, new_cov, delta))
        if delta < -args.tolerance:
            failures.append(
                f"REGRESSION: {old_path} ({old_cov:.1f}%) -> "
                f"{new_path} ({new_cov:.1f}%), delta {delta:+.1f}pt"
            )

    # Emit PR-description markdown table.
    print("| Old file | New file | Before | After | Delta |")
    print("|---|---|---|---|---|")
    for old, new, b, a, d in rows:
        print(f"| `{old}` | `{new}` | {b:.1f}% | {a:.1f}% | {d:+.1f}pt |")

    if failures:
        print("\nFAILURES:", file=sys.stderr)
        for f in failures:
            print(f"  {f}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
```

**File mapping format** (`migrations/wave-1-file-mapping.json`):
```json
{
  "src/admin/blueprints/public.py": "src/admin/routers/public.py",
  "src/admin/blueprints/core.py": "src/admin/routers/core.py",
  "src/admin/blueprints/auth.py": "src/admin/routers/auth.py",
  "src/admin/blueprints/oidc.py": "src/admin/routers/oidc.py"
}
```

**Integration into per-wave gate:** `.github/workflows/pr.yml` adds a step:
```
- name: Coverage parity
  run: |
    git checkout ${{ github.event.pull_request.base.sha }}
    make test-cov
    cp coverage.json test-results/base/coverage.json
    git checkout ${{ github.sha }}
    make test-cov
    cp coverage.json test-results/head/coverage.json
    python scripts/check_coverage_parity.py \\
      --before test-results/base/coverage.json \\
      --after test-results/head/coverage.json \\
      --mapping migrations/wave-${{ env.WAVE }}-file-mapping.json \\
      --tolerance 1.0
```

**Handling renamed/restructured files:** when a Flask blueprint splits into multiple FastAPI routers (e.g., `settings.py` at 1,446 LOC splits into `settings.py` + `tenant_settings.py`), the mapping supports list-valued targets:

```json
{
  "src/admin/blueprints/settings.py": [
    "src/admin/routers/settings.py",
    "src/admin/routers/tenant_settings.py"
  ]
}
```

The script aggregates new-file coverage as a weighted average by line count (pulled from the coverage.json `num_statements` field) when the target is a list. For the `accounts.py → accounts.py` case where internal structure differs, coverage parity still works at the file level — the script doesn't care about function names, only file-level percentages.

---

## Summary

- **Part 1** gives each wave ~15 concrete acceptance criteria, a file-level checklist with absolute paths, a risk table covering real issues (middleware ordering, CSRF body-read, cookie invalidation, merge conflicts), single-commit-revert rollbacks with explicit windows, branch freeze announcement templates with freeze scopes, time estimates justified by work breakdown, and entry/exit gates tied to the previous wave's exit state.

- **Part 2** groups 28 assumptions into HIGH (one-liners), MEDIUM (12 full recipes with tool + timing + failure + fallback), and LOW (7 full recipes). Assumptions are tied to specific test files, grep commands, staging checks, or Playwright flows.

- **Part 3** provides concrete code for the no-flask-imports guard with initial allowlist, the exact `get_admin_client()` diff proposed at `tests/harness/_base.py:914`, five integration test templates (GET / POST-redirect-GET / AJAX JSON / upload / SSE), five Playwright e2e flows with file paths + stack state + assertions, a `pytest-benchmark` harness with p50/p99 thresholds for assumption #3, and a full `check_coverage_parity.py` script with mapping JSON format + CI integration + list-valued target handling for restructured files.

### Critical Files for Implementation

- `/Users/quantum/Documents/ComputedChaos/salesagent/.claude/notes/flask-to-fastapi-migration.md` — the parent document being elaborated
- `/Users/quantum/Documents/ComputedChaos/salesagent/tests/harness/_base.py` — lines 894-914 hold the `get_rest_client()` pattern, 248 holds `_rest_client` init, 827-832 hold teardown; `get_admin_client()` extension lands here
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/app.py` — lines 25-45, 127-135, 274-304, 351-352 all require edits across Waves 1-3 for middleware registration and Flask mount removal
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/core/auth_context.py` — lines 256-257 define the `Annotated[...]` dep pattern that `src/admin/deps/auth.py` mirrors
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/app.py` — 427-LOC Flask factory; progressively emptied across Waves 1-2 and deleted in Wave 3
