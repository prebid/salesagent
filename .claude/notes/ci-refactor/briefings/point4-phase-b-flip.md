## Cold-start briefing — Point 4: PR 3 Phase A merged; Phase B atomic flip about to happen

**Where you are in the rollout**
- Calendar week: Week 4, early
- PRs merged: PR 1, PR 2, PR 3 Phase A (overlap)
- PRs in flight: PR 3 Phase B (admin action — not a PR)
- PRs pending: PR 3 Phase C (cleanup), PR 4, PR 5
- v2.0 phase PR coordination: track via `gh pr list --search "head:v2"` — coordination still active

**Critical context: This is the riskiest moment in the rollout**
Per R1 (HIGH severity), a misconfigured branch-protection flip locks out merging entirely. Phase B is a 5-minute window. Inverse rollback is documented but recovery requires the user.

**What you can rely on (already true on main)**
- `.github/workflows/ci.yml` exists and emits the 11 frozen check names per D17 — `CI / Quality Gate`, `CI / Type Check`, `CI / Schema Contract`, `CI / Unit Tests`, `CI / Integration Tests`, `CI / E2E Tests`, `CI / Admin UI Tests`, `CI / BDD Tests`, `CI / Migration Roundtrip`, `CI / Coverage`, `CI / Summary`
- `.github/actions/_pytest/action.yml` composite action exists (NOT a reusable workflow — Decision-4 P0 sweep eliminates the 3-segment rendered-name issue at design time)
- `.github/actions/setup-env/action.yml` composite action exists
- `.github/scripts/migration_roundtrip.sh` exists and is executable
- `.coverage-baseline` exists with contents `53.5` (D11 revised in 2026-04-25 P0 sweep — hard-gate from PR 3 day 1; ratchet upward only when measured-stable)
- `.github/workflows/test.yml` STILL exists (Phase A overlap — both old and new run); will be deleted in Phase C
- All actions in `ci.yml`, `_pytest/action.yml`, `setup-env/action.yml` are SHA-pinned
- Branch protection STILL points at the OLD required-checks list (e.g., `Security Audit`, `Smoke Tests…` — verify against `.claude/notes/ci-refactor/required-checks-current.txt`)
- ≥48 hours of overlap has elapsed; both old `test.yml` and new `ci.yml` runs are green for ≥2-3 PRs

**Pre-flip verification (MUST be confirmed before Step 2)**
1. New check names appear green on at least 2-3 PRs that landed during overlap. Verify with `gh pr list --state merged --limit 5 --json number --jq '.[].number' | xargs -I{} gh pr checks {} | grep "CI /"`
2. Both old and new workflows green on `main` — `gh run list --workflow=ci.yml --branch=main --limit=3 --json conclusion --jq '[.[].conclusion] | all(. == "success")'` returns `true` AND `gh run list --workflow=test.yml --branch=main --limit=3 --json conclusion --jq '[.[].conclusion] | all(. == "success")'` returns `true`
3. Pre-flight A1 snapshot still on disk: `test -f .claude/notes/ci-refactor/branch-protection-snapshot.json && test -f .claude/notes/ci-refactor/branch-protection-snapshot-required-checks.json`
4. The exact 11 names you'll PATCH in match what `ci.yml` emits — `grep -oE "name: 'CI /[^']*'" .github/workflows/ci.yml | sort -u`

**USER vs AGENT actions for Phase B**

| Step | Owner | Action |
|---|---|---|
| 1. Verify pre-flip artifacts on disk | Agent | `test -f .claude/notes/ci-refactor/branch-protection-snapshot.json && test -f .claude/notes/ci-refactor/branch-protection-snapshot-required-checks.json` |
| 2. Verify Phase A overlap stable | Agent | run the `gh run list` queries above, prepare report |
| 3. Verify ci.yml emits exactly the 11 frozen names | Agent | grep + sort + diff against expected list |
| 4. Compose the `gh api -X PATCH` body | Agent | use the exact heredoc from PR 3 spec §"Phase B" Step 2 |
| 5. **Execute the `gh api -X PATCH`** | **USER** | per `feedback_user_owns_git_push.md` and PR 3 §Coordination notes #3 — agents do not run branch-protection mutations |
| 6. Verify the flip via diff | Agent | `gh api repos/prebid/salesagent/branches/main/protection/required_status_checks --jq '.contexts[]' | sort | diff - <(printf "%s\n" "CI / Admin UI Tests" "CI / BDD Tests" …)` |
| 7. Open trivial test PR | Either (user typically) | confirm all 11 new names show as required, all green |
| 8. **Rollback if needed** | **USER** | run inverse `gh api -X PATCH --input branch-protection-snapshot-required-checks.json` |

**What you must NOT do (agent)**
- Do not execute the `gh api -X PATCH` yourself — admin action
- Do not start Phase C (deleting `test.yml`) until Phase B is stable for ≥48 hours
- Do not allow the user to skip the trivial test PR (Step 7) — that's the canary
- Do not modify branch protection in any way other than the documented flip — anything else can lock out merging

**Files you'll touch in this PR**
None. Phase B is admin-only. No code changes; only branch-protection state changes via `gh api`.

**Verification environment**
- Capture pre-flip state: `gh api repos/prebid/salesagent/branches/main/protection > /tmp/protection-pre-flip.json`
- After flip: capture post-flip state: `gh api repos/prebid/salesagent/branches/main/protection > /tmp/protection-post-flip.json`
- Diff: should show only `required_status_checks.contexts` changed

**Specific commands to run FIRST (agent prepares; user executes flip)**
1. `cd /Users/quantum/Documents/ComputedChaos/salesagent && git status && git pull origin main`
2. Verify pre-flip artifacts exist (Step 1 above)
3. Verify Phase A overlap stable (Step 2 above) — confirm with at least 3 successful runs of each workflow
4. Verify ci.yml emits the 11 names (Step 3 above)
5. Compose the PATCH body — copy exactly from `.claude/notes/ci-refactor/pr3-ci-authoritative.md` §"Phase B Step 2"
6. Hand to user with the exact command and the rollback command pre-formatted
7. After user runs flip: execute Step 6 verification

**Decisions in effect**
D2 (branch protection + bypass — `@chrishuie` on bypass list, was set in PR 1 admin steps), D17 (the 11 frozen names — frozen as a contract; do not deviate), D11 (`.coverage-baseline = 53.5` hard-gate from PR 3 day 1, revised 2026-04-25 P0 sweep), D26 (workflow naming — bare job names; the rendered-name probe in Step 1b is mandatory)

**Risks active right now**
- R1 (HIGH severity, low prob): branch-protection flip locks out merging. Mitigation: pre-flight snapshot + atomic flip + ≤5-minute window. Recovery: <5 minutes via inverse PATCH
- R8: coverage drop after CI restructure — coverage is hard-gated from PR 3 day 1 (D11 revised); a single PR dropping >2pp blocks merge with a clear failure message. No advisory window.

**Escalation triggers**
- Phase A overlap shows ANY new check failing (not green) on main: STOP, do not flip; investigate flake vs real failure
- The 11 names emitted by `ci.yml` differ from D17's frozen list (typo, different casing, missing): STOP, fix in a small PR before flipping
- After flip, trivial test PR shows any of the 11 names as "expected but not running": STOP, run inverse PATCH within 5 minutes
- After flip, an unrelated PR fails to merge because of a check name mismatch: STOP, run inverse PATCH

**How to resume the work**
The user runs the flip. Then:
1. Verify the flip succeeded — diff `/tmp/protection-pre-flip.json` and `/tmp/protection-post-flip.json`
2. Coordinate with user on Step 7 (trivial test PR)
3. Wait ≥48 hours for Phase B stability
4. Then Phase C is a small follow-up PR that deletes `test.yml` and updates `docs/development/ci-pipeline.md` "current state" section — that's authored as a normal PR, not admin action

**Where to find context**
- `.claude/notes/ci-refactor/pr3-ci-authoritative.md` §"Phase B" — the verbatim flip and rollback bodies
- `.claude/notes/ci-refactor/02-risk-register.md` R1 — full mitigation/rollback for this exact risk
- `.claude/notes/ci-refactor/03-decision-log.md` D17 — the frozen check names contract
- `.claude/notes/ci-refactor/branch-protection-snapshot.json` — pre-flight A1 capture (the rollback target)
- `.claude/notes/ci-refactor/branch-protection-snapshot-required-checks.json` — extracted from A1 for the inverse PATCH

---
