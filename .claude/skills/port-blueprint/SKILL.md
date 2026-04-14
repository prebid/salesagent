---
name: port-blueprint
lifecycle: migration
description: >
  Port a Flask blueprint to a FastAPI router following all migration patterns.
  Generates sync handlers with get_db_session() in handler body, named routes,
  render() wrapper, form_error_response(), and List[str] = Form() for multi-value
  fields. Runs make quality as validation.
args: <blueprint-name>
---

# Port Flask Blueprint to FastAPI Router

## Args

`/port-blueprint accounts` — blueprint name without `.py` suffix.

## Protocol

### Step 0: Prerequisite check

Verify these foundation modules exist before proceeding. If ANY are missing, STOP — Phase 0 must be completed first (see `execution-plan.md` Phase 0).

```bash
test -f src/admin/templating.py && test -f src/admin/flash.py && test -f src/admin/deps/__init__.py && test -d src/admin/routers && echo "OK" || echo "STOP: Phase 0 foundation modules not yet created"
```

### Step 1: Read sources (do NOT skip any)

1. `src/admin/blueprints/{name}.py` — full Flask source
2. `.claude/notes/flask-to-fastapi/execution-plan.md` — find the phase containing this blueprint
3. `.claude/notes/flask-to-fastapi/CLAUDE.md` — 6 critical invariants (especially #1: sync def handlers)
4. `tests/migration/fixtures/fingerprints/{name}.json` — golden fixtures (if exists)
5. `.claude/notes/flask-to-fastapi/flask-to-fastapi-worked-examples.md` — find matching worked example (if any)

### Step 2: Extract route inventory

For each `@{bp}.route(...)` in the Flask source, record:
- HTTP method(s) and URL pattern (with parameters)
- Function name
- Form fields — **especially `request.form.getlist()` calls** (these become `List[str] = Form()`)
- Template rendered, flash messages, redirects (target endpoint names)

### Step 3: Generate router

Create `src/admin/routers/{name}.py` applying ALL rules below:

| Rule | Implementation | Guard that catches violations |
|------|---------------|-------------------------------|
| Named routes | `@router.get("/path", name="admin_{name}_{endpoint}")` on EVERY route | `test_architecture_admin_routes_named.py` |
| Sync handlers | `def` (NOT `async def`) with `get_db_session()`, repository Deps | Critical invariant #1: scoped_session event-loop bug |
| Multi-value form | `field: List[str] = Form(default=[])` not bare `Form()` | `test_architecture_form_getlist_parity.py` |
| DTO in handler | `dto = SomeDTO.from_orm(orm_obj)` in handler, NOT repo | Repo returns ORM objects only |
| render() wrapper | `return render(request, "template.html", ctx)` | Never `Jinja2Templates.TemplateResponse` directly |
| form_error_response() | Validation errors use shared helper | DRY invariant / duplication hook |
| Router config | `APIRouter(redirect_slashes=True, include_in_schema=False)` | `test_trailing_slash_tolerance.py` |
| flash() | `from src.admin.flash import flash` (not Flask flash) | `test_architecture_no_flask_imports.py` |
| url_for in redirects | `RedirectResponse(request.url_for("admin_{name}_{target}"), status_code=303)` | 303 is the POST-redirect-GET spec-correct code |

**Handler template (sync `def` — NOT `async def`):**

Repositories are instantiated INSIDE the handler's own `with get_db_session() as session:` block. No `Depends()` for DB sessions — handler owns the session lifecycle.

```python
@router.get("/tenant/{tenant_id}/{name}", name="admin_{name}_list")
def list_{name}(
    request: Request,
    tenant_id: str,
    tenant_context: dict = Depends(require_tenant_access),
    status: Annotated[str | None, Query()] = None,
):
    with get_db_session() as session:
        repo = {Name}Repository(session)
        items = repo.list_all(tenant_context["tenant_id"], status=status)
    return render(request, "{name}_list.html", {"items": items, "tenant_context": tenant_context})


@router.post("/tenant/{tenant_id}/{name}/create", name="admin_{name}_create")
def create_{name}(
    request: Request,
    tenant_id: str,
    tenant_context: dict = Depends(require_tenant_access),
    name_field: str = Form(...),
):
    with get_db_session() as session:
        repo = {Name}Repository(session)
        repo.create(tenant_context["tenant_id"], name=name_field)
        session.commit()
    flash(request, "{Name} created.", "success")
    return RedirectResponse(request.url_for("admin_{name}_list", tenant_id=tenant_id), status_code=303)
```

> **Critical invariant #1:** Admin handlers MUST use sync `def`, NOT `async def`.
> SQLAlchemy's `scoped_session` has an event-loop bug with async handlers.
> No `await` on repository calls. No `SessionDep`. No `Depends()` for DB sessions.
> Handler owns the session lifecycle via `with get_db_session() as session:`.

### Step 4: Validate

```bash
make quality
```

If golden fixture exists:
```bash
uv run pytest tests/migration/test_response_fingerprints.py -k {name} -x
```

### Step 5: Produce diff for review

```bash
git diff --stat
git diff src/admin/routers/{name}.py
```

## Hard rules (a less-advanced agent CANNOT skip these)

1. Read the Flask source completely — every route, every form field
2. `name=` parameter on EVERY route decorator — no exceptions
3. `List[str] = Form(default=[])` for any field that was `request.form.getlist()`
4. DTO conversion in the handler, not the repository (repos return ORM objects)
5. Run `make quality` — it catches missing route names, Flask imports, structural violations
6. `redirect_slashes=True, include_in_schema=False` on the APIRouter constructor
7. For JSON-only routes (no template): use `JSONResponse(content=data)` instead of `render()`. Some blueprints mix HTML and JSON routes — handle each accordingly
8. Dependency overrides for sync deps use plain lambdas: `app.dependency_overrides[get_db_session] = lambda: session`
9. Override teardown: use `app.dependency_overrides.pop(dep, None)`, NOT `.clear()` — clearing wipes higher-scope overrides

## See Also

- `/capture-fixtures` — capture golden response fixtures BEFORE porting (must run first)
- `/write-guard` — create structural guards that enforce these patterns
- `/test-router` — write integration tests AFTER porting
- `/async-convert` — DEFERRED TO v2.1 (v2.0 uses sync handlers)
