### E1 — Dependabot backlog reaches 5+ PRs across two consecutive Mondays (D5 tripwire)


**Trigger**: weekly count exceeds 5 for two Mondays in a row.
**Severity**: P1.
**Detection time**: weekly.
**Affected PR(s)**: ongoing decay.

**Symptoms**
- `gh pr list --author "app/dependabot" --state open --json number --jq 'length'` returns ≥6 on two checks.

**Verification**
```bash
gh pr list --author "app/dependabot" --state open --json number,createdAt
```

**Immediate response (first 15 min)**
1. Pause forward work on issue #1234 (per D5 tripwire policy).
2. Block out a focused 2-4 hour review session.
3. **Do NOT enable auto-merge** as a "quick fix" — D5 is absolute.

**Stabilization (next 1-4 hours)**
1. Triage backlog: group similar bumps with `Update branch`/`rebase`. Merge in priority order: security > minor > patch.
2. Close any obsolete PRs (Dependabot reopens if still applicable).

**Recovery (longer-term)**
- If the pattern persists 3+ weeks, revisit D1 (recruit second maintainer) per D5 escalation.

**Post-incident**
- Update D5 with the trigger date.
- If escalating to D1, file the recruitment issue.

**Why this happens (root cause)**
Solo maintainer + manual-only review of every dep bump is sustainable only at modest dep churn. If churn rises, D1's mitigation (more reviewers) is the right answer — not D5 relaxation.

**Related scenarios**
- See also: D5, D1, R9, C4 (cold-start version of this).

---
