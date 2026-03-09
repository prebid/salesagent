---
name: mol-execute
description: >
  Execute beads tasks through the full lifecycle using molecular workflow.
  Auto-selects formula based on task type: bug tasks use bug-triage, tasks
  needing research/design use task-execute, well-defined tasks use task-single.
  All formulas enforce quality gates and test integrity. Findings stored in
  beads (not filesystem).
args: <task-id-1> [task-id-2] [task-id-3] ...
---

# Task Execution (Molecular)

Molecular workflow for executing beads tasks. Creates an atom graph where each
step is independently executable and survives context compaction. Research
findings are stored in the bead itself, making each task self-contained.

## Args

```
/mol-execute <task-id-1> [task-id-2] [task-id-3] ...
```

One or more beads task IDs. Each task gets atoms chained in sequence.
Multiple tasks execute sequentially.

## Formula Selection

**Auto-select based on task type** — run `bd show <id>` to check the type and
description:

| Task Type | Formula | Atoms per task | When to use |
|-----------|---------|---------------|-------------|
| `bug` | `bug-triage.yaml` | 7 | Bugs that need reproduction and root cause analysis |
| Complex tasks | `task-execute.yaml` | 7 | Tasks needing research, architect review, or TDD (new features, refactors with unknowns) |
| Well-defined tasks | `task-single.yaml` | 3 | Tasks with clear descriptions, known code paths, no design decisions (test rewrites, allowlist fixes, mechanical changes) |

**How to choose between task-execute and task-single:**
- Does the task need **research** (unfamiliar code, unknown APIs)? → `task-execute`
- Does the task need **architect review** (multiple valid approaches)? → `task-execute`
- Does the task need **TDD red-green** (new production code)? → `task-execute`
- Is the task **self-contained** (description says exactly what to do)? → `task-single`
- Is the task **test-only** (rewriting tests, not production code)? → `task-single`
- Is the task **mechanical** (remove from allowlist, update imports)? → `task-single`

**Test-first is enforced in task-execute and bug-triage:**
- Bugs: The `reproduce` atom requires a failing test before the `fix` atom proceeds
- Tasks (task-execute): The `write-test` atom requires a failing regression test before `implement` proceeds
- Refactors: `write-test` guards existing behavior with edge case tests before changes begin

**task-single skips TDD** because the work IS the test — there's no separate
production code to gate on. The execute atom runs `make quality` as the gate.

If a batch contains mixed types, cook separate molecules per formula — don't
mix types in the same epic.

## Protocol

### Step 1: Cook the molecule

**For bugs** (`bd show` shows type=bug):
```bash
python3 .claude/scripts/cook_formula.py \
  --formula .claude/formulas/bug-triage.yaml \
  --var "BUG_IDS={all_args}" \
  --epic-title "Bug triage: {all_args}"
```

**For well-defined tasks** (test rewrites, allowlist fixes, mechanical changes):
```bash
python3 .claude/scripts/cook_formula.py \
  --formula .claude/formulas/task-single.yaml \
  --var "TASK_IDS={all_args}" \
  --epic-title "Execute: {all_args}"
```

**For complex tasks** (needs research, design, or TDD):
```bash
python3 .claude/scripts/cook_formula.py \
  --formula .claude/formulas/task-execute.yaml \
  --var "TASK_IDS={all_args}" \
  --epic-title "Execute: {all_args}"
```

This creates an epic with atoms and dependencies. The output shows the epic ID
and all created atoms.

**Dry run first** (recommended):
```bash
python3 .claude/scripts/cook_formula.py \
  --formula .claude/formulas/<formula>.yaml \
  --var "<VAR>={all_args}" \
  --epic-title "<title>" \
  --dry-run
```

### Step 2: Walk the molecule

Execute atoms one at a time:

```
bd ready  →  bd show <atom-id>  →  read description  →  execute  →  bd close <atom-id>  →  repeat
```

Each atom's description is self-contained:
- **Instructions**: Exactly what to do
- **Preconditions**: What to verify before starting
- **Acceptance Criteria**: How to verify the atom is done

If context compacts mid-workflow: `bd ready` picks up where you left off.

**Gate labels** (formula-dependent):
- task-execute: `research:complete` on the task before review proceeds
- bug-triage: `reproduce:complete` on the bug before trace-similar proceeds

**Triage routing** (same for both formulas):
- ALL_LOW → implement/fix proceeds
- NEEDS_REFINEMENT → spawns refine atom (agent handles autonomously)
- NEEDS_USER_INPUT → blocks for human direction

### Step 3: Done when all atoms closed

All atoms closed means the epic is complete. The finalize atom syncs beads and
verifies clean state.

## Research Storage

Research goes into the bead, not the filesystem:

| Content | Bead Field | Command |
|---------|-----------|---------|
| Findings, spec verification, relevant code, risks | `notes` | `bd update <id> --append-notes "..."` |
| Architecture decisions, implementation plan | `design` | `bd update <id> --design "..."` |
| Research complete gate | label | `bd label add <id> research:complete` |

When any atom needs to read research: `bd show <task-id>` returns everything.

## Core Invariant

Every task requires a **Core Invariant** — one sentence stating the architectural
principle all changes must preserve. Research extracts it, review validates it,
implementation checks against it on every file modification.

When existing tests fail during implementation, the invariant is your first
diagnostic: "Does this failure mean I violated the invariant?" If yes, revert
and rethink. Never adjust tests to fit code without documented justification.

## Anti-Patterns

- Don't skip atoms (even trivial ones like commits or e2e-verify)
- Don't combine atoms (defeats crash recovery)
- Don't hold workflow state in memory (it's in beads)
- Don't store research on filesystem (it goes in the bead)
- Don't proceed past review without the gate label (`research:complete` or `reproduce:complete`)
- Don't apply fixes inside the triage atom (triage routes, doesn't execute)
- Don't re-research in the refine atom (use existing findings, only adjust approach)
- Don't modify existing tests without first checking the Core Invariant
- Don't execute plan steps mechanically — validate each against the invariant
- Don't mix bug and non-bug tasks in the same epic (use separate cooks)
- Don't skip the trace-similar atom for bugs — it catches systemic issues
- Don't refactor surrounding code in the fix atom — fix the bug only
- Don't skip write-test for tasks — no test = no gate on correctness
- Don't combine write-test and implement into one step — the test must fail BEFORE implementation starts
- Don't write tests that pass immediately (unless guarding existing behavior in a refactor)
- Don't use pytest.mark.xfail in regression tests — xfail is NOT a failing test
- Don't use AST/source-code scanning as regression tests — test behavior, not code text
- Don't write unit tests when an integration test is feasible — mock-heavy tests are echo chambers
- Don't report "done" if the regression test never produced a FAILED output
- Don't substitute `make quality` for `scripts/run-test.sh` when iterating — use the targeted runner
- Don't substitute `scripts/run-test.sh` for `./run_all_tests.sh` in the finalize atom — the full suite is mandatory

## Test Integrity — ZERO TOLERANCE

**Read and follow CLAUDE.md "Test Integrity Policy" and `.claude/rules/patterns/testing-patterns.md` "Test Integrity" sections.**

These rules apply to EVERY atom, not just finalize:

- **NEVER** use `--ignore`, `-k "not ..."`, `--deselect` to skip failing tests
- **NEVER** rationalize failures as "pre-existing", "infrastructure issue", "misplaced test", "needs a running server", or "was deselected in full run"
- **NEVER** report success while skipping tests — this is the #1 failure mode
- If a test needs Docker → `./run_all_tests.sh` starts everything automatically
- If infrastructure is broken → STOP and report as blocker, do NOT skip
- Test results persist as JSON in `test-results/<ddmmyy_HHmm>/` — use these to review results instead of re-running

## See Also

- `/guard` — Create structural guards that prevent architecture violations on `make quality`
- `/surface` — Create entity test suites with complete obligation mapping (run before remediation)
- `/remediate` — Fill entity test stubs batch-by-batch with TDD
- `/audit` — Repeatable code review against #1050/#1066 architecture principles
