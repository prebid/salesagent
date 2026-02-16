# Beads Workflow

## 4-Step Loop

### 1. Find & Review
```bash
bd ready                    # Show tasks ready to work (no blockers)
bd show <id>                # Read full description, acceptance criteria
```

Choose a task based on:
- Priority (P0 > P1 > P2 > P3 > P4)
- Dependencies (prefer unblocking other tasks)
- Logical ordering (setup before implementation)

### 2. Validate Requirements

Before writing code, verify you understand:

**From the task itself:**
- What are the acceptance criteria?
- What does "done" look like?
- Are there dependencies or blocked tasks?

**From CLAUDE.md (7 critical patterns):**
- Does this touch schemas? → Check AdCP pattern (#1)
- Does this add routes? → Check route conflict pattern (#2)
- Does this touch the database? → PostgreSQL only (#3)
- Does this serialize models? → Check nested serialization (#4)
- Does this add a tool? → Shared impl pattern (#5)
- Does this touch JavaScript? → script_root pattern (#6)
- Does this change validation? → Environment-based pattern (#7)

**From existing code:**
- Read the files you'll modify
- Check existing tests for the area
- Look for similar implementations to follow

**Decision checklist before implementing:**
- [ ] I understand the acceptance criteria
- [ ] I've read CLAUDE.md patterns relevant to this task
- [ ] I've read the existing code I'll modify
- [ ] I've checked for existing tests
- [ ] I know what "done" looks like

### 3. Claim & Work
```bash
bd update <id> --status=in_progress
```

Implement following TDD workflow (see tdd-workflow.md):
1. Write failing test
2. Make it pass
3. Refactor
4. Run `make quality`

### 4. Verify & Close

**QC validation before closure:**
- [ ] `make quality` passes
- [ ] Acceptance criteria from task description are met
- [ ] No regressions in existing tests
- [ ] Changes committed with conventional commit message

```bash
bd close <id>
```

## Creating New Tasks

For discovered work:
```bash
bd create --title="..." --type=task|bug|feature --priority=2
```

**Priority scale**: 0=critical, 1=high, 2=medium, 3=low, 4=backlog

For dependent work:
```bash
bd dep add <child-id> <parent-id>    # child depends on parent
```

## Task Status Flow

```
pending → in_progress → completed (via bd close)
```

Use `bd blocked` to see tasks waiting on dependencies.
