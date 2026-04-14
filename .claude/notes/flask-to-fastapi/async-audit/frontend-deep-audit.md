# Frontend Deep-Audit Report (2026-04-11)

> **ASYNC IS PHASE 4+ WITHIN v2.0 (2026-04-14).** Phases 0-3 use sync admin handlers. This report is the Phase 4+ implementation roadmap. Do not implement async patterns from this file during Phases 0-3 (Flask removal). The authoritative implementation guide is `execution-plan.md`.

> **Produced by:** 6 parallel Opus subagents with ultrathink, 2nd/3rd/4th-order derivative analysis
> **Scope:** Jinja templates + url_for, JavaScript + fetch endpoints, OAuth + session + auth flows, static assets + CSS, error pages + flash + admin UX, route parity + handler migration
> **Context:** Flask→FastAPI v2.0 migration removes Flask entirely from `src/admin/`

---

## Executive Summary

The frontend surface is **larger and more interconnected than the plan documents estimated**. The audit found **197 Flask routes** (not the ~150 implied by earlier estimates), **74 templates**, **~115 fetch() calls**, **366 flash() calls**, and **~147 script_root/script_name references** — confirming the plan's count on that last item exactly.

**The #1 risk: `base.html` is a force multiplier for every migration bug.** It contains `script_name`(7), `session.*`(9), `get_flashed_messages`(1), `g.test_mode`(1), `csrf_token`(1) — because all 54 page templates inherit from it, a single missed migration crashes the ENTIRE admin UI.

**The #1 surprise: CSRF is currently NOT enforced at all.** Flask-WTF's `CSRFProtect` is never initialized. Zero fetch() calls send CSRF tokens. The migration ADDS CSRF where none existed — every form POST will break unless templates are updated simultaneously with the middleware.

**The #2 surprise: 302→307 redirect default change.** Flask's `redirect()` defaults to 302 (converts POST to GET). FastAPI's `RedirectResponse` defaults to 307 (preserves POST). This affects **338 implicit-302 redirects** — every POST→redirect→GET pattern would become POST→redirect→re-POST, causing duplicate form submissions.

---

## Critical Blockers

### F1. OIDC callback path WRONG in plan documentation
**Source:** FE-3 (Auth)
CLAUDE.md and deep-audit reference `/admin/auth/oidc/{tenant_id}/callback`. The actual code path is **`/admin/auth/oidc/callback`** (no tenant_id — tenant context in session). Also, CLAUDE.md says `/auth/gam/callback` but actual is `/admin/auth/gam/callback`.

### F2. `base.html` cascade — breaks all 54 pages
**Source:** FE-1 (Templates)
Contains `script_name`(7), `session.*`(9), `get_flashed_messages()`(1), `g.test_mode`(1), `csrf_token()`(1). If ANY of these is undefined under FastAPI, every admin page breaks. Must be migrated FIRST.

### F3. 302→307 redirect default
**Source:** FE-5 (Errors/Flash)
338 implicit-302 redirects across 21 files. FastAPI defaults to 307 (preserves method). POST→flash→redirect becomes POST→flash→re-POST = duplicate submissions. Fix: `admin_redirect()` helper defaulting to 302.

### F4. CSRF added where none existed
**Source:** FE-2 (JavaScript), FE-3 (Auth)
`CSRFProtect` is never initialized in current Flask app. Zero fetch() calls send tokens. Adding `CSRFMiddleware` breaks all ~47 HTML form POSTs and ~80 JS POST/DELETE fetch calls unless tokens are added simultaneously.

### F5. `tojson` filter missing in Starlette
**Source:** FE-5 (Errors/Flash)
30+ template expressions across 12 templates use `tojson`. 5 use `tojson(indent=2)`. Starlette's `Jinja2Templates` does NOT include this Flask-specific filter. Must register manually with `indent` kwarg support.

### F6. AJAX Accept false-positive on error handler
**Source:** FE-5 (Errors/Flash)
Planned Accept-aware handler checks `"text/html" in Accept`. Browser `fetch()` sends `Accept: */*` which matches — AJAX error responses would return HTML not JSON, breaking all ~90 fetch error handlers.

### F7. Duplicate adapter route conflict
**Source:** FE-6 (Routes)
`adapters.py:22` and `MockAdServer.register_ui_routes` both register `/adapters/mock/config/{tenant_id}/{product_id}`. In Flask, last-registered wins. In FastAPI, startup error or undefined behavior.

---

## High-Severity Issues

### H1. 366 flash() calls — largest mechanical change
17 files, 4 categories (error/success/warning/info). FastAPI has no equivalent. Needs custom FlashMiddleware + `get_flashed_messages` Jinja global.

### H2. 130 Flask-style url_for calls need name mapping
55 unique Flask endpoint names across 17 blueprints → `admin_<bp>_<endpoint>` convention. Every `url_for('blueprint.endpoint')` must be rewritten.

### H3. 3-way URL prefix variable split in JavaScript
`scriptRoot` (from `request.script_root`, ~73 refs), `scriptName`/`script_name` (~74 refs), `config.scriptName` (35 fetch calls in tenant_settings.js). All three are semantically identical in Flask and all must become `url_for()`.

### H4. `get_flashed_messages()` in base.html cascades to 54 pages
Plus 4 other templates. Needs session-based flash middleware supporting categories.

### H5. `session.*` references in base.html (10 refs)
Flask auto-injects `session` as Jinja global. Starlette does not. Nav bar rendering breaks entirely.

### H6. Authlib integration library change
`authlib.integrations.flask_client.OAuth` → `authlib.integrations.starlette_client.OAuth`. Different API for `authorize_redirect` and `authorize_access_token`.

### H7. `error.html` variable mismatch
gam.py passes `error=` (string), planned handler passes `exc.to_dict()` (dict), template reads `error_title`/`error_message` — neither matches.

### H8. Dynamic adapter routes registered at runtime
`MockAdServer.register_ui_routes` and `GoogleAdManager.register_ui_routes` call `@app.route()` at startup. No FastAPI equivalent — must convert to static route definitions.

### H9. sync_api double url_prefix
Blueprint declares `/api/v1/sync` but registration overrides to `/api/sync`. Must verify actual production paths.

### H10. `filename` → `path` parameter rename for static
3 `url_for('static', filename=...)` calls must change to `path=` for Starlette.

---

## Key Numbers

| Metric | Count | Source |
|---|---|---|
| Total Flask routes | 197 | FE-6 |
| Total template files | 74 | FE-1 |
| Templates inheriting base.html | 54 | FE-1 |
| `url_for` calls (templates) | 134 | FE-1 |
| `url_for` calls (Python) | 336 | FE-6 |
| Unique Flask endpoint names | 55 | FE-1 |
| `script_root`/`script_name` refs | ~147 | FE-1 (confirmed plan count) |
| `flash()` calls | 366 | FE-5 |
| `redirect()` calls | 338 | FE-5 |
| `fetch()` call sites | ~115 | FE-2 |
| JS scriptRoot usages in templates | 33 (across 16 files) | FE-1 |
| Standalone JS files | 5 | FE-4 |
| Static asset files | 11 | FE-4 |
| OAuth callback URLs | 3 (Google, OIDC, GAM) | FE-3 |
| HTML forms with POST | ~47 | FE-5 |
| Templates using `tojson` | 12 (30+ expressions) | FE-5 |

---

## Documentation Bugs Found

1. **OIDC callback path**: CLAUDE.md + deep-audit say `/admin/auth/oidc/{tenant_id}/callback` — actual is `/admin/auth/oidc/callback` (no tenant_id)
2. **GAM callback path**: CLAUDE.md says `/auth/gam/callback` — actual external path is `/admin/auth/gam/callback`
3. **url_for trailing-slash risk count**: Plan says ~111 — actual exposure is 197 routes × 470 url_for sites (understated)

---

## Pre-existing Bugs Worth Fixing During Migration

1. `session.clear()` before OAuth wipes `login_next_url` — user can't return to originally-requested page after login
2. `require_tenant_access` doesn't check `tenant.is_active` (deep-audit §2.1)
3. `error.html` variable name mismatch — gam.py passes `error=` but template reads `error_title`/`error_message`
4. `getCsrfToken()` defined in products.html but never called — zero fetch calls send CSRF tokens
5. HttpOnly=False on session cookie in production (was for SSE — SSE being deleted)
6. `targeting_browser.html` constructs nonexistent route `/tenant/${tenantId}/login`
7. `tenant_dashboard.html` mixes both `request.script_root` AND `script_name` in same template
8. Ad-hoc toast implementations — 3 separate `showToast`/`showNotification` functions, no shared library

---

## CSRF Strategy Recommendation

Current: NO CSRF enforcement at all (Flask-WTF's CSRFProtect never initialized).

Recommended: **SameSite=Lax cookie + Origin header validation** (Option 2/3 hybrid from FE-2).
- SameSite=Lax prevents cross-site POST (main CSRF vector)
- Origin header check in middleware catches any remaining edge cases
- Effort: 0.5 day (middleware only)
- NO changes needed to ~80 fetch() calls or ~47 HTML forms

Alternative: Double-submit cookie with `X-CSRF-Token` header
- Requires adding token to all ~80 POST/DELETE fetch calls + all ~47 forms
- Effort: 2-3 days + high regression risk
- Only justified if SameSite is insufficient (e.g., some browsers don't support it)

---

## Recommended Action Priority

### Wave 0 (before any template changes)
1. **F1** Fix OIDC/GAM callback path documentation (0.5 hr)
2. **F3** Create `admin_redirect()` helper defaulting to 302 (0.5 day)
3. **F5** Register `tojson` filter with indent support (0.25 day)
4. **F2** Implement `_url_for` safe-lookup wrapper catching NoMatchFound (0.5 day)
5. **H4** Implement session-based FlashMiddleware + register `get_flashed_messages` Jinja global (1 day)
6. **H5** Register `session`, `g.test_mode`, `csrf_token` as Jinja globals via middleware (1 day)
7. **F6** Fix Accept-aware error handler AJAX false-positive (1 day)

### Wave 1-2 (template codemod)
8. **H2** Run two-pass template codemod: 147 script_root→url_for + 130 endpoint name mapping (2-3 days)
9. **H3** Migrate 33 JS-embedded scriptRoot blocks to server-resolved URL maps (3-4 days)
10. **H6** Port Authlib from flask_client to starlette_client (0.5 day)
11. **F4** CSRF strategy implementation (0.5 day for SameSite approach)
12. Rewrite `error.html` for Accept-aware handler context (0.5 day)
13. Add Accept-aware handlers for HTTPException + RequestValidationError (0.5 day)

### Wave 3 (Flask removal)
14. **H1** Mechanical rewrite of 366 flash() calls to new FlashMiddleware (2 days)
15. `git mv templates/ src/admin/templates/` + `git mv static/ src/admin/static/` (0.5 hr)
16. Fix favicon upload path in tenants.py (0.25 hr)
17. Reconcile pre-existing `src/admin/static/` duplicate files (0.1 hr)
18. Convert dynamic adapter routes to static definitions (0.5 day)
19. Build complete route name registry (55 names) (0.5 day)
20. Resolve sync_api double url_prefix (0.25 day)
21. Resolve duplicate adapter route conflict (0.25 day)

### Post-v2.0
22. Add dedicated 404.html, 403.html error pages
23. Consolidate ad-hoc toast implementations
24. Add nginx `location /static/` direct-serve block
25. Migrate favicon storage from filesystem to object storage
26. Evaluate Content-Security-Policy headers

---

## Cross-Audit Interaction Map

| Frontend Finding | Interacts with |
|---|---|
| F2 (base.html cascade) | Every Wave 1-2 template change |
| F3 (302→307 redirect) | All 338 redirect() calls in admin blueprints |
| F4 (CSRF added) | FE-2 (115 fetch calls), FE-5 (47 forms) |
| H3 (scriptRoot 3-way split) | FE-2 (tenant_settings.js 35 fetches), FE-4 (5 standalone JS) |
| H6 (Authlib port) | FE-3 (OAuth callback byte-immutability) |
| H7 (error.html mismatch) | Database audit C1 (statement_timeout crash → error page) |

---

## Audit Methodology

Each of the 6 subagents performed exhaustive file reads (not sampling):
- FE-1: All 74 templates read, every url_for/script_root/Flask-ism counted
- FE-2: All 5 JS files + 77 inline script blocks audited, every fetch() documented
- FE-3: auth.py (1070 lines), oidc.py (260 lines), helpers.py (380 lines) full read, complete login/logout flow traces
- FE-4: All 11 static files inventoried, all 3 nginx configs read, every CSS url() checked
- FE-5: Every flash() call counted (366), every redirect() counted (338), every tojson usage found (30+)
- FE-6: Every @route decorator extracted (197 routes), every blueprint prefix traced, all dual-decorator/collision cases identified
