---
name: surface
description: >
  Map the complete test surface for domain entities by cross-referencing
  test-obligations with existing tests. Produces one canonical test module per
  entity with real tests + skip stubs for gaps. Every obligation maps to exactly
  one test. Run this before /remediate.
args: <entity-name-1> [entity-name-2] ...
---

# Entity Test Surface Mapping

Create canonical test suites per domain entity by mapping all test obligations
to real tests or stubs. This gives complete visibility into what's tested and
what's missing.

## Args

```
/surface <entity-name-1> [entity-name-2] ...
```

Entity names (space-separated). Each gets a test file at
`tests/unit/test_{entity_name}.py` (hyphens → underscores).

## Entity → Obligations Mapping

| Entity | Primary Obligations | Output File |
|--------|-------------------|-------------|
| product | UC-001 | test_product.py |
| media-buy | UC-002, UC-003 | test_media_buy.py |
| delivery | UC-004 | test_delivery.py |
| creative-formats | UC-005 | test_creative_formats.py |
| creative | BR-UC-006 | test_creative.py |
| properties | BR-UC-007 | test_properties.py |
| audience-signals | BR-UC-008 | test_audience_signals.py |
| performance | BR-UC-009 | test_performance.py |
| capabilities | BR-UC-010 | test_capabilities.py |
| pricing-option | business-rules, constraints | test_pricing_option.py |
| transport-boundary | #1050/#1066 principles | test_transport_boundary.py |

All entities also check cross-cutting files: `business-rules.md`, `constraints.md`.

## What You Get

For each entity, a test module containing:
- **Real tests**: Ported from existing (possibly scattered) test files
- **Skip stubs**: `@pytest.mark.skip(reason="STUB: [obligation-ID]")` for every gap
- **Obligation traceability**: Each test/stub references its obligation ID

Open `test_creative.py` → see 89 test names → 30 real, 59 stubs → know exactly what's missing.

## Protocol

### Step 1: Cook the molecule

```bash
python3 .claude/scripts/cook_formula.py \
  --formula .claude/formulas/entity-test-surface.yaml \
  --var "ENTITY_NAMES={all_args}" \
  --epic-title "Surface: {all_args}"
```

**Dry run first** (recommended):
```bash
python3 .claude/scripts/cook_formula.py \
  --formula .claude/formulas/entity-test-surface.yaml \
  --var "ENTITY_NAMES={all_args}" \
  --epic-title "Surface: {all_args}" \
  --dry-run
```

### Step 2: Walk the molecule

```
bd ready → bd show <atom-id> → execute → bd close <atom-id> → repeat
```

Each entity goes through: gather-obligations → audit-existing → review → triage → generate-suite → verify → commit.

### Step 3: Done when all atoms closed

All entity suites committed. Coverage summary generated.

## Naming Rules

Test names describe **behavior**, not bugs:
- `test_create_media_buy_rejects_missing_brand` (behavior)
- NOT: `test_bug_fix_123` or `test_v2_compat` (incident-driven)

## See Also

- `/remediate` — Fill the stubs this skill creates
- `/guard` — Structural guards that prevent new violations
- `/mol-execute` — Execute individual beads tasks (now entity-suite-aware)
