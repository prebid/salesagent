---
name: convert-tests
lifecycle: layer-5c
description: >
  For Layer 5c of v2.0 migration: converts sync tests to async when admin
  handlers flip to AsyncSession. DO NOT invoke during Layers 0-4 —
  the test suite is sync through L4 and async conversion begins in L5b/L5c.
original_description: >
  Convert sync test files to async for the SQLAlchemy async migration.
  Applies mechanical transforms, converts TestClient to AsyncClient, converts
  factory session binding, and runs converted tests.
args: <test-file-path>
---

# Convert Sync Test File to Async

## Args

`/convert-tests tests/integration/test_accounts.py`

## Entry conditions (HARD GATES — verify ALL before invoking)

This skill must NOT run until every one of the following is true:

1. **L2 merged** — `rg -w flask src/ | wc -l` returns `0`; `flask` is not in `pyproject.toml` runtime deps.
2. **L4 merged** — `SessionDep = Annotated[Session, Depends(get_session)]` (still sync) exists in `src/admin/deps/` and `baseline-sync.json` has been captured at the L4 exit.
3. **L5a spikes green** — Spikes 1, 2, 3, 4, 4.25, 4.5, and 5.5 all pass per `execution-plan.md` L5a exit gate. Spike gate decisions recorded in `spike-decision.md`.
4. **Async factory shim merged** — `AsyncSQLAlchemyModelFactory` exists in `tests/factories/` and the shim validation test from Spike 4.25 is green.
5. **Target file is in the L5c pilot wave** — the 3-router pilot list (see `execution-plan.md` L5c) or a later L5d/L5e wave. Do NOT convert a test file whose production code has not yet flipped to `AsyncSession`.

If any gate fails, STOP. The skill will produce a broken test suite if invoked early.

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
