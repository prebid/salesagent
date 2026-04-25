### C1 — PR #1217 (adcp 3.12) merges DURING PR 2 review


**Trigger**: PR #1217 transitions to merged while PR 2 is open and in review. `pyproject.toml:10` shifts from `adcp>=3.10.0` to `adcp>=3.12.0`.
**Severity**: P2.
**Detection time**: immediate (next PR 2 push triggers conflict warning).
**Affected PR(s)**: PR 2.

**Symptoms**
- `git rebase main` on PR 2 reports conflict in `pyproject.toml`.
- `uv.lock` is also affected.

**Verification**
```bash
git fetch origin
git rebase origin/main
# Conflict resolution required
```
Compare `pyproject.toml` heads.

**Immediate response (first 15 min)**
1. **Don't panic — PR 2 was designed for this** (R5, merge tolerance section).
2. `git rebase origin/main`. Resolve `pyproject.toml`: keep adcp 3.12 from main, keep PR 2's other changes (`[dependency-groups].dev`, `[dependency-groups].ui-tests`).
3. Re-run `uv sync --group dev` to refresh `uv.lock`.

**Stabilization (next 1-4 hours)**
1. Re-run mypy: `uv run mypy src/ --config-file=mypy.ini`. Verify count hasn't shifted unexpectedly (adcp 3.12 may surface different errors).
2. If commit 5 (`[project.optional-dependencies].dev` deletion) was already done on the v2.0 branch and merged via PR #1221 phase, that commit is now a no-op — skip it but keep its commit message in PR 2's history with `(no-op: already deleted in main)`.
3. Update PR 2 description: note that adcp 3.12 is now the baseline.
4. Re-push and let CI revalidate.

**Recovery (longer-term)**
- D16 cleanup: file the follow-up to remove `ignore: dependency-name: "adcp"` from `dependabot.yml`.

**Post-incident**
- Update D16 with the trigger date.
- Close the follow-up issue once dependabot.yml is cleaned.

**Why this happens (root cause)**
Multi-PR rollouts run for weeks. Other PRs merge in parallel. PR 2's spec was designed with merge tolerance — see R5.

**Related scenarios**
- See also: R5 (risk register), C2 (v2.0 PR collisions), C3 (Dependabot uv.lock collision).

---
