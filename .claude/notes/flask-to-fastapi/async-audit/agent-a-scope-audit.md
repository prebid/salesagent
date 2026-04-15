# Agent A — Async Conversion Scope Audit

> **[ARCHIVED REFERENCE — 2026-04-14]** This report is a preserved artifact from the 3-round verification process (Apr 11-14) that produced the v2.0 8-layer execution model. For current implementation guidance, see:
> - `../CLAUDE.md` — mission briefing + 8-layer model
> - `../execution-plan.md` — layer-by-layer work items
> - `../implementation-checklist.md` — per-layer gate checklist
>
> This file is preserved for institutional memory only. Its recommendations have been absorbed into the canonical docs above. Do NOT use this file as a primary reference for implementation decisions.

**Date:** 2026-04-11
**Author:** Agent A (Opus scoping subagent, post-pivot)
**Context:** Pre-Wave-0 audit for the Flask→FastAPI v2.0 pivot that absorbs full async SQLAlchemy. See `../async-pivot-checkpoint.md` for the pivot directive.
**Scope:** File-by-file action list + lazy-load audit (Risk #1). Does not cover the other 14 risks (Agent B) or plan-file propagation edits (Agent C).

---

## 1. Executive summary

**Feasibility verdict: ABSORBED-ASYNC v2.0 IS FEASIBLE BUT LARGE. Recommendation: CAUTIOUS GO with mandatory pre-Wave-0 spike and a v2.1 fallback plan.**

The checkpoint's LOC estimate (10-15k LOC added) is roughly correct and possibly light. The primary risk (lazy loading — Risk #1) is **manageable but nontrivial**: the codebase has ~129 relationship-traversal sites, most of which are either (a) safe because they use Pydantic schema models (not ORM), (b) safe because they use the repository/UoW pattern and only touch repository method results, or (c) safe because they happen inside `get_db_session()` blocks.

**The concentration of lazy-load risk is small and well-scoped:**
- ~50 `tenant.adapter_config.*` accesses in admin blueprints (Settings/Inventory/Principals/Publisher Partners)
- 5 `@property` methods on `Product`/`Tenant` that touch relationships without being safe (`effective_format_ids`, `effective_properties`, `effective_property_tags`, `effective_implementation_config`, `is_gam_tenant`)
- 2 helper functions (`get_tenant_config_from_db` in admin/utils, and similar)
- Template rendering from admin handlers — the 66 `render_template()` sites need audit, but sampling shows templates mostly access scalar columns on ORM objects (not relationships), so many require no change

**Primary concerns:**
1. **Repository pattern adoption is excellent** — ~328 `get_db_session()` production call sites, and SQL-level structural guards prevent new `session.query()` usage. Most `_impl` functions (12 of 15) are already architected around UoW/Repository. This is why the pivot is feasible — the repository layer is the natural async conversion point.
2. **`factory-boy` has no native async support** — Test harness rewrite will be larger than the checkpoint estimates (3-4 days, not 2).
3. **A silent latent bug already exists** in `src/routes/api_v1.py`: 8 of 13 REST routes are `async def` wrappers calling **sync** `_raw()` helpers without `await`. This is the scoped_session interleaving bug. Under the pivot, these all become proper awaits — this is the checkpoint's "Risk #15 WIN" finding, confirmed.
4. **~166 integration tests** use `integration_db` + `with get_db_session()` bodies. Mechanical async conversion is ~2 days of scripted refactoring plus ~2 days of debugging.

**Total refined scope estimate: ~11,500-14,000 additional LOC modified or added** (slightly below checkpoint's 10-15k upper bound). **Total timeline: 4-6 weeks with the pre-Wave-0 spike and a conservative 20% buffer.**

**GO/NO-GO signal:** GO, provided the Pre-Wave-0 spike (see §6) demonstrates that the 5 `@property` lazy loads and ~50 admin `adapter_config` accesses can be converted mechanically via `selectinload` additions, not architectural surgery. If the spike uncovers any unexpected ORM-relationship-access-after-commit patterns (particularly in schedulers or long-lived context managers), fall back to the v2.0 sync-def resolution and defer async to v2.1.

---

## 2. File inventory — what changes and how much

### 2.1 Database layer (CORE — required)

| File | Current state | Change required | Est LOC delta | Risk |
|---|---|---|---|---|
| `src/core/database/database_session.py` | Sync `create_engine` + `scoped_session(sessionmaker)` + sync `@contextmanager get_db_session` + `DatabaseManager` context manager class + health check (465 LOC) | **Full rewrite**: `create_async_engine`, `async_sessionmaker(class_=AsyncSession, expire_on_commit=False)`, `@asynccontextmanager async def get_db_session` → `AsyncIterator[AsyncSession]`. Delete `scoped_session` entirely. Convert `DatabaseManager` to async context manager. Keep `reset_engine`/`reset_health_state` shapes. Rewrite `execute_with_retry` to async. Rewrite `check_database_health` to async (still called sync from health endpoint — needs `asyncio.run` or an async wrapper). Connection string gets `postgresql+asyncpg://` prefix rewrite at engine construction. | +200 / -120 net | **HIGH** |
| `src/core/database/db_config.py` | `DatabaseConnection` class uses `psycopg2.connect` directly (unused in runtime, kept for tooling) | Either delete `DatabaseConnection` + `get_db_connection()` (they are dead production code — verified via grep, only used by themselves) OR preserve for `scripts/ops/*` tools. Recommend: **DELETE** (they are not imported by any runtime code path). | -70 | LOW |
| `src/core/database/json_type.py` | Uses `psycopg2` in a comment only; `impl = JSONB(none_as_null=True)` — uses SQLAlchemy dialect type | **NO CODE CHANGE.** JSONB type is driver-agnostic at SQLAlchemy level. `asyncpg` auto-deserializes JSONB to `dict`/`list` identical to psycopg2's JSONB codec, so `process_result_value` receives the same shape. **Verify with a smoke test during the spike.** | 0 | LOW (verify) |
| `src/core/database/models.py` | 2143 LOC, all `Mapped[]` typed columns (537 decls), 58 `relationship()` definitions, no explicit `lazy=` settings (all default `lazy="select"`) | **Possibly zero change** to column/relationship declarations (the typing is already async-compatible), BUT the 5 risky `@property` methods must either: (a) get `async` variants alongside the sync ones, (b) be rewritten as `_resolve_effective_*(session)` repository methods, or (c) have their underlying relationships loaded eagerly wherever the property is accessed. See §4 lazy-load audit. | +50 to +300 depending on approach | **HIGH** |
| `src/core/database/database.py` | `init_db` uses sync engine | Convert to async; called once at startup (sync entry point). Wrap with `asyncio.run()`. | +20 | LOW |
| `src/core/database/health_check.py` | Sync health check helper | Convert to async | +15 | LOW |
| `src/core/database/media_package_utils.py` | Sync helper, 1 `get_db_session()` use | Convert to async helper OR make it repository method | +5 | LOW |
| `src/core/database/repositories/*.py` (11 files, 3,087 LOC total) | Sync repositories all using `session.scalars(...)` | **Full rewrite to async**: replace every `self._session.scalars(...)` with `result = await self._session.execute(stmt); result.scalars()`, every `self._session.flush()` with `await self._session.flush()`, etc. All repository methods become `async def`. UoW `__enter__/__exit__` become `__aenter__/__aexit__`. | ~+1,500 (each file gains await keywords + method signature changes) | **HIGH** |
| `src/core/database/repositories/uow.py` | Sync `BaseUoW` with 7 concrete UoW classes (`MediaBuyUoW`, `ProductUoW`, `WorkflowUoW`, `TenantConfigUoW`, `AccountUoW`, `CreativeUoW`, `AdminCreativeUoW`) | Add `__aenter__`/`__aexit__`. Can keep sync methods alongside as deprecated or delete them. Remove `warnings.warn` on `.session` property. | +100 | MEDIUM |
| `src/core/database/queries.py` | 282 LOC; **6** sync functions (corrected from 7 per D4 deep-think) taking `session` as parameter, each uses `session.scalars(...)` / `session.execute(...)`. **3 are dead code** (zero callers). **Zero production callers** — only consumer is `tests/integration/test_creative_review_model.py`. | D4 Option 4A: delete 3 dead functions (~−158 LOC), convert 3 live to async, convert test file. Net: **−100 LOC** (not +50). | −100 | LOW |

**Per-repository LOC snapshot:**

| Repository file | LOC |
|---|---|
| `src/core/database/repositories/media_buy.py` | 525 |
| `src/core/database/repositories/creative.py` | 476 |
| `src/core/database/repositories/workflow.py` | 296 |
| `src/core/database/repositories/uow.py` | 286 |
| `src/core/database/repositories/delivery.py` | 276 |
| `src/core/database/repositories/account.py` | 273 |
| `src/core/database/repositories/adapter_config.py` | 175 |
| `src/core/database/repositories/product.py` | 174 |
| `src/core/database/repositories/tenant_config.py` | 57 |
| `src/core/database/repositories/__init__.py` | 41 |
| `src/core/database/repositories/currency_limit.py` | 37 |

**Database layer subtotal: ~+1,800 to +2,200 LOC modified/added, -190 deleted**

### 2.2 Driver change (pyproject.toml)

| Change | Risk |
|---|---|
| Remove `psycopg2-binary>=2.9.9` (line 19) | LOW |
| Remove `types-psycopg2>=2.9.21.20251012` (line 74 + line 101 — duplicated in two dev groups) | LOW |
| Add `asyncpg>=0.30.0` | LOW |
| Add `asyncpg-stubs>=0.30.0` (for mypy) | LOW |
| `greenlet` should already be pulled in transitively by SQLAlchemy; verify explicit pin if needed | LOW |

**psycopg2 usage inventory (to remove):**
- `src/core/database/db_config.py` — `DatabaseConnection` class uses `psycopg2.connect` (DEAD CODE, delete)
- `src/core/database/json_type.py` — comment reference only
- `src/admin/blueprints/tenants.py:130` — literal string in an error-masking blocklist, not an import
- `tests/conftest_db.py` — `psycopg2.connect` for `CREATE DATABASE` bootstrap in `integration_db` fixture (can stay as a test-only sync helper OR swap to `asyncpg.connect`)

### 2.3 `_impl` function table (15 functions)

All sync `_impl` functions already use UoW internally, so the conversion is mechanical. Every wrapper (MCP, A2A, REST) changes its call site from `fn(...)` to `await fn(...)`.

| `_impl` function | Current state | Top-level MCP wrapper | Raw (A2A) wrapper | REST route | Priority |
|---|---|---|---|---|---|
| `_get_products_impl` (`src/core/tools/products.py:145`) | `async def` ✅ | `async def get_products` ✅ | `async def get_products_raw` ✅ | `await` ✅ | Already done |
| `_get_adcp_capabilities_impl` (`src/core/tools/capabilities.py:66`) | `def` (sync) | `async def get_adcp_capabilities` ✅ (top wrapper) | `async def get_adcp_capabilities_raw` ✅ | `await` ✅ | Convert impl + drop bugs |
| `_list_creative_formats_impl` (`src/core/tools/creative_formats.py:107`) | `def` (sync) | `async def list_creative_formats` ✅ | `def list_creative_formats_raw` ❌ | **no await (BUG)** | Convert all |
| `_list_accounts_impl` (`src/core/tools/accounts.py:113`) | `def` (sync) | `async def list_accounts` ✅ | `def list_accounts_raw` ❌ | **no await (BUG)** | Convert all |
| `_sync_accounts_impl` (`src/core/tools/accounts.py:424`) | `async def` ✅ | `async def sync_accounts` ✅ | `async def sync_accounts_raw` ✅ | `await` ✅ | Already done |
| `_list_creatives_impl` (`src/core/tools/creatives/listing.py:37`) | `def` (sync) | `async def list_creatives` ✅ | `def list_creatives_raw` ❌ | **no await (BUG)** | Convert all |
| `_sync_creatives_impl` (`src/core/tools/creatives/_sync.py:29`) | `def` (sync) | `async def sync_creatives` ✅ | `def sync_creatives_raw` ❌ | **no await (BUG)** | Convert all |
| `_list_authorized_properties_impl` (`src/core/tools/properties.py:31`) | `def` (sync) | `async def list_authorized_properties` ✅ | `def list_authorized_properties_raw` ❌ | **no await (BUG)** | Convert all |
| `_create_media_buy_impl` (`src/core/tools/media_buy_create.py:1270`) | `async def` ✅ | `async def create_media_buy` ✅ | `async def create_media_buy_raw` ✅ | `await` ✅ | Already done |
| `_update_media_buy_impl` (`src/core/tools/media_buy_update.py:117`) | `def` (sync) | `async def update_media_buy` ✅ | `def update_media_buy_raw` ❌ | **no await (BUG)** | Convert all |
| `_get_media_buy_delivery_impl` (`src/core/tools/media_buy_delivery.py:67`) | `def` (sync) | `async def get_media_buy_delivery` ✅ | `def get_media_buy_delivery_raw` ❌ | **no await (BUG)** | Convert all |
| `_get_media_buys_impl` (`src/core/tools/media_buy_list.py:78`) | `def` (sync) | `async def get_media_buys` ✅ | `def get_media_buys_raw` ❌ | (no REST endpoint) | Convert impl + wrappers |
| `_update_performance_index_impl` (`src/core/tools/performance.py:30`) | `def` (sync) | `async def update_performance_index` ✅ | `def update_performance_index_raw` ❌ | **no await (BUG)** | Convert all |
| `_get_signals_impl` (`src/core/tools/signals.py:42`) | `async def` ✅ | (no MCP tool registered for signals yet) | `async def get_signals_raw` ✅ | (no REST endpoint) | Already done |
| `_activate_signal_impl` (`src/core/tools/signals.py:193`) | `async def` ✅ | (no MCP tool registered for signals yet) | `async def activate_signal_raw` ✅ | (no REST endpoint) | Already done |

**Total `_impl` functions to convert from sync to async: 10** (`_get_adcp_capabilities_impl`, `_list_creative_formats_impl`, `_list_accounts_impl`, `_list_creatives_impl`, `_sync_creatives_impl`, `_list_authorized_properties_impl`, `_update_media_buy_impl`, `_get_media_buy_delivery_impl`, `_get_media_buys_impl`, `_update_performance_index_impl`).

**Also `src/core/tools/task_management.py`** — `list_tasks`/`get_task`/`complete_task` are already `async def` at the top but call sync UoW methods inside. Need to add `await` before UoW method calls after UoW is async. No signature changes.

**Latent bug fix (Risk #15):** 8 REST routes in `src/routes/api_v1.py` lines 196-360 call `*_raw()` helpers without `await`:

```
Line 200: response = creative_formats_module.list_creative_formats_raw(identity=identity)      # no await
Line 214: response = properties_module.list_authorized_properties_raw(identity=identity)       # no await
Line 252: response = media_buy_update_module.update_media_buy_raw(...)                         # no await
Line 284: response = media_buy_delivery_module.get_media_buy_delivery_raw(...)                 # no await
Line 305: response = creatives_sync_module.sync_creatives_raw(...)                             # no await
Line 324: response = creatives_listing_module.list_creatives_raw(...)                          # no await
Line 342: response = performance_module.update_performance_index_raw(...)                      # no await
Line 360: response = accounts_module.list_accounts_raw(req=req, identity=identity)             # no await
```

Confirmed awaited sites:
```
Line 175: response = await products_module._get_products_impl(req, identity)                   # await ✓
Line 188: response = await capabilities_module.get_adcp_capabilities_raw(identity=identity)    # await ✓
Line 230: response = await media_buy_create_module.create_media_buy_raw(...)                   # await ✓
Line 374: response = await accounts_module.sync_accounts_raw(req=req, identity=identity)       # await ✓
```

This works today ONLY because the raw functions are sync. Once they become async, the missing `await` becomes a runtime `TypeError: object coroutine can't be awaited` or similar. The fix is mechanical: add `await` before each call. **Do this in the same PR that converts the corresponding `_impl` function.**

**A2A layer — similar pattern at `src/a2a_server/adcp_a2a_server.py`:**
```
Line 1405: response = await core_get_products_tool(...)                     # await ✓
Line 1501: response = await core_create_media_buy_tool(...)                 # await ✓
Line 1558: response = core_sync_creatives_tool(...)                         # no await — becomes bug
Line 1587: response = core_list_creatives_tool(...)                         # no await — becomes bug
Line 1774: response = core_list_creative_formats_tool(...)                  # no await — becomes bug
Line 1798: return core_list_accounts_tool(req=request, identity=identity)   # no await — becomes bug
Line 1813: return await core_sync_accounts_tool(...)                        # await ✓
Line 1842: response = core_list_authorized_properties_tool(...)             # no await — becomes bug
Line 1892: response = core_update_media_buy_tool(...)                       # no await — becomes bug
Line 1961: response = core_get_media_buy_delivery_tool(...)                 # no await — becomes bug
Line 2000: response = core_update_performance_index_tool(...)               # no await — becomes bug
Line 2029: response = await core_get_products_tool(...)                     # await ✓
```

8 A2A handler sites need `await` added in the same PR as the corresponding `_impl` conversion.

**`_impl` layer subtotal: ~+400 LOC net (mostly `await` additions + function signature changes + UoW call rewrites inside existing function bodies).**

### 2.4 Supporting core layer

| File | Current state | Change required | Est LOC | Risk |
|---|---|---|---|---|
| `src/core/auth.py` | Sync `get_principal_object`, `get_principal_adapter_mapping` using `get_db_session()` | Convert to `async def`. **21 call sites across 9 `_impl` files** (products, signals, performance, media_buy_*, capabilities). Each becomes `await get_principal_object(...)`. | +60 | MEDIUM |
| `src/core/config_loader.py` | 5 `get_db_session()` usages in `get_tenant_by_subdomain`, `get_tenant_by_id`, `get_tenant_by_virtual_host`, `ensure_default_tenant_exists`, `load_config` | Convert all to `async def`. Uses ContextVar for current tenant — ContextVar propagation under asyncio is per-task, not per-thread. Verify `set_current_tenant`/`get_current_tenant` still work from async context. | +80 | MEDIUM |
| `src/core/audit_logger.py` | 5 `get_db_session()` usages | Convert to async. Called from many places (9+ files). | +40 | MEDIUM |
| `src/core/tenant_status.py` | 2 `get_db_session()` usages | Convert to async | +15 | LOW |
| `src/core/format_resolver.py` | 1 `get_db_session()` use | Convert to async | +10 | LOW |
| `src/core/signals_agent_registry.py` | 1 `get_db_session()` use | Convert to async | +10 | LOW |
| `src/core/creative_agent_registry.py` | 1 `get_db_session()` use | Convert to async | +10 | LOW |
| `src/core/strategy.py` | 5 `get_db_session()` usages (`StrategyManager`) | Convert to async; used by many `_impl` functions | +40 | MEDIUM |
| `src/core/webhook_delivery.py` | 3 `get_db_session()` usages | Convert to async | +20 | LOW |
| `src/core/helpers/adapter_helpers.py` | 1 `get_db_session()` use | Convert to async | +10 | LOW |
| `src/core/helpers/activity_helpers.py` | 1 `get_db_session()` use | Convert to async | +10 | LOW |
| `src/core/utils/tenant_utils.py` | 1 `get_db_session()` use | Convert to async | +10 | LOW |

**Core layer subtotal: ~+315 LOC**

### 2.5 Services layer (schedulers + background jobs)

| File | Current state | Change required | Est LOC | Risk |
|---|---|---|---|---|
| `src/services/delivery_webhook_scheduler.py` | `async def _run_scheduler` + `async def _send_reports` with `with get_db_session() as session:` inside (lines 92, 134) | Change `with` to `async with`; add `await` before `session.execute` / repository methods; `MediaBuyRepository.get_all_by_statuses` becomes `async`. Scheduler task creation shape unchanged (already `asyncio.create_task`). | +40 | MEDIUM |
| `src/services/media_buy_status_scheduler.py` | Similar to above, 1 `get_db_session()` use at line 83 in scheduler tick | Same pattern | +30 | MEDIUM |
| `src/services/background_approval_service.py` | Sync background | Convert to async | +20 | LOW |
| `src/services/background_sync_service.py` | 9 `get_db_session()` uses, multi-hour `threading.Thread` GAM sync workers | **Decision 9 (2026-04-11): NOT converted to async — sync-bridge instead.** New `src/services/background_sync_db.py` module exposes `get_sync_db_session()` backed by a separate sync psycopg2 engine (pool 2+3, statement_timeout=600s). Background threads stay sync; async request path is untouched. Bundles Wave 3 flask-caching correction (line 472 `from flask import current_app` ImportError → `SimpleAppCache` helper). Validated by Spike 5.5. Structural guard `test_architecture_sync_bridge_scope.py` allowlist contains ONLY this file. Sunset v2.1+. | +200 (new module) +30 (call site updates) | **HIGH (mitigated)** |
| `src/services/order_approval_service.py` | 7 `get_db_session()` uses | Convert to async | +70 | MEDIUM |
| `src/services/protocol_webhook_service.py` | 1 `get_db_session()` use | Convert to async | +10 | LOW |
| `src/services/webhook_delivery_service.py` | 1 `get_db_session()` use | Convert to async | +10 | LOW |
| `src/services/dynamic_products.py` | 2 `get_db_session()` uses | Convert to async | +15 | LOW |
| `src/services/dynamic_pricing_service.py` | Accesses `product.pricing_options.append` — relationship mutation | Verify it's inside session scope | +10 | MEDIUM |
| `src/services/delivery_simulator.py` | 1 `get_db_session()` use | Convert to async | +10 | LOW |
| `src/services/auth_config_service.py` | **10** `get_db_session()` uses — largest in services/ | Convert to async | +80 | MEDIUM |
| `src/services/property_discovery_service.py` | 1 use | Convert to async | +10 | LOW |
| `src/services/property_verification_service.py` | 2 uses | Convert to async | +15 | LOW |
| `src/services/format_metrics_service.py` | 1 use | Convert to async | +10 | LOW |
| `src/services/policy_service.py` | 2 uses | Convert to async | +15 | LOW |
| `src/services/setup_checklist_service.py` | 2 uses | Convert to async | +15 | LOW |
| `src/services/gcp_service_account_service.py` | 3 uses | Convert to async | +20 | LOW |

**Services layer subtotal: ~+610 LOC** (was +460 pre-Decision-9; +150 LOC delta for `background_sync_db.py` new module + sync-bridge call-site rewiring at 9 sites in `background_sync_service.py`. The original +80 LOC "full async conversion" is replaced by +30 LOC of sync-session swap because adapter calls and DB writes inside the worker stay sync.)

### 2.6 Adapters layer

**⚠️ Decision 1 RESOLVED 2026-04-11 — Path B (sync adapters + threadpool wrap).** The original §2.6 estimate below (+345 LOC, +100 LOC HIGH on `google_ad_manager.py`) assumed full async conversion with the cascading async/await contagion. **That is no longer the plan.** Adapters stay sync `def`. The 18 adapter call sites in `src/core/tools/*.py` (and 1 in `src/admin/blueprints/operations.py:252`) wrap in `await run_in_threadpool(...)`. Adapter internals continue using `get_sync_db_session()` from the dual session factory. `AuditLogger.log_operation` splits into `_log_operation_sync` (used inside worker threads) + async public wrapper. Threadpool tuned to 80 via `anyio.to_thread.current_default_thread_limiter().total_tokens` in lifespan startup. Structural guard `test_architecture_adapter_calls_wrapped_in_threadpool.py` prevents drift. Full implementation reference: `flask-to-fastapi-foundation-modules.md` §11.14. Full target state: `async-pivot-checkpoint.md` §3 "Adapters (Decision 1 Path B)". The table below is **kept for traceability** of the original deep-audit but the LOC deltas are stale — the corrected scope is at the end of this section.

**Verdict (pre-Decision-1):** adapters largely use **Pydantic schema Principal** (not ORM), not ORM relationships. Most `self.principal.name`, `self.principal.platform_mappings` accesses are safe. But several adapter files use `get_db_session()` for workflow/audit persistence.

| File | Current state | Change required | Est LOC | Risk |
|---|---|---|---|---|
| `src/adapters/base.py` | Base class | No DB access — safe | 0 | LOW |
| `src/adapters/base_workflow.py` | 1 `get_db_session()` use | Convert to async | +15 | LOW |
| `src/adapters/google_ad_manager.py` | 7 `get_db_session()` uses | Convert to async. Adapter methods are SYNC today — they need to become `async def` to await DB. **This cascades: every adapter caller must await.** | +100 | **HIGH** |
| `src/adapters/gam_reporting_api.py` | 8 `get_db_session()` uses | Convert to async | +80 | MEDIUM |
| `src/adapters/gam/managers/orders.py` | 1 use | Convert to async | +10 | LOW |
| `src/adapters/gam/managers/targeting.py` | 3 uses | Convert to async | +20 | LOW |
| `src/adapters/gam/managers/workflow.py` | 5 uses | Convert to async | +35 | LOW |
| `src/adapters/mock_ad_server.py` | 3 uses | Convert to async | +25 | LOW |
| `src/adapters/kevel.py` | 0 DB uses, Pydantic-only principal access | Adapter methods may still need `async def` to match base class, even without DB access | +5 | LOW |
| `src/adapters/xandr.py` | 1 use | Convert to async | +10 | LOW |
| `src/adapters/broadstreet/adapter.py` | 5 uses | Convert to async | +40 | LOW |
| `src/adapters/triton_digital.py` | 0 DB uses (mostly Pydantic), need async method signatures | +5 | LOW |

**Adapters subtotal: ~+345 LOC**

**IMPORTANT (pre-Decision-1, STALE):** The adapter base class interface (`src/adapters/base.py`) defines methods like `create_media_buy`, `get_media_buy_delivery`, etc. These are currently **sync**. Converting them to `async def` is a **large-surface contract change**:
- ~20 methods on `AdServerAdapter` base class
- Every caller (`_impl` functions, schedulers, admin blueprints that use adapters directly) must `await` adapter calls

However, converting the adapter base class is **required** because once the DB layer is async, adapter methods that touch the DB must also be async, and the async/await contagion propagates through the call graph.

**CORRECTED scope (Decision 1 Path B, 2026-04-11):**

| File | Decision 1 disposition | Est LOC | Risk |
|---|---|---|---|
| `src/adapters/base.py` | **Stays sync.** No interface change. | 0 | LOW |
| `src/adapters/base_workflow.py` | Stays sync. Internal `get_db_session()` swapped to `get_sync_db_session()` (1-line import). | +5 | LOW |
| `src/adapters/google_ad_manager.py` | Stays sync. 7 `get_db_session()` → `get_sync_db_session()`. **No method-signature changes.** | +15 | LOW (was HIGH) |
| `src/adapters/gam_reporting_api.py` | Stays sync. 8 swaps. | +15 | LOW (was MEDIUM) |
| `src/adapters/gam/managers/*.py` | Stays sync. 9 swaps across 3 files. | +20 | LOW |
| `src/adapters/mock_ad_server.py` | Stays sync. 3 swaps. **`threading.Thread` background path stays sync** (the `mock_ad_server.py threading.Thread → asyncio.create_task` conversion in Decision 7 is for a different code path inside `ContextManager` callers, NOT the adapter's mock-task scheduler). | +10 | LOW |
| `src/adapters/{kevel,xandr,broadstreet/adapter,triton_digital}.py` | Stay sync. 0-5 swaps each. | +20 total | LOW |
| **NEW: 18 adapter call sites in `src/core/tools/*.py` + 1 in `src/admin/blueprints/operations.py:252`** | Wrap each `self.adapter.method(...)` in `await run_in_threadpool(...)`. Use `functools.partial` when kwargs are needed (anyio's `to_thread` doesn't accept `**kwargs`). | +90 (≈5 LOC × 18 sites) | MEDIUM (mechanical but easy to miss a site → structural guard) |
| **NEW: `database_session.py` dual factory (async + sync)** | Add `get_sync_db_session()` alongside async `get_db_session()`. Separate engine, pool 5+10, 30s statement_timeout, `application_name='adcp-salesagent-sync-pathb'`. | +60 | LOW |
| **NEW: `AuditLogger.log_operation` split** | `_log_operation_sync` (internal, used in worker threads) + async public wrapper using `run_in_threadpool`. | +25 | LOW |
| **NEW: lifespan startup threadpool tune** | `anyio.to_thread.current_default_thread_limiter().total_tokens = int(os.environ.get("ADCP_THREADPOOL_SIZE", "80"))` | +5 | LOW |
| **NEW: structural guard** `test_architecture_adapter_calls_wrapped_in_threadpool.py` | AST-walk every `self.adapter.X(...)` call site; require enclosing `await run_in_threadpool(...)`. Allowlist only the 18 known sites. | +120 (test file) | LOW |

**Adapters subtotal (corrected): ~+385 LOC** (was ~+345 LOC under full async; the +40 LOC delta is the dual factory + AuditLogger split + threadpool tune + structural guard, OFFSET BY removing the ~+165 LOC of adapter method-signature `def → async def` rewrites that no longer happen). The shape of the work is fundamentally different — fewer files touched at the adapter layer, more files touched at the call-site layer.

**Why Path B over full async:**

1. **`googleads==49.0.0` is sync-only** (depends on `suds-py3`, no async port). Full async requires forking/replacing the GAM SDK or rewriting on top of `aiohttp` directly — ~1500 LOC, zero AdCP-visible benefit.
2. **4 of 5 adapters use `requests`** (sync HTTP client). Full async requires rewriting all four to `httpx` or `aiohttp` for zero AdCP-visible benefit.
3. **AdCP protocol surface is unchanged** either way. Path B is invisible to MCP/A2A/REST clients.
4. **Threadpool capacity** at 80 covers expected burst load (max ~50 concurrent media buys per spec). Pool math (60 peak DB connections within PG `max_connections=100`) works for both engines coexisting — see `async-pivot-checkpoint.md` §3 "Background sync sync-bridge".

### 2.7 Admin layer (v2.0 migration target)

The admin rewrite is Flask → FastAPI PLUS sync → async. Every handler is rewritten anyway. LOC estimate uses the structure of existing handlers.

| File | LOC | Change required | Est LOC delta | Risk |
|---|---|---|---|---|
| `src/admin/app.py` | 427 | **Deleted in Wave 3.** | -427 | MEDIUM |
| `src/admin/server.py` | 103 | **Deleted in Wave 3.** | -103 | LOW |
| `src/admin/blueprints/products.py` | 2464 | Rewrite as async FastAPI router; keep existing eager-loading (good example). 10 `get_db_session()` uses. | +150 net vs rewrite | MEDIUM |
| `src/admin/blueprints/settings.py` | 1446 | Rewrite; 8 `get_db_session()` uses; **11 `tenant.adapter_config.*` lazy-load accesses — CRITICAL hotspot**. Each needs `selectinload(Tenant.adapter_config)` added. | +100 | **HIGH** |
| `src/admin/blueprints/inventory.py` | 1352 | Rewrite; 19 `get_db_session()` uses (highest count!); 7 `tenant_obj.adapter_config.*` lazy-load accesses. | +180 | **HIGH** |
| `src/admin/blueprints/creatives.py` | 1308 | Rewrite; 2 `get_db_session()` uses (already mostly UoW-based — safe). | +50 | MEDIUM |
| `src/admin/blueprints/gam.py` | 1169 | Rewrite; 8 `get_db_session()` uses | +100 | MEDIUM |
| `src/admin/blueprints/auth.py` | 1097 | Rewrite; 11 `get_db_session()` uses; **OAuth flow — byte-immutable callbacks** | +120 | **HIGH** |
| `src/admin/blueprints/authorized_properties.py` | 1003 | Rewrite; 14 `get_db_session()` uses | +120 | MEDIUM |
| `src/admin/blueprints/tenants.py` | 906 | Rewrite; 10 `get_db_session()` uses | +80 | MEDIUM |
| `src/admin/blueprints/principals.py` | 759 | Rewrite; 15 `get_db_session()` uses; 22 `tenant.adapter_config.*` accesses (most in single file!) | +120 | **HIGH** |
| `src/admin/blueprints/inventory_profiles.py` | 720 | Rewrite; 9 uses | +70 | MEDIUM |
| `src/admin/blueprints/operations.py` | 709 | Rewrite; 5 uses | +50 | MEDIUM |
| `src/admin/blueprints/core.py` | 550 | Rewrite; 7 uses | +50 | MEDIUM |
| `src/admin/blueprints/publisher_partners.py` | 549 | Rewrite; 5 uses | +40 | MEDIUM |
| `src/admin/blueprints/api.py` | 448 | Rewrite; 4 uses (admin AJAX) | +30 | LOW |
| `src/admin/blueprints/oidc.py` | 431 | Rewrite; 6 uses; OIDC callbacks | +50 | **HIGH** |
| `src/admin/blueprints/activity_stream.py` | 390 | Rewrite; 1 use (SSE endpoint — already async-friendly) | +30 | MEDIUM |
| `src/admin/blueprints/users.py` | 335 | Rewrite; 8 uses | +35 | LOW |
| `src/admin/blueprints/signals_agents.py` | 325 | Rewrite; 7 uses | +35 | LOW |
| `src/admin/blueprints/format_search.py` | 320 | Rewrite; 1 use | +20 | LOW |
| `src/admin/blueprints/public.py` | 316 | Rewrite; 4 uses | +25 | LOW |
| `src/admin/blueprints/adapters.py` | 307 | Rewrite; 3 uses | +25 | LOW |
| `src/admin/blueprints/creative_agents.py` | 303 | Rewrite; 7 uses | +30 | LOW |
| `src/admin/blueprints/policy.py` | 297 | Rewrite; 3 uses | +25 | LOW |
| `src/admin/blueprints/workflows.py` | 295 | Rewrite; 4 uses | +25 | LOW |
| `src/admin/blueprints/schemas.py` | 207 | Rewrite; **externally consumed** `/schemas/adcp/*` — contract test needed | +20 | **HIGH** |
| `src/admin/blueprints/accounts.py` | 189 | Rewrite; already UoW-based | +20 | LOW |
| `src/admin/tenant_management_api.py` | 529 | Rewrite; 5 uses; **Category 2 external API** (preserve legacy error shape) | +50 | **HIGH** |
| `src/admin/sync_api.py` | 699 | Rewrite; 1 use (mostly uses subprocesses); **Category 2 external API** | +40 | MEDIUM |
| `src/admin/domain_access.py` | — | 8 `get_db_session()` uses | Convert to async | +50 | MEDIUM |
| `src/admin/auth_helpers.py` | 74 | 1 `get_db_session()` use | Convert to async | +15 | LOW |
| `src/admin/auth_utils.py` | 77 | Rewrite for FastAPI session cookies | +40 | MEDIUM |
| `src/admin/utils/helpers.py` | — | 6 `get_db_session()` uses; `get_tenant_config_from_db` lazy-loads `tenant.adapter_config` | Convert to async + add `selectinload` | +40 | **HIGH** |
| `src/admin/services/dashboard_service.py` | — | 4 `get_db_session()` uses; already uses `eager_load_principal` | Convert to async | +50 | MEDIUM |
| `src/admin/services/media_buy_readiness_service.py` | — | 2 uses | Convert to async | +20 | LOW |
| `src/admin/services/business_activity_service.py` | — | 1 use | Convert to async | +15 | LOW |

**Total admin blueprint LOC currently: ~18,196 (just blueprints) + ~1,909 (admin top-level files, excluding tests)**

**Admin layer subtotal: ~+2,300 net LOC (this is the Flask → FastAPI rewrite + async conversion bundled). Delete 530 LOC of Flask wrappers.**

### 2.8 A2A layer

| File | Current state | Change required | Est LOC | Risk |
|---|---|---|---|---|
| `src/a2a_server/adcp_a2a_server.py` | ~2000 LOC; all 30+ handlers already `async def`; 4 `get_db_session()` uses; calls `core_*_tool` (8 sites without `await` — see §2.3) | Update `core_*_tool` call sites to `await` (most are missing today); update 4 DB access sites to `async with get_db_session()`. **No structural change.** | +50 | MEDIUM |

### 2.9 Alembic

| File | Current state | Change required | Est LOC | Risk |
|---|---|---|---|---|
| `alembic/env.py` | 91 LOC sync `engine_from_config` + `connection.run_migrations()` | Rewrite: `create_async_engine(_ASYNC_URL)` + `async with connectable.connect() as connection: await connection.run_sync(do_run_migrations)` + `asyncio.run()` wrapper. Standard SQLAlchemy pattern. | +30 | LOW |
| `alembic/versions/*.py` (161 files) | Sync migration bodies with `op.execute`, `op.get_bind`, etc. 164 total `op.execute()` + `op.get_bind()` references across 43 files | **NO CHANGE.** Migration bodies run inside `do_run_migrations()` which is sync when called from `connection.run_sync()`. | 0 | LOW |

### 2.10 FastMCP tool registration

| File | Current state | Change required | Est LOC | Risk |
|---|---|---|---|---|
| `src/core/main.py` lines 300-315 | 16 `mcp.tool()(with_error_logging(fn))` registrations | **Verified:** FastMCP supports async tool functions. All 13 top-level wrappers are already `async def`. `task_management.py` exports 3 `async def` functions. Registration pattern is unchanged. | 0 | LOW |
| `src/core/main.py` `lifespan_context` (lines 82-124) | Already async; calls scheduler startups | No change to lifespan composition; scheduler bodies updated per §2.5 | 0 | LOW |

### 2.11 Test harness

| File | Current state | Change required | Est LOC | Risk |
|---|---|---|---|---|
| `tests/harness/_base.py` | 915 LOC; `BaseTestEnv`/`IntegrationEnv`; `__enter__`/`__exit__`; factory-boy session binding via `SASession(bind=engine)` | Add `__aenter__`/`__aexit__`. Keep sync `__enter__`/`__exit__` as legacy entry points for existing tests OR mechanically rewrite callers. Recommend: **add both** — async contextmanager AS THE PREFERRED PATTERN + sync contextmanager raising `DeprecationWarning` that wraps the async one with `asyncio.run()`. Factory binding becomes trickier under async — see §4 factory_boy notes. | +200 | **HIGH** |
| `tests/harness/*.py` (28 files) | Domain env subclasses | Each needs `async def call_impl` methods (some already are). Env dispatchers (`dispatchers.py`, `transport.py`) need to know about async vs sync transport. | +400 total | MEDIUM |
| `tests/factories/*.py` (11 files) | `SQLAlchemyModelFactory` with `sqlalchemy_session = None` + persistence `"commit"` | **factory-boy has no native async support**. Options: (a) custom `AsyncSQLAlchemyModelFactory` subclass that accepts a sync session during `_create()` and commits via `asyncio.get_event_loop().run_until_complete(...)`; (b) keep factory bound to a **sync** SQLAlchemy session in tests (separate engine using psycopg2-binary for tests only — rejected because it means shipping two drivers); (c) use `session.run_sync(lambda s: factory(session=s))` trick; (d) manual wrapper that emits INSERT via SQL. **Recommendation: (a) custom subclass. Implement in harness, not factories.** | +150 | **HIGH** |
| `tests/conftest.py` | 814 LOC; asyncio_mode not set | Add `asyncio_mode = "auto"` in `pytest.ini` or `pyproject.toml`. Verify `pytest-asyncio>=1.1.0` is compatible. | +10 | LOW |
| `tests/integration/conftest.py` | 1160 LOC; sync fixtures | Mechanical: sync fixtures that yield ORM objects become `async def` + `asyncio.fixture`. Some fixtures can stay sync (they only touch `os.environ` and subprocess). | +100 | MEDIUM |
| `tests/conftest_db.py` | 536 LOC; `integration_db` fixture uses raw `psycopg2.connect` for CREATE DATABASE (outside SQLAlchemy) | **Can stay sync** — psycopg2 is still allowed as a test-only dependency for bootstrap, OR use `asyncpg.connect()`. Recommend: keep psycopg2 for this one fixture to avoid complicating test infra. Alternative: rewrite to use `asyncpg` with asyncio. | +30 | MEDIUM |
| ~144 integration test files using `integration_db` (1006 references) | Sync `def test_X(integration_db):` with `with get_db_session() as session:` bodies | Mechanical conversion via AST rewriter: `def test_X` → `async def test_X`, `with` → `async with`, add `await` before DB calls. Most tests only use `get_db_session` for assertion queries — these become repository calls under the harness. The existing allowlist of pre-existing `get_db_session()` debt shrinks naturally as the conversion happens. | ~+800 | **HIGH** |
| `tests/bdd/` (7 files) | BDD step files use `ctx["env"]` harness | Already works through `env.call_via()` dispatch — just need async-aware dispatchers | +100 | MEDIUM |

**Test file counts:**
- `tests/unit/` — 315 files
- `tests/integration/` — 176 files
- `tests/admin/` — 3 files
- `tests/e2e/` — 18 files
- `tests/bdd/` — 7 files
- **Total: 532 test files**

**Test layer subtotal: ~+1,800 LOC. This is large and unavoidable.**

### 2.12 Scripts & ops

| File | Current state | Change required | Est LOC | Risk |
|---|---|---|---|---|
| `scripts/ops/migrate.py` | Calls alembic via subprocess | No change | 0 | LOW |
| `scripts/setup/init_database.py` | 1 `get_db_session()` use | Convert to async entry point | +15 | LOW |
| `scripts/setup/init_database_ci.py` | 2 uses | Same | +15 | LOW |
| `scripts/ops/get_tokens.py` | 1 use | Convert | +10 | LOW |
| `scripts/ops/aggregate_format_metrics.py` | 1 use | Convert | +10 | LOW |
| `scripts/ops/gam_helper.py` | 3 uses | Convert | +20 | LOW |
| `scripts/setup/setup_tenant.py` | 1 use | Convert | +10 | LOW |

**Scripts subtotal: ~+80 LOC**

### 2.13 Summary scope rollup

| Layer | Net LOC delta |
|---|---|
| Database core | +1,800 to +2,200 |
| Driver change (pyproject.toml) | +/-0 |
| `_impl` functions (core/tools) | +400 |
| Supporting core layer | +315 |
| Services layer | +460 |
| Adapters layer | +345 |
| Admin layer (incl. Flask→FastAPI rewrite baseline) | +2,300 net |
| A2A layer | +50 |
| Alembic | +30 |
| FastMCP registration | 0 |
| Test harness + factories + tests | +1,800 |
| Scripts & ops | +80 |
| **Subtotal: async-specific changes** | **~+7,600 to +8,000 LOC modified** |
| **Plus baseline Flask→FastAPI admin rewrite (pre-pivot estimate)** | **+9,000 to +10,000** |
| **Grand total v2.0** | **~+16,600 to +18,000 LOC net** |

This is **below** the checkpoint's "30,000-35,000 total" upper bound because:
1. The repository pattern is already extensively adopted (repository layer just needs `await` added, not re-architected).
2. The `_impl` wrappers are already `async def` at the top (only DB-touching bodies change).
3. Templates don't traverse ORM relationships (they use scalars passed explicitly).
4. 3 of 15 `_impl` functions are already fully async.

**Cross-production `get_db_session()` call sites:** 328 (across 79 files in `src/`).
**Cross-test `get_db_session()` call sites:** ~820 (across 145 files in `tests/`).

---

## 3. Tool `_impl` function table (detailed conversion priority)

See §2.3 above for the full table. Conversion priority:

**Wave 4a — Core async infrastructure (foundation, required before any `_impl` conversion):**
- `src/core/database/database_session.py` (async engine, session, get_db_session)
- `src/core/database/repositories/*.py` (all repo classes → async methods)
- `src/core/database/repositories/uow.py` (BaseUoW → async context manager)
- `tests/harness/_base.py` (IntegrationEnv → async context manager, factory binding shim)
- `pyproject.toml` (drop psycopg2, add asyncpg)
- `alembic/env.py` (async migration runner)

**Wave 4b — `_impl` functions + their callers (in dependency order):**
1. **`_get_adcp_capabilities_impl`** (smallest, test bed) — includes `get_adcp_capabilities_raw` + REST route
2. **`_list_creative_formats_impl`** — mostly I/O against creative agents, limited DB
3. **`_list_authorized_properties_impl`** — small DB footprint
4. **`_list_accounts_impl`** — already uses UoW, mechanical
5. **`_list_creatives_impl`** — already uses UoW, moderate
6. **`_sync_creatives_impl`** — largest creative surface, uses CreativeUoW + assignments
7. **`_get_media_buys_impl`** — tenant-scoped listing
8. **`_update_media_buy_impl`** — large `_impl`, extensive UoW usage
9. **`_get_media_buy_delivery_impl`** — calls adapters (requires adapter base async)
10. **`_update_performance_index_impl`** — small DB footprint

**Wave 4c — Support layer:**
- `src/core/auth.py::get_principal_object` / `get_principal_adapter_mapping`
- `src/core/audit_logger.py`
- `src/core/config_loader.py`
- `src/core/strategy.py` (StrategyManager)
- Each `_impl` gains awaits for these as Wave 4b progresses.

**Wave 4d — Adapters:**
- Base class `AdServerAdapter` — methods become `async def`
- `src/adapters/google_ad_manager.py` (largest)
- `src/adapters/mock_ad_server.py`
- `src/adapters/kevel.py`, `src/adapters/xandr.py`, `src/adapters/broadstreet/*`, `src/adapters/triton_digital.py`
- `src/adapters/gam_reporting_api.py`
- `src/adapters/gam/managers/*`
- `src/adapters/base_workflow.py`

**Wave 4e — Services & schedulers:**
- `src/services/delivery_webhook_scheduler.py`
- `src/services/media_buy_status_scheduler.py`
- All other services in §2.5

**Wave 4f — Tests:**
- Mechanical AST rewriter for `integration_db`-using tests
- BDD step files
- conftest fixture conversions

**Wave 5 — Admin (already scheduled as Waves 1-3 for Flask→FastAPI; now merged with async conversion):**
- Each admin blueprint rewritten async-native
- `get_tenant_config_from_db` + `selectinload` fixes
- OAuth + session-cookie handlers

---

## 4. Lazy-load audit findings (Risk #1 deep dive)

### 4.1 Relationship inventory (58 `relationship()` definitions in models.py)

Grouped by parent model (from `src/core/database/models.py`):

**`Tenant` (13+ relationships, all cascade delete-orphan, all `lazy="select"` default):**
- `products` (line 124)
- `principals` (line 125)
- `users` (line 126)
- `accounts` (line 127)
- `media_buys` (line 128, `overlaps="media_buys"`)
- `audit_logs` (line 130)
- `strategies` (line 131, `overlaps="strategies"`)
- `currency_limits` (line 132)
- `adapter_config` (line 133, one-to-one)
- `creative_agents` (line 139)
- `signals_agents` (line 144)
- `auth_config` (line 149)
- Plus backrefs for `creatives` (line 727), `authorized_properties` (1900), `property_tags` (1935), `publisher_partners` (1971), `push_notification_configs` (2010)

**`Product` (4):** `tenant` (line 333), `inventory_profile` (line 334), `pricing_options` (line 338, `passive_deletes=True`)

**`PricingOption` (1):** `product` (line 522)

**`CurrencyLimit` (1):** `tenant` (line 566)

**`Principal` (5):** `tenant` (line 592), `media_buys` (line 593), `strategies` (line 594), `push_notification_configs` (line 595)

**`User` (1):** `tenant` (line 633)

**`TenantAuthConfig` (1):** `tenant` (line 672, back_populates)

**`Creative` (2):** `tenant` (line 727, backref), `reviews` (line 728, cascade)

**`CreativeReview` (2):** `creative` (line 778), `tenant` (line 779)

**`CreativeAssignment` (1):** `tenant` (line 819)

**`Account` (1):** `tenant` (line 879)

**`MediaBuy` (5):** `tenant` (line 964), `principal` (line 965), `strategy` (line 971), `packages` (line 972, cascade), `account` (line 973)

**`MediaPackage` (1):** `media_buy` (line 1050)

**`AuditLog` (1):** `tenant` (line 1089)

**`AdapterConfig` (1):** `tenant` (line 1219)

**`CreativeAgent` (1):** `tenant` (line 1277)

**`SignalsAgent` (1):** `tenant` (line 1314)

**`InventoryProfile` (2):** `tenant` (line 1417), `products` (line 1418)

**`GAMOrder`, `GAMLineItem` and related** (lines 1496-1667) — tenant + order relationships

**`Context` (2):** `tenant` (line 1700), `principal` (line 1701), `workflow_steps` (line 1708, cascade)

**`WorkflowStep` (2):** `context` (line 1753), `object_mappings` (line 1754)

**`ObjectWorkflowMapping` (1):** `workflow_step` (line 1792)

**`Strategy` (4):** `tenant` (line 1826), `principal` (line 1827), `states` (line 1828, cascade), `media_buys` (line 1829)

**`StrategyState` (1):** `strategy` (line 1867)

**`AuthorizedProperty`, `PropertyTag`, `PublisherPartner`, `PushNotificationConfig`** — all backref to tenant (lines 1900, 1935, 1971, 2010-2011)

**Other (lines 2078, 2130-2132)** — additional tenant/principal/media_buy references

**CRITICAL FINDING:** NO explicit `lazy=` settings anywhere. **Every single relationship uses SQLAlchemy's default `lazy="select"`**, meaning every attribute access issues a SELECT on first touch. Under async, every such access must be `await`-able or the relationship must be eager-loaded.

### 4.2 The 5 poisonous `@property` methods

There are 10 `@property` decorators in `src/core/database/models.py`:

**SAFE (5):**
- Line 164: `Tenant.gemini_api_key` — decryption only, no relationship
- Line 188: `Tenant.primary_domain` — scalar column access
- Line 676: `TenantAuthConfig.oidc_client_secret` — decryption only
- Line 1223: `AdapterConfig.gam_service_account_json` — decryption only
- Line 1840: `Strategy.is_production_strategy` — scalar `is_simulation` access

**RISKY (5):**
- Line 193: `Tenant.is_gam_tenant` — accesses `self.adapter_config` (relationship, line 208)
- Line 341: `Product.effective_format_ids` — accesses `self.inventory_profile` (line 352)
- Line 355: `Product.effective_properties` — accesses `self.inventory_profile` AND `self.tenant` (lines 372, 429, 439, 449)
- Line 454: `Product.effective_property_tags` — accesses `self.inventory_profile` (line 462)
- Line 468: `Product.effective_implementation_config` — accesses `self.inventory_profile` (line 481)

**Callers of `Product.effective_*`:**
- `src/core/product_conversion.py` lines 284, 294, 392 — `convert_product_model_to_schema()` — the ONLY caller of `effective_*` properties. Called from:
  - `src/core/main.py::get_product_catalog()` line 232 — uses `selectinload(Product.pricing_options)` — **MISSING `inventory_profile` and `tenant` eager loads** — risky
  - `src/core/database/repositories/product.py::list_all_with_inventory()` lines 87-101 — ALREADY uses `selectinload(Product.pricing_options, Product.inventory_profile, Product.tenant)` — **SAFE**
  - `src/admin/blueprints/products.py::list_products()` lines 440-450 — uses `joinedload(Product.inventory_profile)` — **SAFE**

**Fix for Product properties:** Add `selectinload(Product.inventory_profile)` + `selectinload(Product.tenant)` to `get_product_catalog()` in main.py. Alternatively, refactor `get_product_catalog()` to use `list_all_with_inventory()` (which is already correct).

**Callers of `Tenant.is_gam_tenant`:**
```
src/services/setup_checklist_service.py:344: if tenant.is_gam_tenant:
src/services/setup_checklist_service.py:465: if tenant.is_gam_tenant:
src/services/setup_checklist_service.py:782: if tenant.is_gam_tenant:
src/services/setup_checklist_service.py:876: if tenant.is_gam_tenant:
src/services/policy_service.py:335: updates["measurement_providers"], is_gam_tenant=tenant.is_gam_tenant
src/admin/blueprints/principals.py:128-129: has_gam = tenant.is_gam_tenant
src/admin/blueprints/principals.py:230-231: has_gam = tenant.is_gam_tenant
src/admin/blueprints/principals.py:423-424: gam_enabled = tenant.is_gam_tenant
```

6 `tenant.is_gam_tenant` call sites. All inside `with get_db_session()` context, but `tenant.adapter_config` is NOT eager-loaded at any of them. **Fix:** Add `selectinload(Tenant.adapter_config)` to each query OR change the `is_gam_tenant` property to take an explicit `adapter_config` parameter.

**Better long-term fix:** Make `is_gam_tenant` an instance method `async def is_gam_tenant(self, session)` OR move it to a tenant service class. But this is a larger refactor.

### 4.3 Sampled access sites — classification

**SAFE sites (inside session, eager-loaded OR scalar-only):**
- All `src/core/database/repositories/*` method results (they return the scoped queries)
- `src/admin/services/dashboard_service.py:161` — `media_buy.principal.name` (`eager_load_principal=True` in `list_recent`)
- `src/admin/blueprints/products.py:534-550` — `product.inventory_profile.*` with `joinedload`
- `src/admin/blueprints/operations.py::media_buy_detail` — accesses inside `with get_db_session()` block
- All template renders where templates only access scalar columns (most)
- Adapter `self.principal.*` accesses — Pydantic schema, not ORM
- All `uow.*` method calls in admin blueprints and `_impl` functions — go through repository layer

**RISKY sites (inside session but NO eager loading):**
- `src/admin/utils/helpers.py::get_tenant_config_from_db` lines 70-100 — accesses `tenant.adapter_config.*` 6+ times
- `src/admin/blueprints/inventory.py:32-37` and `581-582` — `tenant_obj.adapter_config.*` (7 lines)
- `src/admin/blueprints/principals.py:128-129, 230-231, 423-424, 431, 459-472` — `tenant.adapter_config.*` and `is_gam_tenant` (22 total access sites)
- `src/admin/blueprints/publisher_partners.py:115, 210` — `tenant.adapter_config.adapter_type` (2 sites)
- `src/admin/blueprints/settings.py:1199-1217` — 11 `tenant.adapter_config.*` accesses (all in one function)
- `src/services/setup_checklist_service.py:344, 465, 782, 876` — `tenant.is_gam_tenant` which triggers `tenant.adapter_config` lazy load
- `src/services/policy_service.py:335` — `tenant.is_gam_tenant`

**Tally: ~50 lazy-load-risky accesses across 8 files.** All are **mechanically fixable** by adding `selectinload(Tenant.adapter_config)` to the corresponding SELECT query.

**Template-side risks (inside the render_template call, with session still open):**
- `templates/media_buy_detail.html` lines 209, 236, 240, 271, 273 — accesses `item.product.name`, `item.product.product_id`, `item.product.pricing_options`. The blueprint pre-loads these via `selectinload(Product.pricing_options)`. **SAFE.**
- `templates/products.html` lines 266-267 — `product.inventory_profile.inventory_summary`, `product.inventory_profile.name`. The blueprint pre-builds a dict BEFORE rendering (see `src/admin/blueprints/products.py:530-591`). **SAFE.**
- `templates/tenant_dashboard.html` — 23 `tenant.X` accesses, ALL scalar columns (`tenant.name`, `tenant.tenant_id`, `tenant.ad_server`). **SAFE.**
- `templates/tenant_settings.html` — 79 `tenant.X` accesses, ALL scalar columns (verified line-by-line). **SAFE.**
- Grep confirmed: 0 template files access `tenant.adapter_config`, `tenant.auth_config`, `tenant.products`, `tenant.principals`, `tenant.currency_limits`, `tenant.media_buys`, `tenant.audit_logs`, `tenant.accounts`, `tenant.users`, `tenant.creative_agents`, or `tenant.signals_agents`.

**Template audit conclusion:** Templates themselves are not a lazy-load source. They access scalar columns on ORM models passed in, which works fine under async without modification. The risk is entirely in the Python code that **builds the template context**, not the templates themselves. This is a huge de-risk for the admin rewrite.

### 4.4 Top 5 most problematic lazy-load patterns

1. **`tenant.is_gam_tenant` property** (~6 sites) — touches `self.adapter_config`. Fix: add `selectinload(Tenant.adapter_config)` to each query or make it a service function.
2. **`get_tenant_config_from_db()` helper** (many callers) — builds config dict from `tenant.adapter_config` relationship access. Fix: add eager load inside the helper.
3. **`Product.effective_*` properties** (1 site: `convert_product_model_to_schema`) — touches `self.inventory_profile` + `self.tenant`. Fix: update single caller (`main.py::get_product_catalog`) to use repository's `list_all_with_inventory()`.
4. **Admin settings page** (`settings.py:1199-1217`) — 11 in-line `tenant.adapter_config.*` mutations. Fix: add `selectinload` to the query at line 1192.
5. **Admin principals page** (`principals.py` — 22 hits) — most cluster around the GAM network/trafficker ID access. Fix: add `selectinload(Tenant.adapter_config)` to the query.

### 4.5 Lazy-load audit size estimate

- **~50 risky access sites**, each fixable by adding 1 line (`selectinload(...)`) to 1 query
- **5 risky `@property` methods** — 3 paths: refactor each property, add `selectinload` to each caller, or make the property a service function
- **Total audit + fix effort:** 2 full days (1 day spike + 1 day mechanical fixes). **This is WITHIN the checkpoint's 1-3 day estimate.**

**Overall lazy-load conversion size: SMALL-TO-MEDIUM, boundable, mechanical, not architectural.**

### 4.6 The key architectural insight

The reason lazy-loading is a smaller risk than feared is **the repository pattern is already extensively adopted**. ~328 production `get_db_session()` call sites, but many admin blueprints and `_impl` functions access ORM data exclusively through `uow.X.method()` method calls — not through relationship traversal on returned ORM instances. The repository returns values that have been materialized inside the session scope, which is exactly what async-safe access requires.

The remaining ~50 risky sites are all in legacy admin code paths that predate the full UoW adoption. These will be rewritten anyway during the Flask→FastAPI admin migration, so the async conversion can piggyback on the rewrite.

---

## 5. Scope delta vs. checkpoint §5

**Checkpoint §5 estimates:**
- Original v2.0: ~18,000 LOC (Flask removal + admin FastAPI rewrite + cleanup)
- Plus async absorption: +10,000-15,000 LOC
- **Total v2.0: ~30,000-35,000 LOC**

**Refined based on this audit:**
- Async absorption: **~7,600-8,000 LOC** (below the 10-15k range)
- **Refinement rationale:**
  - Repository pattern is broadly adopted already (3,087 LOC of repos need +30-40% for `async def` but not new architecture)
  - 3 of 15 `_impl` functions already async (10 sync `_impl` remaining, not 15 full conversions)
  - Templates require zero changes (confirmed via grep audit)
  - Migration scripts require zero changes (alembic bodies stay sync)
  - Lazy-load audit hotspot is small (~50 sites, not hundreds)
  - FastMCP tool registration is unchanged (FastMCP handles both sync/async natively)
  - Schedulers already run in async context; only DB call bodies change

- Plus admin Flask→FastAPI rewrite: **~9,000-10,000 LOC net** (includes new files + deletions)
- **Total v2.0: ~16,600-18,000 LOC**

**Refined waves:**
- Wave 0: pre-implementation + lazy-load spike (1 week)
- Wave 1: middleware + templating foundation + CSRF/approximated ordering (1 week)
- Wave 2: async DB infrastructure + repository conversion + driver swap + alembic (1-1.5 weeks)
- Wave 3: `_impl` + adapters + services conversion + test harness rewrite (1-1.5 weeks)
- Wave 4: admin rewrite (async-native from the start) (1.5-2 weeks)
- Wave 5: cleanup + benchmarking + release (0.5-1 week)
- **Total: 5-7 weeks**

This is slightly longer than the checkpoint's 4-6 week estimate, driven primarily by the test harness + factory-boy shim work and the adapter base class conversion (which the checkpoint did not explicitly size).

---

## 6. Pre-Wave-0 spike checklist (3-5 days, mandatory)

**This is the go/no-go gate. Execute each spike and record results. If ANY spike fails, fall back to v2.0 sync + v2.1 async.**

> **Archived-report alignment note (2026-04-14):** the canonical spike list is now 10 technical spikes + 1 decision gate (Spike 8) = 11 items, codified in `CLAUDE.md` §"v2.0 Spike Sequence". The per-spike details below predate Spikes 4.25, 4.5, 5.5, and 7 (GAM adapter threadpool saturation). Consult the CLAUDE.md canonical table for current gate thresholds.

1. **Spike 1 — AsyncSession + asyncpg smoke test (1 day)**
   - Create branch `spike/async-db-smoke`
   - Swap `pyproject.toml` driver: remove `psycopg2-binary`, add `asyncpg>=0.30.0`
   - Rewrite `src/core/database/database_session.py` to async (as per checkpoint §3 code sample)
   - Run `tests/unit/test_adcp_contract.py` — must still pass (it uses no DB)
   - Run `tests/unit/test_database_health_integration.py` — verify health check still works
   - **Expected outcome:** Clean smoke test; if `JSONType` has asyncpg codec issues they surface here.
   - **Fallback trigger:** asyncpg codec breaks `JSONType` unrecoverably.

2. **Spike 2 — Single `_impl` end-to-end conversion (1 day)**
   - Pick `_get_adcp_capabilities_impl` (smallest, least dependencies)
   - Convert: impl → async, repo (if any) → async, `get_adcp_capabilities_raw` → already async (fix missing await), REST route — already awaits, MCP wrapper already async
   - Run `tests/integration/` subset that covers capabilities
   - Measure: test passes, no `MissingGreenlet`, no `DetachedInstanceError`
   - **Expected outcome:** green test, confirms the pattern is mechanical
   - **Fallback trigger:** a non-trivial architectural issue surfaces (e.g., need to pass session through 10 layers)

3. **Spike 3 — Lazy-load hotspot fix (0.5 day)**
   - In the spike branch, convert `src/admin/utils/helpers.py::get_tenant_config_from_db` to async with `selectinload(Tenant.adapter_config)`
   - Run one admin test that uses it (`tests/admin/` or an integration test that hits a tenant settings blueprint)
   - Measure: no `MissingGreenlet` on the `tenant.adapter_config.*` access path
   - **Expected outcome:** confirms `selectinload` resolves the hotspot
   - **Fallback trigger:** selectinload doesn't work OR cascades into other lazy loads we didn't anticipate

4. **Spike 4 — factory-boy async shim (0.5 day)**
   - Write a minimal `AsyncSQLAlchemyModelFactory` that accepts an async session, uses `session.run_sync()` internally to let factory-boy's sync code drive INSERT, then commits via the async session
   - Wire it into `TenantFactory` and `PrincipalFactory`
   - Run one integration test that uses factories (`tests/integration/test_account_model.py`)
   - **Expected outcome:** factory creates rows, test passes
   - **Fallback trigger:** factory-boy's internal session management is incompatible with `session.run_sync()` in ways that require rewriting factory-boy's `_create()` classmethod extensively

5. **Spike 5 — Alembic async runner (0.5 day)**
   - Rewrite `alembic/env.py` with the async pattern from the checkpoint
   - Run `alembic upgrade head` against the spike database
   - **Expected outcome:** all 161 existing migrations apply cleanly
   - **Fallback trigger:** any migration uses a pattern incompatible with `connection.run_sync(do_run_migrations)` — unlikely given they all use `op.*` helpers

6. **Spike 6 — Benchmark async vs sync (optional, 0.5 day)**
   - Set up `tests/e2e/test_benchmark_admin_routes.py` harness
   - Run 10 representative admin routes under both sync (main) and async (spike branch)
   - Compare p50/p95 latencies
   - **Expected outcome:** async is within ±20% of sync (most likely faster under concurrent load, slower under low concurrency)
   - **Risk flag:** if async is >30% slower under realistic concurrency, investigate pool tuning before committing

**Spike pass criteria (all 5 required + 1 optional):** all mandatory spikes pass → GO. Any mandatory spike fails → NO-GO, fall back to v2.0 sync + v2.1 async.

---

## 7. Open questions (need user input)

> **Status 2026-04-11: LEDGER CLOSED — TWO DEEP-THINK ROUNDS.** All 9 questions are now resolved. Decisions 1, 7, 9 went through Opus deep-think 1st/2nd/3rd-order analysis on 2026-04-11 (round 1). **Decisions 2, 3, 4, 5, 6, 8 were further refined by a SECOND round of Opus deep-think analysis on 2026-04-11.** The resolutions below reflect the Audit 06 originals; the canonical refined text is in `CLAUDE.md` "Open decisions blocking Wave 4". Key refinements from round 2: D2 rationale corrected (fork safety, not loop collision) + Risk #34 surfaced; D3 recipe had 3 bugs (overrides `_create` not `_save`, redundant `sync_session.add`, `flush()` raises `MissingGreenlet`) + Spike 4.25 added; D4 has 6 functions not 7, zero production callers, 3 dead functions; D5's RuntimeError prescription is technically ineffective (inspect guard defeated by unconditional log at line 43) — DELETE the module; D6 is ~90 LOC not 40, both inventory sites cache Flask Response objects not dicts, `threading.RLock` required for thread safety; D8 SSE route is orphan code — DELETE not migrate.

1. **Should the adapter base class (`AdServerAdapter`) become fully async, or do we keep it sync and wrap in `run_in_threadpool` for now?** **RESOLVED 2026-04-11: Path B (sync adapters + `run_in_threadpool` wrap).** Full async requires porting `googleads==49.0.0` off `suds-py3` and rewriting 4 `requests`-based adapters (~1500 LOC) for zero AdCP-visible benefit. 18 adapter call sites in `src/core/tools/*.py` + 1 in `src/admin/blueprints/operations.py:252` wrap in `await run_in_threadpool(...)`. Dual session factory in `database_session.py`, AuditLogger split, anyio limiter to 80, structural guard `test_architecture_adapter_calls_wrapped_in_threadpool.py`. See §2.6 corrected scope, `flask-to-fastapi-foundation-modules.md` §11.14, `async-pivot-checkpoint.md` §3 "Adapters (Decision 1 Path B)".

2. **Do we keep `DatabaseConnection` + `get_db_connection()` in `db_config.py`?** **RESOLVED (Audit 06 OVERRULE): KEEP.** This audit's grep was incomplete — `scripts/deploy/run_all_services.py:84,135` calls `get_db_connection()` as pre-uvicorn health checks. `psycopg2-binary` is also retained for Decision 1's Path B sync factory and Decision 9's sync-bridge. New structural guard `test_architecture_no_runtime_psycopg2.py` allowlists only `db_config.py` + `background_sync_db.py`.

3. **Should factory-boy sessions be wrapped with a custom `AsyncSQLAlchemyModelFactory`?** **RESOLVED: custom shim** with `sqlalchemy_session_persistence = None` so `_create()` only calls `session.add()`; commits belong to fixtures. Full recipe in `flask-to-fastapi-foundation-modules.md` §11.13.1 (D).

4. **`src/core/database/queries.py` (282 LOC, **6** sync functions — corrected from 7 per D4 deep-think)** — **RESOLVED: Option 4A (convert-and-prune).** Zero production callers. 3 dead functions (zero callers anywhere) → delete. 3 live functions → async conversion. Test file converts to async. Net: **−100 LOC** (not +50). See CLAUDE.md Decision 4 for full resolution.

5. **`src/core/database/product_pricing.py` and `src/core/database/database_schema.py`** — **RESOLVED (Audit 06 SUBSTITUTE).** `database_schema.py` is **orphan** (its own docstring says "reference only; use Alembic migrations") — delete in Wave 5 cleanup. `product_pricing.py` has a **latent lazy-load hotspot** at lines 16-71 — `inspect(product).unloaded` silently early-returns when `pricing_options` isn't eager-loaded. Wave 4 fix: raise `RuntimeError` instead of silent early-return; add all callers to Spike 1's explicit eager-load audit list.

6. **Flask-caching in pyproject.toml** — **RESOLVED (Decision 6 deep-think 2026-04-11): replace with `src/admin/cache.py::SimpleAppCache` (~90 LOC, corrected from ~40 per D6 deep-think).** 3 consumer sites confirmed. Both inventory sites cache `jsonify(...)` Response objects (Flask-ism — must cache dicts under FastAPI). `threading.RLock` required (not `asyncio.Lock` — Site 3 is a sync thread). `_NullAppCache` fallback for startup race. `CacheBackend` Protocol for v2.2 Redis swap. See CLAUDE.md Decision 6 for full resolution.

7. **`ContextManager` class** — **RESOLVED 2026-04-11: refactor to stateless async module functions.** The `ContextManager(DatabaseManager)` inheritance caches `self._session` on a process-wide singleton; under `async_sessionmaker` on the single event-loop thread, every concurrent task shares the same cached session → transaction interleaving. `async_sessionmaker` does NOT fix this because the singleton sits above the session factory. Refactor: delete `ContextManager` class + `_context_manager_instance` + `get_context_manager()` + `DatabaseManager` entirely. 12 public methods become module-level `async def` functions taking `session: AsyncSession` as first parameter. 7 production callers (incl. dead `main.py:166` + module-load side effect in `mcp_context_wrapper.py:345`). ~400 LOC across ~15 files; ~50 test patches, 20 collapsible via single `tests/harness/media_buy_update.py` update. `mock_ad_server.py` `threading.Thread` background task → `asyncio.create_task` + `async with session_scope()`. Validated by Spike 4.5. Structural guard `test_architecture_no_singleton_session.py`. Error-path composition gotcha: use SEPARATE `async with session_scope()` for error-logging writes (outer rolls back on raise → wipes error log). See `async-pivot-checkpoint.md` §3 "ContextManager refactor".

8. **`src/admin/blueprints/activity_stream.py` SSE endpoint.** **RESOLVED (Decision 8 deep-think 2026-04-11): DELETE the SSE route entirely.** Audit 06's "already correct, just migrate" was wrong about scope — the `/events` route is **orphan code** (template says "use polling", zero `new EventSource(` in templates). Wave 4 DELETES route + generator + rate-limit state (−170 LOC, −1 pip dep `sse_starlette`). Two surviving routes (`/activity` JSON poll + `/activities` REST) convert mechanically to `async def`. Fix pre-existing `api_mode=False` bug on JSON poll route. See CLAUDE.md Decision 8.

9. **`src/services/background_sync_service.py`** — **RESOLVED 2026-04-11: Option B sync-bridge.** Service runs multi-hour GAM inventory sync jobs via `threading.Thread` workers, incompatible with async SQLAlchemy (asyncpg `pool_recycle=3600` rotates mid-session, identity map grows unbounded over hours, Fly.io TCP keepalives expire). New `src/services/background_sync_db.py` module with separate sync psycopg2 engine + `get_sync_db_session()` factory. Background threads use the sync-bridge; async request path untouched. `psycopg2-binary` + `types-psycopg2` + `libpq-dev` + `libpq5` retained. Also fixes Wave 3 `from flask import current_app` ImportError at line 472 (replaced with `SimpleAppCache` helper, see Decision 6). Scope guarded by `test_architecture_sync_bridge_scope.py` ratcheting allowlist (background_sync_service.py only). Validated by Spike 5.5 (4 test cases: lazy-init/dispose, MVCC bidirectional, 5-async + 1-sync no-deadlock, post-dispose leaks ≤1). Sunset target v2.1+. See §2.5 row + `async-pivot-checkpoint.md` §3 "Background sync sync-bridge".

---

## 8. Final recommendation

**GO with the pivot, with these mandatory controls:**

1. Execute all 5 mandatory Pre-Wave-0 spikes before committing. Budget 3-5 days.
2. If any spike fails, hold the pivot and return to the sync-def v2.0 plan with async as v2.1 follow-on.
3. Assume scope is **~+8,000 LOC for async** (not 10-15k) on top of the pre-pivot Flask→FastAPI baseline.
4. The adapter base class conversion is a required dependency that the checkpoint underscoped; budget an extra 0.5-1 week for it.
5. Lazy-load conversions are small (~50 sites) and mechanical — **confirm in Spike 3**.
6. Tests will feel the biggest pain: factory-boy shim, ~166 integration tests to convert, test harness rewrite. Budget 1 full week for test infrastructure alone.
7. The pre-existing latent `async def` wrapper calling sync `_raw` bug in `src/routes/api_v1.py` is real and will be fixed as a side effect — this is a genuine net win.

**Key file paths (absolute) for the implementation team:**

- `/Users/quantum/Documents/ComputedChaos/salesagent/src/core/database/database_session.py` — full rewrite required
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/core/database/repositories/` (11 files, 3087 LOC) — async conversion
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/core/database/repositories/uow.py` — add `__aenter__`/`__aexit__`
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/core/database/models.py` lines 193, 341, 355, 454, 468 — 5 risky `@property` methods
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/utils/helpers.py::get_tenant_config_from_db` (lines 40-110) — canonical `tenant.adapter_config` lazy-load hotspot
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/routes/api_v1.py` lines 200, 214, 252, 284, 305, 324, 342, 360 — 8 missing `await` calls = latent bug
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/a2a_server/adcp_a2a_server.py` lines 1558, 1587, 1774, 1798, 1842, 1892, 1961, 2000 — 8 missing `await` calls = latent bug (same pattern)
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/core/tools/accounts.py::_list_accounts_impl` (line 113, sync) + 8 other sync `_impl` functions listed in §2.3
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/core/auth.py::get_principal_object` (line 291) — sync helper with 21 `_impl` callers
- `/Users/quantum/Documents/ComputedChaos/salesagent/alembic/env.py` — 91 LOC sync runner, rewrite to async pattern
- `/Users/quantum/Documents/ComputedChaos/salesagent/tests/harness/_base.py` lines 790-867 — `__enter__`/`__exit__` → add `__aenter__`/`__aexit__`
- `/Users/quantum/Documents/ComputedChaos/salesagent/tests/factories/core.py` — `TenantFactory` + others, `SQLAlchemyModelFactory` → custom async shim
- `/Users/quantum/Documents/ComputedChaos/salesagent/tests/conftest_db.py::integration_db` (line 324) — uses raw psycopg2 for `CREATE DATABASE`, may stay sync
- `/Users/quantum/Documents/ComputedChaos/salesagent/pyproject.toml` lines 19 (remove psycopg2), 74+101 (remove types-psycopg2), add `asyncpg>=0.30.0`
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/core/main.py::lifespan_context` lines 82-124 — scheduler starts/stops, no structural change but scheduler bodies update
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/services/delivery_webhook_scheduler.py` lines 87-120 — canonical scheduler DB pattern
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/services/media_buy_status_scheduler.py` line 83 — same pattern

**Confidence level: HIGH on the scope estimate, MEDIUM on the lazy-load audit conclusions (confirmed via sampling, not exhaustive), HIGH on the factory-boy shim approach working (there is prior art), MEDIUM on the timeline estimate (depends heavily on test conversion velocity).**

---

## Appendix: Audit methodology

This audit was produced by reading:
1. `async-pivot-checkpoint.md` §§1-7 (the pivot directive and its rationale)
2. `flask-to-fastapi-adcp-safety.md` §1 (AdCP file classification — confirmed the pivot does not cross any AdCP protocol boundary)
3. `src/core/database/database_session.py` (465 lines — full read)
4. `src/core/database/db_config.py` (173 lines — full read)
5. `src/core/database/json_type.py` (115 lines — full read)
6. `src/core/database/models.py` (2143 lines — sampled: all `@property` methods, all `relationship()` declarations, Product/Tenant classes)
7. `src/core/database/repositories/` (11 files — sampled the 3 largest: media_buy.py full, product.py relevant sections, uow.py full)
8. `src/core/main.py` lines 1-315 (FastMCP registration, lifespan context)
9. `src/core/tools/*.py` (15 `_impl` functions classified; task_management.py full read; accounts.py sampled for async/sync mix)
10. `src/routes/api_v1.py` lines 1-378 (confirmed 8 missing `await` latent bug)
11. `src/a2a_server/adcp_a2a_server.py` (handlers listing + `core_*_tool` call sites grepped)
12. `src/services/delivery_webhook_scheduler.py` + `media_buy_status_scheduler.py` (scheduler pattern verified)
13. `src/admin/blueprints/` (26 files — LOC count + sampled `settings.py`, `operations.py`, `products.py`, `principals.py`, `inventory.py` for lazy-load patterns)
14. `src/admin/services/dashboard_service.py` (verified eager-load pattern)
15. `src/admin/utils/helpers.py` (confirmed `get_tenant_config_from_db` hotspot)
16. `templates/*.html` (56 files — grepped for relationship traversals, sampled tenant_dashboard.html, media_buy_detail.html, products.html, tenant_settings.html)
17. `alembic/env.py` (91 lines — full read) + migration script sampling
18. `tests/harness/_base.py` (915 lines — full read)
19. `tests/conftest_db.py` lines 1-390 (factory binding + `integration_db` fixture)
20. `tests/factories/core.py` (factory-boy pattern confirmed — sync `SQLAlchemyModelFactory`)
21. `pyproject.toml` dependencies list

**What was NOT audited (scope boundary):**
- Each of the 15 non-Risk-#1 risks (that's Agent B's job)
- Plan file edit diffs (that's Agent C's job)
- Exhaustive access-site enumeration for every `.X.Y` relationship (sampling was used instead)
- E2E and unit test conversion LOC (counted only integration tests where the pattern is clearest)
- `src/core/database/database_schema.py` and `src/core/database/product_pricing.py` (brief checks only)

---

**End of Agent A audit report.**
