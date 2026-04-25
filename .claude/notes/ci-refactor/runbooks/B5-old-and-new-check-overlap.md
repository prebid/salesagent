### B5 — A PR's CI shows BOTH old and new check names (Phase A or Phase C overlap)


**Trigger**: during Phase A (intentional 48h overlap) or briefly during Phase C cleanup, a PR's check list shows `Smoke Tests…` (old) AND `CI / Unit Tests` (new).
**Severity**: P3 — informational, not a failure.
**Detection time**: immediate.
**Affected PR(s)**: any PR open during Phase A.

**Symptoms**
- PR check list has 15-20 entries instead of expected 11.
- Some old names (`Smoke Tests with Mock Adapter`, `Security Audit`, `Unit Tests`) appear alongside `CI / *` names.

**Verification**
```bash
gh pr checks <pr-number> --json name,state \
  --jq '.[] | "\(.state)\t\(.name)"' | sort
```
If both `test.yml` and `ci.yml` workflows are running, both name sets appear.

**Immediate response (first 15 min)**
1. **Confirm both are green.** Overlap is intentional during Phase A.
2. Required checks list (per branch protection) determines what BLOCKS merge. As long as the required ones are green, merge is OK.
3. Do NOT take action. This is the design.

**Stabilization (next 1-4 hours)**
- None needed.

**Recovery (longer-term)**
- Phase C deletes `test.yml`; old names disappear after that PR merges.

**Post-incident**
- None.

**Why this happens (root cause)**
Phase A intentionally overlaps to prove the new workflows are stable on real PRs before Phase B's atomic flip. Both workflows running for 48h is the soak.

**Related scenarios**
- See also: B3 (flip 422 — what happens if Phase A skipped this), G2 (rollback if soak failed).

---
