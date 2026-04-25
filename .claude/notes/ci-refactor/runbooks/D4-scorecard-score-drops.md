### D4 — Scorecard score drops unexpectedly


**Trigger**: scheduled OpenSSF Scorecard run reports a score lower than the previous week, with no obvious recent change.
**Severity**: P2.
**Detection time**: weekly cron lag (up to 7 days).
**Affected PR(s)**: any.

**Symptoms**
- Scorecard dashboard shows score going from 7.8 → 7.2.
- A specific check (e.g., `Branch-Protection`, `Pinned-Dependencies`) is degraded.

**Verification**
```bash
docker run --rm -e GITHUB_AUTH_TOKEN=$(gh auth token) \
  gcr.io/openssf/scorecard:stable \
  --repo=github.com/prebid/salesagent --format=json \
  | jq '.checks[] | select(.score < 7) | {name, score, reason, details}'
```
Compare against the previous week's saved JSON. Diff the `details` fields to find the regression.

**Immediate response (first 15 min)**
1. Identify the degraded check (the `name` and `reason` fields are explicit).
2. Common regressions:
   - **Pinned-Dependencies**: a new action was added without SHA pinning → run pinact.
   - **Branch-Protection**: a setting was relaxed → audit `gh api repos/.../branches/main/protection`.
   - **Token-Permissions**: a workflow added broader `permissions:` than needed → narrow.
   - **Dependency-Update-Tool**: `dependabot.yml` removed an ecosystem.

**Stabilization (next 1-4 hours)**
1. Open a fix PR addressing the specific check's failure reason.
2. Wait for next Scorecard run; verify recovery.

**Recovery (longer-term)**
- Save weekly Scorecard JSON to detect future drift early.

**Post-incident**
- Update R10 mitigation if the regression class wasn't anticipated.

**Why this happens (root cause)**
Scorecard checks repository state every week. Any policy relaxation (often unintentional) shows up. PR 5 anchors versions but other dimensions can still drift.

**Related scenarios**
- See also: R10, E3 (allowlist stagnation — different decay).

---
