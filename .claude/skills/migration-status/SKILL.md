---
name: migration-status
lifecycle: migration
description: >
  Show migration progress dashboard: Flask imports remaining, routers created,
  structural guards status, current layer, and remaining work.
---

# Migration Status Dashboard

## Protocol

### Step 1: Gather metrics

Run all checks:

```bash
# Flask imports remaining
rg -c "from flask import|import flask" src/ --type py 2>/dev/null || echo "0 files"

# Blueprints remaining vs routers created
ls src/admin/blueprints/*.py 2>/dev/null | wc -l
ls src/admin/routers/*.py 2>/dev/null | wc -l

# Template url_for count
rg -c "url_for" templates/ --type html 2>/dev/null | paste -sd+ | bc 2>/dev/null || echo "0"

# Foundation modules
for m in templating flash sessions oauth csrf app_factory; do
  test -f "src/admin/${m}.py" && echo "  DONE: ${m}.py" || echo "  TODO: ${m}.py"
done
test -d "src/admin/deps" && echo "  DONE: deps/" || echo "  TODO: deps/"
test -d "src/admin/middleware" && echo "  DONE: middleware/" || echo "  TODO: middleware/"
test -d "src/admin/routers" && echo "  DONE: routers/" || echo "  TODO: routers/"

# Flask still in pyproject.toml?
grep -q '"flask' pyproject.toml && echo "Flask: IN dependencies" || echo "Flask: REMOVED"

# Async signal: any admin handlers using async def / SessionDep yet?
rg -c "async def|SessionDep|AsyncSession" src/admin/routers/ 2>/dev/null || echo "0"

# Structural guards signal: sync-def guard present vs async-def guard active?
test -f tests/unit/test_architecture_handlers_use_sync_def.py && echo "  sync-def guard: present" || echo "  sync-def guard: absent"
test -f tests/unit/test_architecture_admin_routes_async.py && echo "  async-def guard: present" || echo "  async-def guard: absent"
```

### Step 2: Determine current layer

v2.0 ships in 8 layers (L0 through L7). Layers 0-4 are sync; Layers 5-7 convert to async and polish. Infer the layer from measurable state:

| Signal | Layer |
|---|---|
| No routers, no foundation modules | Pre-L0 |
| Foundation modules exist, no routers wired, Flask serves 100% | L0 done |
| Middleware + public/core/auth routers wired, Flask catch-all for unmigrated paths | L1 in progress |
| All admin routers ported, Flask still mounted for fallback | End of L1 |
| `rg -w flask src/` = 0, `flask` removed from `pyproject.toml`, pre-commit + Dockerfile flags updated | L2 done |
| `tests/factories/` consolidated, `dependency_overrides` + `TestClient` pattern adopted | L3 done |
| `SessionDep` introduced (still sync), DTOs at repo boundary, `structlog` wired, `render()` deleted, `baseline-sync.json` captured | L4 done (L5 entry gate) |
| Spike 1/2/3/4/4.25/4.5/5.5 all green; `SessionDep` re-aliased to `AsyncSession`; 3-router pilot async; bulk routers converted; SSE deleted; adapter Path-B `run_in_threadpool` wrap active | L5 in progress (sub-layers 5a/5b/5c/5d1-5d5/5e) |
| `flash.py` deleted (replaced by app.state flash store), `app.state` singletons for `SimpleAppCache`, router subdir reorg, `logfire` instrumentation in | L6 done |
| Structural-guard allowlists at 0, perf baseline vs `baseline-sync.json` green, mypy strict ratcheting green, `docs/ARCHITECTURE.md` refreshed, `v2.0.0` tag applied | L7 done |

### Step 3: Report

```
=== Flask → FastAPI Migration Status ===

Current Layer: L1 (Flask Parity — low-risk HTML routers in flight)

Flask remnants:    34 files with Flask imports
Blueprints:        21 remaining / 6 deleted
Routers:           6 created (all sync def handlers)
Foundation:        11/11 complete
Templates:         134 url_for refs
Flask in deps:     YES (removed in L2)

Async signals:     0 async def in admin/routers (expected through L4)
Sync-def guard:    present (will flip to async-def guard in L5b)

Structural guards: 26 total, 24 passing
```

### Step 4: Show remaining work for current layer

Read `.claude/notes/flask-to-fastapi/execution-plan.md` for the current layer and list uncompleted items based on file existence and test status.
