---
name: mol-execute
description: >
  Execute beads tasks through the full lifecycle using molecular workflow.
  Creates a crash-recoverable atom graph: research → architect review → triage
  → implement → commit. All research stored in beads (not filesystem).
  Use for any beads task that needs research before implementation.
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

One or more beads task IDs. Each task gets 5 atoms (research, review, triage,
implement, commit) chained in sequence. Multiple tasks execute sequentially.

## Protocol

### Step 1: Cook the molecule

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
  --formula .claude/formulas/task-execute.yaml \
  --var "TASK_IDS={all_args}" \
  --epic-title "Execute: {all_args}" \
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

**Research gate**: The review atom won't proceed unless `research:complete` label
is on the task AND notes/design fields are populated. This prevents rushing into
implementation without proper research.

**Triage routing**:
- ALL_LOW → implement proceeds
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

- Don't skip atoms (even trivial ones like commits)
- Don't combine atoms (defeats crash recovery)
- Don't hold workflow state in memory (it's in beads)
- Don't store research on filesystem (it goes in the bead)
- Don't proceed past review without `research:complete` label
- Don't apply fixes inside the triage atom (triage routes, doesn't execute)
- Don't re-research in the refine atom (use existing findings, only adjust approach)
- Don't modify existing tests without first checking the Core Invariant
- Don't execute plan steps mechanically — validate each against the invariant
