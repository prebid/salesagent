# In-flight

Per `templates/executor-prompt.md` Rule 15, executors capture in-progress state to
`in-flight/<pr-N>-<commit-N>.md` before any risky operation (Phase B flip, hook deletion
sweep, autoupdate-freeze, ADR commits). This is the rollback contract.

## File naming

`<pr-N>-<commit-N>.md` — examples:
- `pr3-commit5.md` (PR 3 commit 5 in-flight)
- `pr4-commit5.md` (PR 4 hook-deletion sweep in-flight)
- `pr1-commit8.md` (PR 1 autoupdate-freeze in-flight)

## File contents

Each in-flight file should contain, in this order:

1. **Pre-operation git SHA + branch** — `git rev-parse HEAD` + `git branch --show-current`.
2. **Pre-operation snapshot of mutated files** — paths + `git hash-object <file>` per file
   that the operation will touch.
3. **The operation about to be performed** — exact command(s) or the spec section being executed.
4. **The rollback command** — the literal git command to restore state if the operation fails.

## Lifecycle

- **Created** — before the risky operation begins.
- **Updated** — after each successful step (cumulative log of completed sub-steps).
- **Archived** — when the PR merges, move to `in-flight/archived/<filename>` with a
  one-line completion note appended.
- **Used for rollback** — if the operation fails partway, the file IS the rollback recipe.

## Why this directory exists

The in-flight directory and the rollback-contract pattern were specified in
executor-prompt.md Rule 15 but the directory itself didn't exist on disk until the
2026-04-25 P0 sweep. This closes the "directory doesn't exist; first capture will ENOENT" gap.
