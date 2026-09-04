---
name: ticket-start
description: >
  Start work on one or more GitHub tickets: branch off freshly-fetched
  origin/main, create a sibling worktree, and give it an isolated beads
  database. Use when picking up a ticket or a small batch of related tickets.
  Follow with /prime to orient in the new worktree.
---

# Ticket Start

Sets up the workspace for a ticket. Does no research and writes no code — that
is `/prime`'s job, and it runs in the new worktree where the context belongs.

## Usage

```bash
.claude/skills/ticket-start/ticket-start.sh 1776
.claude/skills/ticket-start/ticket-start.sh 1776 1781      # a related batch
```

The first ticket names the branch and the worktree. Pass several only when they
are genuinely one change — otherwise start them separately so each gets its own
PR.

## What it does

1. **Validates every ticket first.** A typo'd number fails before anything is
   created, so you never end up with a half-made worktree named after nothing.
2. **Fetches `origin/main` and branches off that** — not local `main`, which is
   stale in every worktree in this repo and is the usual cause of a "why is this
   diff so large" PR.
3. **Creates a sibling worktree** at `../salesagent-<ticket>`.
4. **Seeds an isolated beads DB** into `.beads-local/` with `sqlite3 .backup`.
   `cp` is wrong here: the shared DB is normally live with a WAL sidecar and a
   plain copy can capture a torn page.
5. **Writes `.envrc`** exporting `BEADS_DB`, with the mirror-back command baked
   in — see the warning below.
6. **Writes `TICKETS`** so `/prime` needs no arguments.

## The beads warning, because it has already cost work

`bd` resolves its database through `git-common-dir`, so without isolation every
worktree writes the **shared** database and parallel agents clobber each other.
That is why this exists.

But isolated is not the same as disposable. Tickets you create or close in a
worktree live **only** in that worktree until mirrored. On PR #1728 six tickets —
including the entire deferral record for one ticket — existed nowhere else and
were caught by hand minutes before teardown.

So `.envrc` carries the mirror command, and you run it before you finish:

```bash
bd export --id "<ids>" -o /tmp/beads-mirror-<ticket>.jsonl
cd <main-checkout> && env -u BEADS_DB bd import -i /tmp/beads-mirror-<ticket>.jsonl
```

`env -u BEADS_DB` is load-bearing. `cd` alone does **not** escape the exported
variable — you will silently write back into the worktree DB you were trying to
escape.

## What it deliberately does not do

- **Remove worktrees.** Never scripted. Removing a worktree is a decision with
  no undo; do it yourself when you are sure.
- **Touch the shared `.beads/`.** It reads it to seed, and never writes it.
- **Run `bd sync`.** It is destructive in this repo and has lost tickets before.

## Next

```
cd ../salesagent-<ticket>
/prime
```
