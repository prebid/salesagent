---
name: audit
description: >
  Run a repeatable code review audit on migration changes. Inventories files by
  architectural layer, reviews each layer against migration principles, and
  writes findings to a report. Re-run after remediation batches to track progress.
args: [audit-target]
---

# Migration Audit

Repeatable code review workflow for auditing migration changes. Produces
structured review documents for every finding.

## Args

```
/audit [audit-target]
```

The audit target (optional, defaults to "full"):
- **Branch**: `feat/v2.0.0-flask-to-fastapi` — review all changes on branch
- **Commit range**: `main..HEAD` — review commits in range
- **"full"**: Audit entire codebase against architecture principles

## What It Produces

1. **Change inventory** by architectural layer (schema, business, boundary, transport, adapter, database, test)
2. **Layer-specific reviews** against migration checklists
3. **Consolidated report** in `docs/code-reviews/`

## Protocol

### Step 1: Identify scope

Read `.claude/notes/flask-to-fastapi/execution-plan.md` to identify the current phase and which architectural layers are affected. If an audit target was given, scope to that target; otherwise audit all layers.

```bash
# Inventory changed files by layer
git diff --name-only main...HEAD | sort
```

### Step 2: Review each layer

Walk each architectural layer sequentially. For each file in the layer:

1. Read the file
2. Check against the layer review checklist (see table below)
3. Note any findings with file path, line number, and violation description

### Step 3: Write report

Write the consolidated audit report to `docs/code-reviews/` with:
- Timestamp and audit scope
- Per-layer findings table
- Summary counts (pass / warn / fail)

Commit the report with message: `docs: migration audit — <scope>`

## Layer Review Checklists

Each layer has a specific checklist:

| Layer | Key Checks |
|-------|-----------|
| Schema | Correct base class, no field duplication, exclude=True on internals |
| Business | No ToolError, no transport imports, ResolvedIdentity, no error dicts |
| Boundary | All _impl params exposed, version compat at boundary only |
| Transport | Shared _impl pattern, no business logic duplication |
| Database | SQLAlchemy 2.0, JSONType, correct column types |
| Adapter | No protocol code, proper error propagation |
| Admin | Handlers use sync `def` (not `async def`) — except OAuth callbacks which must be `async def` for Authlib. `with get_db_session()` inside handler body. Named routes with `admin_` prefix. `url_for()` in templates, no hardcoded paths. |

## Re-Running After Remediation

The audit is designed to be re-run:
```
/audit main..HEAD    # after first batch
/audit main..HEAD    # after second batch (findings should decrease)
```

Compare reports across runs to track progress.

## See Also

- `/surface` — Create entity test suites for coverage gaps found in audit
- `/guard` — Structural guards enforce the principles audit checks for
- `/phase-gate` — Validate phase exit criteria
