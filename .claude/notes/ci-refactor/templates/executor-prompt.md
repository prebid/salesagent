# Executor Agent Prompt Template

Copy this template when launching an executor agent for any of the 5 PRs in the CI/pre-commit refactor. Replace `<N>`, `<slug>`, and the bracketed sections with PR-specific content from the corresponding spec.

---

```
You are implementing PR <N> of the CI/pre-commit refactor (issue #1234) in
salesagent. This is a self-contained task; you have no prior conversation
context. Read everything before writing code.

## Read in order

1. `.claude/notes/ci-refactor/pr<N>-<slug>.md`  — your spec; the source of truth
2. `.claude/notes/ci-refactor/03-decision-log.md`  — every locked decision
3. `CLAUDE.md`  — codebase patterns; NON-NEGOTIABLE
4. `.claude/rules/workflows/quality-gates.md`  — local quality bar
5. `.claude/rules/patterns/testing-patterns.md`  — test integrity policy

## Scope statement

<one paragraph from spec §Scope>

## Internal commit sequence

Each entry below becomes one git commit. ORDER IS LOAD-BEARING. Do not
batch or reorder.

1. <commit subject 1>
   - Files: <list>
   - Verification: <bash one-liner that exits 0 on success>
   - Acceptance: <bullet from spec>

2. <commit subject 2>
   ...

(Copy from spec §"Internal commit sequence")

## Out of scope (do NOT touch)

- <bullet list from spec>
- Any file under `src/admin/` (Flask-to-FastAPI v2.0 territory; PR #1221 owns)
- Any file under `.guard-baselines/` (v2.0 territory)

## Verification (run after EACH commit)

```bash
make quality
bash .claude/notes/ci-refactor/scripts/verify-pr<N>.sh   # if exists
```

After all commits:
```bash
./run_all_tests.sh
```

## Commit message style

- Conventional Commits format (feat/fix/refactor/chore/docs/ci/test/perf)
- Subject ≤ 72 chars
- Body explains WHY, not WHAT
- Reference issue: `Refs #1234` (NOT `Fixes #1234` — PR doesn't close the issue alone)
- NO trailing "Co-Authored-By" line unless the user has explicitly enabled it

## Escalation triggers — STOP and report to the user if any occur

- <PR-specific triggers from spec §"Escalation triggers">
- A test fails that you cannot diagnose in 15 minutes
- An acceptance criterion cannot be met as written (the spec is wrong, not the code)
- A locked decision in 03-decision-log.md appears to be wrong given new evidence
- The code requires editing files in §"Out of scope"
- Any third-party network call (PyPI, GitHub) fails repeatedly

When escalating: write your findings to a scratch file under
`.claude/notes/ci-refactor/escalations/pr<N>-<topic>.md` and STOP.

## Git workflow

- Branch from latest main: `git checkout -b feat/ci-refactor-pr<N>-<slug>` (or
  `fix/`, `refactor/`, `chore/` per Conventional Commits prefix)
- Make the commits IN ORDER per §"Internal commit sequence"
- Do NOT push to origin (the user owns push)
- Do NOT run `gh pr create` (the user owns PR creation)
- Do NOT run any branch-protection mutation (`gh api -X PATCH branches/main`)
- Do NOT amend or rebase commits once made (one commit per change)
- Do NOT use `--no-verify` on any commit; if pre-commit fails, fix and re-commit

## Final deliverable

When all commits land cleanly on the local branch:

1. Run the full verification block
2. Generate the PR description from `.claude/notes/ci-refactor/templates/pr-description.md`
   (filled in with this PR's specifics)
3. Report a summary in this format:

```
PR <N> ready for user review.
Branch: feat/ci-refactor-pr<N>-<slug>
Commits: <count>
Drift items closed: PD<n>, PD<m>
Acceptance status:
  - <criterion 1>: ✓
  - <criterion 2>: ✓
  ...
Open questions: <list any deferred items>
PR description: <path to filled-in template>
```

DO NOT push or open the PR. The user owns those steps.
```

---

## Notes for the operator (not the agent)

- The executor agent runs in a fresh session each time. Each PR's executor has no memory of previous PRs.
- If the executor stops on an escalation, read `.claude/notes/ci-refactor/escalations/pr<N>-<topic>.md` and decide: amend the spec, answer the question, or pivot.
- The executor cannot resolve concurrent merges with PR #1217 or v2.0 phase PRs. If those happen during execution, you (the operator) rebase manually and resume the executor.
- The executor does not run `gh api` calls that mutate state. Per `.claude/notes/ci-refactor/01-pre-flight-checklist.md`, those are admin actions you handle.
