---
name: async-convert
lifecycle: migration
description: >
  Convert a sync Python module to async SQLAlchemy. Applies mechanical transforms,
  checks for lazy-load risks, session.merge, inspect(), and bulk_*_mappings.
  Runs module tests after conversion.
args: <file-path>
---

# Sync-to-Async Module Conversion

## Args

`/async-convert src/core/database/repositories/accounts.py`

## Protocol

### Step 1: Read the source

Read the full file. Classify every function as: pure logic (no DB), DB read, DB write, mixed.

### Step 2: Apply mechanical transforms

| Sync pattern | Async replacement |
|---|---|
| `def method(self, ...)` | `async def method(self, ...)` |
| `session.execute(stmt)` | `await session.execute(stmt)` |
| `session.scalars(stmt)` | `await session.scalars(stmt)` — this IS a native async method on `AsyncSession` (since SQLAlchemy 1.4.24). The verbose `(await session.execute(stmt)).scalars()` also works but is not preferred. |
| `session.scalar(stmt)` | `await session.scalar(stmt)` — same: native async method on `AsyncSession`. |
| `session.get(Model, pk)` | `await session.get(Model, pk)` |
| `session.merge(obj)` | `await session.merge(obj)` |
| `session.flush()` | `await session.flush()` |
| `session.refresh(obj)` | `await session.refresh(obj)` |
| `session.commit()` | `await session.commit()` |
| `session.rollback()` | `await session.rollback()` |
| `session.close()` | `await session.close()` |
| `session.delete(obj)` | `await session.delete(obj)` |
| `with session.begin():` | `async with session.begin():` |
| `Session` type hint | `AsyncSession` |

Import changes:
```python
# Before
from sqlalchemy.orm import Session
# After
from sqlalchemy.ext.asyncio import AsyncSession
```

### Step 3: Check for risks (report ALL findings)

1. **Lazy-load risks**: any `self.relationship_name` or `obj.relationship_name` access without prior `selectinload()`/`joinedload()` in the query — under async with `lazy="raise"`, these raise `MissingGreenlet`
2. **`session.merge()`**: verify it has `await` — missing await returns a coroutine object silently
3. **`inspect()`**: `inspect(instance)` can trigger mapper lazy loads under async
4. **`bulk_*_mappings`**: `bulk_save_objects`, `bulk_insert_mappings`, `bulk_update_mappings` — do NOT exist on `AsyncSession`, must rewrite to Core `insert().values()` or use `session.run_sync()`
5. **`@property` on ORM models**: any property that accesses a relationship is a lazy-load trap
6. **`__repr__`/`__str__`**: if they access relationships, they explode when logged

### Step 4: Run tests

```bash
uv run pytest tests/ -k "{module_name}" -x -v
make quality
```

**If `make quality` fails:**
- **mypy errors from callers needing `await`:** Fix each caller to add `await`
- **`MissingGreenlet` in tests:** Add `selectinload()` or `joinedload()` to the originating query in the repository
- **Test failures from sync fixtures calling now-async functions:** Convert the test to `async def` with `@pytest.mark.asyncio` (or use `/convert-tests` skill)

### Step 5: Produce diff

```bash
git diff src/
```

## Hard rules

1. The `session.scalars()` transform is `(await session.execute(stmt)).scalars()` — NOT `await session.scalars()` (this is the #1 mistake)
2. Check EVERY relationship access for lazy-load traps — not just the obvious ones
3. Change `Session` type hint to `AsyncSession` — mypy catches callers that need updating
4. Run tests after conversion — do not assume mechanical transforms are correct
5. Never change async fixture scope to `session` or `module` for performance without also changing the engine fixture scope — scope mismatch causes `ScopeMismatch` errors in pytest-asyncio 1.x

## See Also

- `/convert-tests` — convert sync test files to async (different patterns than production code)
- `/port-blueprint` — port Flask blueprints to FastAPI routers
