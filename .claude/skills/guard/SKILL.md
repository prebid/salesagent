---
name: guard
description: >
  Create structural guard tests that enforce architecture principles on every
  `make quality` run. Guards are AST-scanning tests that prevent categories of
  violations automatically. Available guards: schema-inheritance,
  boundary-completeness, query-type-safety, no-error-dicts.
args: <guard-name-1> [guard-name-2] ...
---

# Structural Guard Creation

Create AST-scanning enforcement tests that catch architecture violations
automatically. Each guard runs on `make quality` and prevents regressions.

## Args

```
/guard <guard-name-1> [guard-name-2] ...
```

Guard names (space-separated). Each gets a test file at
`tests/unit/test_architecture_{guard_name}.py`.

## Available Guards

| Guard | What It Enforces |
|-------|-----------------|
| schema-inheritance | Schema classes extend correct adcp library base types |
| boundary-completeness | MCP/A2A/REST wrappers expose all _impl parameters |
| query-type-safety | DB queries use column types matching the column definition |
| no-error-dicts | _impl functions raise exceptions, never return error dicts |

Custom guard names are also supported — the research atom will determine
what to enforce based on the name and #1050/#1066 principles.

## Existing Structural Guards

These already exist (don't recreate):
- `test_no_toolerror_in_impl.py` — No ToolError in _impl functions
- `test_transport_agnostic_impl.py` — No transport imports in _impl
- `test_impl_resolved_identity.py` — _impl accepts ResolvedIdentity

## Protocol

### Step 1: Cook the molecule

```bash
python3 .claude/scripts/cook_formula.py \
  --formula .claude/formulas/structural-guard.yaml \
  --var "GUARD_NAMES={all_args}" \
  --epic-title "Guards: {all_args}"
```

**Dry run first** (recommended):
```bash
python3 .claude/scripts/cook_formula.py \
  --formula .claude/formulas/structural-guard.yaml \
  --var "GUARD_NAMES={all_args}" \
  --epic-title "Guards: {all_args}" \
  --dry-run
```

### Step 2: Walk the molecule

```
bd ready → bd show <atom-id> → execute → bd close <atom-id> → repeat
```

Each guard goes through: research → scan → write-guard → mark-known → verify → commit.

### Step 3: Done when all atoms closed

All guards committed and passing in `make quality`.

## Key Principles

- **Allowlists shrink, never grow.** New violations fail the guard immediately.
- **FIXME comments link to beads tasks.** Every known violation has a tracker.
- **Guards follow existing patterns.** Read the 3 existing structural tests first.

## See Also

- `/surface` — Create entity test suites (what the guards protect)
- `/remediate` — Fill entity test stubs (fix the violations guards find)
- `/mol-execute` — Execute individual beads tasks
