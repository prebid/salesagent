# Beads Workflow

## Read this first: four commands you must not run

The tracker setup changed on 2026-08-18. It is simpler now — one local Dolt server
per project, worktrees sharing the parent's via `.beads/redirect`, automatic JSONL
export, and automatic replication to an off-machine hub every 5 minutes. You never
run anything to sync. Four things will bite:

### NEVER use `bd list` here
On a tracker this size (~6600 issues) `bd list` can balloon the bd process to tens
of GB and take the machine down with it. This is a bug in bd, not a misuse.
Use `bd ready` (workable now), `bd show <id>` (one issue in full), `bd blocked`,
or `bd stats` (counts only) — they answer the same questions for normal cost.

### NEVER run `bd sync`
The subcommand **does not exist** in bd 1.1.2 — not `--from-main`, not
`--flush-only`. It errors. Older instructions across this repo's `.claude/` tree
still tell you to run it; **that instruction is stale, ignore it.** JSONL export is
automatic; the explicit form, if you ever need it, is
`bd export -o .beads/issues.jsonl`.

### NEVER run `bd dolt push` / `bd dolt pull`
Both fail with `Access denied for user 'root'`. Dolt's server-side push path
hardcodes user `root` with an empty password and ignores `DOLT_REMOTE_PASSWORD`.
Replication already happens without you.

### NEVER enable `export.git-add`
`.beads` is gitignored here, so bd's own `git add` is refused and **every bd write
fails** while reads keep working — it presents as "bd create is broken". It is
deliberately `false`; `export.auto` stays `true`. If you need the JSONL staged, do
it from the pre-commit hook.

## If bd looks wrong

If bd reports "No issues found" or `database "..." not found`, **do not create
anything** — you may be talking to an empty database that bd created silently.
`.beads/metadata.json` is the only pointer to which database bd opens, it is
git-tracked, and a branch checkout can revert it. In a worktree a redirect needs
BOTH `.beads/redirect` present AND `metadata.json` ABSENT. Diagnose with:

```bash
cat .beads/metadata.json 2>/dev/null    # in a worktree: should NOT exist
ls .beads/redirect                      # in a worktree: SHOULD exist
bd stats                                # sanity: total should be in the thousands
```

Full rationale: `ox-troubleshooting-demo/docs/adr/0018-beads-topology-local-servers-with-a-replication-hub.md`
and `ox-troubleshooting-demo/docs/runbooks/beads-operations.md`.

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

**From CLAUDE.md (9 critical patterns):**
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
