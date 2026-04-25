# PR 3 Phase B — Atomic flip (admin only, NO PR)

## Briefing
**Where we are.** Week 4. Phase A on main ≥48h, ≥2 real PRs show new check names green. Calendar: highest-risk single moment of rollout.

**What this phase does.** ONE atomic `gh api -X PATCH` call swaps `required_status_checks.contexts` from old names (test.yml's 7 contexts) to new names (the 11 frozen per D17). Window: ~5 minutes. After this point, main is gated by `ci.yml`.

**This is NOT an agent task.** Agents do not run `gh api -X PATCH` per `feedback_user_owns_git_push.md`. The operator (`@chrishuie`) runs this entirely. The block below documents the procedure for the operator's reference.

**You can rely on.** Phase A merged ≥48h ago; new check names verified real. `branch-protection-snapshot-required-checks.json` is your rollback target.

**Concurrent activity.** Pause all in-flight PRs during the 5-minute window. Reopen after Step 4 verification.

**Escalation.** If verification fails, run the inverse PATCH IMMEDIATELY (single command in §Rollback). Recovery: <5 minutes.
