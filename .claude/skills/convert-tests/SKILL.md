---
name: convert-tests
lifecycle: deferred-v2.1
description: >
  DEFERRED TO v2.1 — async test conversion is not in v2.0 scope.
  v2.0 uses sync test patterns. This skill is preserved for v2.1 async migration.
original_description: >
  Convert sync test files to async for the SQLAlchemy async migration (Phase 4c).
  Applies mechanical transforms, converts TestClient to AsyncClient, converts
  factory session binding, and runs converted tests.
args: <test-file-path>
---

# Convert Sync Test File to Async

## Args

`/convert-tests tests/integration/test_accounts.py`

## Protocol

### Step 1: Read the test file completely

Understand every fixture, every test function, every helper. Note:
- Which fixtures use `get_db_session()` or `integration_db`
- Which use `TestClient(app)` vs `httpx.AsyncClient`
- Which use factory-boy `create_sync()` patterns
- Which have `with` context managers for DB sessions

### Step 2: Apply mechanical transforms

| Sync pattern | Async replacement |
|---|---|
| `def test_something(` | `async def test_something(` |
| `with get_db_session() as session:` | `async with get_db_session() as session:` |
| `session.execute(stmt)` | `await session.execute(stmt)` |
| `session.scalars(stmt)` | `await session.scalars(stmt)` — native async method on `AsyncSession` (since SQLAlchemy 1.4.24) |
| `session.commit()` | `await session.commit()` |
| `session.rollback()` | `await session.rollback()` |
| `session.refresh(obj)` | `await session.refresh(obj)` |
| `session.delete(obj)` | `await session.delete(obj)` |
| `session.merge(obj)` | `await session.merge(obj)` |
| `session.get(Model, pk)` | `await session.get(Model, pk)` |
| `TestClient(app)` | `httpx.AsyncClient(transport=ASGITransport(app=app))` |
| `client.get("/path")` | `await client.get("/path")` (inside `async with` block) |
| `Session` type hint | `AsyncSession` |

### Step 3: Convert fixtures

```python
# BEFORE (sync)
@pytest.fixture
def db_session(integration_db):
    with get_db_session() as session:
        yield session

# AFTER (async)
@pytest_asyncio.fixture
async def db_session(integration_db):
    async with get_db_session() as session:
        yield session
```

**Client fixture:**
```python
# BEFORE (sync)
@pytest.fixture
def client(app):
    return TestClient(app)

# AFTER (async)
@pytest_asyncio.fixture
async def client(async_db):
    async def _override():
        yield async_db
    app.dependency_overrides[get_session] = _override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.pop(get_session, None)
```

### Step 4: Convert factory calls

```python
# BEFORE (sync factory)
tenant = TenantFactory.create_sync()

# AFTER (async factory — uses AsyncSQLAlchemyModelFactory shim)
tenant = await TenantFactory.create_async(session=async_db)
```

### Step 5: Check for remaining sync patterns

```bash
grep -n "TestClient\|\.create_sync\|with get_db_session\|session\.query\|session\.scalars" {file}
```

Every match should be zero. If any remain, they need manual conversion.

### Step 6: Run converted tests

```bash
uv run pytest {file} -x -v
make quality
```

**If tests fail:**
- `MissingGreenlet`: A lazy-load is being triggered — add `selectinload()` to the query or convert the access pattern
- `RuntimeError: Event loop is closed`: A fixture is using the wrong scope or `TestClient` wasn't fully converted
- `TypeError: object async_generator can't be used in 'await' expression`: A dependency override is a lambda instead of an async generator
- `ScopeMismatch`: Fixture scopes are misaligned — all async fixtures sharing a session must have the same scope

## Hard rules

1. `await session.scalars(stmt)` is the preferred pattern — it's a native `AsyncSession` method. The verbose `(await session.execute(stmt)).scalars()` also works.
2. `TestClient` → `AsyncClient(transport=ASGITransport(app=app))` — never leave `TestClient` in async tests
3. Dependency overrides: async generator, not lambda. Teardown: `.pop()`, not `.clear()`
4. Do NOT convert BDD step functions to async — they use a sync `asyncio.run()` bridge by design
5. Run the converted file after EVERY conversion — do not batch-convert without testing

## See Also

- `/async-convert` — convert production modules (different patterns than tests)
- `/test-router` — write NEW router tests (this skill converts EXISTING tests)
