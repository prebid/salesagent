---
name: test-audit
description: >
  Audit non-obligation tests to classify each assertion's source of truth.
  Answers: "How do we know this test is testing correct behavior?" For each test,
  traces the assertion to an authoritative source (AdCP spec, CLAUDE.md architecture
  pattern, explicit product decision) or flags it as a characterization test that
  locks current behavior without external validation. Use after writing coverage
  gap tests or any tests not derived from BDD obligations.
args: <test-file-1> [test-file-2] ...
---

# Test Source-of-Truth Audit

Classify every test assertion by its authority level. Tests derived from BDD
obligations have a clear source of truth (the obligation). Tests written for
coverage, regression, or transport contract don't — this skill fills that gap.

## Args

```
/test-audit tests/unit/test_format_resolver.py
/test-audit tests/unit/test_products_transport_wrappers.py tests/unit/test_dynamic_products_pure.py
```

One or more test file paths. Each file is audited independently.

## The Three Questions

For every test function, answer:

1. **What behavior does this test assert?**
   One sentence: "asserts that X returns Y when Z"

2. **What authoritative source says this is correct?**
   Check sources in priority order (see below). Record the source or "none found."

3. **Classification?**
   Based on the source (or lack thereof), classify the test.

## Sources of Truth (Priority Order)

| Priority | Source | Where to check | Example |
|----------|--------|----------------|---------|
| 1 | **AdCP spec** | JSON schemas in `adcontextprotocol/adcp`, Python types in `adcp-client-python` | "Format resolution returns ValueError when format not found" |
| 2 | **CLAUDE.md architecture patterns** | 8 critical patterns (schema inheritance, transport boundary, etc.) | "MCP wrapper returns ToolResult, A2A returns model directly" |
| 3 | **Structural guards** | `tests/unit/test_architecture_*.py` — the guards define enforceable contracts | "ValidationError in wrapper → AdCPValidationError" |
| 4 | **Explicit product decision** | Code comments, PR descriptions, beads task descriptions, docstrings with rationale | "list_available_formats returns [] on error (graceful degradation)" |
| 5 | **None found** | No external authority — test documents current implementation behavior | "DB row not found → returns None" |

## Classification Labels

| Label | Meaning | Action |
|-------|---------|--------|
| `SPEC_BACKED` | AdCP spec or library defines this behavior | Keep as-is, add spec permalink |
| `ARCH_BACKED` | CLAUDE.md pattern or structural guard enforces this | Keep as-is, add pattern reference |
| `DECISION_BACKED` | Explicit product decision documented somewhere | Keep as-is, add decision reference |
| `CHARACTERIZATION` | No external source — locks current behavior | Keep, but add comment: `# Characterization: locks current behavior, no spec backing` |
| `SUSPECT` | Current behavior may be wrong — no source AND the logic seems questionable | Flag for product decision, file beads issue |

## Protocol

### Step 1: Read each test file

For each file in args:

1. Read the full test file
2. List every test function with its class
3. For each test, extract:
   - The assertion(s) — what exactly does it check?
   - The setup — what state is constructed?
   - The action — what production function is called?

### Step 2: Read the production code under test

For each unique production module referenced by the tests:

1. Read the production function(s) being tested
2. Note: docstrings with rationale, code comments explaining "why"
3. Note: any AdCP spec references already present

### Step 3: Check authoritative sources

For each test assertion, check sources in priority order. **Stop at the first match.**

**Priority 1 — AdCP spec:**
- Use DeepWiki or local clone to check if the behavior is spec-defined
- Check JSON schemas in `adcontextprotocol/adcp` repo
- Check Python types in `adcontextprotocol/adcp-client-python` repo
- Check `adcp-req` as an index (follow links to actual source)

**Priority 2 — CLAUDE.md patterns:**
- Pattern #1: Schema inheritance
- Pattern #2: Route conflicts
- Pattern #3: Repository pattern
- Pattern #4: Nested serialization
- Pattern #5: Transport boundary (MCP → ToolResult, A2A → model, _impl → ResolvedIdentity)
- Pattern #6: JavaScript script_root
- Pattern #7: Environment-based validation
- Pattern #8: Factory-based test fixtures

**Priority 3 — Structural guards:**
- Check if the behavior is enforced by any `test_architecture_*.py` guard
- If a guard enforces it, the test is `ARCH_BACKED`

**Priority 4 — Product decisions:**
- Check the beads task that created the test (`bd show <id>`)
- Check git log for the commit that added the production code
- Check docstrings and code comments for "why" explanations
- Check PR descriptions if available

**Priority 5 — No source found:**
- Is the behavior reasonable and unlikely to be wrong? → `CHARACTERIZATION`
- Does the behavior seem arbitrary or potentially incorrect? → `SUSPECT`

### Step 4: Produce the audit report

Add a classification comment block at the top of each test file:

```python
# --- Test Source-of-Truth Audit ---
# Audited: YYYY-MM-DD
#
# SPEC_BACKED (N tests):
#   test_x — AdCP spec: format resolution order
#   test_y — adcp-client-python: FormatId equality
#
# ARCH_BACKED (N tests):
#   test_z — CLAUDE.md #5: MCP returns ToolResult
#
# CHARACTERIZATION (N tests):
#   test_w — locks: returns None when DB row missing
#
# SUSPECT (N tests):
#   test_v — list_available_formats returns [] on error (should it propagate?)
# ---
```

### Step 5: Handle SUSPECT tests

For each `SUSPECT` test:

1. File a beads issue:
   ```bash
   bd create --title="Product decision needed: <behavior>" \
     --description="Test <name> asserts <behavior> but no spec or product decision backs this. Options: A) keep as-is, B) change behavior to <alternative>" \
     --type=task --priority=3
   ```

2. Add a `# SUSPECT` comment to the test with the beads ID:
   ```python
   # SUSPECT(salesagent-xxxx): No spec backing. Returns [] on error — should it propagate?
   def test_registry_creation_fails_returns_empty(self):
   ```

### Step 6: Handle CHARACTERIZATION tests

For each `CHARACTERIZATION` test, add a brief inline comment:

```python
# Characterization: locks current behavior (no spec backing)
def test_no_product_row_returns_none(self):
```

No beads issue needed — characterization tests are valuable as regression guards,
they just don't prove correctness.

### Step 7: Commit

```bash
git add <audited-test-files>
git commit -m "docs: audit test sources of truth for <file(s)>"
```

## What This Skill Does NOT Do

- Does not rewrite tests (audit only — add comments and classifications)
- Does not read the full AdCP spec end-to-end (checks targeted areas per test)
- Does not assume "current behavior = correct behavior"
- Does not remove any tests (even SUSPECT ones stay, just flagged)
- Does not audit BDD obligation tests (use `/verify-spec` for those)

## Output Summary

At the end, print a summary table:

```
Test Source-of-Truth Audit Summary
==================================
File: tests/unit/test_format_resolver.py
  SPEC_BACKED:      3/17
  ARCH_BACKED:      5/17
  DECISION_BACKED:  1/17
  CHARACTERIZATION: 6/17
  SUSPECT:          2/17

File: tests/unit/test_products_transport_wrappers.py
  SPEC_BACKED:      2/15
  ARCH_BACKED:      8/15
  CHARACTERIZATION: 5/15

Total: 32 tests audited
  SUSPECT issues filed: 2 (salesagent-xxxx, salesagent-yyyy)
```

## When to Use

- After `/mol-execute` writes coverage gap tests
- After writing transport wrapper tests
- After writing any tests not derived from BDD obligations
- When reviewing test quality before merge
- When asked "how do we know these tests are correct?"

## See Also

- `/verify-spec` — Verifies BDD obligation tests against AdCP spec (different scope)
- `/audit` — Audits production code against architecture principles (not tests)
- `/obligation-test` — Writes tests FROM obligations (this skill audits tests WITHOUT obligations)
- `/guard` — Structural guards that enforce architecture invariants
