---
name: prime
description: >
  Orient a fresh session against the tickets in this worktree before any plan is
  written: read them, ground them in the pinned AdCP version, and measure what
  BDD coverage actually exists. Use at the start of every session in a ticket
  worktree, including after compaction.
---

# Prime

Produces a **falsifiable orientation** for the tickets in this worktree, then
stops. No plan, no code — the point is to know what is true before proposing
anything.

## This skill does not restate the rules

`CLAUDE.md` and `tests/CLAUDE.md` are already loaded in every session. They own
the architecture patterns, the test-integrity policy, the DRY invariant and the
spec-grounding gate. **Do not summarise them here or in your output.**

A second copy of a rule drifts from the first, and drifted guidance in this repo
has repeatedly sent people at the wrong thing: a stale comment blaming
"generic-step shadowing" pointed at a bug fixed weeks earlier and cost about a
week; a ledger mis-cited its own graduation three separate times; a memory note
asserting a macOS/Linux duplication skew caused a legitimate ratchet improvement
to be reverted three times before anyone measured it.

So: rules live in `CLAUDE.md`. This skill produces only what is
**ticket-specific and dynamic** — things that change per ticket and can be
checked.

## Steps

### 1. Read the tickets

```bash
cat TICKETS
gh issue view <n> --repo prebid/salesagent --comments
```

Read the comments, not just the body. In this repo the sharpest constraint is
often in a comment — and several issues carry a `Measured on: <branch> @ <sha>`
header. **If it does, grep that tree, not yours.** Four issues were once
declared false-premise because someone grepped only the checked-out branch; the
symbols were real, on the branch the author measured. Before writing "X does not
exist", run `git log --all -S"X" --oneline`.

### 2. Ground against the pinned AdCP version

Resolve the pin, do not assume it:

```bash
grep -n 'adcp==' pyproject.toml
grep -n 'targets AdCP spec' docs/adcp-spec-version.md
```

Then read the spec at that version — the worktree at `~/projects/adcp` is
usually checked out somewhere else, so always go through `git show`:

```bash
cd ~/projects/adcp && git show v<PIN>:dist/schemas/<PIN>/<area>/<file>.json
cd ~/projects/adcp && git show v<PIN>:dist/docs/<PIN>/building/implementation/<tool>.mdx
```

For protocol behaviour, record the **exact sentence** that mandates it and
whether a conformance storyboard grades it — check
`dist/compliance/<PIN>/` and say "ungraded" if nothing does. The installed SDK
is a cross-check, never the authority.

### 3. Measure the BDD coverage that exists

This is the part nothing else tells you. For the ticket's use case:

```bash
ls tests/bdd/features/BR-UC-*<area>*.feature
grep -c '^  Scenario' tests/bdd/features/BR-UC-<n>-*.feature
grep -n '@T-UC-<n>' tests/bdd/features/BR-UC-<n>-*.feature | head -20
grep -n 'T-UC-<n>' tests/bdd/conftest.py | head          # xfail routing
grep -c '^tests/bdd/' tests/bdd/e2e_rest_known_failures.txt
```

Report **numbers**: how many scenarios, how many currently xfailed and why, how
many sit in the e2e_rest ledger. "N scenarios, M xfailed, 2 ledgered" is
checkable; "there is good coverage" is not.

If the ticket implies graduating an xfail, read
`.claude/rules/workflows/xpass-graduation.md` **now**, in full, before planning.

### 4. Note who will grade the work

Name them and point at their definitions — do not summarise their practices,
for the drift reason above:

```bash
ls .claude/agents/ 2>/dev/null
```

The `code-review` skill fans out reviewers covering DRY, testing, layering,
consistency, AdCP grounding, ratcheting allowlists and BDD grounding. Read the
relevant one's definition before writing code it will judge.

### 5. Note the tools that are not obvious

- `.agent-index/` — generated `.pyi` stubs. Read these **before** scanning the
  tree; a full-tree grep for a symbol they already index is wasted work.
- `ast-grep` for structural queries; plain `grep` for text.
- `saci test {unit,integration,bdd} <path|-k>` runs slices on the CI box.
  `saci run` is the full gate. **`saci run` silently attaches to an in-flight
  run for the same worktree** — if you have just committed, check the box
  actually has your change before trusting a green result.
- `make quality` is the local gate. It rewrites `.duplication-baseline` when
  duplication genuinely drops; that is the ratchet working, not drift.

### 6. Write the orientation down

Into the ticket's bead if one exists, else `.claude/research/<ticket>.md`:

- the spec citation and whether it is graded
- the coverage numbers from step 3
- which files the change will touch
- **the Core Invariant** — one sentence naming what must stay true
- open questions that would change the approach

Then **stop and report**. Do not plan or implement until the orientation is
agreed.

## Anti-patterns

- Restating `CLAUDE.md` instead of measuring this ticket
- Asserting a symbol is absent from one branch's `src/`
- Reporting prose where a count would be checkable
- Planning inside this skill — orientation first, agreement, then a plan
