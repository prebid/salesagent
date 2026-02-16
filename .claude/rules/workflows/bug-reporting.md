# Bug Reporting & Fix Workflow

## When You Find a Bug

### 1. Create Beads Issue
```bash
bd create --title="Bug: <concise description>" --type=bug --priority=<0-4>
```

Include in the description:
- **Observed behavior**: What actually happens
- **Expected behavior**: What should happen
- **Reproduction steps**: How to trigger it
- **Affected area**: Which files/components

### 2. Validate Against Patterns

Before fixing, check:
- Does this violate a CLAUDE.md critical pattern?
- Is this an AdCP spec compliance issue? (Check `tests/unit/test_adcp_contract.py`)
- Is this a regression from a recent change? (Check `git log --oneline -20`)

### 3. Write Regression Test

**Always write the test FIRST:**
```bash
# Write the failing test
uv run pytest tests/unit/test_<area>.py::test_<bug_description> -x
# Confirm it fails for the right reason
```

The test should:
- Demonstrate the bug clearly
- Be minimal (test one thing)
- Follow existing test patterns in the file

### 4. Fix the Bug

- Fix the root cause, not symptoms
- Keep the fix minimal and focused
- Don't refactor surrounding code (separate PR)

### 5. Quality Gates
```bash
make quality
```

Verify:
- New test passes
- No existing tests broken
- Formatting and linting clean

### 6. Close and Commit
```bash
bd close <id>
git add <specific-files>
git commit -m "fix: <description of what was fixed>"
```

## Bug Priority Guide

- **P0 (critical)**: Data loss, security vulnerability, complete feature broken
- **P1 (high)**: Major feature degraded, blocking other work
- **P2 (medium)**: Feature works but incorrectly in some cases
- **P3 (low)**: Minor issue, workaround exists
- **P4 (backlog)**: Cosmetic, edge case, nice-to-have fix
