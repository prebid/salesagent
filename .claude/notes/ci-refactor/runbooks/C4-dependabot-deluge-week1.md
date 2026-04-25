### C4 — Dependabot opens 15 PRs week 1


**Trigger**: cold-start cron after PR 1 lands enables Dependabot for the first time. ~15 PRs land on Saturday UTC.
**Severity**: P3 — operational, not technical.
**Detection time**: immediate.
**Affected PR(s)**: PR 1 (downstream effect).

**Symptoms**
- `gh pr list --author "app/dependabot" --state open` returns 15 entries.
- Reviewer queue is overwhelmed.

**Verification**
```bash
gh pr list --author "app/dependabot" --state open --json number,title --jq 'length'
```

**Immediate response (first 15 min)**
1. **Do NOT enable auto-merge** — D5 forbids it absolutely.
2. Edit `.github/dependabot.yml`: temporarily set `open-pull-requests-limit: 1` for each ecosystem (was `5`).
3. Push as a fast-track PR. Merge.
4. Identify the 1-2 highest-risk PRs in the queue (security CVEs first); review and merge those.
5. Mass-close low-priority PRs with comment: `Closing temporarily; will reopen in week 2 after backlog clears.`

**Stabilization (next 1-4 hours)**
1. Close 10-12 of the 15 PRs (low-risk minor bumps). Dependabot will re-open them in the next cycle if still applicable.
2. Review and merge 2-3 high-priority ones serially.

**Recovery (longer-term)**
- Week 2: revert `open-pull-requests-limit: 1` back to `5`. The closed PRs will trickle back over the next cycle, by which time the maintainer is on cadence.

**Post-incident**
- Update R9 mitigation list: confirm `groups:` directive is in dependabot.yml.

**Why this happens (root cause)**
Cold-start: every dependency has accumulated bumps since project inception. D5's "no auto-merge" amplifies the review pressure. Mitigated but not eliminated by `groups:` and `open-pull-requests-limit:`.

**Related scenarios**
- See also: R9, E1 (sustained backlog), D5 decision rationale.

---
