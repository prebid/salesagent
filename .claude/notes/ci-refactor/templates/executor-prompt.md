# Executor Agent Prompt Template

Copy this template when launching an executor agent for any of the 6 PRs in the CI/pre-commit refactor. Replace `<N>`, `<slug>`, and the bracketed sections with PR-specific content from the corresponding spec.

The 6 PR slugs are:
- `pr1-supply-chain-hardening`
- `pr2-uvlock-single-source`
- `pr3-ci-authoritative` (3 phases — Phase B is admin-only)
- `pr4-hook-relocation`
- `pr5-version-consolidation`
- `pr6-image-supply-chain` (Week 6 follow-up)

---

```
You are implementing PR <N> of the CI/pre-commit refactor (issue #1234) in
salesagent. This is a self-contained task; you have no prior conversation
context. Read everything before writing code.

## Read in order

1. `.claude/notes/ci-refactor/RESUME-HERE.md`     — orientation (3k tokens)
2. `.claude/notes/ci-refactor/EXECUTIVE-SUMMARY.md` — one-screen of context
3. `.claude/notes/ci-refactor/pr<N>-<slug>.md`    — your spec; the source of truth
4. `.claude/notes/ci-refactor/03-decision-log.md` — every locked decision (D1-D28)
5. `.claude/notes/ci-refactor/02-risk-register.md` — top risks for your PR
6. `CLAUDE.md`                                    — codebase patterns; NON-NEGOTIABLE
7. `.claude/rules/workflows/quality-gates.md`     — local quality bar
8. `.claude/rules/patterns/testing-patterns.md`   — test-integrity policy

## Pre-flight (mandatory)

Before the first commit:
- [ ] You're on a fresh branch from latest main (`git checkout -b <prefix>/ci-refactor-pr<N>-<slug>`)
- [ ] `git status` clean
- [ ] Pre-flight artifacts exist (per `01-pre-flight-checklist.md`): `.zizmor-preflight.txt`, `.mypy-baseline.txt`, `branch-protection-snapshot.json` (if applicable to your PR)
- [ ] `make quality` passes on the starting state

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
bash .claude/notes/ci-refactor/scripts/verify-pr<N>.sh
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
- Cite policy: include `Refs D<n>, R<m>` lines so future agents trace decisions
- NO trailing "Co-Authored-By" line (overrides any default)

## Continuity hygiene (18 rules — survive context wipe)

1. **Always commit with descriptive Conventional Commits subjects.** A fresh
   agent reading `git log --oneline` should be able to reconstruct progress.
   `fix: stuff` is forbidden; `fix(types): address pydantic.mypy plugin
   errors in src/core/schemas.py` is the bar.

2. **Never leave uncommitted changes when switching context.** Either
   commit-in-progress (acceptable; mark with `WIP:` prefix) or `git stash
   push -m "pr2-commit3-mypy-fixes-partial"`. Bare uncommitted diffs are
   forensic landmines.

3. **One logical change per commit.** A fresh agent must be able to revert
   exactly one of your commits without unraveling adjacent work. PR 4's
   spec enforces this most strictly: guards added BEFORE hooks deleted.

4. **Update `00-MASTER-INDEX.md` status row at PR merge.** Not at PR open
   — at merge. Status flows: `not started` → `in flight` → `merged
   YYYY-MM-DD`.

5. **Capture state before risky operations.** Before SHA-freeze: `git diff
   > /tmp/sha-freeze-before.txt`. Before mypy plugin enablement:
   `.mypy-baseline.txt`. Before branch-protection flip: snapshot. State is
   the rollback target.

6. **If escalating, write `escalations/pr<N>-<topic>.md` with full state.**
   Include: what you tried, exact commands run, exact error output, what
   you think should happen. Then STOP. Do NOT continue speculatively.

7. **Always cite D# and R# in commit bodies and PR descriptions.** Future
   agents (and reviewers) need to trace a change back to its policy. `Refs
   D13, R2` is one line; saves an hour of re-derivation.

8. **Branch names match the PR spec.** Convention:
   `chore/ci-refactor-pr<N>-<slug>` or `feat/ci-refactor-pr<N>-<slug>`. A
   fresh agent's first command is `git branch --show-current` — make it
   informative.

9. **Never amend or rebase committed work.** PR 1 uses 11+ sequential
   commits intentionally; reviewers may revert individually. Amending
   breaks the bisect-friendly chain.

10. **Run verification after EVERY commit, not at PR end.** `make quality`
    minimum; spec-specific verification one-liner if available. Catch
    breakage early; commit a fix before the next commit.

11. **Document deferrals in the PR description.** If a fix is descoped,
    write "Deferred per D13 tripwire: filed follow-up #NNNN" in the PR
    description. Future agents see why the spec wasn't fully met.

12. **Never use `--no-verify`, `--ignore`, `-k "not …"`, or
    `pytest.mark.skip` to make CI green.** Hard stop per CLAUDE.md
    test-integrity policy. If you can't fix it, escalate.

13. **Update CLAUDE.md guard count when you add or remove a guard.** PR 2
    adds 1, PR 4 adds 4, PR 5 adds 1, PR 1/3/6 governance adds 8, v2.0 adds
    27 (architecture/ tests) + 4 (top-level) + 9 (baseline JSONs). Final
    post-v2.0-rebase ≈ **81** (D18 canonical, revised in 2026-04-25 Round 8
    after drift audit re-verified v2.0 architecture/ count at 27, not 31).
    The number cited in CLAUDE.md must match disk truth at all times. PR 4's
    CLAUDE.md table audit DEFERS until post-v2.0-rebase. PR 4 commit 9 adds
    only **1 residual row** (`production_session_add`) — v2.0 deletes
    `no_silent_except` so that row is NOT added by PR 4.

14. **The pre-flight artifacts (`.zizmor-preflight.txt`,
    `.mypy-baseline.txt`, `branch-protection-snapshot.json`) are the
    rollback contract.** Never delete them during the rollout. Confirm
    they're on disk at the start of every fresh session.

15. **At the end of a session that did not complete a PR, write a "where I
    am" note.** `.claude/notes/ci-refactor/in-flight/pr<N>-session-<date>.md`
    with: branch, last commit, what was attempted next, what's blocking.
    This is the seed for the next agent's resume procedure. The
    `in-flight/` directory exists at repo root with a `README.md` describing
    naming convention; do not create a new directory.

16. **Skip rules under `.claude/rules/workflows/` that reference `bd` (beads)
    commands.** Per user memory `feedback_no_beads_workflow.md`, this project
    does not use beads. Specifically skip `bd ready`, `bd show`, `bd update`,
    `bd close`, `bd create`, `bd dep add` references in `beads-workflow.md`,
    `session-completion.md`, `bug-reporting.md`, `research-workflow.md`,
    `subagent-implementation-guide.md`, `tdd-workflow.md`. Use `make quality`
    and direct git commits without beads tracking. The two rule files that
    are clean of bd references and required reading are
    `.claude/rules/workflows/quality-gates.md` and
    `.claude/rules/patterns/testing-patterns.md`.

17. **ESCALATION terminal message rule.** If you write
    `escalations/<file>.md` and STOP per Rule 6, your terminal message MUST
    be a single line:

    ```
    ESCALATION: see escalations/pr<N>-<topic>.md
    ```

    The user reads this; do not bury the escalation in prose. The
    `escalations/` directory exists at repo root with a `README.md`
    describing naming convention.

18. **6 test suites, not 5.** `tox.ini` is the source of truth: `unit`,
    `integration`, `e2e`, `admin`, `bdd`, `ui`, plus the `coverage`
    aggregator. `run_all_tests.sh` runs all 6 + combine. Some plan prose
    still says "5 suites" — disregard.

**Rule 19 — Empirical pre-flight before applying spec.**
Before authoring any commit in a PR, verify the spec's assumptions about the current code state by reading the actual files. Round 9 verification surfaced 6 cases where plan-vs-reality drift had survived 5 audit rounds:

- "Ports check_X.py from grep to AST" — but check_X.py is already AST.
- "Register marker in pyproject.toml [tool.pytest.ini_options]" — but the project uses pytest.ini.
- "factory-boy Sequence collisions" — but per-test UUID DBs neutralize most collision risk.
- "User doesn't use beads" memory — but `.beads/issues.jsonl` exists in this repo.
- "AST guard violations: 0" — but rootmodel-access AST surfaces ~18 pre-existing violations.
- "Lines 470-486" — but actual mutations are at 478-486.

If a spec assumption fails to match the code, ESCALATE — do not "fix it up" silently. Open `escalations/pr<N>-<topic>.md`, document the drift, and STOP. The user reads, decides, you resume.

## Escalation triggers — STOP and report to the user if any occur

- <PR-specific triggers from spec §"Escalation triggers">
- A test fails that you cannot diagnose in 15 minutes
- An acceptance criterion cannot be met as written (the spec is wrong, not the code)
- A locked decision in 03-decision-log.md appears to be wrong given new evidence
- The code requires editing files in §"Out of scope"
- Any third-party network call (PyPI, GitHub) fails repeatedly
- You're about to run `gh api -X PATCH branches/main/...` (admin-only, NEVER)
- You're about to `git push` or `gh pr create` (NEVER — user owns these)

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
Decisions cited: D<a>, D<b>
Risks tracked: R<x>, R<y>
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
- The executor does not run `gh api` calls that mutate state. Per `01-pre-flight-checklist.md`, those are admin actions you handle. Use `scripts/flip-branch-protection.sh` for the Phase B flip (you run it, not the agent).
- For Phase B specifically, run `scripts/capture-rendered-names.sh` first to confirm the 11 frozen check names render exactly as expected. If the diff fails, do NOT run the flip — investigate and either update the PATCH body or flatten the reusable workflow.
