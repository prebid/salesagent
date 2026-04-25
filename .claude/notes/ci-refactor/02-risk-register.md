# Risk Register

Risks for the 6-PR rollout. Severity × probability ranked. Each entry has a trigger (how you know it fired), a mitigation (preventive), and a rollback (corrective). Entries R11-R15, R17-R18, R24-R25 from `research/edge-case-stress-test.md` remain LOW-impact informational; R19/R20/R23 promoted into base register and R26-R30 added in 2026-04-25 P0 sweep; R16 promoted and R31/R32 added in 2026-04-25 Round 9 verification sweep.

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
| 19 | Path-filtered job 422 (Migration Roundtrip never reports on non-alembic soak PR) | High | Med | PR 3 Phase B |
| 20 | Compromised Dependabot PR via bypass actor | **Crit** | Low | PR 1 |
| 23 | Concurrent admin overwrite of branch protection mid-rollout | Med | Med | All |
| 26 | CodeQL CSRF estimate 2.4× low (v2.0 dual-state inflates) | Med | High | PR 1 |
| 27 | `actions/upload-artifact` tar-bomb of `.coverage.*` | Low | Low | PR 3 |
| 28 | `concurrency: cancel-in-progress` cancels mid-flip dispatch | Low | Med | PR 3 Phase B |
| 29 | release-please tag publish races with cosign sign step | High | Low | PR 6 |
| 30 | GitHub repo `allow_auto_merge` toggle bypasses D5 | **Crit** | Low | All |
| 16 | Concurrent Dependabot PRs corrupt `uv.lock` | Med | High | PR 1 |
| 31 | `integration_db` per-test CREATE DATABASE saturates Postgres pool under xdist | Med | High | PR 3 |
| 32 | Phase B branch-protection flip leaves in-flight PRs in "expected — waiting" | Low | Med | PR 3 Phase B |

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
  - Coverage hard-gated from PR 3 day 1 per D11 (revised 2026-04-25 P0 sweep — no advisory window). A single PR dropping >2pp triggers immediate failure.
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

## R19 — Path-filtered job 422 (HIGH, MED prob, PR 3 Phase B)

- **Trigger:** `Migration Roundtrip` is path-filtered to `paths: ['alembic/versions/**']` in `_pytest/action.yml`. A 48h soak window run on a non-alembic PR will never publish that check name; the Phase B PATCH would 422 against a missing required check.
- **Mitigation:**
  - Force a `workflow_dispatch` trigger on `ci.yml` BEFORE the Phase B flip, so all 11 jobs publish their check names regardless of path filtering.
  - `capture-rendered-names.sh` verifies the published set against the 11 frozen names AFTER the forced full-suite run, not just from passive soak.
- **Rollback:** if Phase B PATCH 422s, the inverse PATCH from `branch-protection-snapshot.json` restores the prior contexts.

## R20 — Compromised Dependabot PR via bypass actor (CRITICAL, LOW prob, PR 1)

- **Trigger:** @chrishuie's account is compromised (phishing) and the attacker submits a Dependabot security update PR with a malicious payload via the bypass mechanism, OR clicks "Enable auto-merge" on the PR (R30 attack chain).
- **Mitigation:**
  - **A11 pre-flight audit** (`gh api repos/prebid/salesagent --jq '.allow_auto_merge'` MUST be `false`).
  - Daily cron `branch-protection-snapshot.yml` Action (R23 mitigation) detects unexpected bypass-list mutations.
  - 24h cooldown label workflow on Dependabot PRs (auto-applies `cooldown-24h` label that blocks merge until 24h post-open) — gives a human review window even under bypass.
  - Hardware MFA on the bypass actor's GitHub account (organizational, out of plan scope but recommended).
- **Rollback:** revert the malicious commit; rotate any leaked secrets; audit downstream commits in the 72h window.

## R23 — Concurrent admin overwrite of branch protection mid-rollout (MED, MED prob, all PRs)

- **Trigger:** a concurrent org admin updates branch protection via the GitHub UI for an unrelated reason during the 5-week rollout. The next Phase B execution PATCHes against a stale snapshot.
- **Mitigation:**
  - Daily cron `branch-protection-snapshot.yml` Action: runs `gh api repos/.../branches/main/protection > .github/.branch-protection-snapshot.json` and opens an issue (with a label and a CODEOWNERS auto-request) if `git diff` against the prior snapshot shows non-empty drift. Run nightly UTC.
  - Phase B re-captures snapshot immediately before flip.
- **Rollback:** apply the most recent pre-drift snapshot via inverse PATCH.

## R26 — CodeQL CSRF estimate 2.4× low (MED, HIGH prob, PR 1)

- **Trigger:** PR 1's first CodeQL run reports 200-300 CSRF findings (not 99). Cause: v2.0 mid-flight has both Flask `src/admin/blueprints/` AND new FastAPI `src/admin/routers/` emitting CSRF candidates. Verified disk count: 107 mutating + 99 GET-only = 237 total Flask routes.
- **Mitigation:**
  - D10 Path C (advisory CodeQL for 2 weeks) absorbs this — PR 1 is not blocked by the inflated count.
  - Triage budget scaled to 200-300 findings (3-4× the original estimate).
  - Allowlist Flask Blueprint routes that v2.0's `src/admin/csrf.py` will protect with `# codeql:ignore-csrf-deferred-to-v2-fastapi-csrf-middleware` comments. Reduces noise during advisory window.
- **Rollback:** keep CodeQL advisory indefinitely if findings exceed 300; document in a new ADR.

## R27 — `actions/upload-artifact` tar-bomb of `.coverage.*` (LOW, LOW prob, PR 3)

- **Trigger:** `_pytest/action.yml` (the new composite from PR 3 Decision-4) uploads `.coverage.${{ inputs.tox-env }}` with `include-hidden-files: true`. If a test creates `.coverage` files in unexpected paths (e.g., `/tmp/`, sub-pkg dirs), the upload glob picks up files outside intended scope, leaking test artifacts or paths into a public artifact.
- **Mitigation:**
  - Scope upload glob to literal path: `path: '.coverage.${{ inputs.tox-env }}'` (no wildcard).
  - Add `if-no-files-found: error` to fail noisily if the file is absent.
- **Rollback:** delete the leaky artifact via `gh api -X DELETE`; tighten glob in next PR.

## R28 — `concurrency: cancel-in-progress` cancels mid-flip dispatch (LOW, MED prob, PR 3 Phase B)

- **Trigger:** maintainer pushes a confirmation commit during Phase B Step 1b's `workflow_dispatch` on `ci.yml`. The dispatch run gets cancelled by next-push, leaving rendered-names.txt partially populated.
- **Mitigation:**
  - `capture-rendered-names.sh` waits for `gh run watch <run-id>` to reach `completed` status, NOT just to start.
  - `concurrency: cancel-in-progress: true` is gated to `pull_request` trigger only (not `push` or `workflow_dispatch`); state explicitly in `ci.yml`.
- **Rollback:** re-run `workflow_dispatch` and re-execute `capture-rendered-names.sh`.

## R29 — release-please tag publish races with cosign sign step (HIGH, LOW prob, PR 6)

- **Trigger:** release-please pushes a tag and triggers `release_created`. The `publish-docker` job (modified by PR 6) builds + pushes + cosign-signs in one job. cosign step fails (Sigstore TUF refresh hiccup) — tag exists in git AND image pushed but unsigned. Verifiers downstream get the tag without signature; rollback requires deleting GHCR tag + git tag (irreversible if anyone pulled it).
- **Mitigation:**
  - Split `publish-docker` into two jobs (build+push, then sign) with explicit `needs:` and a failure-blocks-tag gate.
  - Document that cosign failures require manual re-sign or tag-deletion + re-cut.
  - Sigstore Rekor v2 transition (TUF-distributed; cosign auto-upgrades) reduces flakiness over time.
- **Rollback:** delete the unsigned GHCR tag; delete the git tag; cut a new patch release.

## R30 — GitHub repo `allow_auto_merge` toggle bypasses D5 (CRITICAL, LOW prob, all PRs)

- **Trigger:** GitHub repo Settings → General → "Allow auto-merge" is enabled (default ON for new repos). Even with D5 forbidding auto-merge in `dependabot.yml`, ANY PR (Dependabot included) can be enabled for auto-merge by clicking the button. Combined with R20 (phishing the bypass actor), an attacker who phishes @chrishuie can merge a malicious Dependabot PR via auto-merge button click.
- **Mitigation:**
  - **A11 pre-flight audit** (`gh api repos/prebid/salesagent --jq '.allow_auto_merge'` MUST be `false`). Disable via UI Settings → General → Pull Requests → Disable "Allow auto-merge", or via `gh api -X PATCH /repos/prebid/salesagent -f allow_auto_merge=false`.
  - Daily cron snapshot Action (R23 mitigation) diffs `.allow_auto_merge` value and opens an issue if drifted to `true`.
- **Rollback:** flip the toggle off via API or UI; audit recent merges for unauthorized auto-merge events.

### R16 — Concurrent Dependabot PRs corrupt `uv.lock`

**Severity:** Medium × High = High (promoted from informational in Round 9)

**Likelihood:** High once Dependabot is enabled (PR 1 commit 4) — two grouped update PRs both regenerate the lock; second-merger has stale lock conflicts.

**Detection:** post-merge CI fails on `uv lock --check`; or runtime errors from version-mismatched dependencies.

**Mitigation:**
- Dependabot config uses aggressive grouping (`python-runtime`, `python-dev`, `gcp-stack`) to reduce concurrent PR count.
- D5 forbids auto-merge — every Dependabot PR is hand-reviewed and rebased before merge.
- Optional: serialize Dependabot PRs via a "dependabot-merge-queue" label (manual process).

**Tripwire:** ≥2 Dependabot PRs in-flight simultaneously → require explicit rebase before second merges.

### R31 — `integration_db` per-test CREATE DATABASE saturates Postgres connection pool under xdist

**Severity:** Medium × High = High

**Likelihood:** High (any `tox -e integration -- -n auto` run with default Postgres config)

**Detection:** First `pytest -n auto` run after PR 3 lands; `connection refused` errors in test logs.

**Mitigation:**
- PR 3 Commit 4b switches `integration_db` to template-clone (`CREATE DATABASE foo TEMPLATE template_db`); ~10-50× faster than per-test `metadata.create_all`.
- Pre-flight 3a runs xdist soak (`-n 4` and `-n auto`) on current main BEFORE Phase A merge.
- Postgres connection limit may need tuning (`max_connections`); document in `_pytest` composite.

**Tripwire:** xdist run takes longer than serial baseline → revert to matrix-shard model.

### R32 — Phase B branch-protection flip leaves in-flight PRs in "expected — waiting" state

**Severity:** Low × Medium = Medium

**Likelihood:** Medium (any open PR at flip time without subsequent push will see status divergence)

**Detection:** post-flip PRs show "5 required checks not yet run" despite Phase A having reported old check names green.

**Mitigation:** PR 3 Phase B Step 2.5 captures in-flight PR HEAD SHAs before the PATCH. After flip, either (a) trigger `gh workflow run ci.yml --ref refs/pull/<n>/head` for each, or (b) post a coordination comment that contributors must push a no-op commit to refresh status.

**Tripwire:** ≥5 PRs stuck in "expected — waiting" after flip → escalate, post-mortem flip procedure.

## Cross-cutting risk: Dependabot review backlog (D5 sustainability tripwire)

Documented in D5. Watch the metric:

```bash
gh pr list --author "app/dependabot" --state open --json number --jq 'length'
```

If consistently > 5 across two consecutive Mondays, pause forward work on issue #1234 and clear backlog. If backlog persists, revisit D1 (recruit second maintainer) — never D5 (auto-merge).

## Risk-monitoring cadence

- **Daily (cron) during the 6-week rollout:** branch-protection snapshot drift check (R23 mitigation, also covers R20 and R30 attack chains)
- **During each PR review:** check the risks specifically tagged for that PR
- **Weekly during the 6-week rollout:** check Dependabot backlog metric
- **End of Week 4:** R3 trigger check (CodeQL finding count) before flipping to gating; R26 follow-up if count >300
- **End of Week 5:** R10 interim Scorecard check
- **End of Week 6:** R10 final Scorecard check (≥7.5/10) + close issue #1234
