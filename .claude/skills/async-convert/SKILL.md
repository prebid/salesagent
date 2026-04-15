---
name: async-convert
lifecycle: layer-5
description: >
  For Layer 5 of v2.0 migration: converts sync modules to async SQLAlchemy
  after Flask removal (L2) and FastAPI-native pattern refinement (L4) are
  merged. DO NOT invoke during Layers 0-4 — admin handlers are sync through L4.
original_description: >
  Convert a sync Python module to async SQLAlchemy. Applies mechanical transforms,
  checks for lazy-load risks, session.merge, inspect(), and bulk_*_mappings.
  Runs module tests after conversion.
args: <file-path>
---

# Sync-to-Async Module Conversion

## Args

`/async-convert src/core/database/repositories/accounts.py`

## Entry conditions (HARD GATES — verify ALL before invoking)

This skill must NOT run until every one of the following is true:

1. **L2 merged** — `rg -w flask src/ | wc -l` returns `0`.
2. **L4 merged** — `SessionDep` exists as a sync alias, DTO boundary is enforced by structural guard, `baseline-sync.json` captured at L4 exit.
3. **L5a spikes green** — Spikes 1 (lazy-load audit), 2 (asyncpg driver compat), 3 (perf baseline — captured at L4 exit, compared at L5 exit), 4 (dual session factory), 4.25 (factory-boy async shim), 4.5 (adapter Path-B wrap), 5.5 (sync-bridge contract). Gate decisions recorded in `spike-decision.md`.
4. **Target file is in the current L5 sub-wave:**
   - L5b: `SessionDep` re-aliased to `AsyncSession` (one commit — not called by this skill).
   - L5c: 3-router async pilot list.
   - L5d1–L5d5: bulk conversions (5d1 sync-bridge landed, 5d2 adapter wrap, 5d3 bulk routers, 5d4 SSE deletion, 5d5 mop-up).
   - L5e: final async sweep.

## Forbidden targets (per Decisions 1 and 9)

This skill MUST NOT be invoked on:

- `src/adapters/**` — adapters stay sync and are called via `await run_in_threadpool(adapter.method, ...)` at the `_impl` caller (Decision 1 Path B). These files use `get_sync_db_session()` via the sync factory — do not flip them to `AsyncSession`.
- `src/services/background_sync_service.py` — runs under the sync-bridge (Decision 9). Stays sync; deletion of `psycopg2-binary` waits until this module is rewritten as a proper async service (Layer 5+ sunset item, not in v2.0 scope for this skill).
- Any module imported by Path-B adapters or by `background_sync_service.py` — check call graph before invoking. The sync-bridge expects these to remain on the sync session.

If the target file matches any pattern above, STOP and report. The skill will break the sync-bridge invariant if invoked incorrectly.

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

1. Both `await session.scalars(stmt)` and `(await session.execute(stmt)).scalars()` are valid on `AsyncSession`. **`await session.scalars(stmt)` is canonical** — it is a real async method on `AsyncSession` since SQLAlchemy 1.4.24 and matches the SQLAlchemy docs' recommended form. Do not "fix" `await session.scalars(...)` to the verbose form; both are correct.
2. Check EVERY relationship access for lazy-load traps — not just the obvious ones
3. Change `Session` type hint to `AsyncSession` — mypy catches callers that need updating
4. Run tests after conversion — do not assume mechanical transforms are correct
5. Never change async fixture scope to `session` or `module` for performance without also changing the engine fixture scope — scope mismatch causes `ScopeMismatch` errors in pytest-asyncio 1.x

## See Also

- `/convert-tests` — convert sync test files to async (different patterns than production code)
- `/port-blueprint` — port Flask blueprints to FastAPI routers
