---
name: integrate
description: >
  Derive integration tests from existing unit test xfails and UNSPECIFIED stubs.
  Uses integration-from-stub formula: catalog → review → triage → architect →
  write-integration → fix-green → reconcile-xfail → verify → commit. Each
  stub's expected behavior is the core invariant. Architect atom reads 7
  architecture docs and cross-references against CRIT-1..CRIT-11 findings.
args: <entity-name-1> [entity-name-2] ...
---

# Integration Tests from Stubs

Derive integration tests from existing unit test xfails (Bucket A: "requires
real DB") and UNSPECIFIED stubs that exercise DB-dependent paths. Each stub's
expected behavior is absolute truth — if the integration test fails, fix
production code, never adjust the expected behavior.

## Args

```
/integrate <entity-name-1> [entity-name-2] ...
```

Entity names (space-separated): `creative`, `media-buy`, `delivery`.
Each must already have a unit test suite from `/surface`.

## Hard Gate: Pre-Check

The catalog atom checks if `tests/unit/test_{entity}.py` exists. If not:

```
┌──────────────────────────────────────────────────────────┐
│  STOP: No entity test suite for {entity}.                │
│                                                          │
│  Run `/surface {entity}` first to create the             │
│  test surface map with obligations and stubs.            │
│                                                          │
│  Integration tests derive from the surface — no surface, │
│  nothing to derive from.                                 │
└──────────────────────────────────────────────────────────┘
```

## Protocol

### Step 1: Cook the molecule

```bash
python3 .claude/scripts/cook_formula.py \
  --formula .claude/formulas/integration-from-stub.yaml \
  --var "ENTITY_NAMES={all_args}" \
  --epic-title "Integrate: {all_args}"
```

**Dry run first** (recommended):
```bash
python3 .claude/scripts/cook_formula.py \
  --formula .claude/formulas/integration-from-stub.yaml \
  --var "ENTITY_NAMES={all_args}" \
  --epic-title "Integrate: {all_args}" \
  --dry-run
```

### Step 2: Walk the molecule

```
bd ready → bd show <atom-id> → read description → execute → bd close <atom-id> → repeat
```

Each entity goes through 9 atoms:

```
catalog → review → triage → architect → write-integration → fix-green → reconcile-xfail → verify → commit
```

**Barrier serialization**: Entities execute in parallel within each atom phase,
but `write-integration` waits for ALL entities' `commit` from the previous
barrier (if any) to complete. This prevents merge conflicts.

### Step 3: Done when all atoms closed

All entities committed. Finalize atom runs `./run_all_tests.sh` and reports
remaining xfails.

## Atom Details

| # | Atom | What It Does |
|---|------|-------------|
| 1 | catalog | Read unit test suite, classify xfails into Bucket A (needs DB) vs B (not implemented), select batch |
| 2 | review | Verify stub fidelity, fixture design, batch scope, no duplicates with existing integration tests |
| 3 | triage | Route: ALL_LOW → proceed, NEEDS_REFINEMENT → adjust, NEEDS_USER_INPUT → block |
| 4 | **architect** | Read 7 architecture docs, trace code paths per test, map layer boundaries, document violations as FINDINGs, design fixtures and adapter mocking strategy |
| 5 | write-integration | Create `tests/integration/test_{entity}_v3.py` following the architect plan exactly |
| 6 | fix-green | Fix production code for failing integration tests (stub is truth, code is wrong) |
| 7 | reconcile-xfail | Remove xfails for tests now covered by passing integration tests |
| 8 | verify | `make quality` + integration tests pass + xfail count reconciled |
| 9 | commit | Commit integration tests + production fixes |

## The Architect Atom (Why It Matters)

The architect atom is the key differentiator from regular `/remediate`. It:

1. **Reads documented architecture** — 7 specific documents define the layer
   boundaries, error handling contracts, and data flow expectations
2. **Traces real code paths** — for each stub, maps wrapper → _impl → DB → adapter
3. **Cross-references CRIT findings** — checks if the code path touches any of
   the 11 critical issues from the migration review
4. **Documents violations** — uses FINDING format when existing code violates
   architecture, rather than silently encoding broken behavior into tests
5. **Does NOT copy patterns** — "match existing patterns" is explicitly forbidden.
   Existing code may violate the architecture.

### Required Reading for Architect

1. `CLAUDE.md` — 7 critical patterns
2. `docs/development/structural-guards.md` — 6 AST-enforced invariants
3. `docs/code-reviews/00-migration-summary.md` — CRIT-1..CRIT-11
4. `docs/code-reviews/01-schema-model-layer.md` — schema constraints
5. `docs/code-reviews/02-api-boundary-layer.md` — boundary constraints
6. `docs/code-reviews/04-adapter-layer.md` — adapter contracts
7. `docs/development/architecture.md` — system architecture

## Iron Rule: Stub Intent Is Absolute Truth

The unit test stub defines WHAT to test. The integration test verifies the
SAME behavior with real PostgreSQL. If the integration test fails, the
production code is wrong — not the stub.

| Outcome | Action |
|---------|--------|
| Integration test passes | Behavior works end-to-end |
| Fails with AssertionError | Production code bug → fix in fix-green atom |
| Fails with setup error | Fixture needs work → fix the fixture |
| Code violates architecture | FINDING → discuss before encoding into test |

**NEVER** adjust the expected behavior to match current code.

## Anti-Patterns

- Don't skip the architect atom — it catches architecture violations that unit tests with mocks cannot see
- Don't copy existing integration test patterns blindly — verify they follow the architecture
- Don't design tests to match broken code — document as FINDING
- Don't combine write-integration and fix-green — tests must exist before fixes
- Don't include Bucket B xfails ("not yet implemented") — those need production code, handled by B2
- Don't over-fixture — module-scope for expensive setup, function-scope for test-specific state

## See Also

- `/surface` — Create entity test suites (prerequisite)
- `/verify-spec` — Verify test expectations against AdCP spec (run after surface)
- `/remediate` — Fill unit test stubs (different from integration derivation)
- `/mol-execute` — Execute individual beads tasks
- `/guard` — Structural guards that protect the architecture
