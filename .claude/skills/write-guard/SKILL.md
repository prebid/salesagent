---
name: write-guard
description: >
  Create a structural guard test with a meta-test fixture that proves the guard
  catches violations. Follows existing AST-scanning patterns, adds FIXME comments
  to allowlisted sources.
args: <guard-name>
---

# Structural Guard Creation

## Args

`/write-guard no-flask-imports` — guard name (hyphenated). Produces `tests/unit/test_architecture_{name_underscored}.py`.

## Protocol

### Step 1: Research existing guards

Read 2 existing guards to learn the AST-scanning pattern:
```bash
head -80 tests/unit/test_architecture_schema_inheritance.py
head -80 tests/unit/test_architecture_no_raw_select.py
```

### Step 2: Define the invariant

State in ONE sentence what the guard prevents. Examples:
- `no-flask-imports`: No `from flask import` outside a shrinking allowlist
- `admin-routes-named`: Every `@router.*` decorator in `src/admin/routers/` has `name=`
- `admin-routes-sync`: Every handler in `src/admin/routers/` is sync `def` (not `async def`)

### Step 3: Write the guard test

File: `tests/unit/test_architecture_{name}.py`

Mandatory structure:
1. Module docstring: what it enforces, scanning approach
2. `_ALLOWLIST` set — files/locations permitted to violate (shrinks over time, never grows)
3. `_scan_file(path)` or `_scan_dir(dir)` function that finds violations via AST walk
4. `test_no_violations()` — asserts found violations are subset of allowlist
5. `test_allowlist_not_stale()` — asserts every allowlist entry still violates (prevents dead entries)

### Step 4: Create meta-test fixture

File: `tests/unit/fixtures/guard_meta/{name}_violation.py`

A minimal Python file containing a KNOWN violation. The guard must detect it.

Add to the guard test file:
```python
def test_meta_catches_known_violation():
    """Guard meta-test: proves the scanner detects violations."""
    violations = _scan_file(Path("tests/unit/fixtures/guard_meta/{name}_violation.py"))
    assert len(violations) > 0, "Meta-test fixture should trigger the guard"
```

### Step 5: Add FIXME comments at source

For every entry in `_ALLOWLIST`, grep the source location and verify a `# FIXME(salesagent-xxxx)` comment exists. Add one if missing. This is a CLAUDE.md hard requirement.

### Step 6: Run and verify

```bash
uv run pytest tests/unit/test_architecture_{name}.py -x -v
make quality
```

All three tests must pass: `test_no_violations`, `test_allowlist_not_stale`, `test_meta_catches_known_violation`.

## Hard rules

1. The `test_allowlist_not_stale` test is MANDATORY — prevents dead allowlist entries
2. The meta-test fixture is MANDATORY — proves the guard actually catches violations
3. FIXME comments at every allowlisted source location — CLAUDE.md requirement
4. Run both the guard AND `make quality`
