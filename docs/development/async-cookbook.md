# Async Cookbook

**Status:** SKELETON — populated at L5a spike 1. Reviewed/refined at each L5d sub-PR.

---

## Purpose

This document is the canonical catalogue of the roughly 68 SQLAlchemy relationship access sites in the codebase and the canonical async fix for each. Agents writing or reviewing L5b through L5e conversion PRs should look up every relationship accessor against this cookbook before pushing a change — the "correct" fix is pattern-specific, and picking the wrong one (joinedload where selectinload is right, or vice versa) produces either N+1 queries or cartesian blow-up. The nine patterns below are sourced from `async-audit/agent-b-risk-matrix.md`.

---

## The 9 lazy-load patterns

Each subsection below is a stub to be filled at L5a spike 1. Source: `.claude/notes/flask-to-fastapi/async-audit/agent-b-risk-matrix.md`.

### Pattern 1: Simple forward reference (`obj.parent`)

Fix: `selectinload(Model.parent)` at the query site. [To be populated at L5a]

### Pattern 2: Collection accessor (`obj.children`)

Fix: `selectinload(Model.children)` at the query site; avoid `joinedload` on collections. [To be populated at L5a]

### Pattern 3: Nested chain (`obj.a.b.c`)

Fix: chained `selectinload(Model.a).selectinload(A.b).selectinload(B.c)`. [To be populated at L5a]

### Pattern 4: Conditional access (`obj.rel if obj.flag else None`)

Fix: eager-load unconditionally; the branch cost is negligible vs. lazy IO. [To be populated at L5a]

### Pattern 5: Loop over collection with per-row accessor

Fix: `selectinload(Model.children).selectinload(Child.rel)` — N+1 trap. [To be populated at L5a]

### Pattern 6: Access inside `model_dump` / serializer

Fix: eager-load at the query site feeding the response; never inside the serializer. [To be populated at L5a]

### Pattern 7: Access inside Jinja template rendering

Fix: eager-load before `render()`; templates are sync and cannot suspend. [To be populated at L5a]

### Pattern 8: Post-commit access (stale / expired instance)

Fix: `await session.refresh(instance, attribute_names=[...])` or `expire_on_commit=False` engine setting. [To be populated at L5a]

### Pattern 9: Adapter path sync accessor

Fix: keep sync; wrap via `run_in_threadpool` per Decision 1 Path B. [To be populated at L5a]

---

## Adapter-path threadpool wrap

Adapter implementations (GAM, Mock, etc.) remain synchronous under the Decision 1 Path B policy — their SDKs are sync, and converting them provides no benefit. Where an async handler needs to call an adapter, wrap the call to offload it from the event loop:

```python
from starlette.concurrency import run_in_threadpool

async def some_handler(...):
    # Adapter is sync; its DB helper opens a fresh sync Session internally.
    result = await run_in_threadpool(adapter.create_line_item, request)
    return result
```

The adapter's internal sync DB helper must open a fresh `Session` via the sync `get_db_session()` context manager — it must not receive an `AsyncSession` across the threadpool boundary. See Decision 1 Path B in the migration mission briefing for the full rationale.

---

## Factory-boy async shim

Per Decision 3 / Spike 4.25 / `foundation-modules.md §11.13.1(D)`, factory-boy does not natively support `AsyncSession`. The shim overrides `_save` (and `_create`) to await the async persistence path while keeping the synchronous factory call surface that test code relies on.

Stub example (real implementation lands at Spike 4.25):

```python
class AsyncSQLAlchemyModelFactory(factory.alchemy.SQLAlchemyModelFactory):
    @classmethod
    def _save(cls, model_class, session, args, kwargs):
        # Bridge the sync factory call to async session.add + flush.
        # See foundation-modules.md §11.13.1(D) for the full shim.
        ...
```

---

## Session-scoping rules

These five rules are non-negotiable during async conversion. Violations produce latent bugs that only surface under load.

1. **Never hold an `AsyncSession` across event-loop boundaries.** No passing sessions into `run_in_threadpool`, no stashing on module globals, no yielding across `await` boundaries that cross handler scope.
2. **One `async with get_db_session()` per logical transaction.** A request handler opens exactly one session for its transactional unit of work. Nested sessions are a code smell.
3. **No `session.commit()` inside repository methods.** Repositories stage writes; the commit happens at the Unit-of-Work or handler boundary. This keeps transactional semantics where the business logic lives.
4. **Background tasks open a fresh session per checkpoint.** Long-running work (GAM sync, report generation) follows the Decision D3 pattern: one session per GAM page / per report chunk, committed and closed before the next checkpoint. No long-lived sessions.
5. **`expire_on_commit=False` is set engine-wide.** Don't fight it by calling `session.expire(instance)` manually. If an instance is stale after commit, use `session.refresh()` explicitly — not the global expiration behaviour.

---

## References

- `.claude/notes/flask-to-fastapi/async-audit/agent-b-risk-matrix.md` — 9-pattern source
- `.claude/notes/flask-to-fastapi/async-audit/database-deep-audit.md` — pre-L5 critical blockers
- `.claude/notes/flask-to-fastapi/foundation-modules.md` §11.13.1(D) — factory-boy async shim
- `.claude/notes/flask-to-fastapi/implementation-checklist.md` §L5b entry preflight H4 — pool sizing assertion
- `docs/development/async-debugging.md` — the companion runbook for incident response
