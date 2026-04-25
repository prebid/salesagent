# Red-Team Stress Test: CI/Pre-commit Refactor

The plan is unusually rigorous. But rigor concentrates blind spots. Here's what's missing.

---

## Dimension 1 — Concurrent-PR cascading failures (top 3 misses)

1. **PR #1217 merges between commits 4 and 5 of PR 2.** Plan assumes PR #1217 either merges before or after PR 2 — never *during*. Internal commit ordering is "load-bearing" (commit 4: `--extra dev` → `--group dev`; commit 5: delete `[project.optional-dependencies].dev`). If PR #1217 merges between those two commits and rebase pulls in adcp 3.12, the rebased branch has commit 4 already applied to v2.0's already-deleted `dev` block on top — `git rebase` produces a clean apply, but commit 5 then deletes a block that doesn't exist, silently no-ops, and the structural guard from commit 8 still passes. The drift is gone, but a follow-up agent is told "PR 2 commit 5 succeeded," when actually it never executed its asserted side effect. No verification catches a no-op delete.
2. **Two Dependabot PRs flight-collide on `uv.lock`.** Plan caps `open-pull-requests-limit: 5` per ecosystem but says nothing about lock-file *serialization*. If two grouped Dependabot PRs both regenerate `uv.lock`, the second to merge has a stale lock; CI succeeds but the merge produces a corrupted lock state. `coverage` job's `coverage combine` won't catch it; `make quality` tests `uv.lock` only via `uv run` invocations which silently re-resolve.
3. **A v2.0 phase PR lands during PR 3 Phase A's 48h soak.** Phase A's contract is "old + new workflows green for 48h." If a v2.0 PR lands on `test.yml` and pushes to main during that soak, Phase A's "green for 48h" clock resets (new `test.yml` content has never run for 48h). Plan does not specify "green for 48h means *unchanged* old workflow for 48h." The 48h window can be silently extended to the heat-death of the universe; eventually the maintainer just flips Phase B without 48h actually being satisfied.

---

## Dimension 2 — Tool failures during execution (top 3 misses)

1. **zizmor finds a critical issue in `ci.yml` itself (chicken-and-egg).** PR 1 commit 11 runs `zizmor` on `.github/workflows/`. If zizmor flags a finding in a workflow PR 3 *will introduce* (`ci.yml`, `_pytest.yml`), nobody finds out until PR 3 commits 1-3 land — at which point zizmor (now gating per PR 1) blocks PR 3's own merge. Plan has no zizmor pre-check on the to-be-introduced files in their pre-merge state. This creates an ordering deadlock unless Phase A explicitly bypasses zizmor on those workflows; spec doesn't mention.
2. **`pre-commit autoupdate --freeze` rate-limits or hangs on hook fetch.** GitHub API rate limits unauthenticated git operations to 60 req/hr. Running autoupdate-freeze across 4 hooks fetches refs and tag SHAs, which can exceed limits if combined with Dependabot/CI traffic in the same hour. Plan's procedure (D12) says "review the diff" but assumes the freeze command completes in seconds. No timeout, no retry guidance. If autoupdate hangs at hook 3 of 4, the maintainer cancels and the partial state is committed — three hooks frozen, one tag-pinned, mixed config that the structural guard (PR 2 commit 8) doesn't catch (it only checks `additional_dependencies`, not `rev:` SHA format).
3. **A SHA-pinned action is force-pushed by upstream (or the SHA is GC'd).** Plan SHA-pins via `gh api repos/$tool/git/refs/tags/$tag --jq '.object.sha'`. If `actions/checkout` ever force-pushes a tag (rare but possible — happened with `tj-actions`), or if a referenced SHA points to an orphan commit that gets GC'd, the workflow silently fails to resolve `uses:` and the job stalls in "queued" forever. Plan has no mitigation; CODEOWNERS doesn't help because nobody pushed a PR. R-status: invisible failure mode.

---

## Dimension 3 — Test environment failures (top 3 misses)

1. **`parse_module()` `lru_cache` keys on `pathlib.Path`, but path comparisons are fragile.** PR 4 commit 1 introduces `@functools.lru_cache(maxsize=2048)` keyed on `pathlib.Path`. If the same logical file is imported via different cwd-relative vs absolute paths (which happens when tests are invoked from a worktree vs main checkout, or via `pytest-xdist` workers with different `os.chdir` state), the cache keys differ but resolve to the same file content — silent O(N) re-parse, latency budget blown, but tests still pass. Worse: under xdist, a worker can mutate cwd; the cache survives across pytest invocations within the worker, so a stale path can return stale AST after a file edit. No `cache_clear()` between test runs.
2. **`make quality` fails because Docker isn't running, but the executor reads it as code failure.** PR 4's verification calls `make quality` after each commit. If the agent's local environment had a Docker daemon shutdown event mid-session (laptop sleep, system update), `make quality` itself doesn't need Docker — but the integration tests it triggers via `tox -e integration` (only when explicitly invoked) do. The plan blurs this: PR 2 commit 3's verification says `make quality passes (including pydantic.mypy plugin re-enabled)`, but pydantic.mypy plugin loading triggers SQLAlchemy model imports which can transitively connect to a DB. Plan doesn't enumerate which `make quality` runs need Docker.
3. **The new `pre-push` stage hooks (R13 mitigation) hang on first invocation.** PR 4 commit 5 moves 5 hooks to `stages: [pre-push]`. The first time a contributor runs `git push`, pre-commit clones the hook environments — which can take minutes for `adcp-contract-tests` and `mcp-contract-validation` (these import the project venv). If the hook hangs (e.g., DNS resolution fail to PyPI), `git push` fails with an opaque error. Contributor diagnoses "git push broken," files an issue, churns. Plan has no `pre-commit install --hook-type pre-push` guidance in CONTRIBUTING.md beyond a single bullet.

---

## Dimension 4 — Branch-protection edge cases (top 3 misses)

1. **The `gh api -X PATCH` returns 422 because contexts haven't appeared yet.** Phase B atomic flip POSTs the 11 frozen names. If Phase A's `ci.yml` has been on main for 48h but no PR has actually triggered all 11 jobs (e.g., `Migration Roundtrip` is `if: paths-filter('alembic/versions/**')`), GitHub's branch-protection API has never *seen* that context name. The 422 says "contexts list invalid: 'CI / Migration Roundtrip' has never reported." Plan assumes 48h soak = all contexts seen. False — only contexts that ran in that window are seen. Migration Roundtrip will never run on a PR that doesn't touch alembic; admin's flip fails hard.
2. **A repo admin (Prebid.org org owner, distinct from @chrishuie) modifies branch protection during the rollout.** Prebid.org has multiple admins. If any admin changes branch protection (e.g., to enable signed commits because of an unrelated security review), they overwrite the bypass list silently. @chrishuie's bypass disappears, all subsequent PRs are blocked from self-approval. Plan has no admin change-monitoring webhook, no scheduled `gh api .../protection` snapshot to detect drift.
3. **Old check-name "Smoke Tests" remains required while `test.yml` still emits it during Phase A.** Phase A's overlap means `test.yml` keeps emitting `Smoke Tests…`. Phase B atomically swaps to the 11 new names. But what about the *interim* — if a PR is open during Phase B, GitHub's "recent checks" view caches the old required list for that PR. Re-running CI on that PR can show stale "Smoke Tests" as required even after Phase B; merging that PR with the new check names green still shows "1 required check missing." Plan offers no "trigger refresh" guidance (the only fix is push an empty commit to the open PR).

---

## Dimension 5 — Plan-execution failure modes (top 3 misses)

1. **`verify-prN.sh` exits 0 but `make quality` would have failed.** Multiple verification scripts use `[[ $(grep -c 'X') == "0" ]]`. If the search expression has a typo (e.g., `grep -c 'GEMINI_API_KEY: secrets'` instead of `grep -c 'secrets.GEMINI_API_KEY'`), the count is 0 trivially and the assertion passes. PR 1 verification step 7 is exactly this shape — `! grep -q 'secrets.GEMINI_API_KEY'` after deletion. If the deletion was incomplete (kept `secrets.GEMINI_API_KEY_v2` or similar), the negation passes. No semantic verification of "the env var is unconditionally `test_key_for_mocking`."
2. **CLAUDE.md guard count math goes off-by-one as v2.0 lands.** PR 4 commit 9 hardcodes "32 rows" in CLAUDE.md. D18 reserves space for "v2.0's 9 guards" → 41 final. If a v2.0 phase PR lands with only 7 guards (not 9), or with 11 (an extra one was added during v2.0 cleanup), the CLAUDE.md count becomes wrong. Plan has no automated row-counter that derives the count from disk; the structural guard (which PR 4 is adding!) doesn't enforce the table count, only its existence. Drift.
3. **Pre-flight checklist captures stale state.** Pre-flight A1-A10 captures branch-protection, coverage, latency, scorecard etc. If pre-flight is run on Monday and PR 1 isn't authored until Friday, captured state can be wrong. Plan says "Re-measure if the value has shifted >1pp since 2026-04" for coverage but doesn't say so for branch protection. If between Monday and Friday a Prebid.org admin enables conversation-required reviews (a plausible policy change), A1's snapshot becomes the *wrong* rollback target — restoring it would *remove* a valid hardening.

---

## Dimension 6 — Human / process failures (top 3 misses)

1. **A Dependabot PR is auto-mergeable due to a label-based GitHub Actions misconfiguration in a different repo template.** D5 forbids auto-merge in this repo's config. But GitHub's *repo-level* settings can include "Allow auto-merge" toggle independent of any workflow. If @chrishuie enables that toggle (perhaps for a different reason, like another repo) at the org level, and Dependabot adds the `auto-merge` label (a default behavior in some configs), the PR can self-merge with no workflow involvement. Plan doesn't audit the org/repo "Allow auto-merge" boolean in pre-flight. Easy miss.
2. **The decision log is changed mid-rollout, but in-flight PRs were authored against the old version.** If decision D17 (the 11 frozen check names) is revised mid-rollout to add a 12th check (e.g., post-PR-5, "CI / Build Image"), but PR 4 was authored against the 11-name list, PR 4's verification scripts hardcode 11 names. Reviewer sees "passing" but actual contract drift. Plan's update protocol (00-MASTER-INDEX) says "update after each PR merges" — not "freeze decision log during PR authoring."
3. **A maintainer keeps a local override that masks PR 1's Gemini key removal.** D15 deletes `secrets.GEMINI_API_KEY` fallback. If the maintainer has `GEMINI_API_KEY` exported in their local shell from a prior run, all local `make quality` invocations during PR 1 authoring use the real key — passing tests they wouldn't have passed in CI. The CI run catches it, but the catch is *late* (only after PR 1 is opened). Plan should `unset GEMINI_API_KEY` in pre-flight P1.

---

## Dimension 7 — Rollback edge cases (top 3 misses)

1. **PR 1 reverted, but the @chrishuie bypass on branch protection persists.** PR 1's coordination note says "after merging, configure branch protection bypass." If PR 1 is reverted, the bypass remains live (a UI config, not a file). Future PRs can self-approve indefinitely. Plan's PR 1 rollback section doesn't list "remove bypass" as a step. Silent governance regression.
2. **PR 3 Phase B reverted, but Phase A workflows are in CI's queue.** Phase B inverse `gh api -X PATCH` restores old check names. If Phase A's `ci.yml` is still on disk and triggering, both old and new contexts report; old is required (post-rollback), new is not, but new can fail spuriously and confuse contributors who see "11 jobs ran, my PR is green per the required list, but reviews show 11 statuses, 5 of which are checking 'CI / X'." Plan's Phase B rollback restores required-check list but doesn't disable `ci.yml`.
3. **PR 4 revert reintroduces deleted hooks, but those hooks reference scripts PR 4 deleted.** PR 4 commit 7 deletes hook definitions; commit 6 consolidated `no-skip-tests` and `no-fn-calls` into `check_repo_invariants.py`. If commit 6 also deleted the originals (likely), reverting just commit 7 reintroduces hooks that reference non-existent scripts. `pre-commit run` errors on every contributor checkout. Rollback section says "Pre-commit reverts cleanly because hook deletion is symmetric" — false; the original scripts are gone. Need a full PR revert, not commit-level.

---

## Dimension 8 — Security edge cases (top 3 misses)

1. **A malicious Dependabot PR (compromised action like `tj-actions`) is opened during the rollout; CODEOWNERS auto-requests @chrishuie; @chrishuie is on bypass.** The bypass (D2) means @chrishuie can self-approve their own PRs — but Dependabot PRs are not @chrishuie's PRs. Wait: re-read ADR-002. "Bypass list: @chrishuie is granted bypass via 'Allow specified actors to bypass required pull requests'." This is *actor* bypass, not author bypass. @chrishuie can merge ANY PR (including Dependabot's) without code-owner review. D5's "no auto-merge" doesn't help — *manual* merge with one click bypasses the second-eyes safety. The real risk: tired Friday-evening review, mass-merge the Dependabot batch, payload lands. Plan does not add a "wait 24h on Dependabot PRs" cooldown.
2. **harden-runner is deferred (D-pending-4) but pip-audit and zizmor are not enough.** PR 1 ships pip-audit + zizmor. Neither catches *runtime exfiltration* during a CI job (the actual tj-actions vector). Plan tags harden-runner as "PR 6 follow-up," meaning the rollout window has 5 weeks of CI runs without runtime hardening. If a malicious action lands via Dependabot during week 2, plan's defenses don't stop exfiltration of `secrets.GITHUB_TOKEN` or environment files. Risk window is 5 weeks long.
3. **Sigstore signing leaks identity.** PR 5 doesn't add signing (D4 deferred), but PR 1's `attest-build-provenance` (mentioned in stress dimension prompt; not in plan) would use keyless signing. If the maintainer's keyless cert email is `chrishuie@personal-gmail.com` not `chris@prebid.org`, every signed artifact leaks the personal email into the public sigstore log. Plan doesn't address this because the feature isn't planned, but if a reviewer suggests adding it during PR 1 review, plan has no "use a github-noreply email" guidance.

---

## Dimension 9 — Long-term decay scenarios (top 3 misses)

1. **12 months in: Dependabot backlog reaches 30 PRs; D5 quietly inverts.** Plan's "tripwire" is "if backlog reaches 5+ across two consecutive Mondays, pause forward work." But this is a *manual* check; nobody runs it. After 12 months the maintainer mass-merges weekly — D5's spirit dies, the wording is technically intact. No automation enforces the tripwire. Suggest: scheduled GitHub Action that posts to an issue when backlog > 5 for 7 days.
2. **6 months in: someone disables a structural guard with `pytest.skip` and forgets.** CLAUDE.md says "NEVER use `pytest.mark.skip`" but a frustrated contributor adds `@pytest.mark.skip(reason="flaky")` to a guard test, opens PR. Pre-commit hook catches it (one of the 11 hooks) IF the hook still exists post-PR-4. After PR 4, that hook may have moved to `pre-push` stage — bypassed by `git push --no-verify`. Plan doesn't add a CI check for `@pytest.mark.skip` in `tests/unit/test_architecture_*.py`.
3. **24 months in: `harden-runner` allowlist is stale.** If PR 6 eventually adds harden-runner with an allowlist of egress endpoints (PyPI, GHCR, Docker Hub), and one of those endpoints deprecates (e.g., `pypi.org` → `pypi.python.org` redirect), harden-runner block-mode silently allows the redirect target, defeating its own audit. Plan doesn't schedule allowlist review. Add to PR 6 follow-up's spec.

---

## Dimension 10 — Tooling-evolution scenarios (top 3 misses)

1. **Mypy strict mode is released and subsumes 5 existing structural guards.** ADR-004 process exists (per CLAUDE.md). But ADR-004 takes ~3 PRs to invoke (deprecate → migrate → remove). During those 3 PRs, both mypy and the structural guard run, doubling check time. Plan doesn't budget for this concurrent-enforcement window.
2. **Ruff custom rules support arrives; contributor proposes migrating 10 guards.** Migration is positive but requires removing entries from `.duplication-baseline`, `.guard-baselines/*.json`, and CLAUDE.md's table — three files in different PRs. Plan's ratchet-only invariant (`Allowlists can only shrink — never add new violations, fix them instead`) doesn't anticipate "remove an entire allowlist" as a legitimate operation. The pre-commit hook `check_code_duplication.py` would crash on missing baseline.
3. **GitHub Actions adds native job-graph dependencies.** If the `_pytest.yml` reusable-workflow architecture is rendered obsolete by a new feature, the migration path is non-trivial because the 11 frozen check names (D17) lock the surface. Plan's D17 "frozen — treat as a contract" is so absolute that even a beneficial feature change requires coordinated branch-protection update. The frozenness becomes its own decay surface.

---

## Top 10 NEW risks (R16-R25)

| # | Risk | Trigger | Probability | Severity | Mitigation | Rollback |
|---|---|---|---|---|---|---|
| R16 | Concurrent Dependabot PRs corrupt `uv.lock` | Two PRs touching `uv.lock` merge within 5 min | Med | High | Add `merge-after: pip-deps-quiet` cooldown via Action; serialize lock-touching merges | `uv lock --reinstall` then commit clean lock |
| R17 | `pre-commit autoupdate --freeze` partial completion | API rate-limit or network blip mid-freeze | Low | Med | Loop with retry + verify all 4 hooks frozen before commit; add post-condition `grep -c 'frozen: v' == 4` | Re-run freeze on top of partial state |
| R18 | SHA-pinned action gets force-pushed/GC'd | Upstream maintainer rewrites tag; orphan SHA collected | V Low | High | Mirror critical actions to internal fork; quarterly SHA refresh job | Pin to fork SHA; PR-fix 1-line update |
| R19 | Branch-protection contexts never reported (Phase B 422) | A required job has `if: paths-filter` and never ran in soak | Med | High | Pre-flip dry-run: trigger workflow_dispatch on `ci.yml` to ensure all 11 jobs report; only flip after success | Inverse PATCH; await missing context emission |
| R20 | Bypass actor approves malicious Dependabot PR | Tired Friday merge of compromised action update | Low | Critical | 24h cooldown label on Dependabot PRs; mandatory `gh pr diff` review checklist | Force-push revert main (with explicit user permission); rotate `GITHUB_TOKEN` |
| R21 | Verification grep typo silently passes | `grep -q` for absence with wrong pattern | Med | Med | Replace negative greps with positive assertion + count check; add property-test layer (`assert var == expected`) | Re-run with corrected pattern; back out commit |
| R22 | Pre-flight state goes stale before PR 1 author | A1-A10 captured > 7 days before PR 1 opens | Med | Med | Re-capture A1, A5, A9 on the day PR 1 is authored; pre-flight has TTL of 7 days | Re-do pre-flight; restart PR 1 |
| R23 | Concurrent admin (org owner) overwrites branch protection | Different admin enables a setting mid-rollout | Low | High | Schedule daily `gh api .../protection` snapshot to `.claude/notes/ci-refactor/`; diff against expected | Reapply expected protection from snapshot |
| R24 | Local maintainer env masks Gemini key removal | `GEMINI_API_KEY` exported in shell from prior session | Med | Low | Pre-flight P-new: `unset GEMINI_API_KEY` and re-run `make quality`; PR 1 commit 10 verification adds `env -i make quality` | None — CI catches it eventually |
| R25 | Structural guard cache returns stale AST under xdist | Worker-local cwd mutation after `parse_module` cache fill | Low | Med | Use `path.resolve()` for cache key; add `pytest_runtest_setup` hook that clears cache | `parse_module.cache_clear()`; re-run |

---

## Top 5 changes to the plan

1. **Add a Phase B pre-flip dry-run.** Before the atomic `gh api -X PATCH`, run `gh workflow run ci.yml --ref main` and wait for *all 11* jobs to emit a status. Only after every check name has appeared in the recent-checks API can you safely reference it. Mitigates R19 (the 422 fail) and the "stale check-name" caching issue.
2. **Add a pre-flight TTL.** Pre-flight checklist gains a "captured at: <ISO timestamp>" header. PR 1's first commit checks `(now - captured_at) < 7 days`; otherwise re-runs A1, A5, A9. Mitigates R22.
3. **Replace negative greps with positive assertions.** Every verification script's `! grep -q 'BAD'` becomes `[[ "$(grep -c 'EXPECTED_NEW_PATTERN' file)" == "1" ]]`. The positive assertion fails on typo; the negative assertion silently passes. Mitigates R21 (quietly the most insidious).
4. **Add a 24h cooldown label on Dependabot PRs.** Workflow that sets `do-not-merge-yet` label on every Dependabot PR for 24h. Maintainer review can happen anytime, but merge requires the label to age out. Mitigates R20 (the highest-severity missing risk) and partially defends D5's spirit against decay.
5. **Add a daily branch-protection drift snapshot.** Scheduled GHA: `gh api .../protection > .branch-protection-current.json; git diff .branch-protection-snapshot.json` and post a notification on diff. Mitigates R23 and creates a post-rollout audit trail.

---

## Rollout-day runbook (per-PR checklist)

For each PR's merge day:

**Pre-merge (T-2h):**
- [ ] Pre-flight checklist completed within last 7 days; no items stale (R22)
- [ ] No other PR (Dependabot, v2.0, contributor) is in "ready to merge" state on overlapping files (R6)
- [ ] `gh pr list --search "is:open"` reviewed for conflicts
- [ ] Last commit on PR branch has all required CI green (and the verification script has run locally)
- [ ] Decision log unchanged since PR was authored (`git log -1 .claude/notes/ci-refactor/03-decision-log.md`) — if changed, re-review
- [ ] Branch-protection drift snapshot is clean (R23)

**At-merge (T=0):**
- [ ] Verification script `verify-prN.sh` returns exit 0
- [ ] All 11 frozen CI checks green (post-PR-3 only; before that, the legacy `test` check)
- [ ] No new Dependabot PR opened in last hour that touches the same files
- [ ] `gh pr merge --squash` (squash to keep linear history; squash msg follows Conventional Commits)

**Post-merge (T+1h):**
- [ ] Smoke test: `gh run list --workflow=ci.yml --branch=main --limit=1 --json conclusion --jq '.[0].conclusion'` returns `"success"`
- [ ] No unexpected check failures on the next PR opened (open a trivial PR if needed)
- [ ] OpenSSF Scorecard re-run shows non-decrease (PR 1 only; later PRs no impact)
- [ ] `00-MASTER-INDEX.md` status column updated; PR # captured
- [ ] If PR 1: confirm bypass actor configured in branch-protection UI
- [ ] If PR 3 Phase B: confirm 11 contexts present via `gh api .../required_status_checks`

**Post-merge (T+24h):**
- [ ] No revert opened
- [ ] Dependabot backlog metric ≤ 5
- [ ] No CI runs on main have `conclusion=failure`

---

## Rollback decision tree (per-PR)

For each PR, the question "revert vs. fix-forward":

- **PR 1 (additive)**: ALWAYS revert if anything fails — purely additive content, fix-forward has no benefit. Exception: if a single ADR has a typo, fix-forward.
- **PR 2 (deletion + plugin enablement)**:
  - If pydantic.mypy errors regress: fix-forward (re-disable plugin in mypy.ini, file follow-up).
  - If `--extra dev` callsite missed and breaks CI: fix-forward (one-line CI fix faster than revert + re-author).
  - If structural guard blocks legitimate `additional_dependencies` (rare): revert.
- **PR 3 Phase A**: Revert if Phase A workflow has a bug. New files only; revert is clean.
- **PR 3 Phase B**: Inverse `gh api -X PATCH` only — no code revert needed (nothing committed for Phase B).
- **PR 3 Phase C**: Revert if `ci.yml` regresses; restores `test.yml`. NEVER fix-forward — the deletion is too coupled to D17.
- **PR 4**: Revert ONLY at the merge level (PR-revert, not commit-level), because hook deletions and script consolidations are interdependent (R7 corrected). Fix-forward is OK only for CLAUDE.md typos.
- **PR 5**: Per-commit revert is fine — commits are independent. Black/ruff reformat regressions: revert just commit 7. Postgres regressions: revert commit 2.

---

## Cumulative risk estimate (Bayesian, independent assumption)

Treating R1-R25 as independent (a deliberate over-simplification — many are correlated through the maintainer's attention budget):

- R1, R7: HIGH severity, LOW probability — P(fire) ≈ 0.05 each
- R2, R3, R4, R8, R10, R16, R19, R21, R22, R24: MEDIUM-MEDIUM — P(fire) ≈ 0.15 each
- R5, R6, R23, R25: MEDIUM-LOW — P(fire) ≈ 0.10 each
- R9: HIGH probability, LOW severity — P(fire) ≈ 0.70
- R17, R18, R20: LOW probability — P(fire) ≈ 0.03 each
- R11-R15 (existing risk register's later items): assume P ≈ 0.10 each

P(no risk fires) = product of (1 - p_i) for all 25 risks
≈ (0.95)² × (0.85)¹⁰ × (0.90)⁴ × (0.30)¹ × (0.97)³ × (0.90)⁵
≈ 0.9025 × 0.1969 × 0.6561 × 0.30 × 0.9127 × 0.5905
≈ 0.0188

**P(at least one risk fires) ≈ 98%.**

This is high because R9 (Dependabot deluge, P=0.70) dominates and is a near-certainty. Excluding R9 (which is low-severity and mitigated):

P(no other risk fires) ≈ 0.0188 / 0.30 ≈ 0.063
**P(at least one HIGH-severity risk fires) ≈ 30-40%** during the 5-week window.

The dominant contributors to "something material goes wrong" are R6 (v2.0 overlap), R19 (Phase B 422), R20 (malicious Dependabot), and R22 (stale pre-flight). Mitigating these four (changes 1, 2, 4, 5 above) drops the joint probability of a material incident to ~15%.

**Bottom line**: a 5-week rollout of this scope cannot have <50% probability of *something* going wrong. The plan's mitigations bring the probability of a *severe* (rollback-required) incident down to ~15-20% if you adopt the 5 plan changes. Without them, R20 alone (compromised Dependabot PR self-approved during the bypass window) carries roughly 1-3% probability — but its severity is catastrophic. That single risk is the strongest argument for the 24h Dependabot cooldown and harden-runner pull-forward into PR 1.
