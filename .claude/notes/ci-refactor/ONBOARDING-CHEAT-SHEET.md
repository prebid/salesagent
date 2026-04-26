# 10-Minute Onboarding Cheat Sheet

You're a fresh agent picking up this rollout. **Don't read the 80k-token corpus.** Read this in 10 minutes, then jump to your assigned PR.

---

## Step 1 — What am I working on? (30 seconds)

Open `COORDINATION.md`. Find a PR with status `NOT STARTED`. Claim it (update the row).

If all PRs are claimed: check stale rows (last-update >48h with `IN PROGRESS` status) — those are abandoned. Reclaim one OR check `in-flight/<pr>-session-*.md` for resume state.

---

## Step 2 — What's already merged? (1 minute)

Open `00-MASTER-INDEX.md`. Look at the "Status" column. Anything with `merged` is on main; anything with `in progress` or `not started` is your potential target.

PR sequencing: PR 1 → PR 2 → PR 3 (3-phase) → PR 4 → PR 5 → PR 6. PRs higher in the chain block lower ones. Phase B of PR 3 is admin-only (special case).

---

## Step 3 — What file do I read first? (5 minutes)

Open `pr<N>-<slug>.md` for your assigned PR. This is the SPEC. Skim it once.

Then open `briefings/pr<N>-briefing.md` for the executive overview (1-2 pages).

Then open `checklists/pr<N>-checklist.md` for the per-step task list.

Then open `scripts/verify-pr<N>.sh` to understand the acceptance check.

That's the full per-PR context. ~30k tokens total. (You can ignore the audit-trail prose, decision-log history, and the 28 runbooks unless you hit a specific failure.)

---

## Step 4 — What if I'm blocked?

See `FAILURE-BROADCAST-PROTOCOL.md`. Short version:
1. Write `escalations/pr<N>-<topic>.md` describing the block
2. Comment on the PR-tracking GitHub issue
3. Update `COORDINATION.md` status to `BLOCKED`
4. STOP work — wait for clearance from human or another agent

---

## Step 5 — What MUST I never do?

- **Push to origin** (user owns this — see `feedback_user_owns_git_push.md`)
- **Run `gh pr create`** (user owns this)
- **Mutate branch protection via `gh api -X PATCH branches/main/...`** (admin only — use `scripts/flip-branch-protection.sh` only when invoked by user)
- **Use `--no-verify`, `--ignore`, `--deselect`, `pytest.mark.skip`** to bypass failures (zero-tolerance per CLAUDE.md test-integrity policy)
- **Touch files outside your PR's spec scope**
- **Amend `03-decision-log.md` mid-execution** (decision-freeze is in effect — escalate ambiguities, do not invent new D-numbers)

---

## Step 6 — Critical context (must-know for ANY PR)

- **14 frozen rendered CI check names** under branch protection: `CI / Quality Gate, CI / Type Check, CI / Schema Contract, CI / Unit Tests, CI / Integration Tests, CI / E2E Tests, CI / Admin UI Tests, CI / BDD Tests, CI / Migration Roundtrip, CI / Coverage, CI / Summary, CI / Smoke Tests, CI / Security Audit, CI / Quickstart`
- **Workflow naming convention (D26)**: workflow `name: CI` (top-level), job `name: 'Quality Gate'` (bare). GitHub auto-prefixes.
- **Hook math (D27 revised)**: 36 effective − 13 deletions − 10 moves − 1 consolidation = 12 commit-stage post-PR-4
- **Phase B is admin-only** (D2 / ADR-002 bypass; D45 forbids Fri/weekend)
- **Production deploy**: Fly.io app `adcp-sales-agent` pulls from `ghcr.io/prebid/salesagent:vX.Y.Z`. Don't break the tag scheme. (See EXECUTIVE-SUMMARY §Production Deploy Coupling.)
- **Decision-freeze**: D1-D48 / R1-R47 are LOCKED for execution. New decisions require explicit user approval.

---

## Step 7 — Verify your work

After each commit:
- `bash scripts/verify-pr<N>.sh` — partial check, allows progressive completion
- `make quality` — full quality gate

After all commits in your PR:
- `./run_all_tests.sh` — full suite via tox (Docker-backed)
- Generate PR description from `templates/pr-description.md`

---

## Step 8 — When in doubt

Read RESUME-HERE.md (lengthy but authoritative). It's an audit trail; the orientation sections are at the top.

Or: stop and ask in `escalations/`. The user reads, decides, you resume.
