# Database Deep-Audit Report (2026-04-11)

> **[ARCHIVED REFERENCE — 2026-04-14]** This report is a preserved artifact from the 3-round verification process (Apr 11-14) that produced the v2.0 8-layer execution model. For current implementation guidance, see:
> - `../CLAUDE.md` — mission briefing + 8-layer model
> - `../execution-plan.md` — layer-by-layer work items
> - `../implementation-checklist.md` — per-layer gate checklist
>
> This file is preserved for institutional memory only. Its recommendations have been absorbed into the canonical docs above. Do NOT use this file as a primary reference for implementation decisions.

> **Produced by:** 6 parallel Opus subagents with ultrathink, 2nd/3rd/4th-order derivative analysis
> **Scope:** ORM models + relationships, session lifecycle + pool, repository pattern + queries, Alembic migrations, test DB infrastructure, data integrity + performance
> **Context:** Flask→FastAPI v2.0 migration with absorbed async SQLAlchemy (asyncpg driver, AsyncSession, 3 coexisting engines per Decisions 1/2/9)

---

## Executive Summary

The database layer is **fundamentally sound** for the async migration — the repository pattern is well-established, query patterns use SQLAlchemy 2.0 style consistently, and the multi-engine pool math works within PG `max_connections=100` for single-container deployment. However, the audit surfaced **3 critical blockers**, **8 high-severity issues**, and **15+ medium/low findings** that must be addressed before or during Wave 4.

**The #1 risk across all 6 audits: ALL 68 relationships (corrected from 58, verified 2026-04-12) have implicit `lazy="select"` and zero explicit `lazy=` arguments.** Combined with 6 Product/Tenant `@property` methods that synchronously access relationships, Spike 1's `lazy="raise"` sweep will produce a significant blast radius. This is expected but the audit quantifies it precisely.

**The #1 surprise: the statement_timeout event listener will CRASH under asyncpg.** The `@event.listens_for(_engine, "connect")` at `database_session.py:139` uses `dbapi_conn.cursor()` — a psycopg2 API that asyncpg does not expose. Every async connection will fail on first use. This is a Wave 4 hard blocker that was not in any prior plan document.

---

## Critical Blockers (must fix before async conversion)

### C1. Statement timeout event listener crashes under asyncpg
**Source:** DB-2 (Session Lifecycle)
**File:** `src/core/database/database_session.py:139`
**Issue:** `@event.listens_for(_engine, "connect")` callback uses `dbapi_conn.cursor().execute("SET statement_timeout=...")`. asyncpg connections do NOT have a `.cursor()` method.
**Fix:** Use `connect_args={"server_settings": {"statement_timeout": "30000"}}` for the async engine. Keep the event listener for the sync engines (Path B + sync-bridge) which use psycopg2.
**Wave:** 4a (foundational — blocks all async DB access)
**Effort:** 0.25 day

### C2. `CreativeRepository.commit()` breaks UoW atomicity
**Source:** DB-3 (Repository Pattern)
**Files:** `src/core/database/repositories/creative.py:234,476`
**Issue:** Two repository methods call `self._session.commit()` directly, bypassing the UoW boundary. Under concurrent async requests, a partial commit from one request is visible to another before the UoW completes. Cannot be rolled back if a later UoW operation fails.
**Fix:** Remove both `commit()` methods. Callers must use the UoW commit boundary.
**Wave:** Pre-Wave-4 prep (must land before async to prevent data integrity races)
**Effort:** 0.5 day

### C3. 20+ `uow.session` direct-access sites are MissingGreenlet crash sites
**Source:** DB-3 (Repository Pattern)
**File:** `src/core/database/repositories/uow.py:69-80` (deprecated property) + 20+ callsites
**Issue:** Production code accesses `uow.session` directly despite deprecation. Under async, `uow.session` returns `AsyncSession` which requires `await` for every operation. Without `await`, every call raises `MissingGreenlet`.
**Fix:** Migrate all 20+ callsites to repository methods or the UoW's typed interface. Hard Wave 4 prerequisite.
**Wave:** 4a pilot
**Effort:** 2 days

---

## High-Severity Issues

### H1. 6 Product/Tenant @property methods access relationships synchronously
**Source:** DB-1 (Models)
**Files:** `models.py:193` (`is_gam_tenant`), `341` (`effective_format_ids`), `355` (`effective_properties`), `428/438/448` (3 branches), `454` (`effective_property_tags`), `468` (`effective_implementation_config`)
**Issue:** These are `@property` methods — NOT awaitable. Under `lazy="raise"`, they crash unless relationships are pre-loaded. Adapter code calls these synchronously. The fix requires either eager-loading at every `select(Product)` site or converting to explicit async methods (breaking the property API).
**Recommendation:** Option (c) — repositories always return products with `selectinload(Product.inventory_profile, Product.tenant)`. Lowest friction, highest reliability.
**Wave:** 4a (Spike 1 will quantify, Wave 4a implements)
**Effort:** 3 days

### H2. 5 `backref=` create invisible attributes on Tenant
**Source:** DB-1 (Models)
**Lines:** 727, 1900, 1935, 1971, 2010
**Issue:** `backref=` defines attributes on the OTHER side of the relationship — `tenant.creatives`, `tenant.authorized_properties`, etc. are invisible in the Tenant class definition, making them impossible to find via model-level audit. Under `lazy="raise"`, accessing these raises `InvalidRequestError`.
**Fix:** Convert all 5 to `back_populates=` with explicit relationship declarations on both sides.
**Wave:** 4a
**Effort:** 0.5 day

### H3. 3 ForeignKeyConstraints missing `ondelete`
**Source:** DB-6 (Data Integrity)
**Lines:** creatives:732 (→principals), creative_assignments:826 (→media_buys), media_packages:1025 (→media_buys)
**Issue:** ORM `cascade="all, delete-orphan"` only works when SQLAlchemy's identity map tracks both objects. Under async with no shared identity map, concurrent deletes bypass ORM cascade → `IntegrityError` instead of clean cascade.
**Fix:** Add `ondelete="CASCADE"` via Alembic migration.
**Wave:** Pre-Wave-4 (Alembic migration)
**Effort:** 0.5 day

### H4. Zero-downtime deploy exceeds `max_connections`
**Source:** DB-2 (Session Lifecycle)
**Issue:** 60 peak connections per container × 2 containers during rolling deploy = 120, exceeding PG `max_connections=100`.
**Fix:** Either (a) increase `max_connections` to 130, (b) reduce pool sizes (async 10+15=25, sync 3+7=10, bridge 2+3=5 = 40/container, 80/2 containers), (c) introduce PgBouncer, or (d) drain-then-start deploy strategy.
**Wave:** Pre-production (ops config)
**Effort:** 0.25 day (docs) or 0.5 day (pool reduction + testing)

### H5. `application_name` NOT SET on any engine
**Source:** DB-2 (Session Lifecycle)
**Issue:** Cannot distinguish the 3 engines in `pg_stat_activity`. Critical for debugging pool exhaustion and connection leaks.
**Fix:** Add `connect_args={"server_settings": {"application_name": "adcp-async"}}` etc. to each engine.
**Wave:** 4a
**Effort:** 0.25 day

### H6. No `engine.dispose()` in production lifespan shutdown
**Source:** DB-2 (Session Lifecycle)
**Issue:** `app_lifespan` at `src/app.py:48-54` has no graceful drain. Connections leak on deploy. All 3 engines need explicit dispose.
**Fix:** Add `await engine.dispose()` for async engine, `engine.dispose()` for sync engines in lifespan post-yield block.
**Wave:** 4a
**Effort:** 0.5 day

### H7. 45+ `server_default` columns stale under `expire_on_commit=False`
**Source:** DB-1 (Models)
**Issue:** `created_at`/`updated_at` on ~25 models, plus JSONB defaults on AdapterConfig, Product. Post-INSERT, these show `None` or Python-side defaults. Any code that reads these immediately after create gets wrong values.
**Fix:** Spike 7 audits all 45+ columns. For business-critical defaults, add `await session.refresh(instance)` after INSERT.
**Wave:** Spike 7 + Wave 4
**Effort:** 1 day (audit) + 0.5 day (fixes)

### H8. N+1 in admin `products.py:list_products`
**Source:** DB-6 (Data Integrity)
**Lines:** 454-466
**Issue:** Per-product ProductInventoryMapping query loop. 50 products = 50 extra queries.
**Fix:** Single batch query grouped by product_id, or `selectinload` on relationship.
**Wave:** Wave 3
**Effort:** 0.5 day

---

## Medium-Severity Issues

### M1. 25 of 37 models have NO repository (283-entry allowlist)
Under async, each of these inline DB access sites needs `await` wrapping — 283 call sites across 50+ files.

### M2. No advisory lock for multi-container migration races
Current retry at `migrate.py:55-66` is a band-aid. Need `pg_advisory_lock()`.

### M3. `Float` for money in GAMOrder/GAMLineItem
`total_budget` and `cost_per_unit` use IEEE 754 doubles — precision loss on reconciliation.

### M4. Lost update risk in `update_fields`/`update_status` methods
Read-then-update without `FOR UPDATE`. Under async concurrency, second UPDATE overwrites first.

### M5. Missing composite indexes
`(tenant_id, status)` on MediaBuy, `(tenant_id, timestamp DESC)` on AuditLog, GIN `pg_trgm` on `creatives.name`.

### M6. `asyncio.run()` in test harness will deadlock under async
`_run_mcp_client` and `_run_a2a_handler` in `_base.py` use `asyncio.run()`. Under pytest-asyncio, raises `RuntimeError: This event loop is already running`.

### M7. Dual `integration_db` implementations
`conftest_db.py` and `tests/fixtures/integration_db.py` both implement `integration_db` — must consolidate before async conversion.

### M8. No health endpoint checks DB
`/health` returns `{"status": "healthy"}` unconditionally. Pool status function exists but isn't wired to HTTP.

### M9. Missing CHECK constraints on status fields
`media_buys.status`, `creatives.status`, `workflow_steps.status` — nothing prevents garbage values at DB level.

### M10. asyncpg prepared statement cache pollution
Variable-length `IN (...)` clauses generate different prepared statements per list size. Fix: `ANY($1::text[])`.

### M11. `onupdate=func.now()` is Python-side only
Doesn't fire for bulk SQL operations or raw queries. `updated_at` goes stale silently.

### M12. No `render_item` hook for JSONType in Alembic env.py
Autogenerated migrations will break if `JSONType` import path changes.

---

## Key Recommendation: Keep Alembic env.py Sync

DB-4 strongly recommends: **do NOT rewrite env.py to async**. Alembic gains nothing from running async (migrations are serial, single-connection). All 161 migrations use sync patterns that work under `run_sync` greenlet. psycopg2 stays in the project anyway (D1/D2/D9). Keep env.py on psycopg2, add a comment explaining the decision. This eliminates Spike 6 scope and risk.

---

## Recommended Action Priority

### Pre-Wave-4 (before any async code lands)
1. **C2** Remove `CreativeRepository.commit()` (0.5 day)
2. **H3** Add `ondelete="CASCADE"` to 3 FKs via migration (0.5 day)
3. **H4** Document `max_connections=130` requirement OR reduce pool sizes (0.25 day)
4. **H8** Fix N+1 in products.py:list_products (0.5 day)
5. **M2** Add advisory lock to migrate.py (0.25 day)
6. **M7** Consolidate dual integration_db implementations (0.5 day)

### Wave 4a (foundational)
7. **C1** Fix statement_timeout for asyncpg via connect_args (0.25 day)
8. **C3** Eliminate 20+ `uow.session` direct-access sites (2 days)
9. **H1** Resolve Product @property lazy-load trap (3 days)
10. **H2** Convert 5 `backref=` to `back_populates=` (0.5 day)
11. **H5** Add `application_name` to all 3 engines (0.25 day)
12. **H6** Add `engine.dispose()` to lifespan shutdown (0.5 day)
13. **H7** Audit server_default columns (Spike 7 scope) (1 day)

### Wave 4b-5
14. **M1** Create repositories for top uncovered models (3 days)
15. **M4** Add `with_for_update()` to read-then-update patterns (0.5 day)
16. **M5** Add composite indexes via migration (0.5 day)
17. **M10** Switch IN(...) to ANY($1::text[]) (1 day)

### Post-v2.0
18. **M3** Fix Float money columns to DECIMAL (migration + data cast)
19. **M9** Add CHECK constraints for status fields
20. **M11** Add DB-level triggers for `updated_at`
21. Evaluate RLS for defense-in-depth tenant isolation
22. Design audit_logs partitioning strategy

---

## Cross-Audit Interaction Map

| Finding | Interacts with |
|---|---|
| C1 (statement_timeout crash) | Decision 1 sync engines unaffected, async engine only |
| C2 (CreativeRepo.commit) | Decision 7 ContextManager refactor (both touch creative code paths) |
| H1 (Product @property) | Spike 1 lazy-load audit will quantify exact blast radius |
| H4 (deploy connection budget) | Decision 9 pool math (sync-bridge 2+3 contributes to total) |
| H7 (server_default stale) | Spike 7 server_default audit (already planned, scope confirmed) |
| M6 (asyncio.run deadlock) | Decision 3 factory shim + Spike 4 test harness |
| M7 (dual integration_db) | Decision 3 Wave 4b/4c ordering |

---

## Audit Methodology

Each of the 6 subagents performed full file reads (not sampling) on their scoped files:
- DB-1: `models.py` (2143 lines full read), every relationship enumerated
- DB-2: `database_session.py` (465 lines), `db_config.py` (173 lines), `app.py` lifespan, `main.py` lifespan
- DB-3: All 11 repository files + `queries.py` + both structural guards
- DB-4: `alembic/env.py` (91 lines), 5 recent + 3 older migration files, both migration guards
- DB-5: `conftest_db.py` (537 lines), `_base.py` (915 lines), `integration_db.py`, factories, agent-db.sh, tox.ini
- DB-6: `models.py` constraints, `json_type.py` (115 lines), 3 complex repositories, `products.py` admin handler

Total lines read across all agents: ~8,000+ lines of production code + ~4,000+ lines of test infrastructure.
