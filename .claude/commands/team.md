---
name: team
description: Launch a coordinated agent team to work on parallel tasks
arguments:
  - name: prompt
    description: What the team should do (e.g., "fix these 5 bugs in parallel using mol-execute")
    required: true
---

# Launch Agent Team: $ARGUMENTS

## MANDATORY: Use Agent Teams (not independent subagents)

You MUST use the `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS` feature. This means:

1. **Create a team** using the `TeamCreate` tool (load it via ToolSearch first)
2. **Create tasks** for each work item using `TaskCreate`
3. **Spawn teammates** using the `Task` tool with the `team_name` parameter set to the team name
4. **Coordinate** via the team's shared task list — teammates can see each other's progress

### What you must NOT do:
- Do NOT launch independent background subagents without `team_name`
- Do NOT use `run_in_background: true` with standalone Task calls
- These are isolated workers that cannot coordinate — that is NOT a team

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
Analyze the user's prompt and break it into parallel work items. Each work item becomes:
- A task in the team's task list (via TaskCreate)
- A teammate spawned to handle it (via Task with team_name)

### Step 4: Spawn teammates
For each work item, spawn a teammate using:
```
Task:
  team_name: "<team-name>"
  name: "<teammate-name>"
  subagent_type: "general-purpose"
  isolation: "worktree"  (if they modify files)
  prompt: "<detailed instructions>"
```

Use `isolation: "worktree"` when teammates modify overlapping files.

### Step 5: Monitor and coordinate
- Teammates send messages when they complete tasks or need help
- Messages are delivered automatically — no polling needed
- Use SendMessage to communicate with teammates by name
- When all tasks are done, shut down the team via SendMessage with shutdown requests

## User's Request

$ARGUMENTS
