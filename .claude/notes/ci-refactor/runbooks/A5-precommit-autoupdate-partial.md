### A5 — `pre-commit autoupdate --freeze` partially completes


**Trigger**: PR 1 commit 8 (`pre-commit autoupdate --freeze`) is interrupted; some hooks have SHA + `# frozen: v<tag>` trailing comments, others still have `rev: v1.2.3`.
**Severity**: P2.
**Detection time**: immediate (commit verification or visual review of the diff).
**Affected PR(s)**: PR 1.

**Symptoms**
- `git diff .pre-commit-config.yaml` shows mixed state.
- 2 of 4 external hooks (`pre-commit-hooks`, `black`, `ruff`, `mirrors-mypy`) have 40-char SHAs; 2 have semver tags.
- `pre-commit run --all-files` may still pass, masking the inconsistency.

**Verification**
```bash
grep -E '^\s+rev: ' .pre-commit-config.yaml | grep -vE '^\s+rev: [a-f0-9]{40}\s+# frozen:'
# Output should be empty after a clean autoupdate --freeze
```
Any non-frozen line is a partial run.

**Immediate response (first 15 min)**
1. `git checkout .pre-commit-config.yaml` to discard the partial state.
2. Re-run: `pre-commit autoupdate --freeze`. Wait for completion (don't Ctrl-C).
3. Re-verify with the grep above; expect empty output.

**Stabilization (next 1-4 hours)**
1. `pre-commit clean && pre-commit run --all-files` to confirm hooks still pass with the new SHAs.
2. Stage and commit.

**Recovery (longer-term)**
- None.

**Post-incident**
- None — operator interruption.

**Why this happens (root cause)**
`autoupdate --freeze` does network I/O per repo. Interruption mid-run leaves partial edits. PR 4 will reduce external hooks further so this becomes less common.

**Related scenarios**
- See also: D2 (pinned-action force-push affects updates), C2 (v2.0 may add new hooks needing freeze).

---
