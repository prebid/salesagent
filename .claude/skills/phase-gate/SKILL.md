---
name: phase-gate
lifecycle: migration
description: >
  Validate a migration phase's exit criteria by running every gate command
  and checking every constraint. Reports PASS/FAIL with details.
args: <phase-number>
---

# Phase Exit Gate Validation

## Args

`/phase-gate 0` or `/phase-gate 2a` or `/phase-gate 3`

## Protocol

### Step 1: Read exit gate

Read `.claude/notes/flask-to-fastapi/execution-plan.md`, find the phase section, extract:
- The "Exit gate" code block (commands to run)
- The "What NOT to do" section (constraints to verify)

### Step 2: Run gate commands

Execute every command from the exit gate block sequentially. Capture exit codes and output.

Example for Phase 0:
```bash
make quality
tox -e integration
tox -e bdd
./run_all_tests.sh
python scripts/codemod_templates_greenfield.py --check templates/
rg -n "url_for" templates/ | wc -l
```

### Step 3: Check "What NOT to do" constraints

For each constraint, run a verification check:

```bash
# Example: "Do not modify src/app.py"
git diff main -- src/app.py | wc -l  # must be 0

# Example: "Flask serves 100% of /admin/* traffic"
grep -c "include_router.*admin" src/app.py  # should be 0 if not wired yet

# Example: "No Flask blueprints deleted"
ls src/admin/blueprints/*.py | wc -l  # should match expected count
```

### Step 4: Report

```
Phase {N} Exit Gate: {PASS|FAIL}

Gate commands:
  [PASS] make quality (exit 0)
  [PASS] tox -e integration (exit 0, 847 passed)
  [FAIL] rg -n "url_for" templates/ | wc -l => 98 (need >= 134)

Constraints:
  [PASS] src/app.py not modified
  [PASS] No Flask blueprints deleted

Overall: FAIL (1 gate command failed)
```

## Hard rules

1. Run EVERY command in the gate — not just `make quality`
2. Check EVERY "What NOT to do" constraint — not just the obvious ones
3. Report actual numbers vs thresholds (e.g., "98 url_for refs, need >= 134")
4. A single FAIL = the phase is not ready to merge
