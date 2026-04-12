---
name: test-router
description: >
  Write integration tests for a ported FastAPI router. Uses httpx.AsyncClient
  (not TestClient), factory-boy for data, compares against golden fixtures,
  and tests success + error paths for every route.
args: <router-name>
---

# Write Integration Tests for Ported Router

## Args

`/test-router accounts` — router name (matches `src/admin/routers/{name}.py`).

> **Run AFTER `/port-blueprint`.** The router must exist before tests can be written.
> Golden fixtures from `/capture-fixtures` are optional but strongly recommended.

## Protocol

### Step 1: Read sources

1. `src/admin/routers/{name}.py` — the ported router (extract every route: path, method, name, params)
2. `tests/migration/fixtures/fingerprints/{name}.json` — golden fixture (if exists)
3. `tests/admin/` — read 1-2 existing admin test files to match conventions
4. `tests/factories/` — identify which factories produce the test data needed (TenantFactory, etc.)

### Step 2: Generate test file

Create `tests/admin/test_router_{name}.py` with this structure:

```python
"""Integration tests for admin {name} router."""
import pytest
from httpx import ASGITransport, AsyncClient

from src.app import app  # or however the app is imported in existing tests


@pytest.fixture
async def client(async_db):
    """Async test client with session override."""
    async def _override():
        yield async_db  # Must be async generator, NOT lambda

    app.dependency_overrides[get_session] = _override
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac
    app.dependency_overrides.pop(get_session, None)  # .pop(), NOT .clear()
```

### Step 3: Write test cases for every route

For each route in the router, write AT MINIMUM:

| Test type | What to assert |
|---|---|
| **Happy path** | Status code matches golden fixture (200 for GET, 302 for POST-redirect-GET). Content-type matches. For HTML: key elements present. For JSON: key schema matches golden fixture. |
| **Auth failure** | Request without session → 401 or redirect to login |
| **Not found** | Invalid tenant_id or entity ID → 404 |
| **Form validation** (POST routes) | Missing required field → re-render form with error, NOT 500 |
| **Golden fixture comparison** | If fixture exists: status_code match, content_type match, header keys superset |

### Step 4: Test data via factories ONLY

```python
# CORRECT: factory-boy
tenant = await TenantFactory.create_async(session=async_db)
account = await AccountFactory.create_async(session=async_db, tenant_id=tenant.tenant_id)

# WRONG: inline session.add
tenant = Tenant(tenant_id="test", ...)  # NEVER do this
async_db.add(tenant)                     # NEVER do this in test bodies
```

### Step 5: Run and validate

```bash
uv run pytest tests/admin/test_router_{name}.py -x -v
make quality
```

### Step 6: Verify port parity (if golden fixture exists)

For each route in the golden fixture, compare:
- Status code: exact match (with exception table for 302→307 intentional changes)
- Content-type: exact match
- JSON key schema: no missing keys (extra keys OK)
- Redirect Location: path component matches

Report any mismatches as potential regressions.

## Hard rules

1. Use `httpx.AsyncClient(transport=ASGITransport(app=app))` — NEVER `TestClient(app)` (event loop conflict)
2. Use factory-boy for ALL test data — NEVER `session.add()` in test bodies
3. Dependency override must be `async def` generator that `yield`s — NOT `lambda: session`
4. Override teardown: `.pop(dep, None)` — NOT `.clear()`
5. Test EVERY route — not just the happy path. Auth failure and not-found are mandatory.
6. `async def test_*` with `@pytest.mark.asyncio` (or `asyncio_mode = auto`)

## See Also

- `/capture-fixtures` — capture golden fixtures BEFORE porting (run first)
- `/port-blueprint` — port the Flask blueprint to FastAPI
- `/convert-tests` — convert existing sync tests to async (Phase 4c)
