---
name: obligation-test
description: >
  Write per-obligation behavioral tests with hard quality enforcement. Each
  obligation gets its own research → write-test → verify → commit chain.
  Replaces batch skills (/surface, /remediate) for new obligation coverage.
  Accepts obligation IDs directly or a use-case prefix (e.g., UC-004) to
  auto-select uncovered obligations from the allowlist.
args: <obligation-ids-or-prefix> [--count N]
---

# Per-Obligation Test Derivation

Write one behavioral test per obligation with 6 hard quality rules enforced
mechanically. Each obligation gets deep research (scenario + production code +
test strategy) before any test code is written.

## Args

```
/obligation-test UC-004-MAIN-01 UC-004-MAIN-02 UC-004-MAIN-03
/obligation-test UC-004 --count 10
/obligation-test UC-001-MAIN --count 15
```

**Direct IDs**: Space-separated obligation IDs. Each must be a behavioral
obligation in `docs/test-obligations/`.

**Prefix mode**: A use-case prefix (e.g., `UC-004`, `UC-001-MAIN`, `BR-RULE`).
Auto-selects uncovered obligations from the allowlist matching the prefix.
`--count N` limits how many (default: 10).

## Protocol

### Step 0: Resolve obligation IDs

If args look like a prefix (no trailing `-NN` sequence number):

```bash
python3 -c "
import json
al = json.loads(open('tests/unit/obligation_coverage_allowlist.json').read())
matches = sorted(oid for oid in al if oid.startswith('{prefix}'))
print(' '.join(matches[:N]))
print(f'Total matching: {len(matches)}, selected: {min(N, len(matches))}')
"
```

Store the resolved IDs for Step 1.

### Step 1: Cook the molecule

```bash
python3 .claude/scripts/cook_formula.py \
  --formula .claude/formulas/obligation-test.yaml \
  --var "OBLIGATION_IDS={resolved_ids}" \
  --epic-title "Obligation tests: {prefix} batch N ({count} obligations)"
```

This creates: 1 setup atom + (4 atoms x N obligations) + 2 finalize atoms.

### Step 2: Baseline (setup atom)

Run `make quality`, record pass count, allowlist size, and coverage count
in the epic notes. Close the baseline atom.

### Step 3: Research all obligations (parallel)

All research atoms unblock after baseline. For each obligation:

1. **Read the scenario** from `docs/test-obligations/`:
   ```bash
   grep -n "{OID}" docs/test-obligations/*.md
   ```
   Extract: Given/When/Then, business rule, priority, layer.

2. **Find the production code**: Locate the `_impl` function and specific
   lines that implement this behavior.

3. **Plan the test**: Decide unit vs integration, list mocks, identify the
   KEY ASSERTION — "What assertion would FAIL if production behavior changed?"

4. **Store findings** in the atom notes via `bd update`.

5. **Close the research atom**.

### Step 4: Write tests (sequential per obligation)

Write-test atoms serialize via `depends_on_prev_barrier` to prevent
concurrent file modifications. For each obligation:

1. Read research notes from the previous atom.

2. Write ONE test following all 6 hard rules:

   | # | Rule | Check |
   |---|------|-------|
   | 1 | Import from `src.` | `from src.` in test file |
   | 2 | Call production function | Test body calls `_impl`, repo method, or schema method |
   | 3 | Assert production output | Assertion checks a value from the production call |
   | 4 | `Covers: {OID}` tag | Docstring contains exactly `Covers: {OID}` |
   | 5 | Use factories where applicable | Use `tests/factories/` or helpers, not inline `Model()` |
   | 6 | Not mock-echo only | Does more than verify `mock.called` |

3. Self-check: Re-read the test and answer 4 yes/no questions.
   If any "no", rewrite before proceeding.

4. Run the test:
   ```bash
   uv run pytest <test_file>::<test_name> -x -v
   ```
   - PASS or XFAIL = proceed
   - ERROR = fix the test (import/name errors)

5. Close the write-test atom.

### Step 5: Verify (per obligation)

Six mechanical checks — no judgment calls:

1. `grep "from src\." <file>` > 0
2. `grep "Covers: {OID}" tests/` count == 1
3. Test runs without ERROR
4. `make quality` passes
5. No duplicate Covers tags
6. If test PASSES: remove OID from allowlist, add file to `_UNIT_ENTITY_FILES`
   if needed, run obligation guard

Close the verify atom.

### Step 6: Commit (per obligation)

```bash
git add <test_file> tests/unit/obligation_coverage_allowlist.json
git add tests/unit/test_architecture_obligation_coverage.py  # if _UNIT_ENTITY_FILES updated
git commit -m "test: add obligation test for {OID}"
```

Close the commit atom.

### Step 7: Finalize

After all obligation chains complete:

1. Run `make quality`, compare to baseline
2. Run obligation guard
3. Record final state in epic notes
4. `bd sync`
5. Close finalize atoms and epic

## Test File Selection

Place tests in existing behavioral test files when possible:

| Use Case | File |
|----------|------|
| UC-002 | `test_create_media_buy_behavioral.py` |
| UC-003 | `test_update_media_buy_behavioral.py` |
| UC-004 | `test_delivery_behavioral.py` |
| UC-006 | `test_creative_behavioral.py` (create if needed) |
| Other | `test_{use_case}_behavioral.py` (create if needed) |

New files must be added to `_UNIT_ENTITY_FILES` in
`tests/unit/test_architecture_obligation_coverage.py`.

## Batch Sizing

Recommended: **7-15 obligations per cook**.

- Fewer than 7: overhead of setup/finalize atoms not worth it
- More than 15: long molecule, risk of context compaction mid-chain
- At 10 per batch: 50 remaining UC-004 obligations = ~5 batches

## xfail Policy

When production code doesn't implement the tested behavior:

```python
@pytest.mark.xfail(reason="<what's missing in production code>")
def test_name(self):
    """... Covers: {OID} ..."""
```

The xfail test STILL must follow all 6 rules. The `Covers:` tag still
removes the OID from the allowlist (the guard counts xfail as covered).

### Alternative: Team-Based Execution

For higher quality at scale, use `/obligation-team` instead of this skill.
It spawns one agent per obligation with fresh context and mandatory Right
Questions self-checks. Each agent gets zero batch pressure and full context
for its single obligation. See `.claude/commands/obligation-team.md`.

## Anti-Patterns

- Don't skip research — shallow understanding produces mock-echo tests
- Don't batch-write 15+ tests at once — quality degrades after 2-3
- Don't assert `mock.called` as the primary assertion (Rule 6)
- Don't drop obligations because "the code doesn't do this" — use xfail
- Don't create test files without adding them to `_UNIT_ENTITY_FILES`
- Don't leave OIDs in the allowlist after a passing test covers them

## See Also

- `/remediate` — Fill existing entity test stubs (batch approach)
- `/surface` — Create entity test suites with obligation mapping
- `/mol-execute` — Execute individual beads tasks
- `/guard` — Structural guards that enforce architecture on `make quality`
