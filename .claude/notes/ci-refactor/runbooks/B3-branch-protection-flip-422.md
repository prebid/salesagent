### B3 — `gh api -X PATCH` for branch-protection flip returns 422


**Trigger**: PR 3 Phase B atomic flip (`gh api -X PATCH ... --input -`) returns HTTP 422 with body `"context not found"` for one of the 14 frozen check names.
**Severity**: P0 — blocks all merges to main until resolved.
**Detection time**: immediate (admin runs the command and sees the error).
**Affected PR(s)**: PR 3 Phase B specifically.

**Symptoms**
- `gh api` exits non-zero with 422.
- Error body lists which contexts are unrecognized (e.g., `CI / Migration Roundtrip`).
- Branch protection is in a half-flipped state OR remains pre-flip (depends on whether the API rejected atomically — it does, atomically).

**Verification**
```bash
gh api repos/prebid/salesagent/branches/main/protection/required_status_checks \
  --jq '.contexts[]' | sort > /tmp/current
# Should still match pre-flip snapshot if the API rejected
diff /tmp/current .claude/notes/ci-refactor/required-checks-current.txt
```
If diff is empty, API rejected atomically — no harm done. If non-empty, partial state; rollback per G2.

**Immediate response (first 15 min)**
1. Diagnose: which check name is GitHub not recognizing? It's a check that hasn't reported on main yet (path-filter excluded recent runs, or the workflow ran only on PRs).
2. Seed the missing check with a manual trigger:
   ```bash
   gh workflow run ci.yml --ref main
   ```
3. Wait 5-15 min for the run to complete and emit all 14 check names on main.
4. Verify the check appears under the repo's check-name registry:
   ```bash
   gh api repos/prebid/salesagent/commits/main/check-runs \
     --jq '.check_runs[] | select(.app.slug == "github-actions") | .name' | sort -u
   ```
5. All 14 names should be present. Re-run the atomic flip.

**Stabilization (next 1-4 hours)**
1. After successful flip, run Step 3 verification from PR 3 spec (diff against expected list).
2. Open a trivial test PR (Step 4) and watch all 14 names report.

**Recovery (longer-term)**
- None.

**Post-incident**
- Update PR 3 spec's Phase B step 1 to require `gh workflow run ci.yml --ref main` BEFORE the atomic flip — this incident proves the 48h soak is necessary but not sufficient.
- Update `02-risk-register.md` R1 mitigation list.

**Why this happens (root cause)**
GitHub's `required_status_checks.contexts` validates against names actually registered. Path-filtered jobs (`paths: ['src/**']`) only register when matching files are touched. The 48h soak in Phase A doesn't guarantee every check has reported on main — only on PRs. A doc-only commit might never trigger `CI / Schema Contract`.

**Related scenarios**
- See also: G2 (rollback the flip), R1 (risk-register entry), B5 (overlap during soak).

> **History:** This runbook was authored against the original D17 contract (a smaller name set). D30 (Round 10) expanded that set to 14 by adding Smoke Tests, Security Audit, Quickstart. Operational copy above reflects the current 14-name contract per D17 amended by D30.

---
