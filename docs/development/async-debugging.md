# Async Debugging Runbook

**Status:** SKELETON — fleshed out at L5a spike 1. Safe to reference before that; sections marked "[L5a]" are placeholders.

This runbook is the first-stop triage guide when async-related errors surface during or after L5 (the async SQLAlchemy conversion). Its audience is the incident commander driving recovery and any fresh-context agent parachuting in to help diagnose from logs.

---

## When to read this doc

- `MissingGreenlet` exception in a production log line
- An async request path is unexpectedly slow (p95 climbing, no obvious DB query culprit)
- DB pool saturation alert fires (`QueuePool limit of size N overflow M reached`)

If none of the above symptoms match, this doc is probably not the right starting point — check `docs/development/troubleshooting.md` first.

---

## Fast triage (5 minutes)

Goal: get from "something is wrong" to "I know which L5 sub-PR to suspect" in under five minutes.

1. **Capture health snapshots.** Hit `/health/db` and `/health/pool` and save the JSON. These endpoints expose engine/pool state and are the single cheapest source of ground truth. If either endpoint is itself hanging, skip to step 4 — the pool is saturated and the app can't self-report.
2. **Grep the stack trace for `greenlet_spawn`.** That frame is the canonical marker of a sync-context lazy load on an `AsyncSession`. If present, this is a cookbook problem (see below), not a novel bug.
3. **Identify the code path.** Note (a) the handler/route name, (b) the repository method on the stack, and (c) the relationship being accessed at the leaf frame. All three are needed to find the correct cookbook entry.
4. **Match against the lazy-load cookbook.** Cross-reference the pattern to `async-cookbook.md` § "The 9 lazy-load patterns". If the pattern is listed, the fix is mechanical and documented. If not, escalate — a novel lazy-load pattern is a finding worth recording against `async-audit/agent-b-risk-matrix.md`.

---

## Lazy-load cookbook

[L5a] Filled at L5a per spike 1 — relationship access patterns audited with `lazy="raise"` enforcement.

See `docs/development/async-cookbook.md` § "The 9 lazy-load patterns" for the canonical catalogue. That doc is populated during Spike 1 with concrete fix recipes for each of the nine patterns identified in the Agent-B risk matrix (~68 total accessor sites across the codebase).

Until Spike 1 lands, treat any lazy-load failure as a novel finding: capture the stack trace, the query site, and the accessed relationship, and attach them to the spike 1 work item.

---

## MissingGreenlet triage

The `MissingGreenlet` exception fires when synchronous code (ORM attribute access, for example) hits a lazy relationship while running on an `AsyncSession`. SQLAlchemy refuses to issue implicit IO without a greenlet context, and the call explodes instead of silently blocking the event loop.

Ten-line fictional example (will be replaced with a real one at L5a):

```python
async def get_media_buy_with_products(media_buy_id: str):
    async with get_db_session() as session:
        stmt = select(MediaBuy).filter_by(media_buy_id=media_buy_id)
        mb = (await session.scalars(stmt)).first()
        # BOOM: products is lazy; this access is synchronous;
        # there is no greenlet to suspend into.
        product_names = [p.name for p in mb.products]
        return product_names
```

Fix options, in order of preference:

- **(a) Eager-load at the query site.** Add `selectinload(MediaBuy.products)` (or `joinedload` for small one-to-one) to the statement. This is the canonical fix — the query knows what it needs, and the cost is paid once.
- **(b) Explicit refresh.** `await session.refresh(mb, attribute_names=["products"])`. Use when the query site cannot be modified (for example, the instance arrived via a factory or fixture).
- **(c) Convert caller to sync.** If the caller is on an adapter path that is legitimately sync, wrap with `run_in_threadpool` per Decision 1 Path B. Do not convert pure admin handlers this way — fix the query instead.

---

## DB pool saturation

Pool saturation during async rollout is almost always a sizing mistake, not a leak. The connection budget must be asserted against pool math before L5b; see `implementation-checklist.md §L5b entry preflight H4` for the exact equation and the failure mode it guards against.

Runbook steps:

1. Check `ADCP_THREADPOOL_TOKENS` — the Starlette threadpool size bounds how many sync handlers can hold a sync `Session` concurrently.
2. Check `pool_size + max_overflow` on both the sync and async engines. Both engines share the Postgres `max_connections` budget.
3. Check rolling-deploy container overlap — during a rollout, old and new containers briefly share the budget. The H4 assertion accounts for this with a `2×` multiplier; if the assertion was bypassed, connections will exhaust during the overlap window.

If the math is off, the fix is config-only — no rollback required. If the math is right and connections are still exhausted, you are looking at a genuine leak: grep for sessions that were opened without a `with` or `async with`.

---

## Rollback escape hatch

If symptoms are widespread and cannot be fixed in-flight, revert the most recent async-conversion PR. The L5 layer is deliberately split into four sub-PRs (L5b, L5c, L5d, L5e) so that each rollback has a bounded blast radius. Consult `implementation-checklist.md §5` rollback table for the exact procedure for each sub-PR, including whether a database migration needs to be reversed alongside the code revert. L5b (SessionDep alias flip) and L5d (per-batch handler conversion) are the rollback targets most likely to matter in practice; L5c (test conversion) and L5e (cleanup) have minimal production surface.

---

## References

- `.claude/notes/flask-to-fastapi/async-audit/agent-b-risk-matrix.md` — 9-pattern lazy-load cookbook source
- `.claude/notes/flask-to-fastapi/async-audit/database-deep-audit.md` — critical blockers identified pre-L5
- `CLAUDE.md` § "Critical Invariants" — the six invariants that govern the migration, including invariant #4 on session scoping
