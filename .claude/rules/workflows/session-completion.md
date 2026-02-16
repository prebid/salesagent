# Session Completion Checklist

## Before Saying "Done" or "Complete"

Run through this checklist in order:

### Step 1: Check Incomplete Work
```bash
bd list --status=in_progress
```
Review any tasks still in progress. Either complete them or file follow-up issues.

### Step 2: File Issues for Remaining Work
For anything discovered but not completed:
```bash
bd create --title="..." --type=task --priority=2
```
Include enough context for the next session to pick up the work.

### Step 3: Run Quality Gates
```bash
make quality
```
All checks must pass. If they fail, fix the issues before committing.

### Step 4: Close Completed Tasks
```bash
bd close <id1> <id2> ...
```
Close all beads tasks that were fully completed this session.

### Step 5: Commit and Sync
```bash
git add <specific-files>
bd sync --from-main
git commit -m "feat/fix/refactor: description"
```

**Important**: This is an ephemeral branch. No `git push`. Code is merged to main locally.

### Step 6: Verify Clean State
```bash
git status
bd list --status=open
```
Confirm:
- Working tree is clean (or only has expected untracked files)
- All completed tasks are closed
- Any remaining open tasks have clear descriptions

## Ephemeral Branch Workflow

This project uses ephemeral branches:
- Work happens on feature branches
- Branches are merged to main **locally** (not pushed)
- `bd sync --from-main` pulls beads updates from main before final commit
- No upstream tracking — don't run `git push`
