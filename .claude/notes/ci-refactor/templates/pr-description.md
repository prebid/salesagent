# PR Description Template

Use this template for every PR in the CI/pre-commit refactor. Filled-in versions live alongside their per-PR spec; the executor produces a final filled-in copy at the end of execution.

---

```markdown
## Scope

<One paragraph: what this PR does, why it exists, what it does NOT do.>

## Drift catalog items closed

Closes (partial) #1234:
- PD<n> — <one-line summary>
- PD<m> — <one-line summary>

(Use `closes` only on the FINAL PR of the rollout; intermediate PRs use `Refs #1234`.)

## Changes by file

- `<path/to/file>` — <one line>
- `<another/path>` — <one line>

## Acceptance criteria

(From issue #1234 §Acceptance criteria, scoped to this PR.)

- [ ] <criterion 1>
- [ ] <criterion 2>

## Verification

```bash
# Commands the reviewer can run locally
<verification commands from spec>
```

Output of `make quality` on this branch:
```
<paste output>
```

## Risks

(From `.claude/notes/ci-refactor/02-risk-register.md`, scoped to this PR.)

- **R<n>**: <description> — mitigation: <text>; rollback: <text>

## Rollback plan

(Each step is a literal command the reviewer or maintainer can execute.)

1. `git revert -m 1 <merge-sha>`
2. <next step if needed>
3. ...

If branch protection mutations are part of rollback, list them explicitly with the
`gh api` command and required token scope.

## Out of scope

- <bullet>
- <bullet>

Follow-up issues filed for deferred items:
- <issue ref or "none">

## Merge tolerance

(Specifies which concurrent PRs this PR can rebase against without semantic conflict.)

- **PR #1217 (adcp 3.12)**: <tolerated / requires merge first / requires rebase>
- **v2.0 phase PR landing on `<file>`**: <tolerated / requires rebase / blocks>

## Decisions referenced

- D<n>: <one-line decision title from 03-decision-log.md>
- D<m>: ...

## Coordination notes for the maintainer

(Anything the reviewer needs to do OUTSIDE the code review — branch-protection
flips, GitHub UI toggles, manual `gh api` calls. None of these are part of the
diff but they're load-bearing for the PR's effect.)

- <step>
- <step>

Refs #1234
```

---

## Notes on filling this in

- **Scope paragraph** should be 2-3 sentences max. Reviewer reads this first; if it doesn't tell them what's happening, the rest is hard to read.
- **Drift catalog items** map to the issue's PD1-PD25 numbers. Not every PR closes specific PDs — some are infrastructure (e.g., "set up the framework that PR 4 uses").
- **Verification block** must be runnable. No pseudo-code, no `<TODO>`. If the verification depends on a non-trivial setup (Docker stack, Postgres), say so explicitly.
- **Rollback plan** must be specific. "Revert the PR" is not enough if the PR mutates branch protection or repo settings.
- **Merge tolerance** matters because PR #1217 and v2.0 phase PRs are concurrent. If your PR breaks when one of them merges, say so up front.
- **Coordination notes** are the difference between "PR merged" and "rollout effective." Branch-protection settings, repo toggles, etc. live here.
