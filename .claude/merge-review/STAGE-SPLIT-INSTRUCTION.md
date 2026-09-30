# Owner decision: commit the merge STAGE BY STAGE, not as one atomic commit

Reason: a single 439-file merge commit cannot be partially reverted. If one stage's
resolution turns out wrong, the only options are revert-everything or surgery. Stage
commits make each stage independently revertable.

## Safety net (already in place — do not delete)

`refs/safety/merge-resolution-snapshot` = `097284e087b19603c64b6357cb03bd3814bd28ea`

A two-parent commit whose tree is byte-identical to the full stage-1..7 resolution as it
stood before the split. Recovery, if anything goes wrong:

```bash
git read-tree -m -u refs/safety/merge-resolution-snapshot   # restore the tree
```

## Do this at a quiescent point

Not while the fan-out is writing. Let `merge-behaviour-green` finish first.

## The hazard that has already bitten twice

`git reset` **clears `MERGE_HEAD`**. It has been lost twice already and I restored it both
times. `MERGE_HEAD` must be `e6f79e71a` (tip of `merge/main-into-rfc9421`). Without it the
commit has ONE parent and the entire RFC 9421 epic's ancestry is erased.

```bash
GD=$(git rev-parse --git-dir)          # worktree: .git is a FILE, not a directory
git rev-parse e6f79e71a > "$GD/MERGE_HEAD"
```

## Ordering

The **first** commit made while `MERGE_HEAD` exists becomes the two-parent merge commit;
every commit after it is an ordinary single-parent commit. So:

* commit stage 0 (or 0+1 together) FIRST, with `MERGE_HEAD` present → that is the merge commit;
* verify immediately: `git rev-list --parents -n1 HEAD | wc -w` must print **3**;
* then stages 2, 3, 4, 5, 6 as ordinary commits, ascending.

Intermediate trees will not import or run — that is expected and is exactly why `gate.sh`
has a rising layer ceiling. Do not try to make each stage green in isolation.

## Stage membership

`.claude/merge-review/classification.json` → `manifest` is a 725-entry list, each with
`path`, `stage`, `verdict`, `reason`. Group by `stage` and stage the paths for each.

## Two completeness checks — both mandatory

1. **Nothing dropped.** The manifest is the 725-file *review surface*; the working tree also
   has changed files outside it (`LEDGER.md`, `.claude/merge-review/*` artifacts, anything
   the fan-out touched). After the stage commits, sweep whatever remains into a final
   `chore(merge): review artifacts and ledger` commit. `git status --porcelain` must end empty.

2. **The sum reproduces the whole.** This is the real gate:

```bash
git diff --quiet refs/safety/merge-resolution-snapshot HEAD && echo "SPLIT OK: tree identical" \
  || { echo "SPLIT LOST CONTENT — diff below"; git diff --stat refs/safety/merge-resolution-snapshot HEAD; }
```

If that diff is non-empty the split dropped or altered something. Do not proceed; restore
from the safety ref and retry.

## Commit messages

Each stage commit carries **that stage's section of `LEDGER.md`** in its message — the
resolution ledger the brief asked for (which files were mechanical + the rule applied, which
were semantic + union/subsume + why, and the stage's overturn rate). `LEDGER.md` already has
these per stage; lift them rather than rewriting.
