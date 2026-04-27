### B4 — All 14 CI jobs green but `Test Summary` shows failure on a PR


**Trigger**: post-Phase-B; the `Summary` job (with `if: always()`) reports failure even though all 13 prior jobs are individually green.
**Severity**: P2.
**Detection time**: immediate.
**Affected PR(s)**: any PR after PR 3 Phase B.

**Symptoms**
- 13 of 14 required checks: green checkmark.
- `CI / Summary`: red X.
- PR shows "All required checks passed" in some places but not in `Merge` button.

**Verification**
```bash
gh run view --log-failed <run-id> | head -80
gh run view --json jobs --jq '.jobs[] | {name, conclusion, result: .conclusion}' <run-id>
```
Look for jobs with `result: "skipped"` or `conclusion: null` that the Summary's `needs:` pulled in.

**Immediate response (first 15 min)**
1. Identify which `needs:` dependency caused the Summary to fail.
2. Most common: a `needs` job had `if:` condition that skipped, and Summary's logic treats `skipped` as failure for required dependencies.
3. Check the `Summary` job's body — it should compute success as `needs.*.result == 'success' or needs.*.result == 'skipped' (when expected)`.

**Stabilization (next 1-4 hours)**
1. Patch the Summary job's expression to handle the `skipped` case explicitly. Example fix:
   ```yaml
   if: always()
   run: |
     if [[ "${{ needs.unit.result }}" == "failure" ]] ||
        [[ "${{ needs.integration.result }}" == "failure" ]]; then
       exit 1
     fi
   ```
2. Push to a fix branch; verify Summary now respects skipped vs failed.

**Recovery (longer-term)**
- None.

**Post-incident**
- Update PR 3 spec commit 3 (orchestrator) if the Summary expression needs hardening.

**Why this happens (root cause)**
GitHub Actions `needs:` dependencies treat `skipped` as a non-success result by default. A Summary job with `if: always()` runs even when upstream skipped, but the implicit join semantics aren't transparent.

**Related scenarios**
- See also: B5 (old + new check overlap — different cause), R8 (coverage — Summary may also be affected).

> **History:** This runbook was authored against the original D17 contract (a smaller name set). D30 (Round 10) expanded that set to 14 by adding Smoke Tests, Security Audit, Quickstart. Operational copy above reflects the current 14-name contract per D17 amended by D30.

---
