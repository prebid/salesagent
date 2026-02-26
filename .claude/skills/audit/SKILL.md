---
name: audit
description: >
  Run a repeatable code review audit on migration changes. Inventories files by
  architectural layer, reviews each layer against #1050/#1066 principles, and
  files beads issues for findings. Re-run after remediation batches to track
  progress.
args: [audit-target]
---

# Migration Audit

Repeatable code review workflow for auditing migration changes. Produces
structured review documents and beads issues for every finding.

## Args

```
/audit [audit-target]
```

The audit target (optional, defaults to "full"):
- **Branch**: `KonstantinMirin/adcp-v3-upgrade` — review all changes on branch
- **Commit range**: `main..HEAD` — review commits in range
- **"full"**: Audit entire codebase against architecture principles

## What It Produces

1. **Change inventory** by architectural layer (schema, business, boundary, transport, adapter, database, test)
2. **Layer-specific reviews** against #1050/#1066 checklists
3. **Consolidated report** in `docs/code-reviews/`
4. **Beads issues** for every finding not already tracked

## Protocol

### Step 1: Cook the molecule

```bash
python3 .claude/scripts/cook_formula.py \
  --formula .claude/formulas/migration-audit.yaml \
  --var "AUDIT_TARGET={all_args}" \
  --epic-title "Audit: {all_args}"
```

If no target specified, use "full":
```bash
python3 .claude/scripts/cook_formula.py \
  --formula .claude/formulas/migration-audit.yaml \
  --var "AUDIT_TARGET=full" \
  --epic-title "Audit: full codebase"
```

**Dry run first** (recommended):
```bash
python3 .claude/scripts/cook_formula.py \
  --formula .claude/formulas/migration-audit.yaml \
  --var "AUDIT_TARGET={all_args}" \
  --epic-title "Audit: {all_args}" \
  --dry-run
```

### Step 2: Walk the molecule

```
bd ready → bd show <atom-id> → execute → bd close <atom-id> → repeat
```

Linear pipeline: inventory-changes → review-per-layer → consolidate → file-issues → commit-report.

### Step 3: Done when report committed

Audit report in `docs/code-reviews/`, beads issues filed for all findings.

## Layer Review Checklists

Each layer has a specific checklist (see formula for full details):

| Layer | Key Checks |
|-------|-----------|
| Schema | Correct base class, no field duplication, exclude=True on internals |
| Business | No ToolError, no transport imports, ResolvedIdentity, no error dicts |
| Boundary | All _impl params exposed, version compat at boundary only |
| Transport | Shared _impl pattern, no business logic duplication |
| Database | SQLAlchemy 2.0, JSONType, correct column types |
| Adapter | No protocol code, proper error propagation |

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
- `/remediate` — Fill test stubs to fix the issues audit finds
- `/mol-execute` — Execute individual beads tasks for specific findings
