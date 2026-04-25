### G3 — PR 4 reverted: hook deletions interdependent


**Trigger**: PR 4 merged but a regression surfaces; commit-level revert breaks because commits 6 + 7 are coupled.
**Severity**: P1.
**Detection time**: hours to days.

**Symptoms**: a deleted hook's invariant is silently violated on a new PR; targeted revert of one commit doesn't restore the hook.

**Verification**: identify the missing invariant. Run `pre-commit run --all-files`; compare to PR 4's pre-merge state.

**Immediate response**
1. **Full PR-revert**, not commit-level:
   ```bash
   git revert -m 1 <PR4-merge-sha>
   ```
   Pre-commit reverts cleanly because hook deletion is symmetric — restoring `.pre-commit-config.yaml` restores the hooks.
2. New structural guards added in commits 1-3 remain (they were added BEFORE deletions, so revert leaves them intact). This is fine — guards are additive.

**Stabilization**: re-author PR 4 with the regression class addressed (likely a missing structural guard).

**Recovery**: ensure red-team test list is exhaustive before re-attempting.

**Post-incident**: update R7 mitigation; expand red-team list.

**Why this happens**: PR 4's commits 6 (consolidate grep one-liners) + 7 (delete migrated/dead hooks) modify the same hook list. Selective revert is mechanically possible but error-prone.

**Related scenarios**: R7, E2 (guard FP — different decay).
