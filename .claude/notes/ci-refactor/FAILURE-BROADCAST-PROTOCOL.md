# Failure Broadcast Protocol

**Purpose:** When an agent escalates, OTHER agents need to know. Without this, parallel agents continue working on PRs that may be invalidated by the escalation.

---

## When to broadcast

Any of:
- You wrote `escalations/<file>.md`
- You discovered a load-bearing defect in another agent's PR
- You blocked on a dependency that requires human resolution
- You need to amend a decision (decision-freeze override required)

---

## Broadcast steps

1. **Write the escalation file** (`escalations/pr<N>-<topic>.md`):
   - What you were trying to do
   - What blocked you (with command output)
   - What you tried
   - What you think should happen

2. **Comment on the PR-tracking GitHub issue** (#1234 for this rollout):
   - "Agent <id> escalating: see `escalations/pr<N>-<topic>.md`"
   - Link to the file (relative path or commit SHA)

3. **Update `COORDINATION.md`**:
   - Change your row's status to `BLOCKED`
   - Add link to escalation file in the "Blocking Issues" column

4. **STOP work** on the PR. Do not commit further. Wait for resolution.

---

## When other agents see a broadcast

On every session start, check:
- `COORDINATION.md` for any `BLOCKED` rows
- `escalations/` directory for any new files

If a PR is blocked AND your PR depends on it (per dependency chain in 00-MASTER-INDEX.md):
- Pause your work
- Comment on the same GitHub issue: "Agent <id> on PR <M> pausing pending PR <N> resolution"
- Update `COORDINATION.md` status to `BLOCKED — waiting on PR <N>`

---

## When the block is resolved

The user (or escalation handler) will:
- Comment on the GitHub issue with resolution
- Update `COORDINATION.md` status of the originally-blocked PR to `IN PROGRESS` or `NOT STARTED`

Other agents resume their work and update their own `COORDINATION.md` rows accordingly.
