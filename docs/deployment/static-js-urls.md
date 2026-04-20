# Static JavaScript URL Strategy (v2.0)

Status: active (Flask→FastAPI v2.0 migration)
Decision: ratified at L0 per `.claude/notes/flask-to-fastapi/L0-implementation-plan-v2.md` §7.5 "RATIFIED WITH REFINEMENT"
Enforcement layer: per-router at L1c and L1d (NOT at L0)

## Summary

The v2.0 admin-UI migration ends the `scriptRoot` / `request.script_root`
URL-prefix convention. There are 37 JavaScript call sites that currently
read `{{ request.script_root }}` out of a Jinja template and concatenate
it with an API path (e.g., `scriptRoot + "/api/inventory"`). Every one
of them moves to a template-emitted `data-*` attribute whose value is
produced by `url_for(...)` against a named FastAPI route.

This document captures the target pattern and the migration boundary so
that new JavaScript written **after L0** always uses `url_for(...)` and
never reintroduces `scriptRoot`.

## Why the old pattern is wrong under FastAPI

Starlette's `include_router(prefix="/admin")` does **not** populate
`scope["root_path"]` the way Flask's blueprint mounting populated
`request.script_root`. A template that reads
`{{ request.script_root }}` under FastAPI gets an **empty string** in
most deployments and silently produces URLs rooted at `/` instead of
`/admin/...` or `/tenant/{tenant_id}/...`. The result looks fine in
local dev (where `/admin` is the URL prefix by convention) and breaks
silently under the canonical multi-tenant mount at
`/tenant/{tenant_id}/...`.

The fix is to use FastAPI-native URL generation: every admin route has
`name="admin_<blueprint>_<endpoint>"`, and every URL the server emits
to a template goes through `{{ url_for('admin_...', **params) }}`.

## Migration boundary

| Layer | Work |
|---|---|
| L0 | Document the target pattern (this file); DO NOT sweep JS. The `rg 'scriptRoot\|script_root' static/js/` = 0 check is EXPLICITLY RELAXED at L0. |
| L1a-L1b | Middleware + OAuth — no JS surface. |
| L1c-L1d | Router-by-router: each HTML router PR deletes the `scriptRoot` JS in the templates it owns and replaces with `data-*`-attribute + `url_for` pattern below. |
| L2 | Flask removal; by this point 0 `scriptRoot` call sites remain. Enforcement guard lands (AST scan of `static/js/` + `templates/*.html`). |

**New JavaScript written at any layer from L0 onward must use the
`url_for`/`data-*` pattern below. Never introduce a new `scriptRoot`
reference.**

## Target pattern

Instead of:

```html
<!-- BEFORE: template emits scriptRoot -->
<script>
  const scriptRoot = '{{ request.script_root }}' || '';
  const apiUrl = scriptRoot + '/api/inventory';
  fetch(apiUrl).then(...);
</script>
```

Emit a `data-*` attribute whose value is pre-computed with `url_for`:

```html
<!-- AFTER: template emits a data-* URL; JS reads it. -->
<body data-inventory-api-url="{{ url_for('admin_inventory_api_list', tenant_id=tenant.id) }}">
  <script src="{{ url_for('static', path='js/inventory.js') }}"></script>
</body>
```

```js
// static/js/inventory.js
const apiUrl = document.body.dataset.inventoryApiUrl;
fetch(apiUrl).then(...);
```

Why `data-*` attributes, not a top-level `window.ADMIN_URLS = {...}`
config object: `data-*` attributes are scoped per page, so a page that
doesn't need a given API doesn't have to emit it. A per-page `window.`
object forces every page to declare every URL it might need, and the
template author has to know every JS file's URL dependencies.

## Rules for new JS in L0-L1d

1. No `scriptRoot`, no `script_root`, no `request.script_root`, no
   hardcoded `/admin/...` or `/api/...` strings in JavaScript files.
2. Every URL the JS needs comes from a `data-*` attribute on a DOM
   element (typically `<body>` or the specific form/button that drives
   the fetch).
3. Every `data-*` attribute value is produced by a
   `{{ url_for('admin_...', **params) }}` in the Jinja template.
4. Every admin FastAPI route has an explicit `name="admin_<...>"` so
   `url_for` can reverse-resolve it. Routes without `name=` produce
   `NoMatchFound` at render time — caught by
   `tests/unit/test_templates_url_for_resolves.py`.
5. Static assets (JS, CSS, images) are referenced via
   `{{ url_for('static', path='...') }}`. The static mount is
   `name="static"` on the outer `app`.

## Enforcement

* L0 (this layer): doc-only. **No guard**, per §7.5 RATIFIED.
* L1c-L1d: each router PR clears the `scriptRoot` references in the
  templates it owns and the fetch sites in its JS files.
* L2: structural guard lands (AST scan of `static/js/**/*.js` for the
  string `scriptRoot` and `script_root`; AST scan of `templates/*.html`
  for `request.script_root`). Empty allowlist — post-L1d there should
  be zero hits. Guard test name:
  `tests/unit/architecture/test_architecture_no_script_root.py` (L2).

## Cross-references

* Invariant #1 in `.claude/notes/flask-to-fastapi/CLAUDE.md`
* §1 blocker 1 in `.claude/notes/flask-to-fastapi/flask-to-fastapi-deep-audit.md`
* `async-audit/frontend-deep-audit.md` — JS + fetch endpoint audit
