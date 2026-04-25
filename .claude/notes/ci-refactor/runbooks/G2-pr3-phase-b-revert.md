### G2 — PR 3 Phase B reverted: how to restore old branch protection


**Trigger**: Phase B atomic flip causes lockout (B3 worsens, or post-flip a critical check is found broken).
**Severity**: P0.
**Detection time**: minutes (next merge attempt blocks).

**Verification**: `gh pr merge` blocked on a known-good PR.

**Immediate response (first 5 min — recovery target)**
1. Run the inverse atomic call:
   ```bash
   gh api -X PATCH \
     /repos/prebid/salesagent/branches/main/protection/required_status_checks \
     --input .claude/notes/ci-refactor/branch-protection-snapshot-required-checks.json
   ```
2. Verify diff is empty against pre-flip snapshot.

**Stabilization**: re-enable old `test.yml` if it was deleted (Phase C). Open a fast-track PR restoring it.

**Recovery**: investigate the failed check; re-attempt Phase B once the issue is fixed.

**Post-incident**: update PR 3 spec with the failure mode found.

**Related scenarios**: B3, R1.

---
