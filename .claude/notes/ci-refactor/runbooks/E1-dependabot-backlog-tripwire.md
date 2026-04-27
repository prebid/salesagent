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
- If the pattern persists 3+ weeks, revisit capacity strategy. Realistic options under solo+agents execution model:
  - **Tighter dependabot grouping** — reduce per-week PR count via `groups` config in `.github/dependabot.yml` (e.g., group all minor bumps for a single ecosystem into one PR/week).
  - **Reduce dependency churn** — pin a wider range or use `update-types: ["security"]` to drop non-security minor/patch noise.
  - **Accept higher backlog** — explicitly raise the D5 tripwire threshold from 5 to N with an ADR documenting the new bandwidth contract.

**Post-incident**
- Update D5 with the trigger date.
- File a follow-up ADR if the tripwire threshold or grouping policy needs to change.

**Why this happens (root cause)**
Solo+agents review of every dep bump is sustainable only at modest dep churn. Agents cannot replace human judgment on Dependabot PRs (security review, breaking-change assessment). If churn rises, the realistic options are: reduce churn, regroup PRs, or raise the threshold. Human-team scaling ("recruit second maintainer") is NOT a recourse under the user's stated solo+agents execution model.

**Related scenarios**
- See also: D5, D1, R9, C4 (cold-start version of this).

---
