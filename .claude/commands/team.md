---
name: team
description: Launch a coordinated agent team to work on parallel tasks
arguments:
  - name: prompt
    description: What the team should do (e.g., "execute gc5w, 6736, to9i in parallel")
    required: true
---

# Launch Agent Team: $ARGUMENTS

## MANDATORY: Use Agent Teams with Executor Agents

You MUST use the `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS` feature with the
**executor** agent type (`.claude/agents/executor.md`). Executors are
self-contained: they set up their own Postgres container, follow mol-execute
workflow, run quality gates, and commit their changes.

### What you must NOT do:
- Do NOT launch independent background subagents without `team_name`
- Do NOT use `run_in_background: true` with standalone Task calls
- Do NOT use `subagent_type: "general-purpose"` for beads task execution — use `executor`

## Protocol

### Step 1: Load team tools
```
ToolSearch: "select:TeamCreate"
ToolSearch: "select:TaskCreate"
ToolSearch: "select:SendMessage"
```

### Step 2: Create the team
```
TeamCreate: team_name="<descriptive-name>", description="<what the team does>"
```

### Step 3: Plan the work
Analyze the user's prompt and break it into parallel work items. For beads
task IDs, run `bd show <id>` to read descriptions and verify they're unblocked.

**Grouping strategy:**
- Group tasks that touch the **same file** into one executor (avoids merge conflicts)
- Each executor gets one file or a small set of non-overlapping files
- The allowlist (`obligation_test_quality_allowlist.json`) is a shared resource —
  coordinate updates or do a reconciliation pass after all executors finish

**Formula selection:**
- If the user specifies a formula, use it
- Otherwise, check each task with `bd show <id>`:
  - Well-defined tasks (test rewrites, allowlist fixes, mechanical changes) → `task-single.yaml`
  - Tasks needing research or TDD (new features, refactors) → `task-execute.yaml`
  - Bugs → `bug-triage.yaml`

Each work item becomes:
- A task in the team's task list (via TaskCreate)
- An executor teammate spawned to handle it (via Task with team_name)

### Step 4: Spawn executor teammates
For each work item, spawn an executor with an **explicit formula and cook command**:
```
Task:
  team_name: "<team-name>"
  name: "executor-<short-label>"
  subagent_type: "executor"
  prompt: |
    Execute these beads tasks using <formula> formula:
    salesagent-<id1> salesagent-<id2> salesagent-<id3>

    Cook:
    python3 .claude/scripts/cook_formula.py \
      --formula .claude/formulas/<formula>.yaml \
      --var "TASK_IDS=salesagent-<id1> salesagent-<id2> salesagent-<id3>" \
      --epic-title "<descriptive title>"

    Then walk atoms: bd ready → bd show <id> → execute → bd close <id> → repeat

    Your files: <list of files this executor owns>
    Shared resource: <any shared files like allowlists>
```

**NOTE: `isolation: "worktree"` is a no-op for team agents.** All executors
share the same working directory and branch. Ensure parallel executors touch
non-overlapping files to avoid conflicts.

### Step 5: Monitor, verify each executor, iterate

When an executor reports "done":

1. **Read the task's `## Verify` section** (from `bd show <id>`)
2. **Ask the executor to run the verify command and paste raw output.**
   Use SendMessage: "Run this and paste the FULL output: `<verify command>`"
3. **Check the output yourself.** Does it match the expected result?
   - `0 failed` where the task promised 0 failed?
   - The specific error pattern is gone?
   - No new regressions introduced?
4. **If verification fails → send the executor back.**
   "Verification failed: output shows X but expected Y. Fix and re-verify."
5. **Only accept completion when verification passes.**

If a task has no `## Verify` section, ask the executor:
"What command proves this task is done? Run it and paste the output."

**DO NOT trust self-reported success.** The executor saying "all tests pass"
is not verification. Seeing the actual command output IS verification.

### Step 6: Verify combined result
After all executors pass individual verification:
1. Run `./run_all_tests.sh` on the combined result
2. Review JSON results in `test-results/<ddmmyy_HHmm>/`
3. If any new failures appear in the combined run that weren't in individual
   runs, identify which executor's changes caused them and send back to fix
4. Push if the user requests it

**Test Integrity — ZERO TOLERANCE**: If any test fails in the combined result,
do NOT skip it or rationalize it. See CLAUDE.md "Test Integrity Policy".
Every failure must be fixed or reported as a blocker.

## User's Request

$ARGUMENTS
