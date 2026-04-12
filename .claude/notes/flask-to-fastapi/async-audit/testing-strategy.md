# Comprehensive Testing Strategy (2026-04-11)

> **Produced by:** 6 parallel Opus subagents with ultrathink, 2nd/3rd/4th-order derivative analysis
> **Scope:** Unit tests + structural guards, integration test infrastructure, E2E + admin UI, performance + benchmarks, BDD + behavioral, migration safety + rollback
> **Context:** Flask→FastAPI v2.0 migration with absorbed async SQLAlchemy, 6 waves (0-5), 10 spikes

---

## Executive Summary

The testing strategy spans **6 tiers** with **~6,000+ existing tests** as the regression safety net, **18 new structural guards**, **~100 new migration-specific tests**, **performance benchmarks** at 4 concurrency levels, **chaos/fault injection** for 7 failure modes, and **per-wave gate verification** with explicit entry/exit criteria.

**The existing test surface is massive:** 4,052 unit tests, 1,817 integration tests, ~120 E2E tests, 2,009 BDD scenarios (auto-parametrized across 4 transports). The migration must keep ALL of these green at every wave boundary while converting ~1,800 integration + BDD tests to async.

**The single highest-risk infrastructure change:** The `conftest.py` autouse fixture `mock_all_external_dependencies` patches `get_db_session` as a sync context manager for every unit test. Post-async, it must patch as an async context manager. This affects 4,052 tests implicitly.

---

## Test Tier Summary

| Tier | Existing | New | Total Post-Migration | Key Risk |
|---|---|---|---|---|
| Unit (domain + guards) | 4,052 functions, 22 guards | 65 migration tests, 18 guards | ~4,117 functions, 40 guards | conftest.py autouse fixture overhaul |
| Integration | 1,817 functions | ~35 migration-specific | ~1,852 functions | 1,817 async conversions via AST rewriter |
| E2E + Admin UI | ~133 functions | ~50 (flows + regression + smoke) | ~183 functions | Zero browser JS testing exists today |
| Performance | ~0 (no harness exists) | 20 route + 5 MCP benchmarks | 25 benchmark targets × 4 concurrency levels | Baseline capture in Spike 3 |
| BDD | 2,009 scenarios | 12 new scenarios | ~2,021 scenarios | asyncio.run() bridge in call_impl() |
| Migration Safety | 0 | ~30 (rollback + chaos + canary) | ~30 | Wave 3 Flask removal is irreversible |

---

## Tier 1: Unit Tests + Structural Guards

### 18 NEW Structural Guards

| # | Guard | Activation | Effort |
|---|---|---|---|
| 1 | Explicit `lazy=` on all relationships | Wave 4a (post-Spike-1) | 0.5d |
| 2 | No `scoped_session` in src/ | Wave 4a | 0.25d |
| 3 | No `session.query()` | Wave 4 | 0.25d |
| 4 | All admin routes have `name=` | Wave 1 | 0.5d |
| 5 | No `script_name`/`script_root` in templates | Wave 1 | 0.25d |
| 6 | No Flask imports in src/ | Wave 3 | 0.25d |
| 8 | Factory inherits async base | Wave 4c | 0.25d |
| 9 | Factory no `post_generation` | Wave 4b | 0.25d |
| 10 | Factory in ALL_FACTORIES | Wave 4b | 0.25d |
| 11 | No SSE handlers | Wave 4 | 0.25d |
| 12 | No runtime psycopg2 outside allowlist | Wave 4a | 0.25d |
| 13 | url_for calls resolve to registered routes | Wave 1 | 1d |
| 14 | No singleton session | Wave 4a | 0.5d |
| 15 | Sync-bridge scope | Wave 4a | 0.25d |
| 16 | Adapter calls wrapped in threadpool | Wave 4a | 0.5d |
| 17 | No Flask-caching imports | Wave 3 | 0.25d |
| 18 | Inventory cache uses module helpers | Wave 3 | 0.25d |

**Total new guard effort: ~5.5 days**

### 65 New Migration-Specific Unit Tests

| Test File | Count | Verifies | Wave |
|---|---|---|---|
| SimpleAppCache | 13 | Cache get/set/delete, TTL, thread-safety, NullAppCache | Wave 3 |
| Factory async shim | 8 | SubFactory chain, RelatedFactory, AccountFactory._create, error rollback | Spike 4.25 |
| Flash middleware | 8 | Session-based flash, categories, cross-request persistence | Wave 0 |
| CSRF middleware | 7 | SameSite, Origin validation, exempt paths | Wave 0 |
| admin_redirect | 5 | 302 default (not 307), Location header | Wave 0 |
| url_for wrapper | 6 | NoMatchFound catch, fallback, kwargs passthrough | Wave 0 |
| tojson filter | 5 | Dict serialization, indent kwarg, HTML escaping | Wave 0 |
| Session Jinja globals | 6 | session.*, g.test_mode, csrf_token() | Wave 0 |
| Accept-aware error handler | 7 | HTML for browser, JSON for fetch, */* handling | Wave 0 |

---

## Tier 2: Integration Tests

### Async Conversion: 4-Tier Migration

| Tier | Files | Functions | Strategy | Wave |
|---|---|---|---|---|
| 1: Harness-based | ~50 | ~900 | Mechanical AST rewrite (libcst) | 4b |
| 2: Repository tests | ~13 | ~200 | Same mechanism | 4b |
| 3: Legacy get_db_session | ~30 | ~400 | Manual conversion | 4c |
| 4: Non-DB integration | ~83 | ~300 | Mechanical def→async def | 4c |

### Decision Verification Tests (one per D1-D9)

| Decision | Test | Assertion |
|---|---|---|
| D1 Path B | Adapter runs in threadpool, uses sync session | Thread ID differs from event loop thread |
| D2 KEEP | DatabaseConnection uses raw psycopg2, fork-safe | No SQLAlchemy engine touched |
| D3 Shim | Factory SubFactory chain creates all entities | All rows visible in AsyncSession query |
| D4 Prune | 3 async query functions return correct data | Results match expected |
| D5 DELETE | list_products renders, product_pricing ImportError | Template renders, module gone |
| D6 Cache | get/set/delete + thread-safety + invalidation | Concurrent access safe, background sync clears |
| D7 DELETE | Stateless async context functions work | ContextManager import raises ImportError |
| D8 DELETE | /activity returns JSON, /events returns 404 | Poll works, SSE gone |
| D9 Bridge | Async write visible to sync read (MVCC) | Both engines see each other's commits |

---

## Tier 3: E2E + Admin UI

### 10 Critical User Flow Tests

Login (Google OAuth + test login), tenant selection, product CRUD, media buy lifecycle, creative review, inventory sync, settings update, user management, logout, unauthenticated access (HTML redirect for browser, JSON 401 for API).

### 11 Migration Regression Tests

302 vs 307 redirect, no duplicate form submission, CSRF skips OAuth, Approximated skips OAuth, trailing-slash GET redirect, trailing-slash POST preserves body, Accept-aware HTML for browser, Accept-aware JSON for fetch, static files after git mv, tojson indent, no duplicate adapter routes.

### 13-Test Deployment Smoke Suite

Health, login page, test auth, dashboard, products, settings, API JSON, MCP health, A2A agent card, static CSS, flash message, error page HTML, unauthenticated redirect. Runs in ~30s post-deploy.

### Browser Testing (Playwright)

Only 4 tests strictly require Playwright: activity polling, inventory tree expand, product AJAX submit, settings AJAX save. All others use httpx.AsyncClient/TestClient.

---

## Tier 4: Performance + Benchmarks

### Benchmark Targets

20 admin routes (trivial → high complexity) + 5 MCP tools at 4 concurrency levels (1/10/50/200 req/s). Tool: Locust (HTTP load) + pytest-benchmark (micro-profiling).

### Wave Exit Criteria

- **Wave 4**: ±5% p95 at 50 req/s vs sync baseline
- **Wave 5**: async ≥20% faster at 200 req/s, or sync saturates while async doesn't

### Query Count Instrumentation

SQLAlchemy `before_cursor_execute` event listener + ContextVar. Per-route query count baseline in Spike 3. Gate: no route's query count increases >20% (detects N+1 regressions from eager-load additions).

### `/health/pool` Endpoint

Per-engine metrics (async/sync_adapter/sync_bridge): pool_size, checked_in, checked_out, overflow, timeout_errors. Alerting: checked_out >80% = warning, timeout_errors >0 = critical.

---

## Tier 5: BDD + Behavioral

### Strategy: Keep Steps Sync + asyncio.run() Bridge

pytest-bdd doesn't support async step functions. When `_impl` becomes `async def`, each harness `call_impl()` changes to `return asyncio.run(_foo_impl(...))` — ~1-line per env class. Already battle-tested in MCP/A2A dispatch paths.

### Existing Safety Net

2,009 scenarios across 29 feature files, auto-parametrized across 4 transports. Release gate: pass count must equal or exceed pre-migration baseline.

### 12 New Scenarios

6 business-preservation (product pricing, media buy lifecycle, OAuth login, multi-tenant isolation, creative review, webhook delivery) + 6 migration-specific (lazy-load, threadpool, engine isolation, session, N+1, compat).

---

## Tier 6: Migration Safety + Rollback

### Pre-Wave Gates

| Wave | Key Gate | Est. Time |
|---|---|---|
| Pre-Wave-0 | 10 spikes pass (Spike 1 HARD, rest SOFT) | 5.5-7.5 days |
| Pre-Wave-1 | make quality green, 16 guards pass, codemod idempotent | ~15 min |
| Pre-Wave-2 | 4 routers ≥90% coverage, real OAuth staging smoke | ~25 min + staging |
| Pre-Wave-3 | Flask catch-all zero traffic 48h, v1.99.0 tag | ~30 min + 48h monitor |
| Pre-Wave-4 | rg -w flask = 0, Docker builds, Playwright green | ~20 min + 48h canary |
| Pre-Wave-5 | All async tests green, M1-M9 landed, pool stats healthy | ~35 min + 24h staging |

### Rollback Verification

| Wave | Reversible? | Method | DB Impact |
|---|---|---|---|
| 0 | Yes | git revert | None |
| 1 | Yes | git revert | None |
| 2 | Yes | git revert or partial (single router) | None |
| 3 | **Dangerous** | git revert + uv lock + docker build, OR v1.99.0 image | None (git-tracked files) |
| 4 | Costly | Redeploy v1.99.0; Alembic downgrade for FK changes | 3 FK constraint additions (reversible) |
| 5 | Yes | git revert | None |

### 7 Chaos/Fault Injection Scenarios

DB connection lost mid-request, DB connection lost during background sync, pool exhaustion, slow query (>30s statement_timeout), container OOM, missing env var, asyncpg codec crash.

### Production Canary Suite

OAuth callbacks (P1 alert), all 197 admin pages (block next wave), API JSON shapes, background sync cycle, MCP tool calls, A2A handler, health endpoints (P1 alert), session cookie sanity, template rendering.

---

## Complete Test Matrix (Wave × Tier)

| Tier | Spikes | Wave 0 | Wave 1 | Wave 2 | Wave 3 | Wave 4 | Wave 5 |
|---|---|---|---|---|---|---|---|
| Unit guards | N/A | 16 new | +3 active | allowlist shrink | Flask allowlist=0 | +10 async guards | final state |
| Unit domain | 5 pilot | foundation smoke | auth/CSRF | per-router | SSE/cache | ALL async | benchmark |
| Integration | lazy="raise" | unchanged | middleware | error shapes | proxy-header | ALL async | async benchmark |
| E2E | N/A | N/A | Playwright login | Playwright CRUD | Playwright full | Playwright async | final regression |
| BDD | N/A | unchanged | unchanged | unchanged | unchanged | asyncio.run bridge | unchanged |
| Performance | baseline capture | N/A | N/A | N/A | N/A | ±5% at 50 req/s | ≥20% at 200 |
| Chaos | 2-engine test | N/A | N/A | N/A | N/A | pool/disconnect | full suite |
| Deploy | spike branch | revert test | revert test | partial revert | v1.99.0 redeploy | alembic roundtrip | v2.0.0 tag |

---

## Effort Summary

| Category | New Tests | New Guards | Effort |
|---|---|---|---|
| Unit migration tests | 65 | — | ~5 days |
| Structural guards | — | 18 | ~5.5 days |
| Integration migration tests | ~35 | — | ~3 days |
| Integration async conversion (1,817 tests) | — | — | ~8-12 days |
| E2E + admin UI | ~50 | — | ~5 days |
| Performance harness | 25 targets × 4 levels | — | ~3 days |
| BDD new scenarios | 12 | — | ~2 days |
| Migration safety + chaos | ~30 | — | ~3 days |
| **Total** | **~217 new tests** | **18 guards** | **~35-40 person-days** |

Testing infrastructure work is ~35-40 person-days spread across Waves 0-5, with the heaviest concentration in Wave 4 (async conversion of 1,817 integration tests).
