# Multi-Agent Execution Coordination

**Purpose:** Single source of truth for which agent is working on which PR. Update on session start, on major milestones, on completion.

**For agents:** Before claiming a PR, check this file. If a PR is `IN PROGRESS` by another agent, work on a different PR or escalate.

---

## PR Status (as of YYYY-MM-DD)

| PR | Status | Agent | Branch | Last Update | Blocking Issues |
|---|---|---|---|---|---|
| PR 1 — Supply-chain hardening | NOT STARTED | (unassigned) | — | — | — |
| PR 2 — uv.lock single-source | NOT STARTED | (unassigned) | — | — | Depends on PR 1 |
| PR 3 — CI authoritative | NOT STARTED | (unassigned) | — | — | Depends on PR 1, PR 2 |
| PR 4 — Hook relocation | NOT STARTED | (unassigned) | — | — | Depends on PR 3 Phase C |
| PR 5 — Version consolidation | NOT STARTED | (unassigned) | — | — | Depends on PR 3 Phase C |
| PR 6 — Image supply chain | NOT STARTED | (unassigned) | — | — | Depends on PR 1 (SHA convention), PR 5 (Dockerfile ARG) |

---

## Status values

- `NOT STARTED` — open to claim
- `IN PROGRESS` — agent actively working; do NOT touch
- `BLOCKED` — agent encountered escalation; see escalations/<file> for details
- `READY FOR REVIEW` — branch ready, awaiting human review/merge
- `MERGED` — landed on main; agent closed out

---

## Claiming a PR

1. Read this file. Find a PR in `NOT STARTED` status.
2. Update its row: `IN PROGRESS`, `<your-agent-id>`, `<branch-name>`, `<timestamp>`.
3. Commit with message: `chore(coord): agent <id> claims PR N`.
4. Begin work on the PR's spec/briefing/checklist.
5. On completion: update status to `READY FOR REVIEW`.
6. On merge: update status to `MERGED`.

---

## Phase B coordination (PR 3 special case)

PR 3 Phase B is admin-only. The `scripts/flip-branch-protection.sh` script enforces a GitHub-issue-based mutex (label `phase-b-in-progress`). Two admins cannot flip simultaneously. If the issue exists, another admin is in mid-flight.

If you see Phase B `IN PROGRESS` here AND no `phase-b-in-progress` issue exists, the row is stale — update it.

---

## Stale rows

If a row says `IN PROGRESS` but the agent's last update is >48h ago, the row is stale. Either:
1. The agent crashed / lost context — update to `BLOCKED` and check `in-flight/<pr>-session-*.md` for resume state
2. The agent abandoned — update to `NOT STARTED` and reclaim

Document the stale-row clearance in your commit message.
