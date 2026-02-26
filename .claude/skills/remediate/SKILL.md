---
name: remediate
description: >
  Fill test stubs in entity test suites systematically. Works batch-by-batch
  with TDD: write failing tests (red), fix production code (green). Has a hard
  gate — STOPS if the entity test suite doesn't exist, directing you to run
  /surface first.
args: <entity-name-1> [entity-name-2] ...
---

# Entity Test Remediation

Systematically convert test stubs into real tests and fix production code
to make them pass. Batch-by-batch, TDD-driven.

## Args

```
/remediate <entity-name-1> [entity-name-2] ...
```

Entity names (space-separated). Each must already have a test suite from
`/surface`. If the suite doesn't exist, the formula STOPS at pre-check.

## Hard Gate: Pre-Check

The first atom checks if `tests/unit/test_{entity}.py` exists:

```
┌─────────────────────────────────────────────────────────┐
│  STOP: No entity test suite for {entity}.               │
│                                                         │
│  Run `/surface {entity}` first to create the            │
│  test surface map with obligations and stubs.           │
│                                                         │
│  Remediation requires a surface to remediate against.   │
└─────────────────────────────────────────────────────────┘
```

This prevents remediation without a surface map — the whole point of the
layered approach.

## Batch Sizing

Each molecule handles one batch per entity (recommended 5-15 stubs):
- Too few stubs per batch = too many molecules
- Too many = too much context for one session

Multiple `/remediate` runs against the same entity are expected:
```
/remediate creative    # batch 1: P0 schema compliance stubs
/remediate creative    # batch 2: P1 lifecycle stubs
/remediate creative    # batch 3: P2 edge case stubs
```

## Protocol

### Step 1: Cook the molecule

```bash
python3 .claude/scripts/cook_formula.py \
  --formula .claude/formulas/entity-remediate.yaml \
  --var "ENTITY_NAMES={all_args}" \
  --epic-title "Remediate: {all_args}"
```

**Dry run first** (recommended):
```bash
python3 .claude/scripts/cook_formula.py \
  --formula .claude/formulas/entity-remediate.yaml \
  --var "ENTITY_NAMES={all_args}" \
  --epic-title "Remediate: {all_args}" \
  --dry-run
```

### Step 2: Walk the molecule

```
bd ready → bd show <atom-id> → execute → bd close <atom-id> → repeat
```

Each entity goes through: pre-check → plan-batch → review → triage → implement-red → fix-green → verify → commit.

### Step 3: Done when all atoms closed

Batch complete. Run `/remediate` again for the next batch until stubs → 0.

## TDD Cycle

```
implement-red (stubs → failing tests)
     ↓
fix-green (fix code → tests pass)
     ↓
verify (make quality)
```

The red/green split ensures tests are written BEFORE production code changes.
This is enforced by atom dependencies — fix-green can't start until
implement-red is closed.

## Iron Rule: Stubs Are Absolute Truth

**Remediation agents MUST NOT drop, skip, or question any stub's expected
behavior.** Stubs were created by `/surface` and verified by `/verify-spec`.
Those upstream phases already confirmed the expected behavior against the
spec and business rules.

The remediation agent's job is to IMPLEMENT, not judge:

| Outcome | Action |
|---------|--------|
| Test passes | Keep it — behavior was correct but untested |
| Test fails, fixable | Fix production code (TDD green) |
| Test fails, not fixable now | Mark `@pytest.mark.xfail(reason="...")` + file beads bug |
| Agent thinks stub is wrong | **IRRELEVANT** — implement it anyway. If there's truly a spec error, that's for `/verify-spec` to catch in a re-run, not for remediation to decide. |

**NEVER**: remove a stub, revert to skip, or conclude "the code doesn't do
this so the stub must be wrong." The code is what's wrong, not the stub.

## Related Bugs

The plan-batch atom checks `bd list --status=open` for bugs related to the
entity. If a stub covers a known bug, both get resolved together.

## See Also

- `/surface` — Create the entity test suite (prerequisite for /remediate)
- `/guard` — Structural guards prevent new violations during remediation
- `/mol-execute` — Execute individual beads tasks
