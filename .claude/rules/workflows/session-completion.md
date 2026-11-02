# Session Completion Checklist

## Before Saying "Done" or "Complete"

Run through this checklist in order:

### Step 1: Check Incomplete Work
```bash
bd ready          # what is workable now
bd show <id>      # detail on anything you touched
```
Review any tasks still in progress. Either complete them or file follow-up issues.

**Do NOT use `bd list` in this repo.** See the warning in
[beads-workflow.md](beads-workflow.md#never-use-bd-list-here) — on a tracker this
size it can balloon the bd process to tens of GB. `bd ready` / `bd show` answer
the same questions safely.

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

**Why this step is not the only thing standing between you and a broken
ratchet — and why it used to be.** `.pre-commit-config.yaml` files the Layer-2
checks (the ratchet counters, docs-links, route-conflicts) under
`stages: [pre-push]`, because the commit stage is capped at 12 fast hooks (D27).
But this page's own workflow is "merged to main **locally**, no `git push`", so
the pre-push stage never fires — and `.github/workflows/ci.yml` triggers only on
`push`/`pull_request` to `main`/`develop`, so the "CI is authoritative" backstop
never fires either. For a long stretch, `make quality` being *documented* here
was the entire enforcement, and counts drifted unnoticed (salesagent-aemue.13).

The ratchets no longer depend on you remembering:

- the fast counters (type-ignore, fixme-citation, admin-raw-session,
  ruff-complexity) run in the unit suite —
  `tests/unit/test_architecture_ratchet_enforcement.py`;
- the two too slow for it (mypy `--check-untyped-defs`, pylint duplication) run
  in the `quality` tox env, which `./run_all_tests.sh` executes with the rest —
  `tests/quality/test_ratchets_slow.py`.

`tests/unit/test_architecture_ratchet_enforcement.py::test_every_ratchet_names_an_enforcement_that_executes`
keeps it that way: a new ratchet filed only under `pre-push` fails until it
names a mechanism that actually runs.

### Step 4: Close Completed Tasks
```bash
bd close <id1> <id2> ...
```
Close all beads tasks that were fully completed this session.

### Step 5: Commit
```bash
git add <specific-files>
git commit -m "feat/fix/refactor: description"
```

There is no beads step here. Do NOT run `bd sync` in any form — the subcommand
does not exist in bd 1.1.2 and errors; JSONL export is automatic, and the tracker
replicates on its own (see [beads-workflow.md](beads-workflow.md)).

**Important**: This is an ephemeral branch. No `git push`. Code is merged to main locally.

### Step 6: Verify Clean State
```bash
git status
bd ready
```
Confirm:
- Working tree is clean (or only has expected untracked files)
- All completed tasks are closed
- Any remaining open tasks have clear descriptions

## Ephemeral Branch Workflow

This project uses ephemeral branches:
- Work happens on feature branches
- Branches are merged to main **locally** (not pushed)
- Nothing needs to be done for beads at commit time — one shared tracker, automatic
  export, automatic replication. Filing is globally visible the moment it happens,
  so `bd sync` and every sync variant are FORBIDDEN; `.claude/agents/executor.md`
  and `.claude/commands/team.md` carry the same prohibition as a hard rule.
- No upstream tracking — don't run `git push`
