# Flask → FastAPI v2.0.0 Migration — Mission Briefing

**Mission:** Migrate `src/admin/` (Flask blueprints + Jinja templates + session auth) to FastAPI without breaking the AdCP MCP/REST surface, OAuth callbacks, or the ~147 template refs that depend on `request.script_root`.

**Branch:** `feat/v2.0.0-flask-to-fastapi` — four waves, one PR per wave, merged to main.

---

## Read me first

This file is the **entry point** for any Claude Code session or engineer touching this migration. The companion docs are large (1k–3k lines each); this file is the map, not the territory. If you read nothing else, read the **Critical Invariants** section below — those are the six things that are easiest to forget and most destructive to miss.

The **source of truth** for "am I ready to ship Wave N?" is `implementation-checklist.md`. Everything else is context.

---

## Critical Invariants (the 6 deep-audit blockers)

These were surfaced by the 2nd/3rd-order audit. Every one of them has shipped-breaking potential. Do not touch admin code without understanding all six.

1. **`script_root` template breakage — use `url_for` everywhere (greenfield).** Starlette's `include_router(prefix="/admin")` does NOT populate `scope["root_path"]` the way Flask's blueprint mounting populated `request.script_root`. ~147 template references would break silently. **Fix:** every admin route has `name="admin_<blueprint>_<endpoint>"` on its decorator; `StaticFiles(..., name="static")` is mounted on the outer app; every URL in every template uses `{{ url_for('admin_...', **params) }}` or `{{ url_for('static', path='/...') }}`. NO `admin_prefix`/`static_prefix`/`script_root`/`script_name` Jinja globals exist — these are strictly forbidden and guarded by `test_templates_no_hardcoded_admin_paths.py`. Missing route names raise `NoMatchFound` at render time; `test_templates_url_for_resolves.py` catches this at CI time. See `flask-to-fastapi-deep-audit.md` §1 (blocker 1).

2. **Trailing slashes.** Flask's `strict_slashes=False` accepts both `/foo` and `/foo/`; Starlette does not by default. ~111 `url_for` call sites at risk. **Fix:** every admin router constructed as `APIRouter(redirect_slashes=True, include_in_schema=False)`. See `flask-to-fastapi-deep-audit.md` §1 (blocker 2).

3. **`@app.exception_handler(AdCPError)` HTML regression.** Admin user clicks a button, the handler returns a JSON blob to the browser, user sees raw JSON. **Fix:** Accept-aware handler — render `templates/error.html` when `Accept: text/html` and path starts with `/admin/`; JSON otherwise. This is intentionally different from the JSON-only handler currently at `src/app.py:82-88`. See `flask-to-fastapi-deep-audit.md` §1 (blocker 3).

4. **Async event-loop session interleaving.** `scoped_session` scopes on `threading.get_ident()`. If admin handlers are `async def`, concurrent requests share the event-loop thread → same session identity → transaction interleaving and cross-request data corruption. **Fix:** admin handlers default to **sync `def`**. Async is reserved for OAuth callbacks, SSE generators, and outbound `httpx` clients. See `flask-to-fastapi-deep-audit.md` §1 (blocker 4).

5. **Middleware ordering: Approximated BEFORE CSRF.** Counterintuitive but correct. If CSRF fires first, an external-domain POST user fails CSRF validation (403) before the Approximated redirect can fire (should be 307). Also switch the redirect from 302 → 307 to preserve the POST body. See `flask-to-fastapi-deep-audit.md` §1 (blocker 5).

6. **OAuth redirect URI byte-immutability.** The paths `/admin/auth/google/callback`, `/admin/auth/oidc/{tenant_id}/callback`, and `/auth/gam/callback` are registered in Google Cloud Console and per-tenant OIDC provider configs. Any path change — including trailing slash, case, or prefix drift — yields `redirect_uri_mismatch` and login is dead. See `flask-to-fastapi-deep-audit.md` §1 (blocker 6).

---

## Recommended reading order (fresh reader, ~2 hours)

1. **This file** — you are here. Mission, blockers, map.
2. **`flask-to-fastapi-migration.md` §1–§2.8** — overall context, Phase 1 vs Phase 2 framing, AdCP boundary verification, deep-audit summary. Skim the rest.
3. **`flask-to-fastapi-deep-audit.md` §1–§2** — read the 6 blockers and the risk register in full detail. This is the single most important read after the overview.
4. **`implementation-checklist.md`** — know what the per-wave acceptance criteria actually are. This is the "am I ready?" source of truth.
5. **`flask-to-fastapi-adcp-safety.md`** — confirm the AdCP boundary is clear; note the 8 first-order action items.
6. **`flask-to-fastapi-foundation-modules.md`** — reference only. Read the module you are about to implement; do not read end-to-end.
7. **`flask-to-fastapi-worked-examples.md`** — reference only. Read the example that matches the blueprint you are translating.
8. **`flask-to-fastapi-execution-details.md`** — reference only. Read the wave you are currently shipping.

---

## File index

| File | When to read | Detail level |
|---|---|---|
| `CLAUDE.md` (this file) | First, always | Entry point / map |
| `flask-to-fastapi-migration.md` | Context pass + before any wave | Overview, 1878 lines |
| `flask-to-fastapi-deep-audit.md` | **Before writing any admin code** | 6 blockers + 20 risks, 787 lines |
| `flask-to-fastapi-adcp-safety.md` | Before touching MCP/REST surface | 1st-order audit, 412 lines |
| `flask-to-fastapi-foundation-modules.md` | When implementing a foundation module | Full code + tests, 2507 lines |
| `flask-to-fastapi-worked-examples.md` | When translating a specific blueprint | 5 worked examples, 2790 lines |
| `flask-to-fastapi-execution-details.md` | When starting / shipping a wave | Per-wave acceptance + rollback, 1142 lines |
| `implementation-checklist.md` | **Before opening a PR** (source of truth) | Consolidated ready-to-ship checklist |

---

## Migration conventions that differ from the rest of the codebase

These are the places where "copy what the rest of the repo does" is **wrong**. Admin is different.

- **Admin handlers default to sync `def`.** The rest of the codebase (e.g. `src/routes/api_v1.py`) uses `async def` freely. Admin does not, because of blocker 4 (scoped_session + event loop). Async is allowed only for OAuth callbacks, SSE handlers, and outbound `httpx`.
- **Middleware order: Approximated BEFORE CSRF.** Counterintuitive relative to standard stacks where CSRF sits near the outside. Here, Approximated's external-domain redirect must fire before CSRF sees the form body. See blocker 5.
- **Templates use `{{ url_for('name', **params) }}` exclusively** — for admin routes AND static assets. No prefix variables, no Jinja globals holding URL strings, no `script_root`, no `admin_prefix`, no `static_prefix`. Every admin route has `name="admin_<blueprint>_<endpoint>"`; the static mount is `name="static"`. This is the FastAPI canonical pattern from the official docs, verified in `Jinja2Templates._setup_env_defaults` at `starlette/templating.py:118-129` (auto-registers `url_for` as a Jinja global that calls `request.url_for(...)` via `@pass_context`). `NoMatchFound` at render time on a missing name is caught pre-merge by `test_templates_url_for_resolves.py`.
- **`AdCPError` handler branches on `Accept`.** For admin HTML browser users, render `templates/error.html`. For JSON API callers, return JSON. Different from the plain JSON-only handler at `src/app.py:82-88` — do not copy that one.
- **Sync admin handlers wrap DB access directly in `with get_db_session():`**, not via `run_in_threadpool`. FastAPI's threadpool handles the offload automatically when the handler is not `async def`. Adding `run_in_threadpool` on top double-offloads and breaks session scoping.
- **`FLASK_SECRET_KEY` is dual-read alongside `SESSION_SECRET`** during v2.0 for dev ergonomics. It is hard-removed in v2.1. Do not rip it out in v2.0 — you will break every dev's local `.env`.

---

## First-order audit action items (quick reference)

Catalogued in `flask-to-fastapi-adcp-safety.md`; listed here so they are not lost:

- `tenant_management_api.py` route count in the main plan is **stale (19 → 6)** — re-verify before scoping.
- `gam_reporting_api.py` is **session-authed → Category 1**, not Category 2.
- `schemas.py` serves external AdCP JSON-Schema validators — preserve URLs **byte-for-byte**.
- `creatives.py` / `operations.py` construct outbound AdCP webhooks — **do not** use AdCP types as `response_model=`.
- Every admin router: `include_in_schema=False`.
- `/_internal/` must be added to the CSRF exempt list.
- Three new structural guards to add: `csrf_exempt_covers_adcp`, `approximated_path_gated`, `admin_excluded_from_openapi`.

---

## Branch and folder cleanup intent

- **Branch:** `feat/v2.0.0-flask-to-fastapi`. All migration work lives here.
- **Merge cadence:** one PR per wave, four waves total, merged to `main` as each wave stabilizes.
- **Post-migration cleanup:** `.claude/notes/flask-to-fastapi/` is a planning-phase artifact. After v2.0.0 ships and stabilizes (~2 releases later), archive or delete this folder. Anything worth keeping long-term gets promoted to `docs/` or `CLAUDE.md` at the repo root.

---

## v2.1 deferred items (do NOT pull forward)

These are intentionally out of scope for v2.0.0. If you find yourself wanting to do them during the migration, stop and file an issue instead.

- Async SQLAlchemy (requires blocker 4's sync default to be lifted first)
- Drop nginx (cannot happen until admin is fully on FastAPI and external-domain handling is battle-tested)
- REST routes ratchet to `Annotated[...]` form
- `Apx-Incoming-Host` IP allowlist (currently trusted on header alone)
- `require_tenant_access` to check `is_active`
- `/_internal/` auth hardening (currently network-gated only)
- Hard-remove `FLASK_SECRET_KEY` dual-read
