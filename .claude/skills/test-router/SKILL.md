---
name: test-router
description: >
  Write integration tests for a ported FastAPI router. Uses Starlette TestClient
  (sync), factory-boy for data, compares against golden fixtures,
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
from starlette.testclient import TestClient

from src.app import app  # or however the app is imported in existing tests
from src.core.database.database_session import get_db_session


@pytest.fixture
def client(integration_db):
    """Sync test client with session override."""
    app.dependency_overrides[get_db_session] = lambda: integration_db
    with TestClient(app) as tc:
        yield tc
    app.dependency_overrides.pop(get_db_session, None)  # .pop(), NOT .clear()
```

### Step 3: Write test cases for every route

For each route in the router, write AT MINIMUM:

| Test type | What to assert |
|---|---|
| **Happy path** | Status code matches golden fixture (200 for GET, 303 for POST-redirect-GET). Content-type matches. For HTML: key elements present. For JSON: key schema matches golden fixture. |
| **Route names** | Route names follow `admin_{blueprint}_{endpoint}` convention and `request.url_for()` resolves correctly. |
| **Router config** | Router uses `redirect_slashes=True, include_in_schema=False`. |
| **Auth failure** | Request without session → 401 or redirect to login |
| **Not found** | Invalid tenant_id or entity ID → 404 |
| **Form validation** (POST routes) | Missing required field → re-render form with error, NOT 500 |
| **Golden fixture comparison** | If fixture exists: status_code match, content_type match, header keys superset |

### Step 4: Test data via factories ONLY

```python
# CORRECT: factory-boy (sync)
tenant = TenantFactory.create()
account = AccountFactory.create(tenant_id=tenant.tenant_id)

# WRONG: inline session.add
tenant = Tenant(tenant_id="test", ...)  # NEVER do this
session.add(tenant)                      # NEVER do this in test bodies
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

1. Use `from starlette.testclient import TestClient` — sync client for sync handlers (no event loop conflict)
2. Use factory-boy for ALL test data — NEVER `session.add()` in test bodies
3. Dependency override for sync deps: `app.dependency_overrides[get_db_session] = lambda: session` (plain lambda is fine)
4. Override teardown: `.pop(dep, None)` — NOT `.clear()`
5. Test EVERY route — not just the happy path. Auth failure and not-found are mandatory.
6. `def test_*` — plain sync test functions, NO `@pytest.mark.asyncio`, NO `async def`

## See Also

- `/capture-fixtures` — capture golden fixtures BEFORE porting (run first)
- `/port-blueprint` — port the Flask blueprint to FastAPI
- `/convert-tests` — Layer 5c of v2.0: converts sync tests to async when admin handlers flip to `AsyncSession`. Do NOT invoke during L0-L4.
