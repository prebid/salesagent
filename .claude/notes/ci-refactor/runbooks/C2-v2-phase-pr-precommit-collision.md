### C2 — A v2.0 phase PR lands on `.pre-commit-config.yaml` mid-PR-1


**Trigger**: a v2.0 (#1221 carve-out) PR merges to main while PR 1 is open; both touch `.pre-commit-config.yaml`.
**Severity**: P2.
**Detection time**: immediate (rebase conflict).
**Affected PR(s)**: PR 1 directly; PR 2 indirectly.

**Symptoms**
- `git rebase origin/main` conflict in `.pre-commit-config.yaml`.
- v2.0 added/removed hooks; PR 1 changed `rev:` lines for SHA freeze.

**Verification**
```bash
git diff origin/main..HEAD .pre-commit-config.yaml
git log origin/main --oneline -- .pre-commit-config.yaml | head -5
```

**Immediate response (first 15 min)**
1. `git rebase origin/main`. Manually resolve:
   - **Keep PR 1's SHA-frozen `rev:` values** with `# frozen: v<tag>` comments.
   - **Keep v2.0's hook list changes** (adds/removes).
2. Re-run `pre-commit run --all-files`. If a v2.0-added hook fails, that's a v2.0 issue, not PR 1 — file separately.

**Stabilization (next 1-4 hours)**
1. Re-run `pre-commit autoupdate --freeze` if v2.0 introduced any new external hook (it should be SHA-pinned per PR 1's policy).
2. Push, let CI verify.

**Recovery (longer-term)**
- None.

**Post-incident**
- Notify v2.0 PR author that the SHA-pinning convention now applies; future v2.0 phase PRs should ship hooks already SHA-pinned.

**Why this happens (root cause)**
Both efforts are running concurrently per D20 Path 1. R6 anticipates overlap on shared files (`pyproject.toml`, `.pre-commit-config.yaml`, `test.yml`, `CLAUDE.md`).

**Related scenarios**
- See also: R6, D20, C1 (similar shape, different file).

---
