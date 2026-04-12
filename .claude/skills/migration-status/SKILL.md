---
name: migration-status
lifecycle: migration
description: >
  Show migration progress dashboard: Flask imports remaining, routers created,
  structural guards status, current phase, and remaining work.
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
```

### Step 2: Determine current phase

Based on metrics:
- No routers, no foundation modules → Pre Phase 0
- Foundation modules exist, no routers → Phase 0 done
- Some routers, Flask still mounted → Phase 1 or 2
- All routers, Flask removed → Phase 3 complete
- v2.0.0 tag in git → Done

> **Note:** v2.0 uses sync `def` handlers with sync SQLAlchemy throughout.
> There is no async phase. Async migration is deferred to v2.1.

### Step 3: Report

```
=== Flask → FastAPI Migration Status ===

Current Phase: 2a (Low-risk HTML routers)

Flask remnants:    34 files with Flask imports
Blueprints:        21 remaining / 6 deleted
Routers:           6 created (all sync def handlers)
Foundation:        11/11 complete
Templates:         134 url_for refs
Flask in deps:     YES (removed in Phase 3)

Structural guards: 26 total, 24 passing
```

### Step 4: Show remaining work for current phase

Read the execution plan for the current phase and list uncompleted items based on file existence and test status.
