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

Each work item becomes:
- A task in the team's task list (via TaskCreate)
- An executor teammate spawned to handle it (via Task with team_name)

### Step 4: Spawn executor teammates
For each work item, spawn an executor:
```
Task:
  team_name: "<team-name>"
  name: "executor-<short-id>"
  subagent_type: "executor"
  prompt: |
    Execute beads task salesagent-<id>.

    Run `bd show <id>` for full description and acceptance criteria.
    Follow the executor protocol: setup DB, cook molecule, walk atoms,
    quality gates, commit.
```

**NOTE: `isolation: "worktree"` is a no-op for team agents.** All executors
share the same working directory and branch. Ensure parallel executors touch
non-overlapping files to avoid conflicts.

### Step 5: Monitor and coordinate
- Executors send messages when they complete tasks or get stuck
- Messages are delivered automatically — no polling needed
- Use SendMessage to communicate with executors by name
- When all executors report done, review their commits on the current branch

### Step 6: Verify and commit
After all executors complete:
1. Run `make quality` on the combined result
2. Squash or organize commits if needed
3. Push if the user requests it

## User's Request

$ARGUMENTS
