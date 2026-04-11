Plan mode is active and I do not have Write access. I will deliver the full deeply-elaborated plan as my final assistant message, as the task instructions explicitly permit: "Return findings directly as your final assistant message — the parent agent reads your text output, not files you create."

---

# Flask → FastAPI Migration: Deep Elaboration of §14, §16, §21

This document elaborates three sections of `/Users/quantum/Documents/ComputedChaos/salesagent/.claude/notes/flask-to-fastapi-migration.md`: the 4-wave migration strategy (§14), the 28 assumptions (§16), and the verification strategy (§21). All file paths are absolute. Line numbers reference the current HEAD of the repository.

---

## PART 1: Per-Wave Execution Detail (elaborates §14)

### Wave 0 — Foundation + template codemod (~2,500 LOC)

#### A. Detailed acceptance criteria

1. `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/templating.py` exists, exports `render(request, name, context)` and a module-level `templates: Jinja2Templates` singleton; `python -c "from src.admin.templating import render, templates"` succeeds.
2. `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/flash.py` exposes `flash(request, message, category='info')` and `get_flashed_messages(request, with_categories=False)`; both are imported by `templating.py` and exposed as Jinja globals.
3. `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/sessions.py` exports `build_session_middleware_kwargs() -> dict`, returning `secret_key` from `SESSION_SECRET`, `session_cookie='adcp_session'`, `same_site='lax'`, `https_only=True` in production.
4. `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/csrf.py` exposes a pure-ASGI `CSRFMiddleware` class plus `csrf_token(request)` jinja helper; `python -c "from src.admin.csrf import CSRFMiddleware"` succeeds.
5. `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/oauth.py` registers an `authlib.integrations.starlette_client.OAuth` instance named `oauth` with a Google client; module-level constant `GOOGLE_CLIENT_NAME = "google"`.
6. `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/deps/auth.py` exports `CurrentUserDep`, `RequireAdminDep`, `RequireSuperAdminDep` as `Annotated[...]` aliases with module-level `async def` dep functions (full-async pivot 2026-04-11 — dep functions use `async with get_db_session()` and `await db.execute(...)`).
7. `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/app_factory.py::build_admin_router()` returns an empty `APIRouter(prefix="/admin", tags=["admin"])` — importable, callable, returns a non-None router.
8. `/Users/quantum/Documents/ComputedChaos/salesagent/scripts/codemod_templates.py` runs to completion against all 72 templates in `/Users/quantum/Documents/ComputedChaos/salesagent/templates/` with exit code 0; stdout reports a count line `"72 templates processed, N transformations applied"`.
9. After the codemod runs, `git diff --stat templates/` shows changes in at least 40 files (every template with a `url_for` call); `grep -R "url_for(" templates/ | wc -l` output unchanged (the codemod rewrites the *argument* of `url_for`, not its name).
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
- `/Users/quantum/Documents/ComputedChaos/salesagent/scripts/codemod_templates.py` (~80 LOC)
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

Database state: no migrations. No env var changes require backing out (SESSION_SECRET only needs to be set when Wave 1 lands). Rollback window: until Wave 1 merges. After Wave 1 merges, a Wave 0 revert is still safe as long as the revert preserves `src/admin/templating.py` (Wave 1 depends on it) — so rollback becomes a *partial* revert by that point: `git revert <sha> -- templates/ scripts/codemod_templates.py` only, leaving foundation modules in place.

#### E. Merge-conflict resolution

**Branch freeze scope:** `templates/**` only (foundation modules live in a new namespace and cannot conflict).

**Announcement template:**
```
[MIGRATION] Wave 0 lands <date>. Templates freeze from <date-1> 17:00 UTC
to <date> 23:59 UTC. Avoid opening PRs that touch files under templates/.
If you must, rebase onto main after the codemod lands; expect conflicts
on url_for(...) sites and resolve by re-running:
    python scripts/codemod_templates.py templates/your_file.html
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
- `python scripts/codemod_templates.py --check templates/` returns exit code 0 (idempotent re-run).
- Coverage for `src/admin/**` not yet changed (foundation modules have smoke-test-only coverage; that's acceptable because nothing calls them yet).
- `git log --oneline main..HEAD` shows a single squashed merge commit.

---

### Wave 1 — Foundational routers + session cutover (~4,000 LOC)

#### A. Detailed acceptance criteria

1. `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/routers/public.py`, `core.py`, `auth.py`, `oidc.py` exist with every route from the corresponding Flask blueprints in `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/blueprints/`.
2. `GET /admin/login` returns 200 from `src/admin/routers/auth.py::login`, not from Flask. Verified by grep: `curl -sI http://localhost:8000/admin/login | grep -i server` returns the uvicorn banner, and a new integration test `tests/integration/test_admin_auth_router.py` asserts the route resolves via `IntegrationEnv.get_admin_client()`.
3. `POST /admin/auth/callback` (Google OAuth) completes a full redirect chain ending at `/admin/` with a valid `adcp_session` cookie set by `SessionMiddleware`.
4. `GET /admin/health` returns 200 from the new FastAPI `core.py` router. Old Flask `/admin/health` route is commented out in `src/admin/app.py`.
5. `SessionMiddleware` is registered in `/Users/quantum/Documents/ComputedChaos/salesagent/src/app.py` between `CORSMiddleware` (outermost) and `UnifiedAuthMiddleware`. Middleware ordering verified by `test_middleware_ordering.py`.
6. `CSRFMiddleware` is registered **inside** `SessionMiddleware` (so `request.session` is available to CSRF code) but **outside** `UnifiedAuthMiddleware`. Unit test asserts order.
7. `ApproximatedExternalDomainMiddleware` is registered in `src/app.py`; test confirms non-admin paths short-circuit without session access.
8. `register_blueprint` calls for `public`, `core`, `auth`, `oidc` in `src/admin/app.py` are commented out (not deleted — Wave 2 deletes them).
9. Flask catch-all mount at `src/app.py:299-304` **still exists** and still serves the other 26 blueprints.
10. Session cookie name change: a stale `session=...` cookie in a request returns a fresh login page (not an error). Verified by Playwright test `login_with_stale_flask_cookie`.
11. CSRF double-submit: `POST /admin/auth/logout` with valid session but no CSRF header returns 403; with valid session + matching cookie+header returns 303.
12. CSRF read-side generates a cookie on first GET of a page with a form; template helper `csrf_token(request)` emits the token in a hidden field; `grep '{{ csrf_token' templates/ | wc -l` > 0 after codemod catches up.
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
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/app.py` — register `SessionMiddleware`, `CSRFMiddleware`, `ApproximatedExternalDomainMiddleware`, `include_router(build_admin_router())`. Lines 274-293 (middleware stack) and 299-304 (mount) both touched.
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/app.py` — comment out `register_blueprint` calls for the 4 migrated blueprints.
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/app_factory.py` — `build_admin_router()` now `include_router`s the 4 feature routers.
- `/Users/quantum/Documents/ComputedChaos/salesagent/tests/unit/test_architecture_no_flask_imports.py` — **remove** `public.py/core.py/auth.py/oidc.py` from the allowlist (forbids re-introducing Flask in migrated files).
- `/Users/quantum/Documents/ComputedChaos/salesagent/tests/integration/conftest.py` — `authenticated_admin_client` fixture swaps from Flask `test_client()` to `IntegrationEnv.get_admin_client()` for routes now served by FastAPI.
- `/Users/quantum/Documents/ComputedChaos/salesagent/pyproject.toml` — add `sse-starlette>=2.2.0`, `pydantic-settings>=2.7.0`, `itsdangerous>=2.2.0` (Wave 1 adds, Wave 3 removes Flask deps).

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
| CSRFMiddleware kills OAuth callback because Google POSTs the callback with no session cookie yet | Medium | Critical — OAuth broken | CSRF middleware exempts paths in a hardcoded list: `/admin/auth/callback`, `/admin/auth/oidc/callback/{tenant_id}`, `/api/v1/*`, `/a2a/*`, `/mcp/*`. Test asserts POST to exempt paths without CSRF returns the handler's response, not 403. |
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
8. `test_architecture_no_flask_imports.py` allowlist is **empty** at the end of Wave 2 (Flask still imported by `src/admin/app.py` + `src/app.py` catch-all + `activity_stream.py` — those 3 files move to Wave 3).
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

### Wave 3 — Activity stream SSE + cleanup cutover (~2,500 LOC)

#### A. Detailed acceptance criteria

1. `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/routers/activity_stream.py` exists using `sse_starlette.EventSourceResponse`.
2. `GET /admin/tenant/{tenant_id}/activity-stream` opens an SSE connection that emits events within 500ms of a tenant activity being logged, verified by `tests/integration/test_activity_stream_sse.py`.
3. Client disconnect detection works: a test that opens the SSE stream and then closes the client connection observes the server stops producing events within 2s (Playwright + manual Fly staging check).
4. `MAX_CONNECTIONS_PER_TENANT` backstop enforced: 11th concurrent connection for a single tenant returns 429.
5. `flask`, `flask-caching`, `flask-socketio`, `python-socketio`, `simple-websocket`, `waitress`, `a2wsgi`, `types-waitress` removed from `pyproject.toml`. `poetry lock --check` or `uv lock --check` succeeds.
6. `src/admin/app.py` **deleted**. `src/app.py:25-45` (`_install_admin_mounts`), `src/app.py:127-135` (`/a2a/` redirect), `src/app.py:299-304` (Flask mount), `src/app.py:351-352` (landing route insert hack) all deleted.
7. `CustomProxyFix` references removed from `src/app.py`. `FlyHeadersMiddleware` kept pending assumption #21 verification.
8. `.pre-commit-hooks/check_route_conflicts.py` **rewritten** to scan FastAPI routes using `app.routes` introspection; passes on current main.
9. `/Users/quantum/Documents/ComputedChaos/salesagent/templates/` moved to `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/templates/`. `git mv` used so history is preserved. `Jinja2Templates` singleton in `src/admin/templating.py` updated to new path.
10. `/Users/quantum/Documents/ComputedChaos/salesagent/static/` moved to `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/static/`. `StaticFiles` mount updated.
11. `test_architecture_no_flask_imports.py` allowlist is empty. `rg -w flask src/` returns zero hits. `rg 'from flask' tests/` returns zero hits.
12. v2.0.0 CHANGELOG entry added at `/Users/quantum/Documents/ComputedChaos/salesagent/CHANGELOG.md` with breaking changes section referencing §15.
13. Docker image build completes; `docker images adcp-salesagent:v2.0.0` size ≤ Wave 2 size − 60MB (conservative of the 80MB estimate in assumption #28).
14. Playwright full regression suite (all 5 flows from Part 3.C) passes against staging v2.0.0 build.
15. Production smoke test plan executed in staging first: deploy → login → create tenant → create product → submit creative → SSE activity stream visible → logout.

#### B. File-level checklist

**CREATE:**
- `/Users/quantum/Documents/ComputedChaos/salesagent/src/admin/routers/activity_stream.py` (~400 LOC)
- `/Users/quantum/Documents/ComputedChaos/salesagent/tests/integration/test_activity_stream_sse.py`
- `/Users/quantum/Documents/ComputedChaos/salesagent/tests/integration/test_activity_stream_disconnect.py`
- `/Users/quantum/Documents/ComputedChaos/salesagent/tests/integration/test_activity_stream_backpressure.py`
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
| `sse-starlette` disconnect detection silently fails behind nginx buffering | Medium | High — server leaks connections | Set `X-Accel-Buffering: no` header in SSE response; staging load test with 100 connections + forced disconnect. |
| Template path rewrite breaks every handler that uses a hardcoded relative path | Medium | High — runtime 500s | `Jinja2Templates(directory=...)` takes a single path; only one config site to change. Integration smoke test after move. |
| Dep removal triggers lockfile resolution hell (transitive deps re-evaluated) | Medium | Medium | Pin all remaining deps explicitly before lockfile regen; test `uv pip compile` in isolation. |
| `.pre-commit-hooks/check_route_conflicts.py` rewrite ships with a bug that passes on main but fails in CI | Low | Low — easy to fix | Unit test the new hook with a known-conflicting FastAPI app fixture. |
| Production traffic hits an unmapped Flask-only URL we forgot to migrate | Low | Critical — 404s for real users | Staging canary for 48h before production cut; monitor 404 rate. Wave 2 `test_route_parity.py` already asserts Wave-2 parity; Wave 3 only removes the catch-all that was already dead. |
| Docker image shrinkage less than 60MB (i.e., something else grew) | Low | Low | Non-blocker; log and investigate. Not a release blocker. |
| SSE test flakiness from timing-sensitive disconnect semantics | High | Medium — CI flake | Use explicit timeouts and retries; mark SSE disconnect test as `@pytest.mark.flaky(reruns=3)` — acceptable for this class of test. |
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

## PART 2: Assumption Verification Plan (elaborates §16)

Grouped by verification strategy. HIGH confidence assumptions get single-line plans; MEDIUM and LOW get full recipes.

### Group 1: HIGH confidence (9 — one-line verifications)

1. **FastAPI 0.128 / Starlette 0.50 ABI-stable.** Verify: `pip show fastapi starlette` matches locked versions pre-Wave-1; pin exact versions during Wave 2. Fail symptom: import error at startup. Fallback: bump pin floor.

2. **`Annotated[T, Depends()]` is canonical idiom.** Verify: `rg 'Annotated\[' src/core/auth_context.py` shows current usage (line 256-257); no verification needed beyond reading. N/A fallback.

3. **Full async SQLAlchemy in v2.0** (pivoted 2026-04-11). Verify: benchmark per Part 3.D compares async vs pre-migration sync baseline. When: Wave 2 baseline captured; Wave 4-5 comparison run. Failure: regression >10% on read-heavy hot endpoints (write-heavy regressions up to 15% acceptable). Fallback: tune `pool_size` (Risk #6) OR (last resort) hand-roll `selectinload` eager-loads on the worst offenders; if that's not enough, fall back to Option C and defer async to v2.1. Pre-Wave-0 lazy-loading audit spike (Risk #1) is the early-warning gate — if the audit reveals relationship-access scope is untenable, switch to Option C before starting Wave 0.

4. **Admin handlers `async def` + full async SQLAlchemy end-to-end** (pivoted 2026-04-11). Verify: AST guard `test_architecture_admin_routes_async.py` (renamed from the original `test_architecture_admin_async_signatures.py` for consistency with sibling guards) asserts every `src/admin/routers/*.py` handler is `async def`; sibling guard `test_architecture_admin_async_db_access.py` asserts DB access uses `async with get_db_session()` + `await db.execute(...)` rather than sync `with` or `run_in_threadpool(_sync_fetch)`. The stale `test_architecture_admin_sync_db_no_async.py` from the pre-pivot sync-def resolution is DELETED (wrong direction). When: Wave 1 entry (handler signature guard); Wave 4 entry (async DB access guard). Failure: sync handler or sync DB access found. Fallback: rewrite that handler.

5. **Starlette `SessionMiddleware` sufficient (<3.5KB).** See Group 3 detailed recipe below.

6. **`SESSION_SECRET` set in every deploy.** Verify: `src/admin/sessions.py::build_session_middleware_kwargs` raises `KeyError` on missing env var; `tests/unit/test_sessions_config.py` asserts this. When: Wave 0. Failure: startup crash. Fallback: obvious — set the env var.

7. **Admins tolerate one forced re-login.** User-confirmed decision #7. N/A verification.

8. **Authlib starlette_client feature-parity.** See Group 2 detailed recipe below.

9. **Route name translation `bp.endpoint → bp_endpoint` unique/stable.** Verify: `tests/admin/test_templates_url_for_resolves.py` asserts all flat names are unique. When: Wave 0. Failure: collision detected. Fallback: rename colliding routes.

### Group 2: MEDIUM confidence (12 — full recipes)

**10. Roll-your-own CSRF secure and correct.**
- **How:** unit tests `tests/unit/test_csrf_middleware.py` covering: valid cookie+header pass, cookie-only fail, header-only fail, mismatched fail, SameSite=Strict cookie flags present, exempt path bypass, non-unsafe method bypass. Plus Playwright `tests/e2e/test_admin_csrf_enforcement.py`. Plus security-focused code review by second engineer.
- **When:** Wave 0 exit (unit tests green). Wave 1 exit (Playwright green). Wave 2 entry (security review sign-off).
- **Failure symptom:** CSRF bypass demonstrated in a test; legitimate forms blocked.
- **Fallback:** adopt `starlette-csrf` from PyPI (explicitly rejected in §15 but reinstatable as escape hatch); ~1 day of work.

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
- **How:** `scripts/codemod_templates.py --dry-run templates/add_product_gam.html` prints 15 target transformations. Manual diff review.
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

**22. `SessionMiddleware` + SameSite=None prod tabs work.**
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

**28. Docker image shrinks ~80 MB.**
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

**How it ratchets:** each wave PR edits the `ALLOWLIST` set to remove migrated files. Wave 1 removes 4 entries. Wave 2 removes 25. Wave 3 removes the last 5 (`src/admin/app.py`, `src/app.py`, `src/admin/utils/helpers.py`, `src/admin/server.py`, `src/admin/blueprints/activity_stream.py`). After Wave 3, `ALLOWLIST = set()` and `test_no_flask_imports_outside_allowlist` enforces zero tolerance.

#### `tests/unit/test_architecture_admin_routes_async.py`

Scans `src/admin/routers/*.py` and asserts every function decorated with `@router.get/post/put/delete/patch` is `async def`. Sibling to existing `test_architecture_repository_pattern.py`. **Pivoted 2026-04-11:** this guard was originally named `test_architecture_admin_async_signatures.py` under a pre-pivot draft; renamed for consistency with other `test_architecture_admin_*_async.py` guards in the full-async pivot. The stale `test_architecture_admin_sync_db_no_async.py` (which asserted async handlers must wrap DB in `run_in_threadpool`) is the wrong direction under the pivot and is DELETED; this guard replaces it.

#### `tests/unit/test_architecture_admin_async_db_access.py`

Scans `src/admin/routers/*.py` and asserts every DB access site uses `async with get_db_session()` + `await db.execute(...)` patterns, NOT sync `with get_db_session()` or `run_in_threadpool(_sync_fetch)` wrappers around DB work. The `run_in_threadpool` helper is still valid for file I/O, CPU-bound, and sync-third-party-library calls — the guard specifically flags calls where the wrapped function does DB work (identified by an inner `get_db_session()` call or a `Session`/`AsyncSession` parameter). Sibling guard added under the full-async pivot (2026-04-11).

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
                session_data["csrf_token"] = "test-csrf-token-fixed"
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
        csrf = client.cookies["adcp_csrf"]

        resp = client.post(
            "/admin/tenant/t1/accounts",
            data={"name": "New Co", "csrf_token": csrf},
            headers={"X-CSRF-Token": csrf},
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
        csrf = client.cookies["adcp_csrf"]

        resp = client.post(
            f"/admin/tenant/t1/accounts/{acc.account_id}/status",
            json={"status": "paused"},
            headers={"X-CSRF-Token": csrf, "Accept": "application/json"},
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
        csrf = client.cookies["adcp_csrf"]

        fake_image = tmp_path / "banner.png"
        fake_image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        with fake_image.open("rb") as fh:
            resp = client.post(
                "/admin/tenant/t1/creatives/upload",
                files={"file": ("banner.png", fh, "image/png")},
                data={"name": "Summer Banner", "csrf_token": csrf},
                headers={"X-CSRF-Token": csrf},
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
- **Assertions:** (a) `/admin/login` returns Google button; (b) click Google button → mock OIDC server issues token → `/admin/auth/callback` redirects to `/admin/`; (c) dashboard visible; (d) `adcp_session` cookie present with `HttpOnly` flag; (e) session contains `email`, `tenant_id`, `authenticated=True`.

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
- **Assertions:** (a) GET form page; (b) extract form HTML; (c) POST with `csrf_token` omitted from body → 403 with `{"detail": "CSRF token missing"}`; (d) POST with mismatched token → 403; (e) POST with valid token → 303.

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

**Failure fallback:** if async regresses significantly under the benchmark, first tune `pool_size` (Risk #6 in `async-pivot-checkpoint.md` §4). If that doesn't close the gap, apply `selectinload` eager-loading to the worst offenders. If THAT doesn't close the gap, invoke the last-resort fallback: revert to Option C (sync `def` admin handlers) and defer async to v2.1.

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
