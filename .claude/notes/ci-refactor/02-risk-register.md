# Risk Register

Risks for the 6-PR rollout. Severity × probability ranked. Each entry has a trigger (how you know it fired), a mitigation (preventive), and a rollback (corrective). Entries R11-R15, R17-R18, R21-R22, R24-R25 from `research/edge-case-stress-test.md` remain LOW-impact informational; R19/R20/R23 promoted into base register and R26-R30 added in 2026-04-25 P0 sweep; R16 promoted and R31/R32 added in 2026-04-25 Round 9 verification sweep; **R33-R37 added in 2026-04-26 Round 10 completeness audit sweep; R38-R42 added in 2026-04-26 Round 11 verification sweep; R43 added in 2026-04-26 Round 12 verification sweep; R44 added in 2026-04-26 Round 12 post-issue-review (surfaced while rewriting #1228 Cluster A4); R45-R47 added in 2026-04-26 Round 13 boss-level / multi-team review (cache poisoning, GHA minute spike, status-check lag)**.

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
| 33 | Pre-push hook tier silently disabled when contributor runs `pre-commit install` | **Crit** | High | PR 4 |
| 34 | `ADCP_AUTH_TEST_MODE` module-level env mutation leaks across xdist workers | High | Med | PR 3 |
| 35 | Schema Contract job runs under wrong tox env (DATABASE_URL drift) | Med | Med | PR 3 |
| 36 | PR 6 `Security / Dependency Review` breaks PR 3's frozen-checks structural guard | Med | High | PR 6 |
| 37 | New pre-commit hook added between PR 1 author and merge slips by SHA-freeze | Med | Low | PR 1 |
| 38 | Frozen-checks structural guard 11→14 transition deadlock | **Crit** | Med | PR 3 |
| 39 | Phase B snapshot file single point of failure for rollback | High | Low | PR 3 Phase B |
| 40 | Cosign + Rekor outage + tag immutability cascade traps unsigned tag | High | Low | PR 6 |
| 41 | CODEOWNERS / dependabot.yml syntax error silently breaks routing or stops dep updates | High | Low | PR 1 |
| 42 | Phase A overlap window exhausts GHA runner-minutes / memory under double workflow load | Med | Med | PR 3 Phase A |
| 43 | Verify-script drift behind spec amendments (silent skip of D-mandated content) | Med | High | All |
| 44 | release-please ships signed-but-broken image (no test gate before cosign+SBOM) | **Crit** | Low | PR 6 |
| 45 | actions/cache cross-PR poisoning via fork-PR cache scope | Med | Med | PR 1, PR 3 |
| 46 | GHA usage-minute spike during Phase A overlap | Med | Med | PR 3 Phase A |
| 47 | Status-check name propagation lag at Phase B flip | Med | Low | PR 3 Phase B |

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
  - Force a `workflow_dispatch` trigger on `ci.yml` BEFORE the Phase B flip, so all 14 jobs (D17 amended by D30) publish their check names regardless of path filtering.
  - `capture-rendered-names.sh` verifies the published set against the 14 frozen names (D17 amended by D30) AFTER the forced full-suite run, not just from passive soak.
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
- Postgres connection limit cannot be tuned via GHA `services:` (no `command:` field). Per **D40** (Round 11) + amended Round 12 R12A-01 fix: app-side override via `DB_POOL_SIZE=4` + `DB_MAX_OVERFLOW=8` env vars in `_pytest/action.yml` AND wired in `src/core/database/database_session.py` (without the source-code wiring, the env override silently no-ops — Round 12 caught this, fix landed in PR 2 commit between 4.5 and 5).

**Tripwire:** xdist run takes longer than serial baseline → revert to matrix-shard model.

### R32 — Phase B branch-protection flip leaves in-flight PRs in "expected — waiting" state

**Severity:** Low × Medium = Medium

**Likelihood:** Medium (any open PR at flip time without subsequent push will see status divergence)

**Detection:** post-flip PRs show "5 required checks not yet run" despite Phase A having reported old check names green.

**Mitigation:** PR 3 Phase B Step 2.5 captures in-flight PR HEAD SHAs before the PATCH. After flip, either (a) trigger `gh workflow run ci.yml --ref refs/pull/<n>/head` for each, or (b) post a coordination comment that contributors must push a no-op commit to refresh status.

**Tripwire:** ≥5 PRs stuck in "expected — waiting" after flip → escalate, post-mortem flip procedure.

### R33 — Pre-push hook tier silently disabled when contributor runs `pre-commit install`

**Severity:** Critical × High = Critical (load-bearing for D27 hook math)

**Likelihood:** High — every contributor who follows `docs/development/contributing.md`'s `pre-commit install` instruction without `--hook-type pre-push` qualifier has zero pre-push hooks installed. Default-state failure.

**Detection:** post-PR-4, `git ls-files .git/hooks/pre-push` is absent on contributor machines. Mypy never runs locally before push despite D3. The 10 hooks moved to pre-push (D27: `check-docs-links, check-route-conflicts, type-ignore-no-regression, adcp-contract-tests, mcp-contract-validation, mcp-schema-alignment, check-tenant-context-order, ast-grep-bdd-guards, check-migration-completeness, mypy`) silently no-op.

**Mitigation:**
- D31: PR 4 commit 1 adds `default_install_hook_types: [pre-commit, pre-push]` to top of `.pre-commit-config.yaml`. Single `pre-commit install` auto-installs both hook types.
- `scripts/check-hook-install.sh` (added in P0 sweep) warns at `make quality` time if `.git/hooks/pre-push` is absent.
- `docs/development/contributing.md` instruction simplified to `pre-commit install` (no `--hook-type` qualifier needed).

**Rollback:** if D31 is incorrectly omitted from PR 4 (e.g., merge conflict drops it), remediate via a follow-up one-line PR. Pre-push hooks are recoverable per-contributor: `pre-commit install --hook-type pre-push` works at any time.

### R34 — `ADCP_AUTH_TEST_MODE` module-level env mutation leaks across xdist workers

**Severity:** High × Medium = High

**Likelihood:** Medium (any `tox -e integration -- -n auto` run that exercises auth-mode-sensitive paths)

**Detection:** `tests/integration/conftest.py:104, 661` does `os.environ["ADCP_AUTH_TEST_MODE"] = "true"` directly (NOT `monkeypatch.setenv`). Under xdist `-n auto` (PR 3 commit 4b), worker processes inherit the parent env at fork; one worker's `os.environ.pop()` won't propagate to siblings; one worker's set persists for tests that run later in the same worker. Symptom: intermittent auth assertions fail in tests that don't explicitly opt-in to auth-test-mode.

**Mitigation:**
- Convert both module-level mutations to `monkeypatch.setenv("ADCP_AUTH_TEST_MODE", "true")` inside the `authenticated_admin_session` and `authenticated_admin_client` fixtures. Pytest's `monkeypatch` is per-test, auto-undone, and xdist-safe.
- Pre-flight 3a (xdist soak on current main BEFORE PR 3 Phase A) verifies the conversion works before composite landing.
- If conversion is too invasive (>20 file refactor), gate integration env to `-p no:xdist` with documentation; revisit in follow-up PR.

**Rollback:** revert PR 3 commit 4b (xdist `-n auto` enablement); integration runs serial; module-level env mutation is no longer hazardous.

### R35 — Schema Contract job runs under wrong tox env (DATABASE_URL drift)

**Severity:** Medium × Medium = Medium

**Likelihood:** Medium — depends on PR 3 spec `Schema Contract` job invocation choice.

**Detection:** post-PR-3, `Schema Contract` job fails on `tests/integration/test_mcp_contract_validation.py` with `OperationalError: connect ... DATABASE_URL not set` OR silently skips DB-dependent assertions. Root cause: `tox -e unit` unsets `DATABASE_URL` at `tox.ini:38`, but `test_mcp_contract_validation.py` lives in `tests/integration/` and depends on the DB-loaded MCP tool registry.

**Mitigation:**
- D38: PR 3 spec runs Schema Contract under `tox -e integration` (with DATABASE_URL set), not `tox -e unit`.
- Verification step in PR 3 spec: invoke locally before authoring with `DATABASE_URL=postgresql://… tox -e integration -- tests/integration/test_mcp_contract_validation.py` and confirm exit 0.

**Rollback:** if Schema Contract job consistently times out under integration (resource contention), spin a third tox env `tox -e schema-contract` with DATABASE_URL and minimal fixtures.

### R36 — PR 6 `Security / Dependency Review` breaks PR 3's frozen-checks structural guard

**Severity:** Medium × High = Medium

**Likelihood:** High — guaranteed to fire on PR 6 author start unless explicitly handled.

**Detection:** PR 6 lands; structural guard `tests/unit/test_architecture_required_ci_checks_frozen.py` (added in PR 3 commit, expected list = 14 names per D30) fails because PR 6 introduces a 12-or-15th required check `Security / Dependency Review`.

**Mitigation:**
- PR 6 commit 4 (or later commit) MUST update the guard's expected-list to include the new check name. Spec checklist item: "verify `test_architecture_required_ci_checks_frozen.py` updated."
- PR 6 acceptance gate: `pytest tests/unit/test_architecture_required_ci_checks_frozen.py` passes against the new guard list.

**Rollback:** if PR 6 lands without guard update, fix-forward in a one-line PR adding the check name. Guard failure is local-only; doesn't block other CI.

### R37 — New pre-commit hook added between PR 1 author and merge slips by SHA-freeze

**Severity:** Medium × Low = Medium

**Likelihood:** Low — depends on PR cadence in the PR 1 author window.

**Detection:** post-PR-1 merge, structural guard `test_architecture_pre_commit_sha_pinned` reports a hook with `rev: v<tag>` (not 40-char SHA). Root cause: dependabot's `pre-commit` ecosystem (enabled by PR 1) opens a PR adding a new hook between when PR 1 was authored and when it merged; the new hook arrives with a tag-only `rev:` that PR 1's `autoupdate --freeze` didn't see.

**Mitigation:**
- PR 1 spec adds structural guard `test_architecture_pre_commit_sha_pinned` (one of the 8 governance guards in `drafts/guards/`); fails if any external `rev:` is not a 40-char SHA. Catches the regression on the next CI run.
- PR 1 verification step (final commit before opening PR): re-run `pre-commit autoupdate --freeze` to catch any new hooks.
- Dependabot PR review checklist: verify `rev:` is full SHA before approving.

**Rollback:** fix-forward in a one-line PR replacing the tag with a SHA.

### R38 — Frozen-checks structural guard 11→14 transition deadlock

**Severity:** Critical × Medium = Critical (cascading deadlock)

**Likelihood:** Medium — fires the moment ci.yml emits 14 names and the guard still expects 11.

**Detection:** every PR fails with `test_architecture_required_ci_checks_frozen` reporting "missing rendered name `CI / Smoke Tests`" (and 2 others).

**Mitigation:**
- PR 3 commit 3 introduces both the new ci.yml jobs (14 names) AND the structural guard with the 14-name expected list, in the SAME merge.
- `drafts/guards/test_architecture_required_ci_checks_frozen.py` updated 2026-04-26 (Round 11) to enumerate all 14 names. PR 4 commit 3 lifts this draft into `tests/unit/`.
- Acceptance gate on PR 3: the structural guard must pass against the 14-job ci.yml.

**Rollback:** revert PR 3 if guard fires post-merge (mechanical revert; <10 min).

**Tripwire:** if a 15th required check is added (e.g., `Security / Dependency Review` per R36), update the guard expected list in the SAME PR commit.

### R39 — Phase B snapshot file single point of failure for rollback

**Severity:** High × Low = High

**Likelihood:** Low (snapshot is committed in `.claude/notes/ci-refactor/branch-protection-snapshot.json` per pre-flight A1; corruption requires accidental commit/diff).

**Detection:** Phase B 422 → operator runs `--input branch-protection-snapshot-required-checks.json` → file corrupted/missing → main unmergeable indefinitely.

**Mitigation:**
- Pre-flight A1 captures + commits snapshot. Hash-verify before Phase B (Phase B Step 0 — NEW): `sha256sum branch-protection-snapshot.json` and compare to a stored value in the PR description.
- Phase B Step 1.5: re-fetch from `gh api` and confirm byte-equal against the committed snapshot.
- Daily branch-protection-snapshot cron (R23 mitigation) maintains a parallel history of valid snapshots; recovery target is "yesterday's cron output" if today's is bad.

**Rollback:** if the snapshot is unrecoverable, hand-reconstruct the required-checks list from `gh api repos/.../check-runs` archive of recent main pushes; admin token still required. If admin token also revoked since A1 capture, escalate to org admin for one-time re-issue.

**Tripwire:** if snapshot file changes outside of A1 capture (`git log -- branch-protection-snapshot.json`), investigate before any Phase B operation.

### R40 — Cosign + Rekor outage + tag immutability cascade traps unsigned tag

**Severity:** High × Low = High

**Likelihood:** Low (Sigstore Rekor uptime is high; correlation with a release window is rare).

**Detection:** release-please publishes a tag; cosign sign step fails with `failed to verify against rekor`; image is pushed but unsigned; subsequent steps (Trivy, attest-build-provenance) skip; tag immutability (PR 6 commit 7 admin step) prevents re-push.

**Mitigation:**
- PR 6 commit 2 splits build-and-push from sign-and-attest into two `needs:`-chained jobs (per R29). Sign job's failure does NOT push the tag.
- Add cosign retry: 3 attempts with 30s/60s/120s backoff before failing the job.
- Document the cosign-failure-with-immutable-tag interaction in PR 6 spec; if it fires, the recovery path is `:vX.Y.Z-hotfix.1` with public deprecation of `:vX.Y.Z` (cannot republish).

**Rollback:** if an unsigned tag escapes (sign step skipped due to upstream race), cut a new patch release as `:vX.Y.Z+1` immediately. Document in CHANGELOG. The unsigned tag stays in GHCR but is annotated via release notes.

**Tripwire:** if any release publishes an unsigned image, file an incident retrospective; if it happens twice in 6 months, audit the harden-runner allowlist for Rekor egress completeness.

### R41 — CODEOWNERS / dependabot.yml syntax error silently breaks routing or stops dep updates

**Severity:** High × Low = High (silent failures are the dangerous class)

**Likelihood:** Low (PR review catches most syntax errors) but elevated during PR 1 author iteration.

**Detection:**
- CODEOWNERS bad-syntax: GitHub silently disables routing; PRs require no specific reviewer; `gh api repos/.../codeowners/errors` returns errors.
- dependabot.yml bad-syntax: Dependabot stops opening PRs entirely; no error surface in the UI.

**Mitigation:**
- PR 1 commit 3 (CODEOWNERS) verification step: `gh api repos/prebid/salesagent/codeowners/errors --jq 'length'` MUST be 0.
- PR 1 commit 4 (dependabot.yml) verification step: `gh api repos/.../dependabot/secrets` (or equivalent validation API) must succeed; pre-merge check.
- Pre-flight A21 (NEW, Round 11): once both files are committed in PR 1, validate via `gh api` before merging the PR.

**Rollback:** revert the offending commit; re-author with corrected syntax.

**Tripwire:** if Dependabot doesn't open any PR within 1 week of PR 1 merge, re-validate dependabot.yml syntax.

### R42 — Phase A overlap exhausts GHA runner-minutes / memory under double workflow load

**Severity:** Medium × Medium = Medium

**Likelihood:** Medium (Phase A runs old test.yml + new ci.yml in parallel for 48h; integration jobs in both build creative-agent + Postgres).

**Detection:** ci.yml integration job fails with OOM or runner-minute quota exceeded mid-run; jobs queue in "waiting for available runner."

**Mitigation:**
- Pre-flight 3a (already documented) measures peak memory under `-n auto` BEFORE Phase A. If peak >5GB, hard-cap workers (`xdist-workers: '4'` not `'auto'`).
- Phase A explicitly time-boxed at 48-72h (not indefinite); if overlap window extends, escalate.
- Larger runner upgrade path: `runs-on: ubuntu-latest-4-cores` (16GB RAM, 4 vCPU) is available for runner-quota orgs.
- Concurrency group includes PR-number/SHA so rapid PR pushes cancel in-flight ci.yml runs but NOT main pushes.

**Rollback:** if Phase A overlap fails consistently, revert ci.yml; postpone Phase B; investigate whether new ci.yml has a memory regression vs old test.yml.

**Tripwire:** if peak memory grows >7GB during Phase A measurement, either (a) bump to larger-runner, (b) reduce xdist workers, or (c) split integration into multiple smaller jobs.

### R43 — Verify-script drift behind spec amendments

**Severity:** Medium × High = High (silently masks D-mandated content)

**Likelihood:** High (every sweep round adds new content; verify scripts historically trail by 1-2 rounds — Round 12 R12-B caught 6 verify-script gaps from Round 10/11 additions).

**Detection:** Round-12-style audit re-grep: `grep -L "<new-D-content>" scripts/verify-pr<N>.sh` returns the script as missing the check. Or: a PR ships missing content yet `verify-pr<N>.sh` reports SUCCESS because no grep covers it. Detected after the fact.

**Mitigation:**
- D46 / pre-flight P9: stale-string grep guard catches the most-common drift class (renamed terms, count changes).
- Per-spec invariant (formalized 2026-04-26 Round 12): every D-numbered spec change MUST include a parallel verify-script update in the same commit. Reviewers reject spec changes that don't extend verify-pr*.sh.
- Round-NN verification rounds — but with diminishing returns; after Round 12 the audit cadence should pause unless substantive new content lands.

**Rollback:** if a PR merges with verify-script drift (i.e., the script reported SUCCESS but the spec content was missing), fix-forward in a one-line commit adding the grep.

**Tripwire:** if a future PR ships content NOT covered by its verify script, file an incident retrospective. Three such incidents in 6 months → automate verify-script generation from spec metadata (Round 13+ work).

### R44 — release-please ships signed-but-broken image (no test gate before cosign+SBOM)

**Severity:** Critical × Low = Critical (signed authority on a broken artifact)

**Likelihood:** Low (release-please tags are explicit user actions; main typically green at tag-time) — but the consequence is downstream-impacting and irreversible (per #1234 PR 6 commit 7 admin step, tag immutability locks the broken `:vX.Y.Z`).

**Detection:** the only way to detect post-merge is: a release ships, downstream consumer pulls and breaks; correlate with the SHA's CI status. **Detection happens AFTER the bad image is in the wild.**

**Trigger:** release-please job creates a release_created output before CI on the same commit has completed (race window between push and CI completion), OR CI failed but release-please still fired because `release_created` only checks release-please's own conclusion.

**Mitigation:**
- D47 (Round 12 post-issue-review): PR 6 commit 2 adds a "Require CI green on release commit" step to `publish-docker` BEFORE the docker setup steps. Uses `gh api` to query `ci.yml` workflow conclusion on the same SHA. Refuses to proceed if not `success`.
- Branch protection (existing) requires CI to pass before main merges, but the workflow-fire timing race means a release-please tag could fire seconds after merge while CI is still queueing. The in-workflow gate closes that race.

**Rollback:** if a signed-but-broken image escapes, the recovery is unpleasant per R40 (cosign + tag-immutability cascade): cannot republish `:vX.Y.Z`; must cut `:vX.Y.Z-hotfix.1` and publicly deprecate the broken tag. Document in release notes; pull GHCR images via the digest, not the tag.

**Tripwire:** if any release publishes a signed image whose corresponding CI run conclusion is not `success`, file an incident retrospective. Two such incidents in 6 months → escalate to GitHub's Rulesets-based deployment gating (ADR-009).

### R45 (Med×Med) — actions/cache cross-PR poisoning via fork-PR cache scope (Round 13 addition)

**Risk:** GHA caches are scoped per-branch by default. A fork-PR's cache could be poisoned by a malicious payload that writes to the cache key. When main runs, restoring from a poisoned cache key could execute attacker-controlled binaries during dependency install.

**Vector:** ruff/uv setup-actions use `cache: true` with default key. PR 1 commit 9 SHA-pin sweep should ensure cache keys include `${{ github.event.pull_request.head.sha }}` for fork-PRs.

**Mitigation:**
- Scope cache keys by `${{ hashFiles('**/uv.lock', '**/pyproject.toml') }}` (PR 3 commit 5 already includes both via `cache-dependency-glob` per Round 12 C5 fix)
- Never `restore-keys:` from a fork-PR's cache on main
- Add structural guard: `test_architecture_no_fork_pr_cache_restore.py` (P2 follow-up)

**Probability:** Medium — exploit is publicly known; depends on attacker willing to author a malicious PR.
**Impact:** Medium — would expose CI runners to attacker code; production not directly affected (CI ≠ deploy).

### R46 (Med×Med) — GHA usage-minute spike during Phase A overlap (Round 13 addition)

**Risk:** Phase A runs both old `test.yml` AND new `ci.yml` on every PR push for 48h. If the team is mid-sprint with high PR throughput, GHA monthly minute quota could exhaust mid-rollout.

**Mitigation:**
- Pre-flight A23 (existing): measure baseline GHA minute consumption
- Pre-flight A26 (new): alert if Phase A projects to exceed 80% of monthly quota
- Hard cap: skip `test.yml` runs once cap is hit (kill switch)
- Concurrency: `cancel-in-progress` on PR pushes (already in plan)

**Probability:** Medium — depends on team PR cadence during weeks 3-4
**Impact:** Medium — billing surprise; not functional impact

### R47 (Med×Low) — Status-check name propagation lag at Phase B flip (Round 13 addition)

**Risk:** GitHub's branch protection PATCH succeeds atomically, but the status-check display in the UI can lag 5-15 minutes behind. A flip that succeeds via API may not appear "applied" via UI immediately, leading to ambiguous state during validation.

**Mitigation:**
- Phase B Step 3 (post-flip): wait 15 minutes before declaring success
- Verify `gh api branches/main/protection` reflects the new contexts (not just PATCH returned 200)
- flip-branch-protection.sh's idempotency check handles this naturally

**Probability:** Low — observed but not a hard failure
**Impact:** Low — visual confusion during operator validation window

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
