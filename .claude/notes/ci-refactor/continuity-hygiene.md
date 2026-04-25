# C. Continuity hygiene rules (10-15 rules)

Conventions every PR's executor agent should follow to ensure context-wipe survivability:

1. **Always commit with descriptive Conventional Commits subjects.** A fresh agent reading `git log --oneline` should be able to reconstruct progress. `fix: stuff` is forbidden; `fix(types): address pydantic.mypy plugin errors in src/core/schemas.py` is the bar.

2. **Never leave uncommitted changes when switching context.** Either commit-in-progress (acceptable; mark with `WIP:` prefix) or `git stash push -m "pr2-commit3-mypy-fixes-partial"`. Bare uncommitted diffs are forensic landmines.

3. **One logical change per commit.** A fresh agent must be able to revert exactly one of your commits without unraveling adjacent work. PR 4's spec enforces this most strictly: guards added BEFORE hooks deleted.

4. **Update `.claude/notes/ci-refactor/00-MASTER-INDEX.md` status row at PR merge.** Not at PR open — at merge. Status flows: `not started` → `in flight` → `merged YYYY-MM-DD`.

5. **Capture state before risky operations.** Before SHA-freeze: `git diff > /tmp/sha-freeze-before.txt`. Before mypy plugin enablement: `.mypy-baseline.txt`. Before branch-protection flip: snapshot. State is the rollback target.

6. **If escalating, write `.claude/notes/ci-refactor/escalations/pr<N>-<topic>.md` with full state.** Include: what you tried, exact commands run, exact error output, what you think should happen. Then STOP. Do NOT continue speculatively.

7. **Always cite D# and R# in commit bodies and PR descriptions.** Future agents (and reviewers) need to trace a change back to its policy. `Refs D13, R2` is one line; saves an hour of re-derivation.

8. **Branch names match the PR spec.** Convention: `chore/ci-refactor-pr<N>-<slug>` or `feat/ci-refactor-pr<N>-<slug>`. A fresh agent's first command is `git branch --show-current` — make it informative.

9. **Never amend or rebase committed work.** PR 1 uses 11 sequential commits intentionally; reviewers may revert individually. Amending breaks the bisect-friendly chain.

10. **Run verification after EVERY commit, not at PR end.** `make quality` minimum; spec-specific verification one-liner if available. Catch breakage early; commit a fix before the next commit.

11. **Document deferrals in the PR description.** If a fix is descoped, write "Deferred per D13 tripwire: filed follow-up #NNNN" in the PR description. Future agents see why the spec wasn't fully met.

12. **Never use `--no-verify`, `--ignore`, `-k "not …"`, or `pytest.mark.skip` to make CI green.** Hard stop per CLAUDE.md test integrity policy. If you can't fix it, escalate.

13. **Update CLAUDE.md guard count when you add or remove a guard.** PR 2 adds 1, PR 4 adds 4, PR 5 adds 1, v2.0 adds 9. The number cited in CLAUDE.md must match disk truth at all times.

14. **The pre-flight artifacts (`.zizmor-preflight.txt`, `.mypy-baseline.txt`, `branch-protection-snapshot.json`) are the rollback contract.** Never delete them during the rollout. Confirm they're on disk at the start of every fresh session.

15. **At the end of a session that did not complete a PR, write a "where I am" note.** `.claude/notes/ci-refactor/in-flight/pr<N>-session-<date>.md` with: branch, last commit, what was attempted next, what's blocking. This is the seed for the next agent's resume procedure.

---
