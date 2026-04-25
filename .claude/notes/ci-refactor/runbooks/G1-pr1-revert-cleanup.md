### G1 — PR 1 reverted: how to clean up


**Trigger**: emergency revert of PR 1 merge.
**Severity**: P1.
**Detection time**: depends on what triggered the revert.
**Affected PR(s)**: PR 1.

**Symptoms**: PR 1's commit is reverted on main; security workflows, SECURITY.md, CODEOWNERS, dependabot.yml are gone.

**Verification**: `git log --oneline | grep -i "Revert"` shows the revert commit.

**Immediate response (first 15 min)**
1. The revert removes files but does NOT auto-remove branch protection rules added via API (e.g., `@chrishuie` bypass).
2. Remove the bypass manually if it was added in pre-flight A1 sequencing:
   ```bash
   gh api -X PATCH repos/prebid/salesagent/branches/main/protection \
     --input .claude/notes/ci-refactor/branch-protection-snapshot.json
   ```
3. Re-disable security workflows that survived (zizmor, codeql) if they reference deleted configs.

**Stabilization**
1. Close any open Dependabot PRs that opened after PR 1 merged. They'll re-open after re-landing.
2. Notify any contributors who based work on PR 1's CONTRIBUTING.md changes.

**Recovery**: re-author PR 1 with the bug fixed.

**Post-incident**: investigate why PR 1 needed revert; update spec.

**Why this happens**: rare — PR 1 is mostly additive. Most likely cause: a CI rule unexpectedly broke a dimension of the v2.0 work.

**Related scenarios**: G2, G3, F-series.

---
