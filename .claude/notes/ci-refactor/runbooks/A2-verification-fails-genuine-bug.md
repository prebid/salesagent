### A2 — A commit's verification fails (genuine bug)


**Trigger**: commit's verification block exits non-zero; running the verifier locally reproduces the failure; the cause is real (a missing change, a wrong file edit, a regression).
**Severity**: P1.
**Detection time**: immediate (verification is run at commit time).
**Affected PR(s)**: All.

**Symptoms**
- `make quality` fails on a commit that the agent claims is green.
- Verification command shows a real diff (e.g., `mirrors-mypy` still in `.pre-commit-config.yaml` after PR 2 commit 2).

**Verification**
```bash
git stash
git checkout <commit-sha>
# Re-run the commit's verification block as listed in the spec
# Compare exit code and output
git stash pop
```
If the verification reproducibly fails on a clean checkout, it's a genuine bug, not a state-leak from earlier work.

**Immediate response (first 15 min)**
1. **Do NOT run `git commit --no-verify`** — bypassing pre-commit is forbidden by CLAUDE.md and re-creates the very drift the rollout closes.
2. Read the commit's verification block in the per-PR spec. Confirm what it expects.
3. Diff against the change set (`git diff --stat HEAD~1`). Identify which file/edit is missing or wrong.
4. Fix the underlying bug — never the verification check.

**Stabilization (next 1-4 hours)**
1. Amend the commit (still on the PR branch, pre-merge): `git commit --amend`.
2. Re-run the verification block; confirm green.
3. Continue to next commit.
4. **Maximum 2 attempts.** If a third attempt is needed, STOP and escalate — the design may be wrong.

**Recovery (longer-term)**
- If the bug points to ambiguity in the spec, file a follow-up to clarify.

**Post-incident**
- Update spec verification text if it was unclear.
- Risk register: no change unless the bug class recurs.

**Why this happens (root cause)**
Specs are written ahead of execution. Real codebases drift. Genuine bugs are caught here; this is the design working.

**Related scenarios**
- See also: A3 (verification typo — distinguish before fixing), A4 (PR 2 mypy specific).

---
