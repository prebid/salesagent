# Layer 0 (L0) — Spike & Foundation Implementation Plan

> **Status:** Draft. Produced from authoritative source docs on 2026-04-18.
> **Scope:** Pure-addition layer. Flask serves 100% of `/admin/*` traffic at L0 exit.
> **Branch:** `feat/v2.0.0-flask-to-fastapi`.
> **Upstream authorities:** `CLAUDE.md` §Critical Invariants; `execution-plan.md` Layer 0; `implementation-checklist.md` §4 Wave 0 / L0; `foundation-modules.md` §11.1-11.15, §11.26, §11.34-11.36; `flask-to-fastapi-deep-audit.md` §1-§2.

---

## 1. L0 mission summary

L0 is a **pure-addition** layer that lands the foundation modules, codemod scripts, structural guards, and observability scaffolding required by L1a-L7, without mutating a single byte of user-visible admin behavior. Flask still serves 100% of `/admin/*` traffic; `src/app.py` middleware stack, router registration, and exception handler are untouched. The 6 Critical Invariants are respected vacuously (no admin routes land at L0) except D2 — **`scoped_session` is retired from `src/core/database/database_session.py` at L0**, flipping the bare-sessionmaker contract that underpins sync-def admin handlers in L1a-L4. L0 also ships the `ADCP_USE_FASTAPI_ADMIN` feature flag and the `X-Served-By` response header so L1a's traffic split is verifiable and instantly reversible.

---

## 2. L0 entry-gate audit

### 2.1 Section 1.1 pre-migration prerequisites

| # | Prerequisite | Status | Evidence |
|---|---|---|---|
| 1 | `SESSION_SECRET` in staging/prod/test secret stores | **PENDING** | Not verified in repo; requires user attestation |
| 2 | `SESSION_SECRET` documented in `.env.example` | **PENDING** | Needs audit of `.env.example` (L0 work item) |
| 3 | `SESSION_SECRET` in `docs/deployment/environment-variables.md` | **PENDING** | L0 work item |
| 4 | OAuth redirect URIs enumerated in runbook | **PENDING** | L0 work item (creates `docs/migration/v2.0-oauth-uris.md`) |
| 5 | External consumer contracts confirmed (tenant_management_api, sync_api, schemas) | **PENDING** | L0 work item |
| 6 | Feature branch `feat/v2.0.0-flask-to-fastapi` exists | **PASS** | Current branch |
| 7 | Customer-comms plan for L2 Flask removal | **N/A (L2)** | Not an L0 gate |
| 8 | Rollback windows documented | **PASS** | `implementation-checklist.md` §5 |
| 9 | Staging matches prod topology | **PASS** | User-attested; agent-team model |
| 10 | `main` passes `make quality` + `tox -e integration` + `tox -e bdd` | **PENDING** | User must re-verify on branch tip (`4514f54d`) before L0-01 lands |
| 11 | `a2wsgi` Flask mount still at `src/app.py:44-45` | **PASS** | Verified at `src/app.py:44-45` (`app.mount("/admin", admin_wsgi)` + `app.mount("/", admin_wsgi)`) |
| 12 | v1.99.0 tag plan documented | **PASS** | `CLAUDE.md` §NO-GO release-tag naming rule |
| 13 | Agent-workflow gates confirmed | **PASS** | `CLAUDE.md` §Execution model (landed `5271ed49`) |
| 14 | Rollback cold-read test | **PENDING** | User must run before L1a entry, not required for L0 entry |

### 2.2 Agent-F pre-L0 hardening items (landed in pre-L0 PR `246067de..64cf0125`)

| Item | Status | Commit |
|---|---|---|
| `psycopg2-binary` retained + `asyncpg>=0.30.0,<0.32` added alongside | **PASS** | `246067de` |
| `DATABASE_URL` sslmode→ssl rewriter (conditional on `+asyncpg`) | **PASS** | `d2399452` |
| `DatabaseConnection.connect()` hardened (`connect_timeout=10`, `statement_timeout=5000`) | **PASS** | `cd1a59a1` |
| `examples/upstream_quickstart.py` migrated to ORM | **PASS** | `7f6433c5` |
| `scripts/deploy/entrypoint_admin.sh` deleted (dead) | **PASS** | `e877ed97` |
| CI Postgres aligned to 17 across `.github/workflows/test.yml` | **PASS** | `719d8049` |
| Dead `test-migrations` pre-commit hook removed | **PASS** | `fe9aefb4` |
| `.env.example` canonical minimal ref | **PASS** | `da6e5d2e` |
| Structural guard 1: `test_architecture_no_runtime_psycopg2.py` (1-entry allowlist: `db_config.py`) | **PASS** | `64cf0125` |
| Structural guard 2: `test_architecture_get_db_connection_callers_allowlist.py` (frozen `run_all_services.py`) | **PASS** | `64cf0125` |
| Structural guard 3: `test_architecture_no_module_level_get_engine.py` (empty allowlist) | **PASS** | `64cf0125` |
| Planning-doc sync with agent-team execution model | **PASS** | `5271ed49` |
| `async-debugging.md` + `async-cookbook.md` skeletons drafted | **PASS** | `f23f3fe4` |

### 2.3 Section 1.2 architectural decisions recorded

All 12 rows in §1.2 are **PASS** — `CLAUDE.md` §Critical Invariants + §D1/D2/D3/D8 decisions cover each. (Decisions 1/7/9 flagged `[L5+]`; D4/D5/D6/D8 flagged for the respective layers; session-cookie rename locked for L1a.)

### 2.4 Pre-L0 refactor PRs (PRE-1 through PRE-4)

| PR | Status | Rationale |
|---|---|---|
| PRE-1 `src/admin/blueprints/oidc.py:173` nested-session fix | **PENDING** | **Hard blocker for L0-03** (`scoped_session` retirement) |
| PRE-2 `src/admin/blueprints/operations.py:304` extract-dict-close-outer | **PENDING** | **Hard blocker for L0-03** |
| PRE-3 `src/admin/blueprints/workflows.py:158` extract-dict-close-outer | **PENDING** | **Hard blocker for L0-03** |
| PRE-4 `ContextManager(DatabaseManager)` runtime compat test | **PENDING** | Soft blocker — deferred to L4 Spike 4.5 if PRE-4 reveals refactor is larger than compat-test scope |

### 2.5 L0 entry-gate verdict

**L0 can enter** after: (a) user attests the three `SESSION_SECRET` rows, (b) user confirms `main` is green on `4514f54d`, (c) PRE-1/2/3 land (these are strict improvements under CURRENT `scoped_session` world and ship as independent commits before L0-03).

---

## 3. Work items

Work items run **roughly** in order, but items at the same depth are parallelizable by different agents. Each item lists rationale, files touched, LOC estimate, test coverage, dependencies, `foundation-modules.md` reference, and risk.

### L0-00 directory rename `src/admin/blueprints` → `src/admin/routers` (day-1 codemod, single commit)

- **Rationale:** Eliminates mixed-directory state (`blueprints/` vs `routers/`) across the entire L0→L1d window. D8 #6 breaking rename, zero behavioral change.
- **Files:** `git mv src/admin/blueprints src/admin/routers`; codemod ~40 importers via `ast.NodeTransformer` one-liner.
- **LOC:** ~5-line diff × ~40 importers ≈ 200 LOC moved, 0 LOC net new.
- **Tests:** `tests/unit/test_architecture_no_blueprints_dir.py` (empty allowlist; asserts directory absence + zero `from src.admin.blueprints.` references). Run full `make quality` after.
- **Dependencies:** None.
- **Reference:** `execution-plan.md` Layer 0 day-1 codemod paragraph.
- **Risk:** **LOW** — mechanical rename; guard catches misses.

### L0-01 foundation-modules-discipline guards (written FIRST, TDD)

- **Rationale:** Per `CLAUDE.md` §Test-Before-Implement and `execution-plan.md` item 1, structural guards land as Red commits BEFORE the foundation modules they enforce. This locks the specification in code before implementation.
- **Files:** 16 guard test files (see §5 Structural Guard Inventory). All under `tests/unit/architecture/` (new subfolder — see Open Question §7.1).
- **LOC:** ~120 LOC per guard × 16 guards = ~1,920 LOC tests. Meta-test fixture ~60 LOC per guard.
- **Tests:** Each guard self-tests via meta-fixture: a hand-crafted violating file fixture proves the AST scanner fires. Green state means: guard runs, meta-fixture green, target code absent (so guard's real scan is vacuously green).
- **Dependencies:** L0-00 must land first (guard scanning paths assume `src/admin/routers/`).
- **Reference:** `foundation-modules.md` §11.26 meta-guard skeleton; `implementation-checklist.md` §5.5 guards table.
- **Risk:** **MEDIUM** — meta-test-fixture authorship is novel; each guard needs its own planted-violation YAML fixture per `/write-guard` skill pattern.

### L0-02 AdCP boundary protective tests (9 tests, written FIRST)

- **Rationale:** `execution-plan.md` L0 item 1a — protects the AdCP MCP/A2A/REST surface from accidental breakage during L1-L5 work.
- **Files:**
  - `tests/migration/test_openapi_byte_stability.py` (byte-hash snapshot of `/openapi.json`)
  - `tests/migration/test_mcp_tool_inventory_frozen.py` (MCP tool name+signature frozen)
  - `tests/migration/test_a2a_agent_card_snapshot.py` (agent card JSON frozen)
  - `tests/unit/architecture/test_architecture_approximated_middleware_path_gated.py` (empty allowlist)
  - `tests/unit/architecture/test_architecture_csrf_exempt_covers_adcp.py` (empty allowlist)
  - `tests/unit/architecture/test_architecture_admin_routes_excluded_from_openapi.py` (empty allowlist)
  - `tests/integration/test_error_shape_contract.py` (Category 2 `{"success":false,"error":...}` shape pinned)
  - `tests/integration/test_schemas_discovery_external_contract.py` (first-order audit #4)
  - `tests/integration/test_rest_response_wire.py` (REST wire snapshot)
- **LOC:** ~1,000 LOC tests + ~200 LOC fixture JSON snapshots.
- **Tests:** All land green at L0 (capture baseline on first run). L1+ Red-Greens against these fixtures.
- **Dependencies:** `tests/migration/` directory does not exist yet — L0-02 creates it.
- **Reference:** `implementation-checklist.md` §5.5 guards table + `adcp-safety.md` §7.
- **Risk:** **LOW-MEDIUM** — OpenAPI byte-hash is sensitive to transitive Pydantic/FastAPI version bumps; fixture regeneration protocol needs to be documented.

### L0-03 D2 `scoped_session` retirement in `src/core/database/database_session.py`

- **Rationale:** Critical Invariant #4 — admin handlers use sync `def` with **bare `sessionmaker`** (D2). Current file has `_scoped_session = scoped_session(_session_factory)` at line 196 + `scoped.remove()` at lines 206, 278, 289, 373, 322. Retirement flips the contract before L1a admin handlers begin appearing.
- **Files:**
  - `src/core/database/database_session.py` — rewrite `get_engine()` to drop `_scoped_session` creation; rewrite `get_db_session()` to call `_session_factory()` directly (fresh `Session` per `with`-block); rewrite `reset_engine()` to skip `.remove()`; rewrite `execute_with_retry()` to skip `scoped.remove()` call; rewrite `DatabaseManager.session` property to call `_session_factory()` directly; delete `get_scoped_session()` function (all call sites audited).
  - `tests/unit/architecture/test_architecture_no_scoped_session.py` (empty allowlist; AST-scans `src/` for `from sqlalchemy.orm import scoped_session` and `scoped_session(` calls). **Already exists per CLAUDE.md reference** — verify it stays green after the refactor.
- **LOC:** ~80 LOC production diff (delete ~40, rewrite ~40); ~60 LOC test.
- **Tests:**
  - Red: `test_scoped_session_retirement.py` — write a test that calls `get_db_session()` in thread A, asserts `id(session_A) != id(session_B)` when called in thread B (proves no registry sharing). Currently fails (both threads get the same scoped session).
  - Green: rewrite `get_db_session()`. Re-run `tox -e integration` + `tox -e bdd` against PRE-1/2/3 refactored blueprints.
  - Guard: `test_architecture_no_scoped_session.py` green.
- **Dependencies:** **Hard blocker:** PRE-1, PRE-2, PRE-3 landed. Soft blocker: PRE-4 compat test run (detached-instance check on `ContextManager`).
- **Reference:** `CLAUDE.md` §Critical Invariants #4; `foundation-modules.md` §D8-native + §11.0.2; `implementation-checklist.md` §387-396 PRE-1..PRE-4.
- **Risk:** **HIGH** — any untracked nested-session site in admin code surfaces as detached-instance or lost-update under bare sessionmaker. PRE-1..PRE-3 address the 3 known sites; unknown sites are the residual risk. Mitigation: run the full `tox -e integration` suite + targeted e2e admin login flow on staging before L1a entry.

### L0-04 foundation module: `src/admin/deps/messages.py` (D8-native replaces flash.py)

- **Rationale:** Flash-message state across 366 `flash()` call sites (landed at L1+ codemod). Session-backed `list[FlashMessage]` per D8 #4.
- **Files:** `src/admin/deps/messages.py` (~100 LOC); `src/admin/deps/__init__.py` (stub, ~2 LOC).
- **LOC:** ~100 production + ~120 unit tests.
- **Tests:**
  - Red: `tests/unit/admin/test_messages_dep.py` — round-trip info/success/warning/error through a `TestClient` with in-memory session backend. Fails (module does not exist) with ImportError — planted-red pattern per `CLAUDE.md` §Red-Green.
  - Green: implement module per `foundation-modules.md` §D8-native.1.
  - Also add `tests/unit/architecture/test_architecture_no_admin_wrapper_modules.py` (empty allowlist; asserts `src/admin/flash.py`, `src/admin/sessions.py`, `src/admin/templating.py` do NOT exist).
- **Dependencies:** L0-01 (guards land first).
- **Reference:** `foundation-modules.md` §D8-native.1; `implementation-checklist.md` §423-424.
- **Risk:** **LOW** — greenfield module, no Flask dependency.

### L0-05 foundation module: `src/admin/deps/templates.py` (D8-native replaces templating.py)

- **Rationale:** `Jinja2Templates` attached to `app.state.templates` at lifespan startup (not a wrapper module). `TemplatesDep` + `BaseCtxDep` replace Flask's `inject_context()` processor.
- **Files:** `src/admin/deps/templates.py` (~30 LOC for `TemplatesDep`; ~60 LOC for `BaseCtxDep`).
- **LOC:** ~100 production + ~150 unit tests.
- **Tests:**
  - Red: `tests/unit/admin/test_templates_dep.py` — asserts `BaseCtxDep` returns dict with keys `{messages, support_email, sales_agent_domain, user_email, user_authenticated, user_role, test_mode}`; fails ImportError.
  - Green: implement module.
  - Also add `tests/unit/architecture/test_template_context_completeness.py` (empty allowlist; asserts `BaseCtxDep()(request)` returns all 7 keys).
- **Dependencies:** L0-01.
- **Reference:** `implementation-checklist.md` §422; `foundation-modules.md §D8-native`.
- **Risk:** **LOW**.

### L0-06 foundation module: `src/admin/csrf.py` (CSRFOriginMiddleware)

- **Rationale:** Critical Invariant #5 — pure-ASGI Origin header validation; exempts MCP/A2A/_internal/static/OAuth-callback paths.
- **Files:** `src/admin/csrf.py` (~120 LOC).
- **LOC:** ~120 production + ~180 tests.
- **Tests:**
  - Red: 7 Origin-scenario unit tests (missing, matching, scheme-mismatch, port-mismatch, subdomain, null, evil) fail because module absent.
  - Green: implement per `foundation-modules.md` §11.7.
  - Guard: `test_architecture_csrf_exempt_covers_adcp.py` green.
- **Dependencies:** L0-01 (guard first).
- **Reference:** `foundation-modules.md` §11.7; Critical Invariant #5.
- **Risk:** **LOW-MEDIUM** — Origin header parsing edge cases (null, `file://`, IPv6 brackets) need coverage; middleware is NOT wired into `src/app.py` at L0 (L1a wires it) so no runtime exposure yet.

### L0-07 foundation module: `src/admin/middleware/external_domain.py` (ApproximatedExternalDomainMiddleware)

- **Rationale:** Critical Invariant #5 — Approximated runs BEFORE CSRF; 307 (not 302) to preserve POST body; path-gated to `/admin/*` + `/tenant/*`.
- **Files:** `src/admin/middleware/external_domain.py` (~90 LOC); `src/admin/middleware/__init__.py` (stub).
- **LOC:** ~90 production + ~120 tests.
- **Tests:**
  - Red: unit test asserts 307 with preserved POST body; fails ImportError.
  - Green: implement per `foundation-modules.md` §11.8.
  - Guard: `test_architecture_approximated_middleware_path_gated.py` green.
- **Dependencies:** L0-01.
- **Reference:** `foundation-modules.md` §11.8.
- **Risk:** **LOW**.

### L0-08 foundation module: `src/admin/middleware/fly_headers.py` (FlyHeadersMiddleware)

- **Rationale:** Rewrites `Fly-Forwarded-Proto` → `X-Forwarded-Proto` complementing uvicorn's `--proxy-headers` flag.
- **Files:** `src/admin/middleware/fly_headers.py` (~40 LOC).
- **LOC:** ~40 production + ~80 tests.
- **Tests:** Red: unit test with `Fly-Forwarded-Proto: https` asserts `request.url.scheme == "https"`; Green: implement.
- **Dependencies:** L0-01.
- **Reference:** `foundation-modules.md` §11.9.
- **Risk:** **LOW**.

### L0-09 foundation module: `src/admin/middleware/request_id.py` (RequestIDMiddleware)

- **Rationale:** Per-request UUID stamped on `request.state.request_id`; echoed as `X-Request-ID`. Wired to structlog binding at L4.
- **Files:** `src/admin/middleware/request_id.py` (~30 LOC).
- **LOC:** ~30 production + ~60 tests.
- **Tests:** Red: unit test asserts response header echoed AND inbound-provided ID preserved; Green: implement.
- **Dependencies:** L0-01.
- **Reference:** `foundation-modules.md` §11.9.5.
- **Risk:** **LOW**.

### L0-10 foundation module: `src/admin/unified_auth.py` (UnifiedAuthMiddleware)

- **Rationale:** Pure-ASGI auth middleware; resolves `ResolvedIdentity`; path-gated; Accept-aware 401 vs 302-to-login. Replaces Flask's `require_auth` decorator.
- **Files:** `src/admin/unified_auth.py` (~250 LOC).
- **LOC:** ~250 production + ~300 tests.
- **Tests:**
  - Red: 4 unit tests — (a) authenticated request populates `request.state.identity`, (b) unauth `/admin/*` + `Accept: text/html` → 302 to login, (c) unauth `/admin/*` + `Accept: application/json` → 401, (d) public paths bypass. All fail ImportError.
  - Green: implement per `foundation-modules.md` §11.36 middleware stack.
- **Dependencies:** L0-01, L0-04 (Messages dep for post-login flash).
- **Reference:** `foundation-modules.md` §11.4 deps/auth.py + §11.36 middleware stack; Critical Invariant #3/#5.
- **Risk:** **MEDIUM** — largest L0 module; identity resolution has subtle path-gating rules (the D1 `/tenant/` mount adds a second path prefix class to gate).

### L0-11 foundation module: `src/admin/oauth.py` (Authlib OAuth client registration)

- **Rationale:** Critical Invariant #6 — OAuth redirect URIs byte-immutable. Constants pinned in code.
- **Files:** `src/admin/oauth.py` (~60 LOC).
- **LOC:** ~60 production + ~80 tests.
- **Tests:**
  - Red: `tests/unit/architecture/test_oauth_callback_routes_exact_names.py` — pins the 3 exact names+paths: `/admin/auth/google/callback`, `/admin/auth/oidc/callback`, `/admin/auth/gam/callback`. Fails because module absent.
  - Green: implement.
  - Note: since no admin routes are included at L0, `url_for('admin_auth_google_callback')` cannot resolve yet — test asserts constants in the module source, not route registration. L1a adds the route-registration assertion as a Red-Green pair.
- **Dependencies:** L0-01.
- **Reference:** `foundation-modules.md` §11.6; Critical Invariant #6.
- **Risk:** **LOW-MEDIUM** — the L0 test asserts string constants; L1a tightens the assertion to route registration. The test must be honestly scoped or drift ensues.

### L0-12 foundation module: `src/admin/deps/auth.py` + `deps/tenant.py` + `deps/audit.py`

- **Rationale:** FastAPI `Depends()`-based auth/tenant/audit ports. Sync `def` with `with get_db_session()` per Critical Invariant #4 (corrected 2026-04-12 from original async pivot).
- **Files:**
  - `src/admin/deps/auth.py` (~260 LOC) — `CurrentUserDep`, `RequireAdminDep`, `RequireSuperAdminDep`.
  - `src/admin/deps/tenant.py` (~90 LOC) — `CurrentTenantDep` with `is_active=True` filter.
  - `src/admin/deps/audit.py` (~110 LOC) — audit port via `request.state`, NOT `flask.g`.
- **LOC:** ~460 production + ~350 tests.
- **Tests:** Red: `tests/unit/admin/test_deps_auth.py`, `test_deps_tenant.py`, `test_deps_audit.py` — each Red-Green pair tests one dep's happy path + one denial path.
- **Dependencies:** L0-01, L0-03 (bare-sessionmaker), L0-04 (Messages for auth failure flash).
- **Reference:** `foundation-modules.md` §11.4, §11.5; `implementation-checklist.md` §430-432.
- **Risk:** **MEDIUM** — the is_active filter at `deps/tenant.py` is a latent-bug fix (pre-existing Flask code did NOT filter is_active); must not break existing admin tests. Mitigation: green against `tox -e integration` before L0 exit.

### L0-13 foundation module: `src/admin/cache.py` (SimpleAppCache)

- **Rationale:** Decision 6 — `flask-caching` replacement using `cachetools.TTLCache(maxsize=1024, ttl=300)` + `threading.RLock`. NOT wired at L0 (wired in Wave 3 / L2 per D6 12-step migration).
- **Files:** `src/admin/cache.py` (~90 LOC).
- **LOC:** ~90 production + ~150 tests.
- **Tests:** Red: round-trip set/get + TTL expiry + 4-thread RLock contention. Green: implement per `foundation-modules.md` §11.15.
  Also add `test_architecture_inventory_cache_uses_module_helpers.py` (empty allowlist; asserts no `from flask_caching import` in `src/`).
- **Dependencies:** L0-01.
- **Reference:** `foundation-modules.md` §11.15; Decision 6.
- **Risk:** **LOW** — not yet consumed.

### L0-14 foundation module: `src/admin/content_negotiation.py` (Accept-aware AdCPError handler)

- **Rationale:** Critical Invariant #3 — `AdCPError` renders HTML for `/admin/*` + `/tenant/*` browsers, JSON otherwise. Replaces the JSON-only handler currently at `src/app.py:82-88`.
- **Files:** `src/admin/content_negotiation.py` (~50 LOC); the `_response_mode()` helper and the Accept-aware handler live here but are **NOT wired** into `src/app.py` at L0 (wired at L1a).
- **LOC:** ~50 production + ~100 tests.
- **Tests:**
  - Red: unit test asserts `/admin/x + Accept: text/html` → HTML path; `/admin/x + Accept: application/json` → JSON; `/mcp/x` always JSON.
  - Green: implement.
  - Guard: `test_architecture_exception_handlers_accept_aware.py` (empty allowlist).
- **Dependencies:** L0-01, L0-05 (TemplatesDep).
- **Reference:** `foundation-modules.md` §11.10 and §11.11; Critical Invariant #3.
- **Risk:** **LOW-MEDIUM** — the `_response_mode()` helper must check `request.url.path.startswith(("/admin/", "/tenant/"))` — both prefixes per D1 2026-04-16.

### L0-15 foundation module: `src/admin/app_factory.py` (empty `build_admin_router()`)

- **Rationale:** Seed the admin router aggregate. Empty at L0 (no routes included). Populated per-sub-router at L1a/L1c/L1d.
- **Files:** `src/admin/app_factory.py` (~80 LOC); `src/admin/routers/__init__.py` (stub).
- **LOC:** ~80 production + ~40 tests.
- **Tests:**
  - Red: `tests/unit/admin/test_app_factory_empty.py` — asserts `build_admin_router().routes == []`. Fails ImportError.
  - Green: implement.
- **Dependencies:** L0-01.
- **Reference:** `foundation-modules.md` §11.10.
- **Risk:** **LOW**.

### L0-16 foundation-modules import smoke test

- **Rationale:** `test_foundation_modules_import.py` asserts all 13 foundation modules import without ImportError in under 1 second (slow = likely circular).
- **Files:** `tests/unit/test_foundation_modules_import.py` (~40 LOC).
- **LOC:** ~40 tests.
- **Tests:** Runs `python -c "import src.admin.deps.messages, src.admin.deps.templates, ..."`; fails if any circular.
- **Dependencies:** L0-04 through L0-15.
- **Reference:** `execution-plan.md` Exit gate for Work Item 2.
- **Risk:** **LOW**.

### L0-17 `form_error_response()` shared helper

- **Rationale:** DRY helper for form-validation re-rendering across 25 routers (engineering-practice-3).
- **Files:** `src/admin/helpers/form_errors.py` (~50 LOC).
- **LOC:** ~50 production + ~80 tests.
- **Tests:** Red: unit test asserts function signature + returns `TemplateResponse` with 422 status. Green: implement.
- **Dependencies:** L0-01.
- **Reference:** `execution-plan.md` item 3; EP-3.
- **Risk:** **LOW**.

### L0-18 feature flag `ADCP_USE_FASTAPI_ADMIN` + `X-Served-By` middleware

- **Rationale:** L0 entry observability + instant rollback mechanism per `CLAUDE.md` §Observability §6.5 and `implementation-checklist.md` §2088-2091 (L0 entry observability).
- **Files:**
  - `src/core/feature_flags.py` or extension of `src/core/config.py` (~20 LOC) — reads `ADCP_USE_FASTAPI_ADMIN` env var via pydantic-settings.
  - `src/admin/middleware/served_by.py` (~20 LOC) — sets `X-Served-By: flask` at L0 (FastAPI admin routers don't exist yet; wiring this middleware is L1a's job, but stamping the constant at L0 lets the guard be introduced now).
- **LOC:** ~40 production + ~80 tests.
- **Tests:**
  - Red: `tests/unit/architecture/test_architecture_feature_flag_gate_active.py` — asserts `ADCP_USE_FASTAPI_ADMIN` read via config module, not direct `os.environ.get`. Empty allowlist; retires at L2.
  - Red: `tests/unit/architecture/test_architecture_x_served_by_header_emitted.py` — asserts `ServedByMiddleware` present in `src/admin/middleware/served_by.py` and emits `X-Served-By` header key. Empty allowlist; retires at L2.
  - Green: implement both.
- **Dependencies:** L0-01.
- **Reference:** `implementation-checklist.md` §2088-2091; §5.5 guards table rows `test_architecture_feature_flag_gate_active` + `test_architecture_x_served_by_header_emitted`.
- **Risk:** **LOW-MEDIUM** — the flag is not yet wired into routing (L1a wires it); `pydantic-settings>=2.7.0` pin needs to land in `pyproject.toml` at L0 (per `execution-plan.md` §138).

### L0-19 `/metrics` endpoint scaffold (Prometheus placeholder)

- **Rationale:** L0 entry observability item per `implementation-checklist.md` §2089.
- **Files:** `src/routes/metrics.py` (~40 LOC) — FastAPI router returning 200 with empty Prometheus exposition. Mounted on `src/app.py` via new `app.include_router(metrics_router)` — **this is NOT a middleware-stack mutation**, just a leaf route; safe at L0.
- **LOC:** ~40 production + ~40 tests.
- **Tests:** Red: `GET /metrics` returns 404 (not wired). Green: returns 200 + `text/plain; version=0.0.4` + empty body.
- **Dependencies:** None at L0; alignment with `foundation-modules.md` §11.31 health endpoints deferred to L2.
- **Reference:** `implementation-checklist.md` §2165-2168.
- **Risk:** **LOW** — the NOT-to-do list in `execution-plan.md` §145 says "Do not modify `src/app.py`" but **observability scaffolding is explicitly called out as an L0 entry-gate item** in §2088. Resolution: adding a bare `app.include_router(metrics_router)` line is the minimum acceptable modification. Open Question §7.2 escalates.

### L0-20 template codemod scripts (written, NOT executed at L0)

- **Rationale:** `execution-plan.md` items 6, 7, 8 — script lands in L0, execution is L1a.
- **Files:**
  - `scripts/generate_route_name_map.py` (~50 LOC) — imports `src/admin/app.py::create_app()` + walks `url_map.iter_rules()`, emits `FLASK_TO_FASTAPI_NAME` and `HARDCODED_PATH_TO_ROUTE` dicts as a generated Python module.
  - `scripts/codemod_templates_greenfield.py` (~200 LOC) — Pass 0 (csrf/g/flash), Pass 1a (static), Pass 1b (hardcoded paths), Pass 2 (Flask-dotted names).
- **LOC:** ~250 production + ~250 tests.
- **Tests:**
  - Red: `tests/migration/test_codemod_idempotent.py` — applies codemod to a frozen-state template fixture once, then twice; asserts second run produces zero diff. Empty allowlist.
  - Green: implement codemod.
  - Bonus: `scripts/codemod_templates_greenfield.py --check templates/` runs clean at L0 exit (exits 0) because the codemod is a no-op on the pre-codemod templates (they still use Flask patterns; the codemod has NOT been run).
- **Dependencies:** L0-01 (codemod idempotent guard).
- **Reference:** `execution-plan.md` items 6-8; `implementation-checklist.md` §442-464.
- **Risk:** **MEDIUM** — the exit gate says `test_codemod_idempotent.py` green at L0. Interpretation: the codemod is authored such that re-running on a hypothetical post-codemod fixture is idempotent. The test uses a frozen POST-codemod fixture as input (or captures one by running the codemod once at green-commit time). Open Question §7.3 escalates.

### L0-21 golden-fixture capture infrastructure

- **Rationale:** `execution-plan.md` item 10 — response fingerprint capture infra, powered by the `capture-fixtures` skill. L1+ uses this for parity verification.
- **Files:**
  - `tests/migration/fingerprint.py` (~100 LOC) — fingerprint-computation helper.
  - `tests/migration/conftest_fingerprint.py` (~60 LOC) — pytest fixtures.
  - `tests/migration/test_response_fingerprints.py` (~80 LOC) — per-fingerprint assertions.
  - `tests/migration/fixtures/fingerprints/*.json` — captured baselines (populated at L0 exit by running `capture-fixtures` skill against pre-L1 Flask admin).
- **LOC:** ~240 production test code + ~N fixture JSON files (N = number of routes captured).
- **Tests:** The tests ARE the fingerprint assertions; they capture-on-first-run, compare thereafter.
- **Dependencies:** L0-04 through L0-15 (foundation modules merged so fingerprint run reflects their import side-effects).
- **Reference:** `execution-plan.md` item 10; `implementation-checklist.md` §TI-1.
- **Risk:** **LOW-MEDIUM** — fingerprint scope (which routes to capture, how to handle dynamic content) needs per-route calibration. Safer to start with a small set (home, login, 3 dashboard pages) and expand at L1a.

### L0-22 `IntegrationEnv.get_admin_client()` harness extension

- **Rationale:** `execution-plan.md` item 11 — enables L1+ integration tests to obtain a Starlette `TestClient` with `dependency_overrides` snapshot/restore.
- **Files:** `tests/harness/_base.py` — add `get_admin_client()` method near line 914 as sibling to `get_rest_client()`.
- **LOC:** ~60 modification + ~60 unit test.
- **Tests:** Red: `tests/unit/architecture/test_architecture_harness_overrides_isolated.py` — creates two `get_admin_client()` contexts sequentially, sets a dep override in the first, asserts the second sees no leakage. Empty allowlist.
- **Dependencies:** L0-01, L0-15 (empty admin router).
- **Reference:** `execution-plan.md` item 11; Blocker-3.3.
- **Risk:** **LOW**.

### L0-23 doc-drift linters (6 guards, §11.34)

- **Rationale:** `foundation-modules.md` §11.34 — prevents spec drift in planning docs. Each linter scans `.claude/notes/flask-to-fastapi/*.md`.
- **Files:**
  - `tests/unit/architecture/test_architecture_invariants_consistent.py`
  - `tests/unit/architecture/test_architecture_oauth_uris_consistent.py`
  - `tests/unit/architecture/test_architecture_csrf_implementation_consistent.py`
  - `tests/unit/architecture/test_architecture_layer_assignments_consistent.py`
  - `tests/unit/architecture/test_architecture_spike_table_consistent.py`
  - `tests/unit/architecture/test_architecture_proxy_headers_in_entrypoints.py`
- **LOC:** ~100 LOC per linter × 6 = ~600 LOC + meta fixtures.
- **Tests:** Each ships with a planted-violation fixture markdown file proving it catches drift.
- **Dependencies:** L0-01.
- **Reference:** `foundation-modules.md` §11.34.
- **Risk:** **MEDIUM** — markdown-parsing + fuzzy-substring assertions are brittle. Each linter needs a well-scoped allowlist for legitimate mentions in rejection sentences.

### L0-24 native-idiom guard: `test_architecture_no_pydantic_v1_config.py` (§11.35)

- **Rationale:** Codebase has 0 `class Config:` blocks today; monotonic empty allowlist prevents L1/L2/L4 regression.
- **Files:** `tests/unit/architecture/test_architecture_no_pydantic_v1_config.py` (~80 LOC).
- **LOC:** ~80 tests.
- **Tests:** Planted violation: a fixture file with `class MyModel(BaseModel): class Config: ...`; guard catches it. Green state: real `src/` scan returns 0 matches.
- **Dependencies:** L0-01.
- **Reference:** `foundation-modules.md` §11.35.
- **Risk:** **LOW**.

### L0-25 meta-guards (structural-guard hygiene)

- **Rationale:** `foundation-modules.md` §11.26 + `implementation-checklist.md` §1929 — enforce allowlist monotonicity + FIXME coverage across all `Captured→shrink` guards.
- **Files:**
  - `tests/unit/architecture/test_structural_guard_allowlist_monotonic.py` (~150 LOC) — diffs `*_allowlist.py` / `allowlist.txt` / embedded `ALLOWED = {...}` sets; fails if any set added entries.
  - `tests/unit/architecture/test_architecture_allowlist_fixme_coverage.py` (~100 LOC) — asserts every allowlist entry has a paired `# FIXME(salesagent-xxxx)` comment at the entry's source location.
- **LOC:** ~250 tests.
- **Tests:** Planted violation fixtures for each meta-guard.
- **Dependencies:** L0-01.
- **Reference:** `foundation-modules.md` §11.26; `implementation-checklist.md` §1929.
- **Risk:** **MEDIUM** — meta-guards must parse the repo's ad-hoc allowlist formats (`.txt` file, embedded `set`, JSON); format-inventory is a prerequisite.

### L0-26 `pyproject.toml` dep bump + documentation updates

- **Rationale:** `execution-plan.md` §135, `implementation-checklist.md` §521 — `pydantic-settings>=2.7.0` added as explicit dep (currently transitive). `itsdangerous` stays transitive (Origin-based CSRF doesn't need it).
- **Files:**
  - `pyproject.toml` — add `pydantic-settings>=2.7.0` to `[project.dependencies]`.
  - `.env.example` — add `SESSION_SECRET`, `ADCP_USE_FASTAPI_ADMIN=false`, `ADCP_THREADPOOL_TOKENS=80`.
  - `docs/deployment/environment-variables.md` — add entries.
  - `docs/migration/v2.0-oauth-uris.md` — enumerate the 3 pinned OAuth callback URIs (L0-11 byte-pin).
- **LOC:** ~40 LOC across config files + docs.
- **Tests:** None new (docs/config changes; `tox -e unit` green is the assertion).
- **Dependencies:** None.
- **Reference:** `execution-plan.md` §135; `implementation-checklist.md` §65-75.
- **Risk:** **LOW** — discipline waiver `discipline: N/A - dep bump; golden fingerprints unchanged`.

### L0-27 BDD sweep for Flask references

- **Rationale:** `implementation-checklist.md` §505 — BDD feature files and step definitions swept for bare `flask` imports, `url_for` blueprint-dot-syntax, `script_root`.
- **Files:** `tests/bdd/**/*.py` + `tests/bdd/**/*.feature` (audit-and-fix sweep).
- **LOC:** ≈0 net changes expected (BDD tests should be feature-level; if hits appear, they're legitimate bugs).
- **Tests:** `rg -n 'from flask|import flask|script_root|url_for\([^)]*\.' tests/bdd/` returns zero unexpected hits.
- **Dependencies:** None.
- **Reference:** `implementation-checklist.md` §505.
- **Risk:** **LOW**.

### L0-28 `.pre-commit-hooks/check_hardcoded_urls.py` rewrite

- **Rationale:** `implementation-checklist.md` §501 — hook rewritten to reject Jinja `scriptRoot|script_root|script_name|admin_prefix|static_prefix` and accept only `url_for()`.
- **Files:** `.pre-commit-hooks/check_hardcoded_urls.py`; `.pre-commit-config.yaml` registration.
- **LOC:** ~80 hook + ~60 fixture tests.
- **Tests:** Fixture template with `scriptRoot` → hook exits non-zero; fixture with `url_for()` → hook exits 0.
- **Dependencies:** None.
- **Reference:** `implementation-checklist.md` §501.
- **Risk:** **LOW**.

### L0-29 static JS URL strategy decision + doc

- **Rationale:** `implementation-checklist.md` §502 — exactly one of (a) inline JS, (b) `window.AppURLs`, (c) `data-*` attributes; applied to 37 call sites.
- **Files:** `docs/deployment/static-js-urls.md` (~150 LOC) — decision + migration plan.
- **LOC:** ~150 docs.
- **Tests:** `rg -n 'scriptRoot|script_root' static/js/` returns zero **after** the JS files are migrated. **Note:** L0 lands the DOC only; the migration itself is L1c/L1d work per the 37-site inventory.
- **Dependencies:** None (docs-only).
- **Reference:** `implementation-checklist.md` §502.
- **Risk:** **LOW** — open question §7.5 escalates whether the JS migration belongs at L0 or later.

### L0-30 `.duplication-baseline` temporary relaxation

- **Rationale:** `implementation-checklist.md` §503 — relax baseline at L0 entry to accommodate L0-L2 parallel old/new code; re-tighten at L2 exit.
- **Files:** `.duplication-baseline` — re-snapshot at L0 entry; value NOT decreased during L0-L2.
- **LOC:** ≈0 code; baseline file bump only.
- **Tests:** `check_code_duplication.py` green at every L0-L2 commit; L2 exit diff restores to ≤ L0-entry value.
- **Dependencies:** None.
- **Reference:** `implementation-checklist.md` §503.
- **Risk:** **LOW**.

### L0-31 `anyio` threadpool limiter bump in lifespan

- **Rationale:** `implementation-checklist.md` §438-440 — `anyio.to_thread.current_default_thread_limiter().total_tokens = 80` configured in `src/app.py::lifespan`. Moved from L5+ to L0 per 2026-04-14 plan update. Default 40 is too low for sync-handler admin concurrency.
- **Files:** `src/app.py` — add 2 lines to `app_lifespan()` (before `yield`).
- **LOC:** ~4 production + ~30 tests.
- **Tests:** Red: `tests/unit/test_threadpool_limiter_configured.py` — asserts `anyio.to_thread.current_default_thread_limiter().total_tokens == 80` after lifespan startup; fails (current: 40). Green: patch lifespan.
- **Dependencies:** None; one of the few `src/app.py` modifications permitted at L0 (see Open Question §7.2).
- **Reference:** `implementation-checklist.md` §438; `foundation-modules.md` §11.14.F.
- **Risk:** **LOW** — pure config value; no behavioral change under current load.

---

## 4. Sub-commit plan (Red→Green pairs)

Per `CLAUDE.md` §Test-Before-Implement Discipline, each work item is one Red+Green pair unless it qualifies for a discipline waiver.

| # | Commit sequence | Waiver? |
|---|---|---|
| L0-00 | `refactor: rename src/admin/blueprints to src/admin/routers` + `test: add test_architecture_no_blueprints_dir.py guard` (Red) + `refactor: complete blueprints→routers import sweep` (Green; already merged into the rename commit via codemod — atomic) | `discipline: N/A - pure mechanical codemod; the codemod re-run is the test` |
| L0-01 | 16 Red commits (`test: add <guard>.py — expected failing`) + 16 Green commits (`test: seed <guard>.py allowlist / empty state`). Batched as 4 PRs of 4 guards each. | No |
| L0-02 | 9 Red+Green pairs (one per protective test) | No |
| L0-03 | Red: `test: retire scoped_session — prove session_A != session_B across threads` → Green: `refactor(core): drop scoped_session; get_db_session yields from bare sessionmaker` | No |
| L0-04 | Red: `test: MessagesDep round-trip — expected ImportError` → Green: `feat(admin): add src/admin/deps/messages.py (D8-native flash)` | No |
| L0-05 | Red: `test: BaseCtxDep key completeness — expected failing` → Green: `feat(admin): add src/admin/deps/templates.py + app.state.templates binding` | No |
| L0-06 | Red: `test: CSRFOriginMiddleware 7 Origin scenarios — expected failing` → Green: `feat(admin): add src/admin/csrf.py` | No |
| L0-07 | Red+Green pair for `external_domain.py` | No |
| L0-08 | Red+Green pair for `fly_headers.py` | No |
| L0-09 | Red+Green pair for `request_id.py` | No |
| L0-10 | Red: 4 auth scenarios failing → Green: `feat(admin): add src/admin/unified_auth.py` | No |
| L0-11 | Red: `test: OAuth callback name constants — expected ImportError` → Green: `feat(admin): add src/admin/oauth.py (Authlib registration + callback constants)` | No |
| L0-12 | 3 Red+Green pairs for `deps/auth.py`, `deps/tenant.py`, `deps/audit.py` | No |
| L0-13 | Red+Green pair for `cache.py` | No |
| L0-14 | Red+Green pair for `content_negotiation.py` | No |
| L0-15 | Red+Green pair for `app_factory.py` | No |
| L0-16 | `test: foundation modules import smoke` (single atomic commit; this IS the test, green on first run if L0-04..L0-15 landed correctly) | `discipline: N/A - smoke verifies prior impl; no new behavior` |
| L0-17 | Red+Green pair for `form_error_response()` | No |
| L0-18 | 2 Red+Green pairs (feature flag, served-by middleware) | No |
| L0-19 | Red: `GET /metrics → 404` → Green: `feat: mount /metrics placeholder router` | No |
| L0-20 | Red: `test: codemod idempotency on post-codemod fixture` → Green: `feat(scripts): add codemod_templates_greenfield.py + generate_route_name_map.py` | No |
| L0-21 | Red: golden-fixture capture test expecting baseline to exist → Green: capture baseline via `/capture-fixtures` skill + commit fixtures | No |
| L0-22 | Red: `test: dependency_overrides leakage across get_admin_client contexts` → Green: `feat(tests/harness): add IntegrationEnv.get_admin_client()` | No |
| L0-23 | 6 Red+Green pairs (one per doc-drift linter) | No |
| L0-24 | Red+Green pair for pydantic-v1-config guard | No |
| L0-25 | 2 Red+Green pairs (allowlist-monotonic, fixme-coverage meta-guards) | No |
| L0-26 | Single commit bumping `pyproject.toml` + `.env.example` + docs | `discipline: N/A - dep bump; golden fingerprints unchanged` |
| L0-27 | Single commit sweeping BDD files | `discipline: N/A - dead-code deletion verified by BDD suite green` |
| L0-28 | Red: `test: check_hardcoded_urls.py rejects scriptRoot fixture` → Green: rewrite hook | No |
| L0-29 | Single commit adding `docs/deployment/static-js-urls.md` | `discipline: N/A - docs-only edit` |
| L0-30 | Single commit bumping `.duplication-baseline` | `discipline: N/A - infra config; pre-commit hook is the assertion` |
| L0-31 | Red+Green pair for threadpool limiter | No |

**Total commits at L0:** ≈ 62 Red-Green commits + 6 waiver commits + 1 codemod-atomic = **~69 commits on the feature branch** before squash-merge to `main`.

---

## 5. Structural guard inventory (L0)

### 5.1 Guards landed pre-L0 (in hardening PR, commits `246067de..64cf0125`)

| # | Guard | Allowlist strategy |
|---|---|---|
| 1 | `test_architecture_no_runtime_psycopg2` | Captured→shrink (1 entry: `db_config.py`) |
| 2 | `test_architecture_get_db_connection_callers_allowlist` | Frozen (1 file: `run_all_services.py`) |
| 3 | `test_architecture_no_module_level_get_engine` | Frozen empty |

### 5.2 NEW guards landing at L0 (per `implementation-checklist.md §5.5` rows tagged "Introduced at L0")

| # | Test filename | What it guards | Allowlist | Meta-test fixture |
|---|---|---|---|---|
| 1 | `test_architecture_no_flask_imports.py` | No `from flask import`/`import flask` outside allowlist | Captured→shrink (~40 entries, target L2=0) | Plant an unauthorized `from flask import Flask` |
| 2 | `test_architecture_handlers_use_sync_def.py` | Admin handlers are sync `def`, not `async def` (with 3-4 frozen OAuth carve-outs) | Empty + small Frozen carve-out | Plant `async def admin_handler` fixture |
| 3 | `test_architecture_no_async_db_access.py` | No `async with get_db_session()` in admin code | Empty | Plant `async with get_db_session()` fixture |
| 4 | `test_architecture_middleware_order.py` | Runtime middleware order matches `MIDDLEWARE_STACK_VERSION` table | Empty | At L0, no middleware wired; guard asserts `MIDDLEWARE_STACK_VERSION == 0` (pre-L1a) |
| 5 | `test_architecture_exception_handlers_complete.py` | 6 exception handlers registered in `src/app.py` | Empty (but dormant at L0 — current count is 1; handler addition is L1a work) | Open Question §7.4 escalates enforcement timing |
| 6 | `test_architecture_csrf_exempt_covers_adcp.py` | CSRF exemption list covers MCP/A2A/_internal/static/OAuth-callback | Empty | Plant an exempt-list missing `/mcp/` |
| 7 | `test_architecture_approximated_middleware_path_gated.py` | Approximated middleware only fires on `/admin` + `/tenant` path prefixes | Empty | Plant a middleware that fires on `/api/v1/` |
| 8 | `test_architecture_admin_routes_excluded_from_openapi.py` | All admin routers have `include_in_schema=False` | Empty (dormant at L0 — `build_admin_router()` is empty) | Plant an admin router missing the flag |
| 9 | `test_architecture_admin_routes_named.py` | Every admin `@router.<method>(...)` has `name=` kwarg | Empty (dormant at L0) | Plant a route without `name=` |
| 10 | `test_architecture_admin_route_names_unique.py` | No two admin routes share a `name=` | Empty | Plant duplicate `name="admin_x"` pair |
| 11 | `test_architecture_no_module_scope_create_app.py` | No top-level `create_app()` calls in `tests/` | Empty (enforced L2, written at L0) | Plant `app = create_app()` at module scope |
| 12 | `test_architecture_scheduler_lifespan_composition.py` | `FastAPI(lifespan=combine_lifespans(...))` in `src/app.py` | Empty | Plant `FastAPI(lifespan=app_lifespan_only)` |
| 13 | `test_architecture_a2a_routes_grafted.py` | `/a2a`, `/.well-known/agent-card.json`, `/agent.json` are top-level Routes, NOT inside a Mount | Empty | Plant a `Mount("/a2a", ...)` |
| 14 | `test_architecture_form_getlist_parity.py` | No `request.form.getlist(...)` in admin code (FastAPI uses `List[str] = Form()`) | Empty (retires at L2) | Plant `request.form.getlist(...)` |
| 15 | `test_architecture_no_module_level_engine.py` | No `create_engine()`/`create_async_engine()` at module scope | Empty | Plant a module-level `engine = create_engine(...)` |
| 16 | `test_architecture_no_direct_env_access.py` | No `os.environ.get(...)` in `src/admin/` + `src/core/` (except `src/core/config.py`) | Captured→shrink (seed ~107 current sites; target L7=0) | Plant `os.environ.get("FOO")` outside allowlist |
| 17 | `test_templates_url_for_resolves.py` | Every `url_for('name', ...)` in templates resolves to a registered route | Empty | Plant `url_for('nonexistent_route')` |
| 18 | `test_templates_no_hardcoded_admin_paths.py` | No `script_name`/`script_root`/`admin_prefix`/`static_prefix` + no bare `"/admin/"`/`"/static/"` string literals | Empty | Plant `{{ script_name }}/x` fixture |
| 19 | `test_codemod_idempotent.py` | Re-running codemod yields zero diff | Empty (retires after L1a codemod execution) | Plant a non-idempotent rewrite |
| 20 | `test_oauth_callback_routes_exact_names.py` | Byte-pins 3 OAuth callback route name+path pairs | Empty (3 frozen assertions) | Plant `/admin/auth/oidc/{tenant_id}/callback` (known-wrong form) |
| 21 | `test_trailing_slash_tolerance.py` | Every admin route tolerates trailing slash (via `redirect_slashes=True`) | Empty (dormant at L0 — `build_admin_router()` empty) | Plant `APIRouter(redirect_slashes=False)` |
| 22 | `test_template_context_completeness.py` | `BaseCtxDep` returns all 7 Flask-inject_context keys | Empty | Drop one key, assert guard fires |
| 23 | `test_foundation_modules_import.py` | All 13 foundation modules import in <1s | N/A (smoke) | Plant a circular import |
| 24 | `test_openapi_byte_stability.py` | `/openapi.json` byte-hash matches fixture | Empty (byte-hash snapshot) | Mutate a schema; assert guard fires |
| 25 | `test_mcp_tool_inventory_frozen.py` | MCP tool name+signature frozen | Empty | Add a new tool; assert guard fires |
| 26 | `test_architecture_no_admin_wrapper_modules.py` | `src/admin/flash.py` + `sessions.py` + `templating.py` do NOT exist (D8 #4) | Empty | Create one of the banned files, assert guard fires |
| 27 | `test_architecture_inventory_cache_uses_module_helpers.py` | No `from flask_caching import` in `src/` | Empty (inverts to L6 app.state guard) | Plant `from flask_caching import` |
| 28 | `test_structural_guard_allowlist_monotonic.py` | Meta: every `Captured→shrink` allowlist is non-increasing | Meta (no allowlist) | Add entry to any allowlist, assert guard fires |
| 29 | `test_architecture_allowlist_fixme_coverage.py` | Meta: every allowlist entry has a `# FIXME(salesagent-xxxx)` at source | Meta (no allowlist) | Plant an allowlist entry without FIXME |
| 30 | `test_architecture_exception_handlers_accept_aware.py` | `AdCPError` handler is Accept-aware | Empty (dormant at L0 — L1a wires) | Plant a JSON-only handler |
| 31 | `test_architecture_templates_no_script_root.py` | No `script_root` in template source | Empty | Plant `{{ request.script_root }}` |
| 32 | `test_architecture_x_served_by_header_emitted.py` | `ServedByMiddleware` registers `X-Served-By` response header | Empty (retires at L2) | Plant middleware without the header |
| 33 | `test_architecture_feature_flag_gate_active.py` | `ADCP_USE_FASTAPI_ADMIN` read via config module, not direct env | Empty (retires at L2) | Plant `os.environ.get("ADCP_USE_FASTAPI_ADMIN")` bypass |
| 34 | `test_architecture_no_werkzeug_imports.py` | No `import werkzeug`/`from werkzeug` outside allowlist | Captured→shrink (seed current; target L2=0) | Plant `import werkzeug` outside allowlist |
| 35 | `test_architecture_no_flask_caching_imports.py` | No `from flask_caching import` (enforced L2, written L0) | Empty | Plant `from flask_caching import Cache` |
| 36 | `test_architecture_no_blueprints_dir.py` | `src/admin/blueprints/` does NOT exist | Empty | Create `src/admin/blueprints/x.py`, assert guard fires |
| 37 | `test_architecture_no_scoped_session.py` | No `from sqlalchemy.orm import scoped_session` or `scoped_session(` calls in `src/` | Empty (after L0-03) | Plant `scoped_session(sessionmaker())` |
| 38 | `test_architecture_no_pydantic_v1_config.py` (§11.35) | No `class Config:` inside Pydantic BaseModel subclasses | Empty | Plant `class Config: orm_mode = True` |
| 39 | `test_architecture_invariants_consistent.py` (§11.34) | Doc-drift: 6 invariants parse-equivalent across CLAUDE.md variants | Empty | Plant a drift |
| 40 | `test_architecture_oauth_uris_consistent.py` (§11.34) | Doc-drift: OAuth URI whitelist | Empty | Plant unauthorized URI |
| 41 | `test_architecture_csrf_implementation_consistent.py` (§11.34) | Doc-drift: no double-submit-cookie tokens in planning docs | Empty | Plant `adcp_csrf` token outside rejection context |
| 42 | `test_architecture_layer_assignments_consistent.py` (§11.34) | Doc-drift: layer scope consistent across plan + checklist | Empty | Plant L5d1 relabel drift |
| 43 | `test_architecture_spike_table_consistent.py` (§11.34) | Doc-drift: Spike table is single source of truth | Empty | Plant "Spike 4 at L1a" drift |
| 44 | `test_architecture_proxy_headers_in_entrypoints.py` (§11.34) | Canonical entrypoint has `proxy_headers=True` + `forwarded_allow_ips='*'` | Empty | Plant a drop of the flag |
| 45 | `test_architecture_harness_overrides_isolated.py` | `app.dependency_overrides` leakage protection | Empty | Plant a cross-context leak fixture |
| 46 | `test_architecture_single_worker_invariant.py` | Scheduler singleton protection | Empty | Plant `workers=2` in `uvicorn.run(...)` |
| 47 | `test_architecture_handlers_use_annotated.py` | Handler params use `Annotated[T, ...]`, not default-value syntax | Empty (dormant at L0) | Plant `x = Query(...)` |
| 48 | `test_architecture_templates_receive_dtos_not_orm.py` | Templates receive primitives/Pydantic/Request, not ORM models | Empty (dormant at L0) | Plant `render(..., {"tenant": tenant_orm})` |

**Total new L0 guards:** **48** (implementation-checklist.md claims "total Wave 0 structural guards = 16" at line 497 — **this is stale**; the §5.5 guards table lists ~28 guards introduced at L0 + §11.34 adds 6 + §11.35 adds 1 + earlier §2.4/L0-03 adds 3 + §6.5 observability adds 2. **See Open Question §7.6 for reconciliation.**)

---

## 6. Exit-gate audit

| Exit criterion | Satisfied by |
|---|---|
| `make quality` green | L0-01..L0-31 (all work items have Green commits passing `make quality`) |
| `tox -e integration` green | L0-03, L0-12, L0-22 (bare-sessionmaker + deps + harness) |
| `tox -e bdd` green | L0-27 (BDD sweep) |
| `./run_all_tests.sh` green | Full suite passes after all work items land |
| `test_codemod_idempotent.py` green | L0-20 |
| `test_architecture_no_pydantic_v1_config.py` green with empty allowlist | L0-24 |
| All 48 structural guards green | L0-01, L0-02, L0-03, L0-23, L0-24, L0-25, L0-36..45 (full matrix) |
| Branch mergeable state verified | Post-squash review |
| Single squashed merge commit on `main` | User discretion at L0 exit |
| **Flask traffic share = 100%** (L0 thesis) | Verified by running staging with `ADCP_USE_FASTAPI_ADMIN=false` and observing `X-Served-By: flask` on every admin response (L0-18 enables this) |
| `/metrics` endpoint scaffolded | L0-19 |
| `X-Served-By` header wired to emit `fastapi` or `flask` | L0-18 (at L0, always emits `flask` since no FastAPI admin routes exist) |
| Dashboard `admin-migration-health` created, Traffic tab populated | **PENDING** — Dashboard creation is user-side (Datadog/etc) and cannot be automated at L0; tracked as a user checklist item |
| `pyproject.toml` has `pydantic-settings>=2.7.0` | L0-26 |
| `src/app.py` unchanged except for `metrics_router` include + threadpool-limiter bump | L0-19, L0-31 |
| No Flask files deleted | Invariant respected throughout |
| OAuth callback URIs enumerated in runbook | L0-26 (`docs/migration/v2.0-oauth-uris.md`) |
| PRE-1..PRE-3 landed before L0-03 | **Hard blocker** — user must confirm |

---

## 7. Open questions / escalations

### §7.1 Test-directory convention drift

**Location:** `implementation-checklist.md` §5.5 rows consistently reference `tests/unit/architecture/<guard>.py`; existing pre-v2.0 guards (`test_architecture_no_runtime_psycopg2.py`, etc.) live at `tests/unit/<guard>.py` (flat). **Proposal:** follow the checklist convention (new subfolder `tests/unit/architecture/`) for L0+ guards; leave pre-v2.0 guards at their current locations. This satisfies the §5.5 table's literal path strings and allows pytest to discover both. **Default if no user decision:** follow §5.5.

### §7.2 `src/app.py` modification scope at L0

**Location:** `execution-plan.md` §145 says "Do not modify `src/app.py` (no middleware, no router inclusion)"; but `implementation-checklist.md` §2088-2091 says L0 entry must have `/metrics` endpoint + `X-Served-By` header + dashboard. These conflict if taken literally — `/metrics` requires `app.include_router()` in `src/app.py`. **Proposal:** interpret §145 as "no middleware added, no ADMIN routers included" — leaf routes (`/metrics`) and lifespan-only mods (threadpool limiter) are permitted. L0-19 and L0-31 land these in a single commit `feat(app): L0 observability scaffolding — /metrics router + threadpool limiter`. **Default if no user decision:** proceed with proposal; file-scope of the commit is transparent.

### §7.3 Codemod idempotency test fixture strategy

**Location:** L0-20 exit gate requires `test_codemod_idempotent.py` green at L0, but `execution-plan.md` §124 says codemod is NOT run at L0. **Question:** what does `test_codemod_idempotent.py` assert on unchanged Flask templates? **Proposal:** the test runs the codemod against a committed frozen-state "golden post-codemod" fixture (tiny — 3-5 representative templates hand-crafted to look post-codemod) and asserts re-running the codemod yields zero diff. It does NOT assert against the live `templates/` directory at L0. **Default if no user decision:** proceed with the fixture-based approach; document fixture provenance in the test docstring.

### §7.4 Dormant vs active guard enforcement at L0

**Location:** Multiple L0 guards (e.g., `test_architecture_exception_handlers_complete.py` at 6 handlers, `test_architecture_admin_routes_named.py`, `test_architecture_admin_routes_excluded_from_openapi.py`) assert invariants on admin-router code that **does not exist at L0**. If the guard scans empty state and passes, it's not actually enforcing anything. **Proposal:** each dormant-at-L0 guard ships with a planted-violation meta-fixture (per §5.5 authoring policy) that proves the AST scanner fires when the violation is present. The guard "passes" at L0 because the real codebase has zero subjects to scan; the planted fixture ensures the scanner is not a no-op. **Default if no user decision:** proceed; `/write-guard` skill already follows this pattern.

### §7.5 Static JS URL migration timing

**Location:** `implementation-checklist.md` §502 says "applied to all 37 call sites" — literal reading suggests the migration happens at L0. **Proposal:** L0 lands the decision doc only (`docs/deployment/static-js-urls.md`); the 37-site JS migration is part of L1c/L1d router ports (each migrated JS file travels with its owning template). Without moving templates at L0, migrating JS in isolation creates test breakage. **Default if no user decision:** proceed with L0=doc-only; file `v2.0.0 L1c/L1d: migrate static JS URL call sites` as a follow-up work item.

### §7.6 Guard-count reconciliation (48 vs "16")

**Location:** `implementation-checklist.md` §497 says "total Wave 0 structural guards = 16"; §5.5 guards table lists ~28 guards introduced at L0; `foundation-modules.md` §11.34 adds 6 doc-drift linters at L0; §11.35 adds 1 (pydantic-v1-config); pre-L0 hardening adds 3; observability adds 2 (feature_flag + x_served_by); bare-sessionmaker adds 1 (no_scoped_session); blueprints→routers rename adds 1 (no_blueprints_dir); wrapper-modules adds 1 (no_admin_wrapper_modules); plus ~5 more in the §5.5 table I may have missed → **48 guards total at L0 exit**. The "16" figure appears to be stale. **Proposal:** accept 48 as the correct count; open a separate doc-drift fix PR against `implementation-checklist.md` §497 to correct "16 → 48" (or similar actual count after final verification). **Default if no user decision:** proceed with 48; file the doc fix in parallel.

### §7.7 Pre-L0 refactor PRs (PRE-1..PRE-3) ownership

**Location:** `implementation-checklist.md` §391-393 lists PRE-1..PRE-3 as hard blockers for L0-03 (`scoped_session` retirement). **Question:** do these refactors ship as pre-L0 commits (like the hardening PR `246067de..64cf0125`) or as the first sub-PR of L0 itself? **Proposal:** ship as pre-L0 PRs on the migration branch — each is a strict improvement under the current `scoped_session` world and does not touch any L0 scope. Post-merge, L0-03 can proceed safely. **Default if no user decision:** treat as pre-L0; user attests their landing before L0-03 Red commit.

### §7.8 Agent-team execution of L0

**Location:** `CLAUDE.md` §v2.0 Timeline Summary estimates L0 at 5-7 engineer-days for a single engineer; `implementation-checklist.md` §449-450 says L0 foundation modules parallelize across ~3 engineers. **Question:** is the 69-commit, 31-work-item plan sized for 3-agent parallelism or 1-agent sequential? **Proposal:** items with no cross-dependencies (L0-04..L0-09, L0-11, L0-13, L0-14, L0-23, L0-24, L0-27, L0-28, L0-29) can fan out to 3 parallel agents after L0-00 + L0-01 + L0-03 complete (which are sequential blockers). **Default if no user decision:** sequence L0-00 → L0-01 → L0-03 on the trunk, then fan out the 17 parallelizable items to 3 agents, re-serialize at L0-16 (smoke test) + L0-21 (golden-fixture capture) + L0-22 (harness). User attests the parallelism strategy before L0-02 lands.

---

**End of L0 implementation plan.**
