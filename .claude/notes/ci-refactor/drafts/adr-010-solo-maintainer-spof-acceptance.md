# ADR-010 — Solo-maintainer SPOF acceptance (closes A25)

## Status

Accepted. Round 14 (2026-04-27).

## Context

Issue #1234's branch-protection model (D2 / ADR-002) places `@chrishuie` as the sole bypass actor on `main`. This is a documented single point of failure: if `@chrishuie`'s GitHub account becomes inaccessible (lost MFA hardware key, vacation/illness, prebid org-admin SSO break, GitHub-side outage on the account, GitHub Support ticket open), no one can merge until the account is recovered.

Round 13 introduced pre-flight A25 with two options:
- **Option A:** Recruit a second maintainer with bypass-list access during PR 3 Phase B execution week ONLY (with documented revoke procedure).
- **Option B:** Confirm hardware MFA (FIDO2 / hardware key) on `@chrishuie`'s account.

Round 14 reframed the rollout's execution model from "human team" to **solo + agents** (per user-memory `feedback_agent_team_execution_model.md`). Under this model, Option A is **categorically impossible**: agents are not GitHub maintainers, cannot be added to bypass lists, cannot hold hardware-MFA recovery responsibility. The realistic-only path is Option B + a documented unavailability runbook.

This ADR documents the SPOF acceptance under solo+agents execution and locks the operational mitigations.

## Decision

**Accept the solo-maintainer SPOF**, mitigated by the following layered controls:

1. **Hardware MFA on `@chrishuie`** (FIDO2 / hardware key) — confirmed via pre-flight A25 before PR 3 Phase B. Recovery procedure documented (recovery codes location + GitHub Support escalation path).
2. **`runbooks/E4-account-lockout-recovery.md`** — codified procedure for the four lockout scenarios: lost MFA, unavailability (vacation/illness), org-admin SSO break, GitHub-side outage / Support ticket open.
3. **`branch-protection-snapshot.json`** — captured at pre-flight A1 + refreshed daily via cron (R23 / R39 mitigation). Restores the pre-rollout state if a Phase B flip 422s mid-flight.
4. **`scripts/flip-branch-protection.sh`** — admin-only, idempotent, dry-run-first. D45 day-of-week guard forbids Fri/Sat/Sun + holiday eve execution to avoid weekend lockout windows.
5. **A14 (org-admin authority for bypass list)** — confirmed before PR 1 lands. If org admin refuses bypass authority, escalate before authoring PR 1 — the rollout cannot proceed without bypass authority for `@chrishuie`.
6. **Documentation in ADR-002** — the bypass model is acknowledged as transitional. If the prebid org adds a permanent co-maintainer in the future, ADR-002 / D1 / this ADR are amended together.

## Consequences

**Accepted risks:**
- A multi-day GitHub-side outage on `@chrishuie`'s account blocks all merges until recovered. Mitigation: E4 runbook + GitHub Support escalation. Realistic recovery time: 1-3 business days.
- A weekend / holiday-eve lockout (D45 forbids by default; emergency exceptions require a temporarily-added second admin with documented revoke time).
- Sustained Dependabot backlog (D5 tripwire at 5+ open PRs) cannot be solved by recruiting more reviewers under solo+agents. Mitigation in `runbooks/E1-dependabot-backlog-tripwire.md`: tighter dependabot grouping, reduced churn via narrower update-types, or raising the threshold via a follow-up ADR.

**Rejected alternatives:**
- *Recruit a second human maintainer for the rollout* — incompatible with solo+agents execution model. If a co-maintainer joins the prebid org organically (post-#1234), revisit this ADR.
- *Migrate to GitHub Rulesets immediately* — out of scope; tracked in ADR-009 as Q3 2026 follow-up.
- *Auto-merge Dependabot* — D5 forbids absolutely; no carve-out for backlog clearance.

**Reviewable conditions** (if any of these become true, re-evaluate this ADR):
- Prebid org adds a permanent co-maintainer with bypass authority.
- `@chrishuie` proves unable to maintain hardware-MFA recovery materials.
- A concrete lockout incident occurs and E4 runbook gaps are exposed.
- GitHub Rulesets adoption (per ADR-009) changes the bypass-actor surface.

## References

- D1 (decision-log): solo maintainer model, Round 13/14 amendment
- D2 / ADR-002: bypass actor model
- D45: Phase B day-of-week guard
- A14, A25 (pre-flight): bypass-list authority + hardware-MFA confirmation
- R20, R23, R30, R39 (risk register): bypass-actor compromise / snapshot SPOF
- runbooks/E4-account-lockout-recovery.md
- ADR-009: GitHub Rulesets future migration
