# Risk Register

Top-10 risks for the 5-PR rollout. Severity × probability ranked. Each entry has a trigger (how you know it fired), a mitigation (preventive), and a rollback (corrective).

| # | Risk | Sev | Prob | PR |
|---|---|---|---|---|
| 1 | Branch-protection flip locks out merging | High | Low | PR 3 Phase B |
| 2 | Pydantic.mypy errors > 200 in PR 2 | Med | Med | PR 2 |
| 3 | CodeQL findings explode beyond 99 CSRF | Med | Med | PR 1 |
| 4 | zizmor finds > 50 issues | Med | Low | PR 1 |
| 5 | PR #1217 merges mid PR 2 review | Low | Med | PR 2 |
| 6 | v2.0 phase PR lands on overlapping file mid-rollout | Med | Med | All |
| 7 | `make quality` regression after hook deletion | High | Low | PR 4 |
| 8 | Coverage drops > 2% after CI restructure | Med | Med | PR 3 |
| 9 | Dependabot deluge week 1 (>15 PRs) | Low | High | PR 1 |
| 10 | OpenSSF Scorecard < 7.5 after all PRs | Med | Low | All |

## R1 — Branch-protection flip locks out merging (HIGH)

- **Trigger:** during PR 3 Phase B, the `gh api -X PATCH` call sets `required_status_checks.contexts` to a list that doesn't match any actually-running check. Main becomes unmergeable.
- **Mitigation:**
  - Phase A (overlap) keeps both old and new check names emitting for ≥48h. Confirms new names are real before the flip.
  - PR 3 spec embeds the EXACT inverse `gh api -X PATCH` command, pre-filled with the snapshot from pre-flight A1.
  - Flip happens in a 5-minute window, not interleaved with other work.
- **Rollback:**
  ```bash
  gh api -X PATCH \
    /repos/prebid/salesagent/branches/main/protection/required_status_checks \
    --input .claude/notes/ci-refactor/branch-protection-snapshot-required-checks.json
  ```
  Recovery: < 5 minutes.

## R2 — Pydantic.mypy errors > 200 in PR 2 (MED)

- **Trigger:** the pre-flight `.mypy-baseline.txt` (P2) shows > 200 errors when the `pydantic.mypy` plugin actually loads.
- **Mitigation:**
  - Pre-flight P2 captures the count BEFORE PR 2 is authored. If > 200, downscope per D13's tripwire.
  - PR 2 ships ONLY the local-hook migration; pydantic plugin re-enablement deferred to a follow-up PR.
  - Comment out the plugin line in `mypy.ini` temporarily; track the deferral in a follow-up issue.
- **Rollback:** revert PR 2 commit; mypy hook regresses to mirrors-mypy block; drift returns but CI stays green.

## R3 — CodeQL findings explode beyond 99 CSRF (MED)

- **Trigger:** PR 1's first CodeQL run on main turns up CSRF + N other categories such that triage exceeds 1 day.
- **Mitigation:**
  - D10's Path C (advisory) means PR 1 is not blocked by CodeQL findings.
  - Per-finding triage during the 2-week advisory window; allowlist medium/low with FIXME comments.
  - High/critical findings get a follow-up issue and a separate fix PR before flipping to gating.
- **Rollback:** keep CodeQL advisory indefinitely; document the deferred gating in a new ADR.

## R4 — zizmor finds > 50 issues (MED)

- **Trigger:** pre-flight P3 (`.zizmor-preflight.txt`) shows > 50 medium+ findings.
- **Mitigation:**
  - Pre-flight P3 captures the count. If > 50, file a follow-up issue for non-PR-1-scope findings; PR 1 fixes only the load-bearing ones (excessive-permissions, dangerous-triggers).
  - Unpinned-uses findings (~30 expected): convert all `actions/*@v4` to SHA-pinned in a single PR 1 commit (mechanical, scriptable).
  - `pull_request_target` on pr-title-check.yml + ipr-agreement.yml: legitimate; allowlist with `# zizmor: ignore[dangerous-triggers]` comments + ADR-003 (drafted in PR 1) explaining the trust model.
- **Rollback:** revert PR 1 commit that added zizmor as required check; keep zizmor advisory.

## R5 — PR #1217 merges mid PR 2 review (LOW prob, LOW sev)

- **Trigger:** PR #1217 transitions from CONFLICTING to merged while PR 2 is open.
- **Mitigation:**
  - PR 2 spec includes a "merge tolerance" section: rebase against latest main; mypy hook automatically validates against the new `uv.lock`; no semantic change to PR 2.
- **Rollback:** none needed; rebase resolves it.

## R6 — v2.0 phase PR lands on overlapping file mid-rollout (MED)

- **Trigger:** during the 5-week window, a v2.0 phase PR (carved from PR #1221) lands on `pyproject.toml`, `.pre-commit-config.yaml`, `test.yml`, `CLAUDE.md`, `Dockerfile`, or `docker-compose*.yml`.
- **Mitigation:**
  - Sequence v2.0 carve-outs to NOT touch the PRs in flight. Coordinate before each v2.0 phase PR opens.
  - Each issue #1234 PR description has a "merge tolerance" section listing which v2.0 lands it tolerates.
  - Most overlaps are mechanical rebases.
- **Rollback:** rebase issue #1234 PR on top of v2.0 changes. If conflict is non-mechanical, pause issue #1234 PR until v2.0 PR is reviewed and the merge contract is clear.

## R7 — `make quality` regression after hook deletion (HIGH)

- **Trigger:** PR 4 deletes a hook whose intended invariant is not actually enforced by the replacement (structural guard or CI step).
- **Mitigation:**
  - PR 4 internal commit ordering: ALL new structural guards must be added (and pass on main) BEFORE any hook is deleted. Spec enforces this.
  - Per-deleted-hook coverage map (`.pre-commit-coverage-map.yml`) lists where each hook's enforcement now lives.
  - Red-team test list (PR 4 §Verification): introduce a fake violation of each migrated rule on a scratch branch; CI must catch.
- **Rollback:** revert PR 4 commit; pre-commit reverts cleanly because hook deletion is symmetric.

## R8 — Coverage drops > 2% after CI restructure (MED)

- **Trigger:** PR 3's reusable workflows miss a test path; combined coverage post-Phase A is below the pre-rollout 55.56%.
- **Mitigation:**
  - Coverage advisory for 4 weeks per D11.
  - Phase A overlap runs both old and new workflows in parallel; baseline-current values are captured during this window.
  - `.coverage-baseline = 53.5` (current - 2pp) absorbs measurement noise from xdist worker shuffling.
- **Rollback:** investigate (find the missed path); fix forward in a small follow-up PR. If unfixable in <48h, extend advisory window.

## R9 — Dependabot deluge week 1 (>15 PRs) (LOW sev, HIGH prob)

- **Trigger:** Dependabot's first cron after PR 1 lands; opens many PRs at once.
- **Mitigation:**
  - `open-pull-requests-limit: 5` per ecosystem caps initial cycle at ~13 PRs.
  - PR 1 lands on a Friday so the first cycle (Saturday UTC) has weekend triage runway.
  - Aggressive grouping (`minor-and-patch` in one PR per ecosystem) keeps weekly steady-state ~3-5 PRs.
- **Rollback:** temporarily set `open-pull-requests-limit: 1` for first cycle; revert in week 2. Last-resort: disable Dependabot at repo level for 1 week.

## R10 — OpenSSF Scorecard < 7.5 after all PRs (MED)

- **Trigger:** post-PR-5 Scorecard run reports < 7.5/10.
- **Mitigation:**
  - Pre-flight A9 captures baseline; track delta per PR.
  - Common gaps: Branch-Protection penalty for admin-bypass (we accept this per D2), Signed-Releases (deferred per D4), Fuzzing (low-weight gap).
  - File a follow-up issue with specific check failures. Not a rollout blocker; iterate.
- **Rollback:** none needed. Document gap in [03-decision-log.md](03-decision-log.md) and continue.

## Cross-cutting risk: Dependabot review backlog (D5 sustainability tripwire)

Documented in D5. Watch the metric:

```bash
gh pr list --author "app/dependabot" --state open --json number --jq 'length'
```

If consistently > 5 across two consecutive Mondays, pause forward work on issue #1234 and clear backlog. If backlog persists, revisit D1 (recruit second maintainer) — never D5 (auto-merge).

## Risk-monitoring cadence

- **During each PR review:** check the 1-2 risks specifically tagged for that PR
- **Weekly during the 5-week rollout:** check Dependabot backlog metric
- **End of Week 4:** R3 trigger check (CodeQL finding count) before flipping to gating
- **End of Week 5:** R10 final Scorecard check + close issue #1234
