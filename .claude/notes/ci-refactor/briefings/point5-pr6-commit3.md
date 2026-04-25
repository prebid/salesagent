## Cold-start briefing — Point 5: PR 6 commit 3 (harden-runner block-mode flip) being authored, 2 weeks of audit data captured

**Where you are in the rollout**
- Calendar week: Week 7 (post-rollout follow-up territory)
- PRs merged: PR 1, PR 2, PR 3 (all 3 phases), PR 4, PR 5; PR 6 commits 1 and 2
- PRs in flight: PR 6 commit 3 (harden-runner audit→block flip)
- PRs pending: none from issue #1234; this is the post-rollout Fortune-50 hardening follow-up (was D-pending-4, deferred from the 5-PR rollout)
- v2.0 phase PR coordination: v2.0 phase PRs are likely landing actively now; verify with `gh pr list --search "head:v2"`

**Context on PR 6**
PR 6 was filed as a follow-up issue per D-pending-4 ("harden-runner adoption — Fortune-50 pattern not in the issue. Default: file as a follow-up PR 6 candidate; out of scope for the 5-PR rollout."). It adds StepSecurity's `harden-runner` action to all CI jobs, which monitors and gates network egress.

- Commit 1 (Week 5): added `harden-runner` in `egress-policy: audit` mode to every job in `.github/workflows/ci.yml`. SARIF uploads went to GitHub's security dashboard. No traffic blocked
- Commit 2 (likely Week 5-6): may have added harden-runner to `_pytest.yml` reusable workflow and `security.yml` if not in commit 1; or refined audit thresholds
- Commit 3 (now, Week 7): flips `egress-policy: audit` → `egress-policy: block` and adds an explicit `allowed-endpoints:` list derived from the 2-week audit

**What you can rely on (already true on main)**
- `harden-runner@<SHA>` is in every job in `ci.yml`, `_pytest.yml` (or invoked through it), `security.yml` (verify with `grep -RE 'harden-runner' .github/workflows/`)
- 2 weeks of audit data has been collected — visible at GitHub Security tab → "Code scanning" → "harden-runner" alerts
- StepSecurity's web dashboard at app.stepsecurity.io has the egress correlation data (if user enrolled)
- All other rollout artifacts: 11 frozen check names enforced, `.pre-commit-config.yaml` ≤12 hooks, layered hook model active, version anchors consolidated, all 5 PRs merged
- Branch protection: 11 frozen names + `@chrishuie` bypass; PR 5 added `tests/unit/test_architecture_uv_version_anchor.py`
- Coverage: should have flipped from advisory to gating in Week 7-8 per D11 tripwire — verify with `grep continue-on-error .github/workflows/ci.yml` (coverage job should NOT have it)

**FORENSICS: What the LAST agent likely did before context wiped**
The previous agent was authoring PR 6 commit 3. They would have:
1. Pulled the 2-week audit data from GitHub Code Scanning alerts (or StepSecurity dashboard if enrolled)
2. Compiled the `allowed-endpoints:` list from observed legitimate egress: at minimum `api.github.com:443`, `pypi.org:443`, `files.pythonhosted.org:443`, `objects.githubusercontent.com:443`, `github.com:443`, `pkg.actions.githubusercontent.com:443`, `gcr.io:443` (for codeql), and `ghcr.io:443` (for the uv image in PR 5)
3. Updated each `harden-runner` step to use `egress-policy: block` and the compiled allowlist
4. Possibly hit a step that needed an unobvious endpoint (e.g., `dl-cdn.alpinelinux.org` for postgres:17-alpine startup, `proxy.golang.org` for tooling, etc.)

**"Did the previous agent leave anything broken?" (forensics checklist)**
- [ ] `git status` clean? Stash exists?
- [ ] `git log origin/main..HEAD --oneline` — should show 0-2 commits depending on how far the agent got
- [ ] `git diff` — see in-flight `.github/workflows/*.yml` edits
- [ ] Any branches matching `feat/ci-refactor-pr6-*` or `chore/harden-runner-*`?
- [ ] Any escalation files in `.claude/notes/ci-refactor/escalations/pr6-*.md`?
- [ ] Was the agent attempting to compile the egress allowlist? Look for `/tmp/harden-runner-egress.txt` or `.claude/notes/ci-refactor/harden-runner-allowlist.txt`
- [ ] Run `grep -RE 'egress-policy: (audit|block)' .github/workflows/` — see how far the flip got

**What you can rely on (likely on the local branch)**
- Commit 1 (already on main): `harden-runner` in audit mode on every job
- Commit 2 (likely on main): refinements (e.g., audit on `_pytest.yml` reusable; SARIF upload tweaks)
- The 2-week audit window has captured legitimate egress

**What you must NOT do**
- Do not flip to `block` mode without a complete allowlist — CI will fail on any unenumerated endpoint, locking the repo
- Do not assume the audit data is complete — some endpoints only fire in specific CI scenarios (e.g., e2e tests, BDD tests). The allowlist must cover ALL 11 frozen check jobs' egress
- Do not skip hardening for any one job to "make it work" — the security posture is uniform or it isn't
- Do not amend prior commits or rebase

**Files you'll touch in this PR (heat map)**
- Primary (modify): `.github/workflows/ci.yml` (every job's `harden-runner` step), `.github/workflows/_pytest.yml` (the reusable workflow's harden-runner block), `.github/workflows/security.yml`, `.github/workflows/codeql.yml`, `.github/workflows/release-please.yml`, `.github/workflows/pr-title-check.yml`, `.github/workflows/ipr-agreement.yml`
- Possibly add: `.github/harden-runner-allowlist.yml` (a single source of truth for the allowed-endpoints, referenced by each workflow via `allowed-endpoints: ${{ needs.allowlist.outputs.endpoints }}` or inlined)

**Verification environment**
- Before flipping: pull all 2 weeks of audit data via the GitHub API or StepSecurity dashboard. Compile per-job and aggregate
- After each workflow file edit: run via `gh workflow run ci.yml -F branch=harden-runner-flip` (manual workflow_dispatch) and observe egress events
- Test in a feature branch first; Phase B-style admin caution applies

**Specific commands to run FIRST (in order)**
1. `cd /Users/quantum/Documents/ComputedChaos/salesagent && git status && git log origin/main..HEAD --oneline`
2. Pull audit data: `gh api repos/prebid/salesagent/code-scanning/alerts --paginate --jq '[.[] | select(.tool.name == "harden-runner")]' > /tmp/harden-runner-alerts.json`
3. Extract the egress endpoints actually observed: `jq -r '.[] | .most_recent_instance.message.text' /tmp/harden-runner-alerts.json | grep -oE 'to [a-z0-9.-]+:[0-9]+' | sort -u`
4. Cross-reference against the per-job egress profile (different jobs need different endpoints — schema-contract job vs e2e job)
5. Read `.claude/notes/ci-refactor/escalations/pr6-*.md` if exists
6. Inspect any in-flight `.github/workflows/*.yml` edits via `git diff`

**Decisions in effect**
The original D-pending-4 ("harden-runner... Default: file as a follow-up PR 6 candidate") has been promoted to active by the existence of PR 6. New decision needed: how broad is the allowed-endpoints list — minimal (require user to update for each new dependency) vs broader (less friction, slightly weaker posture). Default: minimal, scoped per-job, with a clear comment marking each entry.

**Risks active right now**
- New risk: incomplete allowlist locks out CI runs. Mitigation: run a "pre-flip" PR that flips ONE job to block mode first; observe; then expand. This is analogous to PR 3's Phase A overlap pattern
- R10 (Scorecard <7.5): harden-runner adoption boosts Scorecard's "Pinned-Dependencies" and "Token-Permissions" weights — verify post-flip with `docker run --rm -e GITHUB_AUTH_TOKEN=$(gh auth token) gcr.io/openssf/scorecard:stable --repo=github.com/prebid/salesagent --format=json | jq '.score'`
- v2.0 coordination: if a v2.0 phase PR is in flight when this PR opens, harden-runner block mode may break the v2.0 PR's CI runs. Coordinate with v2.0 timing

**Escalation triggers**
- The 2-week audit data is incomplete (e.g., e2e tests didn't run during the window): STOP, extend audit by another week before flipping
- Any flip-to-block test run fails on a legitimate endpoint not in the audit: STOP, do NOT add to allowlist reflexively — investigate why audit missed it
- Any harden-runner SARIF upload itself fails (network egress to GitHub): STOP, this means harden-runner is breaking its own reporting — investigate ordering of steps

**How to resume the work**
1. Determine commit 3's progress from `git diff` and any escalation files
2. Compile the complete allowlist per-job (different jobs have different egress profiles — `quality-gate` needs `pypi.org`, `e2e-tests` needs whatever the test stack reaches, `coverage` needs upload endpoints)
3. For each workflow file, transform `egress-policy: audit` → `egress-policy: block` and add `allowed-endpoints: |` block per job
4. Test by re-running each workflow once on a feature branch, observe failures, iterate
5. When all 11 jobs run green with block mode: commit, push, open PR (user owns push)
6. After merge: re-run OpenSSF Scorecard; verify ≥7.5

**Where to find context**
- `.claude/notes/ci-refactor/03-decision-log.md` D-pending-4 — the original deferral decision
- StepSecurity harden-runner docs (`https://github.com/step-security/harden-runner`) — for syntax
- `/tmp/harden-runner-alerts.json` — the audit data (you just pulled)
- Previous PR 6 commits — `git log --oneline | head -10` shows the harden-runner introduction pattern
- `.claude/notes/ci-refactor/02-risk-register.md` R10 — Scorecard target

---
