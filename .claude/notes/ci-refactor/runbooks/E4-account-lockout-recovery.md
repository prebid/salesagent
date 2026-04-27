### E4 — Solo-maintainer (@chrishuie) account becomes inaccessible

**Trigger:** any of the following render @chrishuie unable to push, merge, or grant bypass for ≥24h while a phase rollout is in flight:

1. **Cannot authenticate** — lost MFA device, expired auth tokens, locked-out password reset, broken hardware key.
2. **Unavailable** — vacation, illness, emergency, off-network travel; no inbound Slack/email response within 24h.
3. **Org-admin SSO break** — prebid.org SAML/SSO outage prevents admin actions even when GitHub login works.
4. **GitHub-side outage or support ticket open** — GitHub Support is investigating an account flag, repo migration, or platform incident.

**Severity:**
- **P1** if a Phase B rollout is in flight (PR 3 phase B, PR 4, PR 6 Commit 4 audit→block flip, or any branch-protection-required-check change).
- **P2** otherwise (Phase A only, no time-pressure window, no required-check flips pending).

**Detection signals:**
- `git push` to origin fails with auth error from @chrishuie's account for >24h, OR
- `gh auth status` returns errors for the maintainer account, OR
- No human response to Slack/email/issue mention within 24h while a rollout is mid-flight, OR
- A Phase B PR has merged but no follow-up branch-protection flip occurred within the documented window (typically 24h after merge).

---

#### Immediate response (first 2 hours)

1. **Pause all in-flight PRs.** Do not merge anything that requires bypass-list membership or required-check flips while the only bypass actor is unreachable.
2. **Do NOT proceed with Phase B.** If PR 3 Phase B, PR 4, or PR 6 Commit 4 is queued, hold. Phase A merges (audit-mode, advisory-only) can continue at the team's discretion.
3. **Identify the backup admin path.** Two options, in priority order:
   - **Option A (preferred):** another prebid.org owner with admin rights on `prebid/salesagent`. Run `gh api orgs/prebid/members --jq '.[] | select(.role=="admin") | .login'` to enumerate.
   - **Option B (fallback):** open a GitHub Support ticket referencing the existing case (if any) and request emergency maintainer-access reassignment. Document the case number in `escalations/`.
4. **Post a status note** in `escalations/E4-account-lockout-<YYYYMMDD>.md` recording: trigger scenario, time of detection, in-flight PRs paused, contacted parties.

---

#### Stabilization (next 24 hours)

**If a backup admin is identified (Option A):**
1. Brief the backup admin via `briefings/pr<N>-briefing.md` for whichever phase is in flight.
2. Have them run the standard `flip-branch-protection.sh` for the queued change (require fresh OAuth — do NOT reuse @chrishuie's credentials).
3. Document the temporary grant in `escalations/E4-account-lockout-<YYYYMMDD>.md`: backup admin handle, grant timestamp, expected revocation date (≤14 days; tight TTL because temporary grants are higher-risk than the SPOF they replace).

**If no backup admin (Option B / pause):**
1. Pause the rollout entirely. Mark all in-flight PRs `draft` to prevent accidental merge by automation.
2. File an `escalations/` doc containing:
   - Trigger scenario (1, 2, 3, or 4 above).
   - GitHub Support case number (if applicable).
   - Estimated time-to-recovery (or "unknown").
   - Phase rollback decision: hold-in-place vs. revert to last-known-good (consult `runbooks/G2-pr3-phase-b-revert.md`, `runbooks/G3-pr4-revert-interdependent.md` as applicable).
3. Update every active row in `COORDINATION.md` to `BLOCKED — pending maintainer recovery` so other parallel agents see the rollout is paused.

---

#### Recovery (account restored)

Once @chrishuie's account is operational again:

1. **Verify auth.** `gh auth status` returns clean. `git push` to a scratch branch succeeds.
2. **Rotate tokens.** All personal access tokens issued before the lockout incident are considered potentially compromised — revoke and reissue. Update any local CI/automation that references those tokens.
3. **Revoke the temporary grant.** If Option A was used, remove the backup admin from any temporary bypass list / branch-protection bypass actor list. Verify with `flip-branch-protection.sh --dry-run` showing the bypass actor list is back to baseline.
4. **Resume after 24h cooldown.** Do not immediately re-flip required checks or resume Phase B merges; wait 24h to confirm the lockout cause is genuinely resolved (e.g., MFA device replaced and tested, SSO outage post-mortem complete).
5. **Post-mortem.** File a brief retrospective in `escalations/E4-account-lockout-<YYYYMMDD>-postmortem.md` covering: root cause, time-to-detection, time-to-recovery, what worked, what failed, action items for permanent mitigation.

---

#### Why this happens

The CI refactor's branch-protection bypass list contains exactly one actor: @chrishuie individually. ADR-002 (Solo Maintainer Bypass) acknowledges this as a known SPOF, traded against the ergonomic cost of running a per-action bypass workflow. A25 (pre-flight) elevated the unavailability case to in-scope and mandated this runbook as a partial mitigation — codifying the recovery procedure so that an unavailable maintainer doesn't become a multi-day rollout halt.

A25's other partial mitigation is the per-week "expected vacation" check at pre-flight time. This runbook covers the *unexpected* unavailability case.

---

#### Permanent mitigation (out of scope this rollout)

The durable fix is to add a permanent backup admin to the bypass actor list, with documented procedures for invocation and an audit trail of any usage. This requires:

- Identifying a long-term backup admin (prebid org owner with willingness to be on-call for emergency rollouts).
- Updating ADR-002 to reflect the dual-actor bypass list.
- Updating `flip-branch-protection.sh` and verify-scripts to expect a 2-element bypass list.
- A test rollout exercise to validate the backup actor's access and tooling.

This is tracked separately from the current CI refactor; do not attempt to bundle it into Phase B.

---

#### Related

- **F1** — compromised dependabot PR (overlaps recovery if the lockout is caused by token compromise)
- **F3** — branch-protection bypass exploit (overlaps if the lockout is suspected to be adversarial)
- **A25** — pre-flight checklist item that mandated this runbook

**Authoring round:** Round 14 M7.
