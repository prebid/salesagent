# Layer 0 (L0) — Spike & Foundation Implementation Plan

> **Version:** 2 (revised 2026-04-19)
> **Supersedes:** `L0-implementation-plan.md` v1 (2026-04-18). v1 preserved intact for traceability; v2 is a SUPERSET that clarifies and extends, not a rewrite.
> **Status:** Revised. Incorporates findings from 4 parallel audits (frontend-deep-audit, flask-to-fastapi-deep-audit, flask-to-fastapi-adcp-safety, testing-strategy) and the 8 ratifications from the independent adjudicator pass.
> **Scope:** Pure-addition layer. Flask serves 100% of `/admin/*` traffic at L0 exit.
> **Branch:** `feat/v2.0.0-flask-to-fastapi`.
> **Upstream authorities:** `CLAUDE.md` §Critical Invariants; `execution-plan.md` Layer 0; `implementation-checklist.md` §4 Wave 0 / L0; `foundation-modules.md` §11.1-11.15, §11.26, §11.34-11.36; `flask-to-fastapi-deep-audit.md` §1-§2; `async-audit/frontend-deep-audit.md`; `async-audit/testing-strategy.md`; `flask-to-fastapi-adcp-safety.md`.

---

## Revision summary — what changed from v1

1. **NEW work item L0-32 `admin_redirect()` 302-default helper** (~30 LOC + 5 tests). Per `async-audit/frontend-deep-audit.md` F3 / `async-audit/testing-strategy.md` Tier 1. L0 total work items: **32** (was 31 + L0-00 = 32; now 33 including L0-00).
2. **L0-05 `TemplatesDep` extended** to register Jinja `tojson` filter (with `indent` kwarg support) and widen `BaseCtxDep` key set from 7 → 11 keys. Per `frontend-deep-audit.md` F5 / H4-H5.
3. **L0-14 `content_negotiation.py` extended** to author `templates/error.html` with pinned `{error_code, message, status_code}` contract + golden fingerprint, AND to extend `_response_mode()` test matrix to cover AJAX and browser-fetch `Accept: */*` false-positive cases. Per `flask-to-fastapi-deep-audit.md` §1.3 + `frontend-deep-audit.md` F6/H7.
4. **Test-Before-Implement Red reframe** across L0-04..L0-15. v1 used ImportError as the Red failure mode; v2 declares per-item which Red pattern is used: (a) minimal-stub-then-semantic-test, or (b) `pytest.raises(ImportError)` as SEMANTIC assertion about module absence. Items using pattern (a): L0-05, L0-06, L0-07, L0-08, L0-09, L0-10, L0-12, L0-13, L0-14. Items using pattern (b): L0-04, L0-11, L0-15 (guards-and-constants-only). Rationale stated per item.
5. **Stale-entry meta-guard added** (§5.X) — every CAPTURED→shrink allowlist carries a stale-entry assertion via the meta-guard `test_structural_guard_allowlist_monotonic.py` extended with stale-entry logic (one convention applied uniformly).
6. **Enumerated baseline file paths** for the 3 Captured→shrink guards: rows 1 (`no_flask_imports`), 16 (`no_direct_env_access`), 34 (`no_werkzeug_imports`). All under `tests/unit/architecture/allowlists/<guard_name>.txt`.
7. **Orphan guards assigned owners** (§5.6) — guards #17, #18, #22, #27, #31, #32, #33, #34, #35, #38 each now have an L0-XX owning work item.
8. **Citations added** to L0-03, L0-07, L0-02 schemas row, L0-15, L0-18 per audit-finding coverage.
9. **§7 renamed** "Ratified Decisions (previously Open Questions)" with all 8 items marked RATIFIED 2026-04-19 and refinements captured.

---

## 1. L0 mission summary

L0 is a **pure-addition** layer that lands the foundation modules, codemod scripts, structural guards, and observability scaffolding required by L1a-L7, without mutating a single byte of user-visible admin behavior. Flask still serves 100% of `/admin/*` traffic; `src/app.py` middleware stack, router registration, and exception handler are untouched (with narrow permitted exceptions per §7.2: leaf-route `/metrics` include + `anyio` threadpool-limiter lifespan bump). The 6 Critical Invariants are respected vacuously (no admin routes land at L0) except D2 — **`scoped_session` is retired from `src/core/database/database_session.py` at L0**, flipping the bare-sessionmaker contract that underpins sync-def admin handlers in L1a-L4. L0 also ships the `ADCP_USE_FASTAPI_ADMIN` feature flag and the `X-Served-By` response header so L1a's traffic split is verifiable and instantly reversible.

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
| 11 | `a2wsgi` Flask mount still at `src/app.py:44-45` | **PASS** | Verified |
| 12 | v1.99.0 tag plan documented | **PASS** | `CLAUDE.md` §NO-GO release-tag naming rule |
| 13 | Agent-workflow gates confirmed | **PASS** | `CLAUDE.md` §Execution model |
| 14 | Rollback cold-read test | **PENDING** | User must run before L1a entry, not required for L0 entry |

### 2.2 Agent-F pre-L0 hardening items (landed in pre-L0 PR `246067de..64cf0125`)

All 14 items PASS — see v1 §2.2 for commit-by-commit evidence. Retained unchanged in v2.

### 2.3 Section 1.2 architectural decisions recorded

All 12 rows PASS — retained unchanged from v1.

### 2.4 Pre-L0 refactor PRs (PRE-1 through PRE-4)

| PR | Status | Rationale |
|---|---|---|
| PRE-1 `src/admin/blueprints/oidc.py:173` nested-session fix | **PENDING** | **Hard blocker for L0-03** (`scoped_session` retirement) |
| PRE-2 `src/admin/blueprints/operations.py:304` extract-dict-close-outer | **PENDING** | **Hard blocker for L0-03** |
| PRE-3 `src/admin/blueprints/workflows.py:158` extract-dict-close-outer | **PENDING** | **Hard blocker for L0-03** |
| PRE-4 `ContextManager(DatabaseManager)` runtime compat test | **PENDING** | Soft blocker — deferred to L4 Spike 4.5 |

PRE-1..PRE-3 ship as pre-L0 commits on `feat/v2.0.0-flask-to-fastapi` per §7.7 RATIFIED.

### 2.5 L0 entry-gate verdict

**L0 can enter** after: (a) user attests the three `SESSION_SECRET` rows, (b) user confirms `main` is green on `4514f54d`, (c) PRE-1/2/3 land on the migration branch.

---

## 3. Work items

Work items run **roughly** in order; items at the same depth are parallelizable by different agents (per §7.8 RATIFIED refinement: L0-04 runs SOLO as a single-agent canary; L0-05..L0-15 fan out to 3 agents). Each item lists rationale, files, LOC, test coverage, dependencies, module reference, and risk.

### L0-00 directory rename `src/admin/blueprints` → `src/admin/routers` (day-1 codemod)

- **Rationale:** Eliminates mixed-directory state across L0→L1d. D8 #6 breaking rename, zero behavioral change.
- **Files:** `git mv src/admin/blueprints src/admin/routers`; codemod ~40 importers via `ast.NodeTransformer` one-liner.
- **LOC:** ~5-line diff × ~40 importers ≈ 200 LOC moved, 0 LOC net new.
- **Tests:** `tests/unit/architecture/test_architecture_no_blueprints_dir.py` (empty allowlist; asserts directory absence + zero `from src.admin.blueprints.` references).
- **Dependencies:** None.
- **Reference:** `execution-plan.md` Layer 0 day-1 codemod paragraph.
- **Risk:** **LOW** — mechanical rename; guard catches misses.

### L0-01 foundation-modules-discipline guards (written FIRST, TDD)

- **Rationale:** Per `CLAUDE.md` §Test-Before-Implement and `execution-plan.md` item 1, structural guards land as Red commits BEFORE the foundation modules they enforce.
- **Files:** 16 guard test files (see §5 Structural Guard Inventory). All under `tests/unit/architecture/`.
- **LOC:** ~120 LOC per guard × 16 guards = ~1,920 LOC tests. Meta-test fixture ~60 LOC per guard.
- **Tests:** Each guard self-tests via meta-fixture: a hand-crafted violating file fixture proves the AST scanner fires.
- **Dependencies:** L0-00.
- **Reference:** `foundation-modules.md` §11.26; `implementation-checklist.md` §5.5.
- **Risk:** **MEDIUM** — meta-fixture authorship novelty.
- **Test-Before-Implement pattern:** Guards ARE the tests; `/write-guard` skill convention used.

### L0-02 AdCP boundary protective tests (9 tests)

- **Rationale:** `execution-plan.md` L0 item 1a — protects AdCP MCP/A2A/REST surface.
- **Files:**
  - `tests/migration/test_openapi_byte_stability.py`
  - `tests/migration/test_mcp_tool_inventory_frozen.py`
  - `tests/migration/test_a2a_agent_card_snapshot.py`
  - `tests/unit/architecture/test_architecture_approximated_middleware_path_gated.py`
  - `tests/unit/architecture/test_architecture_csrf_exempt_covers_adcp.py`
  - `tests/unit/architecture/test_architecture_admin_routes_excluded_from_openapi.py`
  - `tests/integration/test_error_shape_contract.py`
  - `tests/integration/test_schemas_discovery_external_contract.py` — **cites `flask-to-fastapi-adcp-safety.md:348-351`** (schemas.py external-validator contract).
  - `tests/integration/test_rest_response_wire.py`
- **LOC:** ~1,000 LOC tests + ~200 LOC fixture JSON.
- **Tests:** Land green at L0 (capture baseline); L1+ Red-Greens against fixtures.
- **Dependencies:** None beyond `tests/migration/` directory creation.
- **Reference:** `implementation-checklist.md` §5.5 + `adcp-safety.md` §7.
- **Risk:** **LOW-MEDIUM** — OpenAPI byte-hash fragility to dep bumps.

### L0-03 D2 `scoped_session` retirement in `src/core/database/database_session.py`

- **Rationale:** Critical Invariant #4 — admin handlers use sync `def` with **bare `sessionmaker`** (D2). Current file has `_scoped_session = scoped_session(_session_factory)` at line 196 + `scoped.remove()` at lines 206, 278, 289, 373, 322.
- **Citations:** `flask-to-fastapi-deep-audit.md:192-235` (Option C narrative) + `async-audit/agent-b-risk-matrix.md` Risk #19.
- **Files:**
  - `src/core/database/database_session.py` — rewrite `get_engine()` to drop `_scoped_session`; `get_db_session()` calls `_session_factory()` directly; `reset_engine()` skips `.remove()`; `execute_with_retry()` skips `scoped.remove()`; `DatabaseManager.session` property calls `_session_factory()`; delete `get_scoped_session()`.
  - `tests/unit/architecture/test_architecture_no_scoped_session.py` (AST-scans `src/`).
- **LOC:** ~80 LOC production; ~60 LOC test.
- **Tests:**
  - Red: `test_scoped_session_retirement.py` — asserts `id(session_A) != id(session_B)` when `get_db_session()` is called in different threads. Currently fails (both threads get the same scoped session).
  - Green: rewrite `get_db_session()`. Re-run `tox -e integration` + `tox -e bdd` against PRE-1/2/3 refactored blueprints.
  - Guard: `test_architecture_no_scoped_session.py` green.
- **Test-Before-Implement pattern:** Pattern (a) — the Red test asserts a SEMANTIC property (distinct identity across threads) that currently fails with a passing `==` comparison. No ImportError; module already exists.
- **Dependencies:** **Hard blocker:** PRE-1, PRE-2, PRE-3 landed. Soft blocker: PRE-4.
- **Reference:** `CLAUDE.md` §Critical Invariants #4; `foundation-modules.md` §D8-native + §11.0.2; `implementation-checklist.md` §387-396.
- **Risk:** **HIGH** — any untracked nested-session site surfaces as detached-instance under bare sessionmaker. Mitigation: PRE-1..PRE-3 + full `tox -e integration` + staging admin login e2e.

### L0-04 foundation module: `src/admin/deps/messages.py` (D8-native)

- **Rationale:** Flash-message state across 366 `flash()` call sites (codemod lands at L1+). Session-backed `list[FlashMessage]` per D8 #4.
- **Files:** `src/admin/deps/messages.py` (~100 LOC); `src/admin/deps/__init__.py` (stub, ~2 LOC).
- **LOC:** ~100 production + ~120 unit tests.
- **Tests:**
  - Red: `tests/unit/admin/test_messages_dep.py` — round-trips info/success/warning/error through `TestClient` with in-memory session backend. Red asserts `pytest.raises(ModuleNotFoundError)` or `pytest.raises(ImportError)` on `from src.admin.deps.messages import MessagesDep, FlashMessage` (pattern b — the absence IS the contract).
  - Green: implement module per `foundation-modules.md` §D8-native.1.
  - Also add `tests/unit/architecture/test_architecture_no_admin_wrapper_modules.py` (empty allowlist; asserts `src/admin/flash.py`, `sessions.py`, `templating.py` do NOT exist).
- **Test-Before-Implement pattern:** Pattern (b) — module-absence is itself the semantic obligation; `pytest.raises(ImportError)` is the real assertion. Justified because no other semantic behavior exists before the module lands; a stub would be vacuous.
- **Dependencies:** L0-01.
- **Reference:** `foundation-modules.md` §D8-native.1; `implementation-checklist.md` §423-424.
- **Risk:** **LOW** — greenfield, no Flask dependency.
- **L0-04 execution note (§7.8 refinement):** L0-04 runs SOLO as the single-agent canary. Fresh-agent review pass AFTER L0-04 Green before scaling L0-05..L0-15 to 3 parallel agents.

### L0-05 foundation module: `src/admin/deps/templates.py` (D8-native) — EXTENDED

- **Rationale:** `Jinja2Templates` attached to `app.state.templates` at lifespan startup (not a wrapper module). `TemplatesDep` + `BaseCtxDep` replace Flask's `inject_context()` processor. **Extended in v2** to register Jinja `tojson` filter + widen `BaseCtxDep` key set to 11.
- **Files:** `src/admin/deps/templates.py` (~50 LOC for `TemplatesDep` + filter registration; ~100 LOC for `BaseCtxDep` — 11 keys; v1's 7 + `session`, `g_test_mode`, `csrf_token`, `get_flashed_messages`).
- **LOC:** ~150 production + ~220 unit tests.
- **Tests:**
  - Red (pattern a — stub first): Land an empty stub `src/admin/deps/templates.py` with `TemplatesDep = None` / `BaseCtxDep = None` sentinels. Then write the Red test: `tests/unit/admin/test_templates_dep.py` asserts `BaseCtxDep` returns dict with keys `{messages, support_email, sales_agent_domain, user_email, user_authenticated, user_role, test_mode, session, g_test_mode, csrf_token, get_flashed_messages}`. Red asserts AttributeError or TypeError on the sentinel — a SEMANTIC failure.
  - Green: implement. `csrf_token` is a callable returning `""` (NULL-OP by contract — CSRFOriginMiddleware uses Origin validation, not tokens, but templates coded against Flask reference `{{ csrf_token() }}`). `get_flashed_messages` is a callable drain wrapper over `MessagesDep`. `g_test_mode` bridges `g.test_mode` semantics via request state.
  - Jinja `tojson` filter test: 5 tests — basic dict, `|tojson(indent=2)`, nested, nullable, unicode. Per `frontend-deep-audit.md` F5 / `testing-strategy.md` Wave 0.
  - Also add `tests/unit/architecture/test_template_context_completeness.py` (empty allowlist; asserts `BaseCtxDep()(request)` returns all 11 keys).
- **Test-Before-Implement pattern:** Pattern (a) — stub first, then semantic behavioral test. Justified because `BaseCtxDep` has concrete behavior (key set) that a stub can fail against without invoking ImportError.
- **Dependencies:** L0-01, L0-04 (MessagesDep for drain wrapper).
- **Reference:** `implementation-checklist.md` §422; `foundation-modules.md §D8-native`; `frontend-deep-audit.md` F2, F5, H4-H5.
- **Risk:** **LOW-MEDIUM** — 11-key contract is load-bearing for `base.html` across all 54 admin pages; drop one key and every page breaks.

### L0-06 foundation module: `src/admin/csrf.py` (CSRFOriginMiddleware)

- **Rationale:** Critical Invariant #5 — pure-ASGI Origin header validation; exempts MCP/A2A/_internal/static/OAuth-callback.
- **Files:** `src/admin/csrf.py` (~120 LOC).
- **LOC:** ~120 production + ~180 tests.
- **Tests:**
  - Red (pattern a — stub first): Land empty `CSRFOriginMiddleware` stub class (no __init__, raises NotImplementedError on __call__). 7 Origin-scenario unit tests (missing, matching, scheme-mismatch, port-mismatch, subdomain, null, evil) assert specific response codes — all fail with NotImplementedError, a SEMANTIC failure.
  - Green: implement per `foundation-modules.md` §11.7.
  - Guard: `test_architecture_csrf_exempt_covers_adcp.py` green.
- **Test-Before-Implement pattern:** Pattern (a).
- **Dependencies:** L0-01.
- **Reference:** `foundation-modules.md` §11.7; Critical Invariant #5.
- **Risk:** **LOW-MEDIUM** — Origin parsing edge cases.

### L0-07 foundation module: `src/admin/middleware/external_domain.py` (ApproximatedExternalDomainMiddleware)

- **Rationale:** Critical Invariant #5 — Approximated runs BEFORE CSRF; 307 (not 302) to preserve POST body; path-gated to `/admin/*` + `/tenant/*`.
- **Citations:** `flask-to-fastapi-adcp-safety.md:236-260` (path-gating inventory) + `flask-to-fastapi-deep-audit.md:283-322` (Approximated ordering rationale).
- **Files:** `src/admin/middleware/external_domain.py` (~90 LOC); `src/admin/middleware/__init__.py` (stub).
- **LOC:** ~90 production + ~120 tests.
- **Tests:**
  - Red (pattern a — stub first): Land empty `ApproximatedExternalDomainMiddleware` stub. Unit tests assert 307 + POST body preserved + path-gating (no-op for `/api/`, `/mcp/`). Stub raises NotImplementedError — SEMANTIC failure.
  - Green: implement per `foundation-modules.md` §11.8.
  - Guard: `test_architecture_approximated_middleware_path_gated.py` green.
- **Test-Before-Implement pattern:** Pattern (a).
- **Dependencies:** L0-01.
- **Reference:** `foundation-modules.md` §11.8.
- **Risk:** **LOW**.

### L0-08 foundation module: `src/admin/middleware/fly_headers.py` (FlyHeadersMiddleware)

- **Rationale:** Rewrites `Fly-Forwarded-Proto` → `X-Forwarded-Proto`.
- **Files:** `src/admin/middleware/fly_headers.py` (~40 LOC).
- **LOC:** ~40 production + ~80 tests.
- **Tests:** Pattern (a) — stub first, semantic tests on `request.url.scheme` and `request.client.host` after middleware invocation.
- **Test-Before-Implement pattern:** Pattern (a).
- **Dependencies:** L0-01.
- **Reference:** `foundation-modules.md` §11.9.
- **Risk:** **LOW**.

### L0-09 foundation module: `src/admin/middleware/request_id.py` (RequestIDMiddleware)

- **Rationale:** Per-request UUID stamped on `request.state.request_id`; echoed as `X-Request-ID`.
- **Files:** `src/admin/middleware/request_id.py` (~30 LOC).
- **LOC:** ~30 production + ~60 tests.
- **Tests:** Pattern (a) — stub first; semantic tests for header echo + inbound-ID preservation.
- **Test-Before-Implement pattern:** Pattern (a).
- **Dependencies:** L0-01.
- **Reference:** `foundation-modules.md` §11.9.5.
- **Risk:** **LOW**.

### L0-10 foundation module: `src/admin/unified_auth.py` (UnifiedAuthMiddleware)

- **Rationale:** Pure-ASGI auth; resolves `ResolvedIdentity`; path-gated; Accept-aware 401 vs 302-to-login. Replaces `require_auth` decorator.
- **Files:** `src/admin/unified_auth.py` (~250 LOC).
- **LOC:** ~250 production + ~300 tests.
- **Tests:**
  - Red (pattern a): Land `UnifiedAuthMiddleware` stub that raises NotImplementedError. 4 unit tests — (a) authenticated → `request.state.identity` populated, (b) unauth `/admin/*` + HTML → 302, (c) unauth `/admin/*` + JSON → 401, (d) public paths bypass. All fail with NotImplementedError.
  - Green: implement per `foundation-modules.md` §11.36.
- **Test-Before-Implement pattern:** Pattern (a) — largest module; semantic behavior is layered and must be tested against a stub, not an absent import.
- **Dependencies:** L0-01, L0-04.
- **Reference:** `foundation-modules.md` §11.4 + §11.36; Critical Invariant #3/#5.
- **Risk:** **MEDIUM** — path-gating subtlety across `/admin/` + `/tenant/` prefixes (D1 2026-04-16).

### L0-11 foundation module: `src/admin/oauth.py` (Authlib OAuth client registration)

- **Rationale:** Critical Invariant #6 — OAuth redirect URIs byte-immutable.
- **Files:** `src/admin/oauth.py` (~60 LOC).
- **LOC:** ~60 production + ~80 tests.
- **Tests:**
  - Red (pattern b): `tests/unit/architecture/test_oauth_callback_routes_exact_names.py` asserts module constants EXIST with exact values. `pytest.raises(ImportError)` is the SEMANTIC assertion because there is no behavior to stub — just 3 string constants + Authlib client registration. Absence of constants = violation of Critical Invariant #6.
  - Green: implement.
- **Test-Before-Implement pattern:** Pattern (b) — module-level string constants; presence/absence IS the obligation.
- **Dependencies:** L0-01.
- **Reference:** `foundation-modules.md` §11.6; Critical Invariant #6.
- **Risk:** **LOW-MEDIUM** — test scoped honestly (L0 asserts constants, L1a tightens to route registration).

### L0-12 foundation module: `src/admin/deps/auth.py` + `deps/tenant.py` + `deps/audit.py`

- **Rationale:** FastAPI `Depends()`-based auth/tenant/audit. Sync `def` with `with get_db_session()` per Critical Invariant #4.
- **Files:**
  - `src/admin/deps/auth.py` (~260 LOC)
  - `src/admin/deps/tenant.py` (~90 LOC) — `is_active=True` filter
  - `src/admin/deps/audit.py` (~110 LOC)
- **LOC:** ~460 production + ~350 tests.
- **Tests:**
  - Red (pattern a): Stub each Depends callable returning None. Red tests assert semantic happy-path + denial-path behavior (e.g., `RequireAdminDep` denies non-admin user); all fail against stubs.
  - Green: implement.
- **Test-Before-Implement pattern:** Pattern (a) — happy/denial paths are semantic; stub + behavioral assertion fits.
- **Dependencies:** L0-01, L0-03, L0-04.
- **Reference:** `foundation-modules.md` §11.4, §11.5; `implementation-checklist.md` §430-432.
- **Risk:** **MEDIUM** — `is_active` filter is a latent-bug fix; verify vs existing admin tests.

### L0-13 foundation module: `src/admin/cache.py` (SimpleAppCache)

- **Rationale:** Decision 6 — `flask-caching` replacement using `cachetools.TTLCache(maxsize=1024, ttl=300)` + `threading.RLock`. NOT wired at L0.
- **Files:** `src/admin/cache.py` (~90 LOC).
- **LOC:** ~90 production + ~150 tests.
- **Tests:**
  - Red (pattern a): Stub `SimpleAppCache` class with no-op methods. Behavioral tests: set/get round-trip, TTL expiry, 4-thread RLock contention. Fail against stub.
  - Green: implement per `foundation-modules.md` §11.15.
  - Also add `test_architecture_inventory_cache_uses_module_helpers.py` (empty allowlist).
- **Test-Before-Implement pattern:** Pattern (a).
- **Dependencies:** L0-01.
- **Reference:** `foundation-modules.md` §11.15; Decision 6.
- **Risk:** **LOW**.

### L0-14 foundation module: `src/admin/content_negotiation.py` (Accept-aware AdCPError handler) — EXTENDED

- **Rationale:** Critical Invariant #3 — `AdCPError` renders HTML for `/admin/*` + `/tenant/*` browsers, JSON otherwise. Replaces JSON-only handler at `src/app.py:82-88`. **Extended in v2:** authors `templates/error.html` with pinned contract + tests cover AJAX/browser-fetch `Accept: */*` cases.
- **Files:**
  - `src/admin/content_negotiation.py` (~50 LOC) — `_response_mode()` helper + handler.
  - `templates/error.html` (~40 LOC) — pinned variable contract `{error_code, message, status_code}`. Normalizes `gam.py` `error=...` vs handler `exc.to_dict()` drift.
- **LOC:** ~90 production + ~170 tests.
- **Tests:**
  - Red (pattern a): Stub `_response_mode()` returning always-JSON; stub handler. Semantic tests:
    - `/admin/x + Accept: text/html` → HTML path
    - `/admin/x + Accept: application/json` → JSON
    - `/mcp/x` always JSON
    - `/tenant/{id}/x + Accept: text/html` → HTML
    - **NEW:** AJAX request `Accept: */*` + `X-Requested-With: XMLHttpRequest` → JSON
    - **NEW:** Browser-fetch `Accept: */*` (no AJAX indicator) on `/admin/*` → HTML
  - Golden-fingerprint test: render `templates/error.html` with fixed context, assert rendered HTML byte-hash matches committed fingerprint.
  - Green: implement + author `error.html`.
  - Guard: `test_architecture_exception_handlers_accept_aware.py` (empty allowlist).
- **Test-Before-Implement pattern:** Pattern (a) — `_response_mode()` is a pure function; stub returning trivially-wrong answer lets tests fail semantically.
- **Dependencies:** L0-01, L0-05 (TemplatesDep for `error.html` render).
- **Reference:** `foundation-modules.md` §11.10 + §11.11; Critical Invariant #3; `flask-to-fastapi-deep-audit.md` §1.3; `frontend-deep-audit.md` F6 / H7; `testing-strategy.md` Tier 1 row 7.
- **Risk:** **LOW-MEDIUM** — `_response_mode()` must check `request.url.path.startswith(("/admin/", "/tenant/"))` AND handle `*/*` correctly to avoid false-positive JSON for browser fetch.

### L0-15 foundation module: `src/admin/app_factory.py` (empty `build_admin_router()`)

- **Rationale:** Seed admin router aggregate. Empty at L0.
- **Citations:** `flask-to-fastapi-adcp-safety.md:141-165` (aggregation + `include_in_schema=False` requirement).
- **Files:** `src/admin/app_factory.py` (~80 LOC); `src/admin/routers/__init__.py` (stub).
- **LOC:** ~80 production + ~40 tests.
- **Tests:**
  - Red (pattern b): `tests/unit/admin/test_app_factory_empty.py` asserts `build_admin_router().routes == []`. `pytest.raises(ImportError)` is SEMANTIC because the function's absence IS the obligation at L0. (L1a adds route-presence assertions.)
  - Green: implement.
  - Also `test_trailing_slash_tolerance.py` dormant at L0 (§5.6 owner assignment) — asserts every `APIRouter` constructed in `src/admin/` uses `redirect_slashes=True`. At L0 scan returns zero hits; meta-fixture proves scanner works.
- **Test-Before-Implement pattern:** Pattern (b) — factory-function absence is the obligation.
- **Dependencies:** L0-01.
- **Reference:** `foundation-modules.md` §11.10; `adcp-safety.md:141-165`.
- **Risk:** **LOW**.

### L0-16 foundation-modules import smoke test

- **Rationale:** `test_foundation_modules_import.py` asserts all 14 foundation modules (now including `templates/error.html` via TemplatesDep resolve test) import in <1s.
- **Files:** `tests/unit/test_foundation_modules_import.py` (~40 LOC).
- **LOC:** ~40 tests.
- **Dependencies:** L0-04..L0-15 + L0-32.
- **Reference:** `execution-plan.md` Exit gate.
- **Risk:** **LOW**.

### L0-17 `form_error_response()` shared helper

- **Rationale:** DRY helper for form-validation re-rendering across 25 routers.
- **Files:** `src/admin/helpers/form_errors.py` (~50 LOC).
- **LOC:** ~50 production + ~80 tests.
- **Tests:** Red (pattern a): stub returning None; semantic test asserts returned `TemplateResponse` has status 422 and re-renders with form data.
- **Test-Before-Implement pattern:** Pattern (a).
- **Dependencies:** L0-01.
- **Reference:** `execution-plan.md` item 3.
- **Risk:** **LOW**.

### L0-18 feature flag `ADCP_USE_FASTAPI_ADMIN` + `X-Served-By` middleware

- **Rationale:** L0 entry observability + instant rollback per `CLAUDE.md` §Observability §6.5 and `implementation-checklist.md` §2088-2091.
- **Citations:** `implementation-checklist.md:2088-2091` + `async-audit/testing-strategy.md:174` (Pre-Wave-3 gate: `X-Served-By` is the verification signal).
- **Files:**
  - `src/core/feature_flags.py` / `src/core/config.py` extension (~20 LOC) — pydantic-settings read of `ADCP_USE_FASTAPI_ADMIN`.
  - `src/admin/middleware/served_by.py` (~20 LOC) — stamps `X-Served-By: flask` at L0.
- **LOC:** ~40 production + ~80 tests.
- **Tests:**
  - Red (pattern a): Stub `ServedByMiddleware` class emitting nothing. Semantic tests: (1) feature flag value readable via config module; (2) response header `X-Served-By` present. Fail against stub.
  - Guards: `test_architecture_feature_flag_gate_active.py` + `test_architecture_x_served_by_header_emitted.py`.
  - Green: implement.
- **Test-Before-Implement pattern:** Pattern (a).
- **Dependencies:** L0-01.
- **Reference:** `implementation-checklist.md` §2088-2091 + `testing-strategy.md:174`.
- **Risk:** **LOW-MEDIUM** — `pydantic-settings>=2.7.0` pin lands at L0 per `execution-plan.md` §138.

### L0-19 `/metrics` endpoint scaffold

- **Rationale:** L0 entry observability per `implementation-checklist.md` §2089.
- **Files:** `src/routes/metrics.py` (~40 LOC); `src/app.py` — add `app.include_router(metrics_router)`. Per §7.2 RATIFIED: permitted as leaf route (not middleware mutation).
- **LOC:** ~40 production + ~40 tests.
- **Tests:** Red: `GET /metrics` → 404 (not wired). Green: 200 + `text/plain; version=0.0.4`. Pattern (a) with absence-of-route as semantic failure.
- **Test-Before-Implement pattern:** Pattern (a) — route absence is asserted via `assert response.status_code == 404` against current state.
- **Dependencies:** None.
- **Reference:** `implementation-checklist.md` §2165-2168.
- **Risk:** **LOW**.

### L0-20 template codemod scripts (written, NOT executed at L0)

- **Rationale:** `execution-plan.md` items 6-8 — script lands at L0, execution at L1a.
- **Files:**
  - `scripts/generate_route_name_map.py` (~50 LOC)
  - `scripts/codemod_templates_greenfield.py` (~200 LOC)
- **LOC:** ~250 production + ~250 tests.
- **Tests:**
  - `tests/migration/test_codemod_idempotent.py` — applies codemod to frozen-state post-codemod fixture (3-5 templates per §7.3 RATIFIED) once, then twice; asserts second run zero diff. Red: stub codemod returns input unchanged (idempotent vacuously); ACTUAL test asserts codemod handles each of the 4 passes (csrf/g/flash, static, hardcoded paths, Flask dotted names) — each pass tested independently via frozen fixture.
  - Ownership: `test_templates_no_hardcoded_admin_paths.py` (§5.6 — owner L0-20) ships in the codemod PR.
- **Test-Before-Implement pattern:** Pattern (a) — stub codemod exists; behavioral test asserts pass-by-pass correctness.
- **Dependencies:** L0-01.
- **Reference:** `execution-plan.md` items 6-8; `implementation-checklist.md` §442-464.
- **Risk:** **MEDIUM** — fixture provenance documented in codemod idempotency test docstring (§7.3 refinement).

### L0-21 golden-fixture capture infrastructure

- **Rationale:** `execution-plan.md` item 10 — response fingerprint capture for L1+ parity.
- **Files:**
  - `tests/migration/fingerprint.py` (~100 LOC)
  - `tests/migration/conftest_fingerprint.py` (~60 LOC)
  - `tests/migration/test_response_fingerprints.py` (~80 LOC)
  - `tests/migration/fixtures/fingerprints/*.json`
- **LOC:** ~240 production test + fixture JSON.
- **Dependencies:** L0-04..L0-15.
- **Reference:** `execution-plan.md` item 10; `implementation-checklist.md` §TI-1.
- **Risk:** **LOW-MEDIUM**.

### L0-22 `IntegrationEnv.get_admin_client()` harness extension

- **Rationale:** `execution-plan.md` item 11 — enables L1+ integration tests to obtain `TestClient` with `dependency_overrides` snapshot/restore.
- **Files:** `tests/harness/_base.py` — add `get_admin_client()` method.
- **LOC:** ~60 + ~60 unit test.
- **Tests:** Red (pattern a): stub method returns a shared client (broken isolation); test asserts sequential contexts see no dep-override leakage; fails.
- **Test-Before-Implement pattern:** Pattern (a).
- **Dependencies:** L0-01, L0-15.
- **Reference:** `execution-plan.md` item 11; Blocker-3.3.
- **Risk:** **LOW**.

### L0-23 doc-drift linters (6 guards, §11.34)

- **Rationale:** `foundation-modules.md` §11.34 — prevents spec drift.
- **Files:** 6 guard files (per v1).
- **LOC:** ~600 + meta fixtures.
- **Dependencies:** L0-01.
- **Risk:** **MEDIUM** — markdown-parsing brittleness.

### L0-24 native-idiom guard: `test_architecture_no_pydantic_v1_config.py`

- **Rationale:** §11.35 — empty allowlist.
- **LOC:** ~80.
- **Dependencies:** L0-01.
- **Risk:** **LOW**.

### L0-25 meta-guards (structural-guard hygiene)

- **Rationale:** `foundation-modules.md` §11.26 + `implementation-checklist.md` §1929.
- **Files:**
  - `tests/unit/architecture/test_structural_guard_allowlist_monotonic.py` (~150 LOC) — **extended in v2** with stale-entry assertion logic (§5.X convention).
  - `tests/unit/architecture/test_architecture_allowlist_fixme_coverage.py` (~100 LOC).
- **LOC:** ~250 tests.
- **Dependencies:** L0-01.
- **Reference:** `foundation-modules.md` §11.26; `implementation-checklist.md` §1929.
- **Risk:** **MEDIUM**.

### L0-26 `pyproject.toml` + docs

- **Rationale:** `execution-plan.md` §135, `implementation-checklist.md` §521.
- **Files:** `pyproject.toml`, `.env.example`, `docs/deployment/environment-variables.md`, `docs/migration/v2.0-oauth-uris.md`.
- **LOC:** ~40.
- **Tests:** None (dep bump; discipline waiver).

### L0-27 BDD sweep for Flask references

- **Rationale:** `implementation-checklist.md` §505.
- **LOC:** ≈0 net.

### L0-28 `.pre-commit-hooks/check_hardcoded_urls.py` rewrite

- **Rationale:** `implementation-checklist.md` §501.
- **LOC:** ~80 hook + ~60 fixture tests.

### L0-29 static JS URL strategy decision + doc

- **Rationale:** `implementation-checklist.md` §502. Per §7.5 RATIFIED WITH REFINEMENT: **doc-only at L0**. The `rg 'scriptRoot\|script_root' static/js/` = 0 check is EXPLICITLY RELAXED at L0; enforced per-router at L1c/L1d.
- **Files:** `docs/deployment/static-js-urls.md` (~150 LOC).
- **Risk:** **LOW**.

### L0-30 `.duplication-baseline` temporary relaxation

- **Rationale:** `implementation-checklist.md` §503.
- **Risk:** **LOW**.

### L0-31 `anyio` threadpool limiter bump in lifespan

- **Rationale:** `implementation-checklist.md` §438-440. Permitted `src/app.py` lifespan-only mod per §7.2 RATIFIED.
- **Files:** `src/app.py` — 2 lines in `app_lifespan()` before `yield`.
- **LOC:** ~4 production + ~30 tests.
- **Dependencies:** None.
- **Risk:** **LOW**.

### L0-32 `admin_redirect()` 302-default helper (NEW in v2)

- **Rationale:** Per `async-audit/frontend-deep-audit.md` F3 + `async-audit/testing-strategy.md` Tier 1. Flask's `redirect()` defaults to 302; FastAPI's `RedirectResponse` defaults to 307. At L1+, 338 `redirect()` call sites port to FastAPI — without a 302-default helper, each port risks accidentally switching to 307, which preserves POST body and breaks GET-after-POST PRG idioms. The ApproximatedExternalDomainMiddleware correctly uses 307 (POST body preservation for external-domain redirects); admin handler redirects default to 302 (GET after POST-Redirect-Get).
- **Files:** `src/admin/helpers/redirects.py` (~30 LOC).
- **LOC:** ~30 production + ~60 tests.
- **Tests:**
  - Red (pattern a): Stub `admin_redirect(url) -> RedirectResponse` returning 307. 5 tests: (1) default is 302; (2) `status=307` overrideable; (3) preserves query string; (4) absolute-URL passthrough; (5) `url_for(...)` target shape (redirect-to-named-route). Fail against stub.
  - Green: implement — `def admin_redirect(url: str, status_code: int = 302) -> RedirectResponse: return RedirectResponse(url, status_code=status_code)`.
- **Test-Before-Implement pattern:** Pattern (a) — stub-then-behavioral.
- **Dependencies:** L0-01.
- **Reference:** `async-audit/frontend-deep-audit.md` F3; `async-audit/testing-strategy.md` Tier 1 `admin_redirect` row.
- **Risk:** **LOW**.

---

## 4. Sub-commit plan (Red→Green pairs)

Per `CLAUDE.md` §Test-Before-Implement, each work item is one Red+Green pair unless waiver applies.

| # | Commit sequence | Waiver? | Red pattern |
|---|---|---|---|
| L0-00 | `refactor: rename blueprints→routers` + guard Red | `discipline: N/A - pure mechanical codemod` | N/A |
| L0-01 | 16 Red + 16 Green commits, batched 4 per sub-PR | No | Pattern (a) — meta-fixtures |
| L0-02 | 9 Red+Green pairs | No | Pattern (a) |
| L0-03 | Red: `test: retire scoped_session` → Green: `refactor(core): drop scoped_session` | No | Pattern (a) — semantic identity assertion |
| L0-04 | Red: `test: MessagesDep round-trip — expected ImportError` → Green: `feat(admin): add messages.py` | No | **Pattern (b)** |
| L0-05 | Red (stub-first) + Green for templates.py + 11-key BaseCtxDep + tojson filter tests | No | **Pattern (a)** |
| L0-06 | Red (stub-first) + Green for csrf.py | No | **Pattern (a)** |
| L0-07 | Red (stub-first) + Green for external_domain.py | No | **Pattern (a)** |
| L0-08 | Red (stub-first) + Green for fly_headers.py | No | **Pattern (a)** |
| L0-09 | Red (stub-first) + Green for request_id.py | No | **Pattern (a)** |
| L0-10 | Red (stub-first) + Green for unified_auth.py (4 scenarios) | No | **Pattern (a)** |
| L0-11 | Red: `test: OAuth callback constants — ImportError` → Green | No | **Pattern (b)** |
| L0-12 | 3 Red+Green pairs (stub-first) | No | **Pattern (a)** |
| L0-13 | Red (stub-first) + Green for cache.py | No | **Pattern (a)** |
| L0-14 | Red (stub-first) + Green for content_negotiation.py + error.html + 6-case matrix | No | **Pattern (a)** |
| L0-15 | Red: `test_app_factory_empty — ImportError` → Green | No | **Pattern (b)** |
| L0-16 | Atomic smoke commit | `discipline: N/A` | N/A |
| L0-17 | Red+Green (stub-first) for form_error_response | No | **Pattern (a)** |
| L0-18 | 2 Red+Green pairs (stub-first) | No | **Pattern (a)** |
| L0-19 | Red (404) → Green (200) | No | **Pattern (a)** |
| L0-20 | Red: codemod idempotency → Green | No | Pattern (a) |
| L0-21 | Red: fingerprint baseline expectation → Green: capture via `/capture-fixtures` | No | Pattern (a) |
| L0-22 | Red: dep-override leakage → Green | No | **Pattern (a)** |
| L0-23 | 6 Red+Green pairs | No | Pattern (a) |
| L0-24 | Red+Green for pydantic-v1-config | No | Pattern (a) |
| L0-25 | 2 Red+Green pairs | No | Pattern (a) |
| L0-26 | Dep-bump commit | `discipline: N/A - dep bump` | N/A |
| L0-27 | BDD sweep | `discipline: N/A - dead-code deletion` | N/A |
| L0-28 | Red: hook rejects scriptRoot → Green | No | Pattern (a) |
| L0-29 | Docs-only commit | `discipline: N/A - docs-only` | N/A |
| L0-30 | Baseline bump | `discipline: N/A - infra config` | N/A |
| L0-31 | Red+Green for threadpool limiter | No | Pattern (a) |
| L0-32 | Red+Green for admin_redirect (302 default) | No | **Pattern (a)** |

**Total commits at L0:** ≈ 64 Red-Green pairs + 6 waivers + 1 codemod-atomic = **~71 commits** before squash-merge.

---

## 5. Structural guard inventory (L0)

### 5.1 Guards landed pre-L0 (hardening PR)

| # | Guard | Allowlist strategy |
|---|---|---|
| 1 | `test_architecture_no_runtime_psycopg2` | Captured→shrink (1 entry: `db_config.py`) |
| 2 | `test_architecture_get_db_connection_callers_allowlist` | Frozen (`run_all_services.py`) |
| 3 | `test_architecture_no_module_level_get_engine` | Frozen empty |

### 5.2 NEW guards landing at L0

Retained from v1 with ownership clarifications (§5.6) and allowlist paths enumerated (§5.4). Full table is the v1 §5.2 table (rows 1-48) — preserved byte-for-byte for traceability. Key rows below are those with v2 changes:

**Row 16** `test_architecture_no_direct_env_access.py` — Captured→shrink. Allowlist file: `tests/unit/architecture/allowlists/no_direct_env_access.txt` (v2 addition).

**Row 34** `test_architecture_no_werkzeug_imports.py` — Captured→shrink. Allowlist file: `tests/unit/architecture/allowlists/no_werkzeug_imports.txt` (v2 addition).

**Row 1** `test_architecture_no_flask_imports.py` — Captured→shrink. Allowlist file: `tests/unit/architecture/allowlists/no_flask_imports.txt` (v1 already enumerated; v2 confirms convention).

All other rows (2-15, 17-33, 35-48) retain v1 specification exactly.

### 5.3 Guard-count reconciliation

**48 guards total at L0 exit.** Per §7.6 RATIFIED: accept 48; `implementation-checklist.md §497` doc-fix (16 → 48) ships as SEPARATE doc-only PR BEFORE L0-00 to prevent contributor confusion.

### 5.4 Captured→shrink allowlist file convention

All Captured→shrink guards use: `tests/unit/architecture/allowlists/<guard_name>.txt` (one entry per line, `# FIXME(salesagent-xxxx)` comment paired at source per `test_architecture_allowlist_fixme_coverage.py`).

Enumerated:
- Row 1 `no_flask_imports` → `tests/unit/architecture/allowlists/no_flask_imports.txt`
- Row 16 `no_direct_env_access` → `tests/unit/architecture/allowlists/no_direct_env_access.txt`
- Row 34 `no_werkzeug_imports` → `tests/unit/architecture/allowlists/no_werkzeug_imports.txt`

### 5.5 Stale-entry meta-guard convention (NEW in v2)

Per revision (C). Every CAPTURED→shrink guard must assert that allowlisted files still exhibit the banned pattern — otherwise the allowlist is "lying" and the fix has already happened but the entry wasn't removed.

**Uniform convention:** `test_structural_guard_allowlist_monotonic.py` (L0-25) is extended with stale-entry logic. For each allowlist file, the meta-guard:
1. Parses the allowlist entries (one path per line).
2. For each entry, re-runs the guard's pattern scanner ONLY against that file.
3. Asserts the pattern IS still present (i.e., the allowlist entry is still earning its keep).
4. If ANY allowlisted file no longer matches the pattern, the meta-guard FAILS with a message naming the stale entry and instructing the author to remove it from the allowlist.

This is the single source of truth for stale-entry detection — no per-guard stale-entry tests needed. Meta-fixture for this logic plants a stale entry (allowlist mentions a file that doesn't contain the pattern) and proves the meta-guard fires.

### 5.6 Orphan guard owner assignments (NEW in v2)

Per revision (E), each orphan guard now has an owning L0 work item:

| Guard | Owner | Rationale |
|---|---|---|
| #17 `test_templates_url_for_resolves.py` | **L0-05** (explicit, not folded into L0-01) | Critical Invariant #1. Must be explicit L0 item because it enforces the single most load-bearing template contract. Lands with TemplatesDep. |
| #18 `test_templates_no_hardcoded_admin_paths.py` | **L0-20** (codemod PR) | The codemod enforces this pattern; guard ships in the codemod PR. |
| #22 `test_trailing_slash_tolerance.py` | **L0-15** (build_admin_router) | Critical Invariant #2. Dormant at L0 (routers empty); guard lands with `build_admin_router()`. |
| #27 `test_architecture_inventory_cache_uses_module_helpers.py` | **L0-13** (cache.py) | Ships alongside `SimpleAppCache`. |
| #31 `test_architecture_templates_no_script_root.py` | **L0-05** (templates) | Same scanning domain as Row 17; ships with TemplatesDep. |
| #32 `test_architecture_x_served_by_header_emitted.py` | **L0-18** (feature flag) | Already cited in v1 L0-18; explicit in v2. |
| #33 `test_architecture_feature_flag_gate_active.py` | **L0-18** (feature flag) | Same. |
| #34 `test_architecture_no_werkzeug_imports.py` | **L0-01** (batch) | Captured→shrink landing in the 16-guard batch. |
| #35 `test_architecture_no_flask_caching_imports.py` | **L0-13** (cache.py) | Ships with SimpleAppCache. |
| #38 `test_architecture_no_pydantic_v1_config.py` | **L0-24** (already owned in v1) | Confirmed. |

All 48 guards now owned.

### 5.7 Dormant vs active guards at L0 (§7.4 RATIFIED)

Every dormant-at-L0 guard ships with a planted-violation meta-fixture per `/write-guard` skill convention. Listed dormant guards: `test_architecture_admin_routes_named.py`, `test_architecture_admin_routes_excluded_from_openapi.py`, `test_trailing_slash_tolerance.py`, `test_architecture_handlers_use_annotated.py`, `test_architecture_templates_receive_dtos_not_orm.py`, `test_architecture_exception_handlers_accept_aware.py` (dormant at L0), `test_architecture_exception_handlers_complete.py` (dormant at L0). Each has a planted-violation fixture proving the scanner works.

---

## 6. Exit-gate audit

| Exit criterion | Satisfied by |
|---|---|
| `make quality` green | L0-01..L0-32 |
| `tox -e integration` green | L0-03, L0-12, L0-22 |
| `tox -e bdd` green | L0-27 |
| `./run_all_tests.sh` green | Full suite after all items land |
| `test_codemod_idempotent.py` green on post-codemod fixture | L0-20 (per §7.3) |
| `test_architecture_no_pydantic_v1_config.py` green empty | L0-24 |
| All 48 structural guards green | L0-01, L0-02, L0-03, L0-23, L0-24, L0-25, L0-13, L0-18, plus L0-05/L0-15/L0-20 (orphan owners) |
| `admin_redirect()` default-302 contract asserted | L0-32 |
| `templates/error.html` pinned + golden fingerprint | L0-14 |
| 11-key `BaseCtxDep` contract asserted | L0-05 |
| Jinja `tojson` filter registered | L0-05 |
| `_response_mode()` handles AJAX + browser-fetch `*/*` correctly | L0-14 |
| Captured→shrink allowlist files exist at `tests/unit/architecture/allowlists/` | L0-01 (3 files per §5.4) |
| Stale-entry meta-guard catches lying allowlists | L0-25 (per §5.5) |
| Every dormant guard has planted-violation meta-fixture | L0-01 (per §5.7) |
| Flask traffic share = 100% (L0 thesis) | L0-18 (`X-Served-By: flask`) |
| `/metrics` scaffolded | L0-19 |
| `X-Served-By` header wired | L0-18 |
| `pyproject.toml` has `pydantic-settings>=2.7.0` | L0-26 |
| `src/app.py` mods scoped to permitted set (§7.2): metrics router include + threadpool limiter | L0-19, L0-31 |
| No Flask files deleted | Invariant respected |
| OAuth callback URIs enumerated in runbook | L0-26 |
| PRE-1..PRE-3 landed before L0-03 | Hard blocker |
| Static JS `scriptRoot` scan: **RELAXED at L0** (§7.5) | enforced per-router at L1c/L1d |
| `admin-migration-health` dashboard Traffic tab | User-side (Datadog/etc); pending |
| `implementation-checklist.md §497` doc-fix (16→48) landed as separate pre-L0 doc PR | Per §7.6 RATIFIED |

---

## 7. Ratified Decisions (previously Open Questions)

All 8 questions resolved by the independent adjudicator pass 2026-04-19. Decisions are binding unless user explicitly overrides.

### §7.1 Test-directory convention — RATIFIED 2026-04-19

**Decision:** Use `tests/unit/architecture/` for ALL L0 guards. Per `implementation-checklist.md:1962-1964`, pre-L0 landed guards are already at `tests/unit/architecture/` — so there is no "pre-v2.0 flat" location to leave behind. The v1 phrase "leave pre-v2.0 guards at current locations" is struck.

### §7.2 `src/app.py` modification scope at L0 — RATIFIED 2026-04-19

**Decision:** PERMIT leaf-route `/metrics` include + `anyio` threadpool-limiter lifespan bump at L0. The "do not modify `src/app.py`" sentence in `execution-plan.md:145` is narrowly scoped to middleware + admin-router inclusion. L0-19 + L0-31 are the permitted scope; any other `src/app.py` change requires user approval.

### §7.3 Codemod idempotency test fixture strategy — RATIFIED 2026-04-19

**Decision:** Frozen post-codemod fixture (3-5 template files, hand-crafted). Document fixture provenance in the codemod idempotency test docstring (naming each source template, explaining why that template was chosen, and how to regenerate if the codemod's pass-semantics change).

### §7.4 Dormant vs active guard enforcement at L0 — RATIFIED 2026-04-19

**Decision:** Every dormant guard ships with a planted-violation meta-fixture per `/write-guard` skill convention. See §5.7 inventory.

### §7.5 Static JS URL migration timing — RATIFIED WITH REFINEMENT 2026-04-19

**Decision:** Doc-only at L0. The `rg 'scriptRoot\|script_root' static/js/` = 0 check is EXPLICITLY RELAXED at L0 — at L0 exit, this command will return non-zero matches and that is acceptable. Per-router enforcement happens at L1c/L1d (each JS file migrates with its owning template). This relaxation is documented inline in the L0 exit gate (§6).

### §7.6 Guard-count reconciliation (48 vs "16") — RATIFIED 2026-04-19

**Decision:** Accept 48 guards as the correct count. The `implementation-checklist.md §497` doc-fix (changing "16" to the accurate count) lands as a SEPARATE doc-only PR BEFORE L0-00 to prevent contributor confusion during L0 implementation. Doc-fix PR has `discipline: N/A - docs-only` waiver.

### §7.7 Pre-L0 refactor PRs (PRE-1..PRE-3) ownership — RATIFIED 2026-04-19

**Decision:** PRE-1..PRE-3 ship as pre-L0 commits on `feat/v2.0.0-flask-to-fastapi` (consistent with the hardening PR pattern `246067de..64cf0125`). Each is a strict improvement under the current `scoped_session` world. User attests landing before L0-03 Red commit.

### §7.8 Agent-team execution of L0 — RATIFIED WITH REFINEMENT 2026-04-19

**Decision:** 3-agent fan-out structurally, BUT **L0-04 runs SOLO as a single-agent canary** first. Sequence:
1. L0-00 (trunk, 1 agent)
2. L0-01 (trunk, 1 agent — meta-fixture authorship is novel, stabilize before parallelizing)
3. L0-03 (trunk, 1 agent — HIGH-risk)
4. **L0-04 SOLO** (trunk, 1 agent — canary for the D8-native deps pattern)
5. **Fresh-agent review pass** on L0-04 before scaling
6. L0-05..L0-15 fan out to 3 parallel agents
7. Re-serialize at L0-16 (smoke), L0-21 (fingerprints), L0-22 (harness)
8. Remaining items (L0-17..L0-20, L0-23..L0-32) parallelize freely.

---

**End of L0 implementation plan v2.**
