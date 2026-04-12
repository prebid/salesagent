# Flask → FastAPI v2.0.0 — Execution Plan

**Status:** Self-contained, phase-ordered implementation guide.
**Each phase is a standalone briefing. No cross-referencing required.**
**Last updated:** 2026-04-12 (3-round Opus verification audit)

> **How to use this file:** Read ONE phase section. It contains everything you need —
> goal, prerequisites, knowledge sources, work items in order (tests first per TDD),
> files to touch, exit gate, and scope warnings. The `[§X-Y]` references point back
> to `implementation-checklist.md` for full detail when needed, but you should NOT
> need to open it during implementation.
>
> **Relationship to other docs:**
> - `implementation-checklist.md` — verification/tracking document (tick boxes after work is done)
> - `execution-plan.md` (this file) — **what to do, in what order** (read before coding)
> - Knowledge source files — deep reference (read when noted in "Knowledge to read")

---

## Phase -1 — Pre-migration spikes + test infrastructure (~1,500 LOC, ~8-12 days)

**Goal:** Validate all unknowns that could kill the async pivot; build migration-specific test infrastructure before any production code changes.

**Prerequisites:** `main` green (`make quality` + `tox -e integration` + `tox -e bdd`). Branch `feat/v2.0.0-flask-to-fastapi` exists.

**Knowledge to read:**
- `async-pivot-checkpoint.md` §4 — risks 1-15, 20, 34
- `CLAUDE.md` (notes) — spike sequence (10 spikes)
- `async-audit/agent-b-risk-matrix.md` §4 — spike acceptance criteria
- `implementation-checklist.md` §1.1 — spike items

**Work items (in order):**

1. **Spike 1 — Lazy-load audit** (HARD GATE): set `lazy="raise"` on all 68 relationships in `models.py`, run `tox -e integration`. Exercise every Product `@property` (`effective_format_ids`, `effective_properties`, `effective_property_tags`, `effective_implementation_config`, `is_gam_tenant`) against every repo method [§1.1, §3.5.4 AE-1]. Pass: <40 failures fixable in <2 days. **Fail = abandon async pivot.**
2. **Spike 2 — Driver compat**: run full test suite under `asyncpg`. Include JSONB roundtrip test for Pydantic types — asyncpg bypasses SQLAlchemy's `json_serializer` [§3.5.2 LB-1, §3.5.4 AE-2]. Fallback: `psycopg[binary,pool]>=3.2.0`.
3. **Spike 3 — Performance baseline**: capture sync latency on 20 admin routes + 5 MCP tool calls as `baseline-sync.json`. Include adapter `run_in_threadpool` shape under Decision 1 Path B [§1.1]. (Can run in parallel with Wave 0.)
4. **Spike 4 — Test harness**: convert `tests/harness/_base.py` + 5 representative tests to async; verify xdist + factory-boy work [§1.1].
5. **Spike 4.25 — Factory async-shim** (soft blocker): create `tests/factories/_async_shim.py`, flip `TenantFactory` temporarily, run 8 edge-case tests [§1.2 Decision 3].
6. **Spike 4.5 — ContextManager refactor smoke** (soft blocker): rewrite `context_manager.py` to stateless async module functions, convert smallest caller, validate error-path composition [§1.2 Decision 7].
7. **Spike 5 — Scheduler alive-tick**: convert 2 scheduler tick bodies to async DB; observe container logs [§1.1].
8. **Spike 5.5 — Two-engine coexistence** (soft blocker): prove async asyncpg + sync psycopg2 coexist. 4 test cases [§1.2 Decision 9].
9. **Spike 6 — Alembic**: add `render_item` hook for JSONType + advisory lock (~0.5 day). **env.py stays sync per DB-4** — do NOT rewrite to async [§1.1].
10. **Spike 7 — `server_default` audit**: grep + categorize columns; confirm <30 to rewrite [§1.1]. (Can run in parallel with Wave 0.)
11. **TI-1: Response fingerprint system** (~430 LOC): capture Flask response shapes as JSON fixtures before any routes are ported [§3.5.5].
12. **TI-4: Structural guard meta-tests** (~400 LOC): known-violation fixtures for every new guard [§3.5.5].
13. **TI-5: Wave checkpoint tests** (~300 LOC): per-wave invariant gate shell [§3.5.5].
14. Add `[tool.pytest.ini_options]` with `asyncio_mode = "auto"` to `pyproject.toml` [§1.1 Agent F].
15. Add `asyncpg>=0.30.0,<0.32` to `pyproject.toml` alongside (NOT replacing) `psycopg2-binary` [§1.1 Agent F].
16. Add `[testenv:driver-compat]` to `tox.ini` [§1.1 Agent F].
17. Record all spike outcomes in `spike-decision.md`. Go condition: Spike 1 PASSES AND ≤2 soft spikes fail [§1.1].

**Files to create:** `tests/driver_compat/`, `tests/migration/fingerprint.py`, `tests/migration/conftest_fingerprint.py`, `tests/migration/test_response_fingerprints.py`, `tests/migration/fixtures/fingerprints/*.json`, `tests/unit/test_architecture_guard_meta.py`, `tests/migration/test_wave_checkpoints.py`, `.claude/notes/flask-to-fastapi/spike-decision.md`, `tests/performance/baselines/baseline-sync.json`.

**Files to modify:** `pyproject.toml`, `tox.ini`.

**Exit gate:**
```bash
make quality                    # green
tox -e integration              # green (with lazy="raise" reverted after Spike 1 audit)
# spike-decision.md committed with PASS/FAIL + fallback for all 10 spikes
# Spike 1 PASS is a HARD requirement
```

**What NOT to do:** Do not modify any production code outside spike branches. Do not convert any repositories or handlers. Do not touch templates. Spike branch is discarded after gate decision.

---

## Phase 0 — Foundation modules + template codemod (~2,500 LOC, ~5-7 days)

**Goal:** Land all 11 foundation modules, run template codemod, create 26+ structural guards. Flask still serves 100% of traffic. Pure addition — nothing changes behavior.

**Prerequisites:** Phase -1 complete. All spikes passed (or soft-blocker fallbacks documented). `main` green.

**Knowledge to read:**
- `flask-to-fastapi-foundation-modules.md` §11.1-11.15 — all 11 module implementations with code
- `flask-to-fastapi-migration.md` §11-12 — module descriptions + codemod details
- `async-pivot-checkpoint.md` §3 — target async state (code blocks corrected 2026-04-12)
- `async-audit/agent-e-ideal-state-gaps.md` — 14 idiom upgrades (minimum apply: E1/E2/E3/E5/E6/E8)
- `async-audit/frontend-deep-audit.md` — 7 critical blockers for templates/JS/OAuth
- `flask-to-fastapi-deep-audit.md` §1 — blockers 1, 2

**Work items (in order):**

1. Write structural guard tests FIRST (TDD): `test_templates_url_for_resolves.py`, `test_templates_no_hardcoded_admin_paths.py`, `test_architecture_admin_routes_named.py`, `test_codemod_idempotent.py`, `test_oauth_callback_routes_exact_names.py` (pins: `/admin/auth/google/callback`, `/admin/auth/oidc/callback`, `/admin/auth/gam/callback`), `test_trailing_slash_tolerance.py`, `test_architecture_no_flask_imports.py` (full allowlist), `test_architecture_admin_routes_async.py`, `test_architecture_admin_async_db_access.py`, `test_architecture_handlers_use_annotated_depends.py`, `test_architecture_templates_receive_dtos_not_orm.py`, `test_architecture_no_sync_session_usage.py`, `test_architecture_no_module_level_engine.py`, `test_architecture_no_direct_env_access.py`, `test_architecture_middleware_order.py`, `test_architecture_exception_handlers_complete.py`, `test_architecture_csrf_exempt_covers_adcp.py`, `test_architecture_approximated_middleware_path_gated.py`, `test_architecture_admin_routes_excluded_from_openapi.py`, `test_architecture_scheduler_lifespan_composition.py`, `test_architecture_a2a_routes_grafted.py`, `test_foundation_modules_import.py`, `test_template_context_completeness.py`, `test_architecture_form_getlist_parity.py` [§4 Wave 0, §3.5.1 SB-2/SB-3]. Every allowlist entry gets a `FIXME(salesagent-xxxx)` comment at its source location [§3.5.6 EP-5].
2. Create 11 foundation modules: `templating.py` (~150 LOC — `render()` wrapper passes `test_mode`, `user_role`, `user_email`, `user_authenticated`, `username` as context; registers `tojson` filter with HTML-escaping), `flash.py` (~70), `sessions.py` (~40), `oauth.py` (~60), `csrf.py` (~100), `app_factory.py` (~80), `deps/auth.py` (~260, async def per pivot), `deps/tenant.py` (~90), `deps/audit.py` (~110), `middleware/external_domain.py` (~90, status 307), `middleware/fly_headers.py` (~40) [§4 Wave 0, §2 Blockers 1-2, §3.5.1 SB-3].
3. Create `form_error_response()` shared helper for DRY form-validation re-rendering across 25 routers [§3.5.6 EP-3].
4. Create feature flag routing toggle `ADCP_USE_FASTAPI_ADMIN` (~50 LOC) [§3.5.6 EP-1].
5. Create `X-Served-By` header middleware (~20 LOC) [§3.5.6 EP-2].
6. Write `scripts/generate_route_name_map.py` (~50 LOC) — introspects Flask `url_map` [§2-B1].
7. Write `scripts/codemod_templates_greenfield.py` (~200 LOC) — Pass 0 (csrf, g.*, flash), Pass 1a (static), Pass 1b (hardcoded paths), Pass 2 (Flask-dotted names) [§2-B1].
8. Run codemod against all 72 templates. Manual audit of `add_product_gam.html`, `base.html`, `tenant_dashboard.html` [§2-B1].
9. Document `request.form.getlist()` → `List[str] = Form()` migration pattern in worked examples [§3.5.7 CP-2].
10. Add harness extension: `IntegrationEnv.get_admin_client()` with `dependency_overrides` snapshot/restore [§4 Wave 0].
11. Write `tests/integration/test_schemas_discovery_external_contract.py` [§3 audit action #4].
12. Complete §1.1 prerequisites: `SESSION_SECRET` in `.env.example` and secret stores, OAuth URI docs, external consumer contract confirmation [§1.1].

**Files to create:** 11 foundation modules under `src/admin/`, 2 scripts, 26+ test files.
**Files to modify:** 72 templates (codemod), `tests/harness/_base.py`.

**Exit gate:**
```bash
make quality && tox -e integration && tox -e bdd && ./run_all_tests.sh  # all green
python scripts/codemod_templates_greenfield.py --check templates/       # exit 0 (idempotent)
rg -n "url_for" templates/ | wc -l                                      # >= 134
```

**What NOT to do:** Do not modify `src/app.py` (no middleware, no router inclusion). Do not modify `pyproject.toml` (deps already added in Phase -1). Do not delete any Flask files. Flask serves 100% of `/admin/*` traffic.

---

## Phase 1a — Middleware stack + public/core routers (~1,800 LOC, ~3-4 days)

**Goal:** Wire middleware in correct order, port public + core routers. FastAPI serves these routes; Flask catch-all handles everything else.

**Prerequisites:** Phase 0 merged. `SESSION_SECRET` live in staging.

**Knowledge to read:**
- `flask-to-fastapi-worked-examples.md` §4.1 — OAuth login worked example
- `flask-to-fastapi-deep-audit.md` §1 — Blocker 5 (middleware ordering)
- `flask-to-fastapi-foundation-modules.md` §11.4 — deps/auth.py

**Work items (in order):**

1. Write tests first: `test_external_domain_post_redirects_before_csrf.py` (Blocker 5), `test_middleware_ordering.py` [§2 Blocker 5].
2. Port `src/admin/routers/public.py` (~400 LOC) [§4 Wave 1].
3. Port `src/admin/routers/core.py` (~600 LOC) [§4 Wave 1].
4. Wire middleware stack in `src/app.py`: CORS → Session → Approximated → CSRF → RestCompat → UnifiedAuth [§2-B5].
5. Wire admin router via feature flag (`ADCP_USE_FASTAPI_ADMIN`) [§3.5.6 EP-1].
6. Activate dual-stack shadow testing (TI-2, ~255 LOC) [§3.5.5].
7. Activate response fingerprint comparison [§3.5.5 TI-1].

**Files to create:** `src/admin/routers/public.py`, `src/admin/routers/core.py`, `tests/migration/dual_stack_client.py`, test files.
**Files to modify:** `src/app.py`.

**Exit gate:**
```bash
make quality && tox -e integration && tox -e bdd  # green
curl -s http://localhost:8000/admin/login          # served by FastAPI (check X-Served-By header)
```

**What NOT to do:** Do not port auth/OIDC (Phase 1b). Do not change session cookie name. Do not delete Flask blueprints.

---

## Phase 1b — Auth + OIDC routers + session cutover (~2,200 LOC, ~4-5 days)

**Goal:** Port Google OAuth and OIDC login flows. Cut session cookie to `adcp_session`. Validate SameSite with OIDC `form_post`. This is the highest-risk router work in the migration.

**Prerequisites:** Phase 1a merged. Middleware passing on staging. Authlib `starlette_client` happy-path spike done.

**Knowledge to read:**
- `flask-to-fastapi-worked-examples.md` §4.1-4.2 — OAuth + OIDC worked examples
- `flask-to-fastapi-deep-audit.md` §1 — Blockers 3, 6
- `async-audit/frontend-deep-audit.md` §3 — OAuth + session audit
- `flask_migration_critical_knowledge.md` items 2, 4, 5, 6, 17

**Work items (in order):**

1. Write tests first: `test_admin_error_page.py` (Blocker 3), `test_oauth_redirect_uris_immutable.py` (Blocker 6 — pins `/admin/auth/google/callback`, `/admin/auth/oidc/callback`, `/admin/auth/gam/callback`), `test_oidc_form_post_samesite.py` [§2 Blockers 3, 6; §3.5.3 SG-5].
2. Port `src/admin/routers/auth.py` (~1,100 LOC) — Google OAuth via Authlib [§4 Wave 1].
3. Port `src/admin/routers/oidc.py` (~500 LOC) [§4 Wave 1].
4. Implement Accept-aware `AdCPError` handler; create `templates/error.html` [§2-B3].
5. If OIDC providers use `form_post`: adjust SameSite/CSRF for that callback path [§3.5.3 SG-5].
6. Enable `adcp_session` cookie name [§1.2].
7. Add `pyproject.toml` deps: `pydantic-settings>=2.7.0`, `itsdangerous>=2.2.0` [§4 Wave 1].
8. Send 48-hour customer communication for forced re-login [§3.5.6 EP-7].
9. Write `test_stale_flask_cookie_returns_login.py` — old `session=` cookie returns login page, not 500 [§4 Wave 1].
10. Rollback procedure tested in staging [§4 Wave 1].
11. Manual staging OAuth smoke by 2 engineers [§4 Wave 1].
12. Update `test_architecture_no_flask_imports.py` allowlist (shrink) [§4 Wave 1].

**Files to create:** `src/admin/routers/auth.py`, `src/admin/routers/oidc.py`, `templates/error.html`, test files.
**Files to modify:** `src/app.py`, `pyproject.toml`.

**Exit gate:**
```bash
make quality && tox -e integration && tox -e bdd  # green
# Manual: walk real Google OAuth flow on staging end-to-end
# test_oidc_form_post_samesite.py green
# 2 engineers confirm staging login works
```

**What NOT to do:** Do not port any blueprint beyond auth/OIDC. Comment out `register_blueprint`, do not delete. Do not begin bulk migration.

---

## Phase 2a — Low-risk HTML routers (~3,000 LOC, ~4-5 days)

**Goal:** Port 8 low-risk HTML-rendering admin blueprints. Flask catch-all still wired as safety net.

**Prerequisites:** Phase 1b merged. Stable in staging >= 3 business days. Cookie size < 3.5KB confirmed.

**Knowledge to read:**
- `flask-to-fastapi-worked-examples.md` §4.4-4.5 — products + GAM worked examples
- `flask-to-fastapi-migration.md` §3 — Flask inventory (route counts)
- `flask_migration_critical_knowledge.md` items 16 — getlist

**Work items (in order):**

1. Capture golden-fixture response shapes from Flask for all routes being ported [§3.5.6 EP-4].
2. Port routers (each with golden-fixture comparison test): `accounts.py`, `principals.py`, `users.py`, `settings.py`, `authorized_properties.py`, `publisher_partners.py`, `format_search.py` (4 routes), `api.py` (7 routes — dashboard AJAX) [§4 Wave 2].
3. Use `List[str] = Form()` for every multi-value form field [§3.5.1 SB-2, §3.5.7 CP-2].
4. Every route decorator has `name="admin_<bp>_<endpoint>"` [§2-B1].
5. No `adcp.types.*` as `response_model=` [§3].

**Files to create:** 8 router files under `src/admin/routers/`.

**Exit gate:**
```bash
make quality && tox -e integration && tox -e bdd  # green
# Golden fixtures match for all ported routes
```

**What NOT to do:** Do not port high-risk routers (Phase 2b). Do not port APIs or external contracts (Phase 2b). Do not delete Flask files.

---

## Phase 2b — Medium/high-risk routers + APIs (~5,500 LOC, ~5-7 days)

**Goal:** Port remaining 14 HTML routers (including webhook-preserving ones), 4 JSON API files with Category-2 error shape preservation.

**Prerequisites:** Phase 2a merged. Team freeze announced 48h prior.

**Knowledge to read:**
- `flask-to-fastapi-adcp-safety.md` §1-7 — Category 1 vs 2 classification
- `async-audit/frontend-deep-audit.md` §1-2 — templates + JS audit
- `flask_migration_critical_knowledge.md` items 11, 12

**Work items (in order):**

1. Write `test_category1_native_error_shape.py` and `test_category2_compat_error_shape.py` FIRST [§4 Wave 2].
2. Port HTML routers: `products.py` (audit `getlist` — 12+ sites), `tenants.py`, `gam.py`, `inventory.py`, `inventory_profiles.py`, `creatives.py` (webhook audit), `creative_agents.py`, `signals_agents.py`, `operations.py` (webhook audit), `policy.py`, `workflows.py` [§4 Wave 2].
3. Port JSON APIs: `schemas.py` (external contract — byte-identical), `tenant_management_api.py` (Cat-2), `sync_api.py` (Cat-2 + `/api/sync` mount), `gam_reporting_api.py` (Cat-1) [§4 Wave 2].
4. Implement Category-2 scoped exception handler [§4 Wave 2].
5. `datetime` serialization format audit [§4 Wave 2].
6. Port 8 GAM inventory routes from `src/services/gam_inventory_service.py` to `src/admin/routers/inventory_api.py` — these are NOT blueprints and would be missed otherwise [§3.5.3 SG-1].
7. Change `register_ui_routes(app: Flask)` interface to accept `APIRouter`; re-home adapter routes into `src/admin/routers/adapters.py` [§3.5.3 SG-3].
8. Migrate Flask imports in `src/services/` and `src/adapters/` files [§3.5.3 SG-6].
9. Delete 21 blueprint files, legacy test files/fixtures [§4 Wave 2].
10. Shrink Flask imports allowlist to 3 entries [§4 Wave 2].
11. Write `test_flask_catchall_unreached.py` [§4 Wave 2].
12. Coverage parity check via `scripts/check_coverage_parity.py` [§4 Wave 2].
13. Playwright staging flows: login, create account, create/delete product, logout [§4 Wave 2].

**Files to create:** 14 HTML routers, 4 API routers, `inventory_api.py`, `adapters.py`, test files.
**Files to delete:** 21+ blueprint files, legacy tests.

**Exit gate:**
```bash
make quality && ./run_all_tests.sh  # ALL 5 suites green
git grep -l "flask" src/admin/ | wc -l  # <= 2
# Category-2 error shape byte-identical, schemas contract green
# Playwright green, Flask catch-all 0 requests 24h staging
```

**What NOT to do:** Do not delete `src/admin/app.py` (Phase 3). Do not remove Flask from `pyproject.toml` (Phase 3).

---

## Phase 3 — Cache migration + Flask removal (~2,500 LOC, ~5-7 days)

**Goal:** Delete Flask entirely. Migrate flask-caching to SimpleAppCache. Tag `v1.99.0` BEFORE merging.

> **What "irreversible" means here:** This phase removes Flask from the codebase. A `git revert` of the merge IS technically possible, but it's expensive — you'd also need to `uv lock` (lockfile may drift), rebuild the Docker image, and either revert Phases 0-2 (templates were codemod'd to FastAPI `url_for` format) or accept broken templates. Users would also get force-logged-out again (cookie name reverts). In Phases 0-2, rollback is instant via the feature flag. After Phase 3, rollback means deploying the archived `v1.99.0` container (losing data written since) or a multi-commit revert. After Phase 4 (async driver), rollback becomes effectively impossible. This is why Phase 3 has the strictest entry criteria.

**Prerequisites:** Phase 2b merged. Flask catch-all 0 traffic for 48h. `v1.99.0` tag created and container image archived in registry as break-glass fallback.

**Knowledge to read:**
- `flask-to-fastapi-foundation-modules.md` §11.15 — SimpleAppCache (Decision 6, 12-step order)
- `flask-to-fastapi-execution-details.md` §Wave 3 — rollback + proxy-header smoke tests
- `flask-to-fastapi-migration.md` §15 — dependency changes
- `flask_migration_critical_knowledge.md` items 7, 10

**Work items (in order):**

1. Implement `src/admin/cache.py::SimpleAppCache` (~90 LOC) [§1.2 Decision 6].
2. Migrate 3 cache consumer sites in strict 12-step order (a→l) [§1.2 Decision 6].
3. Fix `from flask import current_app` at `background_sync_service.py:472` → `SimpleAppCache` [§3.5.3 SG-6].
4. Move `atexit` handlers (`webhook_delivery_service.py:185`, `delivery_simulator.py:45`) to FastAPI lifespan post-yield [§3.5.3 SG-2].
5. Delete: `src/admin/app.py`, `activity_stream.py`, `blueprints/` dir, `server.py`, `scripts/run_admin_ui.py`, dead helpers [§4 Wave 3].
6. Modify `src/app.py`: delete Flask mount, `/a2a/` redirect shim, landing route hack, proxy refs, feature flag [§4 Wave 3].
7. `git mv templates/ src/admin/templates/` and `static/ src/admin/static/` [§4 Wave 3].
8. Add `--proxy-headers --forwarded-allow-ips='*'` to uvicorn in `scripts/run_server.py` [§4 Wave 3].
9. Remove Flask deps from `pyproject.toml` (`flask`, `flask-caching`, `flask-socketio`, `python-socketio`, `simple-websocket`, `waitress`, `a2wsgi`, `types-waitress`), run `uv lock` [§4 Wave 3].
10. Rewrite `check_route_conflicts.py` pre-commit hook for FastAPI [§4 Wave 3].
11. Two cache structural guards: `test_architecture_no_flask_caching_imports.py`, `test_architecture_inventory_cache_uses_module_helpers.py` [§1.2 Decision 6].
12. Flask imports allowlist: EMPTY [§4 Wave 3].
13. Write `CHANGELOG.md` v2.0.0 entry (breaking changes list) [§4 Wave 3].
14. **CRITICAL proxy-header smoke tests on staging:** verify `https://` in OAuth redirect URIs, manual browser OAuth flow [§4 Wave 3].
15. Prune planning docs: remove Wave 0-2 execution details that are now historical [§senior eng audit].
16. Migrate 6 critical invariants from planning docs to code comments in `src/app.py` and `src/admin/app_factory.py` [§senior eng audit].

**Files to delete:** `src/admin/app.py`, `src/admin/blueprints/`, `src/admin/server.py`, `scripts/run_admin_ui.py`.
**Files to modify:** `src/app.py`, `pyproject.toml`, `scripts/run_server.py`, `src/admin/templating.py` (template path).

**Exit gate:**
```bash
make quality && ./run_all_tests.sh  # green
rg -w flask .                       # zero hits
docker build .                      # succeeds
# Manual OAuth flow on staging with correct https scheme
# 48h canary clean before proceeding
```

**What NOT to do:** Do not apply `v2.0.0` tag (Phase 5). Do not start async conversion. Do not remove `psycopg2-binary` or `FLASK_SECRET_KEY` dual-read (v2.1).

---

## Phase 4a — Async engine + accounts pilot (~3,000 LOC, ~3-5 days)

**Goal:** Convert database layer to async engine, pilot with accounts domain end-to-end. Proves the async pattern works before cascading to all domains.

**Prerequisites:** Phase 3 stable >= 3 days in staging.

**Knowledge to read:**
- `async-pivot-checkpoint.md` §3 — full target state (corrected 2026-04-12)
- `async-audit/agent-a-scope-audit.md` — file-by-file conversion inventory
- `async-audit/database-deep-audit.md` — 3 critical blockers
- `async-audit/agent-d-adcp-verification.md` — M1-M9 mitigations
- `flask_migration_critical_knowledge.md` items 1, 3, 7, 8, 9, 13, 14, 15

**Work items (in order):**

1. **TI-3: Async correctness harness** (~410 LOC): concurrent session isolation, MissingGreenlet provocation, blocking detection, pool stress [§3.5.5].
2. Rewrite `database_session.py`: lifespan-scoped `create_async_engine`, `async_sessionmaker(expire_on_commit=False, autoflush=False)`, `get_db_session()` as asynccontextmanager, `connect_args={"server_settings": {"statement_timeout": "30000"}}` (NOT event listener — asyncpg has no `cursor()`) [§4 Wave 4, §3.5.2 LB-2].
3. Add `get_sync_db_session()` for adapter Path B [§1.2 Decision 1].
4. Register asyncpg JSONB codec via `set_type_codec()` [§3.5.2 LB-1].
5. Create `src/core/database/deps.py` with `SessionDep` [§4 Wave 4].
6. Convert `AccountRepository` to async, accounts `_impl`/`_raw`, accounts router as pilot [§4 Wave 4].
7. Agent D M1: 8 missing `await` in `api_v1.py` (lines 200, 214, 252, 284, 305, 324, 342, 360) [§4 Wave 4].
8. Agent D M2: 2 missing `await` in `capabilities.py` (lines 265, 310) [§4 Wave 4].
9. Fix `onupdate=func.now()` staleness: application-side timestamp or explicit refresh [§3.5.1 SB-4].
10. Agent D guards M4-M9 [§4 Wave 4].
11. Delete `scripts/deploy/entrypoint_admin.sh` (dead code), migrate `examples/upstream_quickstart.py:137` to `get_db_session()`, harden `DatabaseConnection.connect()` [§1.1 Agent F].
12. Structural guards: `test_architecture_no_runtime_psycopg2.py`, `test_architecture_get_db_connection_callers_allowlist.py` [§1.1 Agent F].
13. Fix Risk #34: `run_all_services.py:175` init via `subprocess.run()` (not in-process) [§1.1].
14. Threadpool tune: `total_tokens = 80` at lifespan startup [§1.2 Decision 1].
15. Create `src/services/background_sync_db.py` (~200 LOC, Decision 9 sync-bridge) [§1.2 Decision 9].
16. Pool sizing: `pool_size=20, max_overflow=10`, combined budget under `max_connections - 15` [§4 Wave 4, §5.3 checkpoint].

**Files to create:** `deps.py`, `background_sync_db.py`, ~10 test files.
**Files to modify:** `database_session.py`, `api_v1.py`, `capabilities.py`, `run_all_services.py`.
**Files to delete:** `scripts/deploy/entrypoint_admin.sh`.

**Exit gate:**
```bash
make quality && tox -e integration && tox -e unit  # green
# Accounts end-to-end async, async correctness harness passes
# Staging 72h clean before Phase 4b
```

**What NOT to do:** Do not convert all repos at once (Phase 4b). Do not mass-convert tests (Phase 4c). Do not convert adapters to async (stay sync per Decision 1).

---

## Phase 4b — Repository/impl sweep + ContextManager refactor (~4,000 LOC, ~5-7 days)

**Goal:** All remaining repositories, UoW, `_impl`/`_raw` to async. Delete ContextManager singleton. Fix all known lazy-load and async edge cases.

**Prerequisites:** Phase 4a merged. Pilot stable 72h.

**Knowledge to read:**
- `async-audit/agent-a-scope-audit.md` — remaining conversion inventory
- `async-pivot-checkpoint.md` §3 "ContextManager refactor" + "Adapters"

**Work items (in order):**

1. Convert all remaining repositories to `async def` [§4 Wave 4].
2. Delete/convert UoW classes (prefer deletion — FastAPI DI is the UoW) [§4 Wave 4].
3. Convert all remaining `_impl`/`_raw` to `async def` [§4 Wave 4].
4. Wrap 18 adapter call sites with `await run_in_threadpool(...)` [§1.2 Decision 1].
5. Wrap 1 adapter call in `operations.py:252` [§1.2 Decision 1].
6. Split `AuditLogger`: `_log_operation_sync` (internal) + `async log_operation` (public) [§1.2 Decision 1].
7. Guard: `test_architecture_adapter_calls_wrapped_in_threadpool.py` [§1.2 Decision 1].
8. **Decision 7:** Delete `ContextManager`/`DatabaseManager`, convert 12 methods to module functions, migrate 7 callers, `mock_ad_server.py` Thread→create_task [§1.2 Decision 7].
9. Guard: `test_architecture_no_singleton_session.py` [§1.2 Decision 7].
10. **Decision 4:** Delete 3 dead query functions, convert 3 live, convert test file [§1.2 Decision 4].
11. **Decision 5:** Delete `product_pricing.py`, inline as DTO at single caller [§1.2 Decision 5].
12. **Decision 8:** Delete SSE route/generator/state/HEAD probe, delete `sse_starlette` dep, fix `api_mode=False→True`, guard `test_architecture_no_sse_handlers.py` [§1.2 Decision 8].
13. Add `selectinload(Product.inventory_profile)` to all Product repo methods [§3.5.1 SB-1].
14. Add `joinedload(ObjectWorkflowMapping.workflow_step)` to `get_object_lifecycle` query [§3.5.1 SB-5].
15. Fix `await session.merge()` in `delivery.py:274` [§3.5.4 AE-3].
16. Rewrite `bulk_insert_mappings` to Core `insert().values()` (stays sync via bridge) [§3.5.2 LB-3].
17. Migrate private `scoped_session` in GAM services to `get_sync_db_session()` [§3.5.3 SG-4].
18. Agent D M3: 8 `await` in `adcp_a2a_server.py` [§4 Wave 4].
19. Convert remaining sync `with get_db_session()` to `async with` [§4 Wave 4].
20. Add `/health/pool` + `/health/deep` endpoints, Prometheus gauges [§4 Wave 4 Agent F].

**Files to modify:** ~40-60 files across `src/core/`, `src/admin/`, `src/services/`, `src/adapters/`.
**Files to delete:** `context_manager.py` (class), `product_pricing.py`, dead query functions.

**Exit gate:**
```bash
make quality && tox -e unit  # green
# All repos async, all _impl async, ContextManager deleted, SSE deleted
```

**What NOT to do:** Do not mass-convert tests (Phase 4c). Do not convert `background_sync_service.py` to async (stays on sync-bridge). Do not delete `psycopg2-binary`.

---

## Phase 4c — Test infrastructure async conversion (~3,000 LOC, ~3-5 days)

**Goal:** Convert test harness, factory-boy, and all integration tests to async.

**Prerequisites:** Phase 4b merged. All production code async.

**Knowledge to read:**
- `async-pivot-checkpoint.md` §3 "Test harness" + "factory_boy"
- `async-audit/testing-strategy.md`

**Work items (in order):**

1. Implement `AsyncSQLAlchemyModelFactory` shim (overrides `_save`, NOT `_create`; no flush) [§1.2 Decision 3].
2. Flip all 15 ORM factories to async base [§1.2 Decision 3].
3. Three factory guards: `test_architecture_factory_inherits_async_base.py`, `test_architecture_factory_no_post_generation.py`, `test_architecture_factory_in_all_factories.py` [§1.2 Decision 3].
4. Convert `IntegrationEnv` to `__aenter__`/`__aexit__` [§4 Wave 4].
5. Switch from `TestClient` to `httpx.AsyncClient(transport=ASGITransport(app=app))` [§4 Wave 4].
6. Mass-convert ~166 integration tests to `async def` + `@pytest.mark.asyncio` [§4 Wave 4].
7. Per-test engine/session fixtures (function-scoped) [§4 Wave 4].
8. Verify xdist parallel execution [§4 Wave 4].
9. BDD stays sync with `asyncio.run()` bridge [§4 Wave 4].

**Files to modify:** `tests/harness/_base.py`, `tests/factories/*.py`, `tests/conftest.py`, ~166 test files.

**Exit gate:**
```bash
./run_all_tests.sh  # ALL 5 suites green
# No MissingGreenlet, no sync TestClient in integration/admin
```

**What NOT to do:** Do not convert BDD tests to fully async. Do not change factory `_create` overrides (only `_save`).

---

## Phase 4d — CI + Docker + docs polish (~1,500 LOC, ~2-3 days)

**Goal:** CI updates, Docker optimization, docs refresh, remaining Agent F non-code items.

**Prerequisites:** Phase 4c merged. All tests green.

**Knowledge to read:**
- `async-audit/agent-f-nonsurface-inventory.md` — 105 non-code items
- `async-audit/agent-e-ideal-state-gaps.md` — remaining idiom upgrades

**Work items (in order):**

1. CI: Align Postgres to 17 across all workflows, add `tox -e driver-compat`, remove dead `test-migrations` hook [§1.1 Agent F].
2. Docker: verify compose `DATABASE_URL` compatibility, RETAIN `libpq-dev`/`libpq5` (Decision 9 — do NOT remove) [§4 Wave 4].
3. `contextvars` request-ID propagation [§4 Wave 4 Agent F].
4. Audit 5 scripts with top-level `database_session` imports for Risk #33 [§4 Wave 4 Agent F].
5. Update `CLAUDE.md` (root) and `/docs` with async patterns [§4 Wave 4 Agent F].
6. Draft `docs/development/async-debugging.md` + `async-cookbook.md` [§4 Wave 4 Agent F].
7. Ensure `FIXME` comments at source for all remaining allowlist entries [§3.5.6 EP-5].

**Files to modify:** CI workflows, `Dockerfile`, `docker-compose*.yml`, `CLAUDE.md`, docs.

**Exit gate:**
```bash
./run_all_tests.sh             # green
docker build . && docker compose up -d  # succeeds
```

**What NOT to do:** Do not bump version to 2.0.0 (Phase 5). Do not remove `psycopg2-binary`.

---

## Phase 5 — Benchmarks + v2.0.0 release (~1,000 LOC, ~3-5 days)

**Goal:** Validate performance parity, tag `v2.0.0`, deploy to production, archive planning artifacts.

**Prerequisites:** Phase 4d merged. Staging stable >= 3 days.

**Knowledge to read:**
- `async-audit/agent-e-ideal-state-gaps.md` — remaining idiom upgrades (defer to v2.1)
- `async-audit/agent-f-nonsurface-inventory.md` — remaining non-code items
- `implementation-checklist.md` §6-7 — post-migration verification + cleanup

**Work items (in order):**

1. Benchmark: async vs sync `baseline-sync.json` on 20 routes + 5 MCP tools [§4 Wave 5].
2. Verify latency net-neutral to ~5% improvement at 50 req/s; tune pool if regression [§4 Wave 5].
3. Scheduler alive-tick log assertion, `/health/pool` + `/health/schedulers` verification [§4 Wave 5].
4. `created_at`/`updated_at` post-commit access audit (expire_on_commit=False consequence) [§4 Wave 5].
5. Delete `database_schema.py` (confirmed orphan), strip stale docstring [§1.2 Decision 5].
6. Ratchet `.duplication-baseline` [§4 Wave 5 Agent F].
7. **TI-6: Production canary system** (~330 LOC) [§3.5.5].
8. Bump `pyproject.toml` version to `2.0.0`, finalize `CHANGELOG.md` [§4 Wave 5].
9. Update auto-memory `flask_to_fastapi_migration_v2.md` to reflect completion [§4 Wave 5].
10. Apply `v2.0.0` tag [§4 Wave 5].
11. Production deploy + 48h monitoring: error rates, latency p50/p99, Docker size, cookie size [§6].
12. File v2.1 tickets: nginx removal, Annotated ratchet, `FLASK_SECRET_KEY` removal, `Apx-Incoming-Host` IP allowlist, `require_tenant_access` is_active, scheduler lease design [§6, §8].
13. Archive `.claude/notes/flask-to-fastapi/` planning artifacts, delete branch after 1 week, remove `CLAUDE.md` migration breadcrumb [§7].

**Files to create:** `scripts/canary/production_canary.py`, `src/routes/health_deep.py`.
**Files to delete:** `src/core/database/database_schema.py`.

**Exit gate:**
```bash
./run_all_tests.sh  # green
# v2.0.0 tag applied, production deploy successful
# 48h monitoring: no 5xx spike, latency within 5% of baseline
# All v2.1/v2.2 tickets filed, planning artifacts archived
```

**What NOT to do:** Do not remove `psycopg2-binary` (v2.1). Do not drop nginx (v2.1). Do not remove `FLASK_SECRET_KEY` dual-read (v2.1). Do not design multi-worker scheduler (v2.2).
