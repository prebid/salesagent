# CI/Pre-commit Refactor — Executive Summary

**If you have time to read ONE file before being parachuted in, this is it.**
Read order for cold-start: this file → `RESUME-HERE.md` → `pr<N>-<slug>.md` for your PR. ~14-20k tokens total.

Last refresh: 2026-04-26 (Round 12 verification + sweep applied; D46 added, R43 added, propagation-discipline grep-guard introduced, Round 11 self-introduced gaps closed including DB_POOL_SIZE app-wiring per D40 amendment).

---

## What is this?

A 6-PR rollout (issue [#1234](https://github.com/prebid/salesagent/issues/1234)) that brings salesagent to top-tier OSS supply-chain posture. **~19.5-23.5 engineer-days, ~6 calendar weeks part-time.** PRs land sequentially; PR 6 is a Week-6 follow-up. (Effort revised across rounds: Round 10 sweep added ~3.5-4 days for `default_install_hook_types`, 14-name expansion, creative-agent bootstrap, container hardening, gitleaks, ADR promotion. Round 11 added ~0.5 day for D39 script-step revert + D44 + ADR-008 copy. Round 12 added ~0.5 day for the DB_POOL_SIZE app-wiring + D46 propagation guard + verify-script extensions. Calendar slack absorbs without extension.)

A concurrent v2.0 (Flask-to-FastAPI) effort runs under [PR #1221](https://github.com/prebid/salesagent/pull/1221). Per **D20** (Path 1 sequencing), issue #1234 lands first; v2.0 phase PRs rebase onto the new layered model.

---

## Production Deploy Coupling (Round 13 addition)

The salesagent app is deployed to **Fly.io** as `adcp-sales-agent`. The deploy script lives at `scripts/deploy/fly-set-secrets.sh`. The existing `release-please.yml publish-docker` job pushes `:vX.Y.Z` and `:latest` tags to **ghcr.io** AND **Docker Hub** on every release commit. **Fly.io pulls from `ghcr.io/prebid/salesagent:vX.Y.Z`** (verify with `fly image show -a adcp-sales-agent`).

**What this rollout changes for production:**
- **Tag scheme:** Unchanged. `:vX.Y.Z` and `:latest` continue to publish.
- **Cosign signing (PR 6):** Net-new on every release. Fly.io does NOT currently `cosign verify` before pulling. Signing failures do NOT block deploy.
- **D47 gate (Round 12 / R44):** `release-please publish-docker` now requires CI green on the release commit BEFORE publishing. Red main does NOT auto-publish. This is a NET PROTECTION for production (closes a "signed-but-broken-image" attack surface).
- **R29 split (Round 13):** PR 6 splits publish into `build-and-push` + `sign-and-attest`. If sign-and-attest fails (Sigstore outage), the image still publishes; recovery is `:vX.Y.Z-hotfix.1` re-tag once Sigstore returns.

**Production rollback (any breaking release):** `fly deploy --image ghcr.io/prebid/salesagent:v<previous>` — direct rollback via Fly CLI. No CI involvement required.

**Out of scope:** enabling `cosign verify` on the Fly.io side. Documented as a follow-up after PR 6 lands and the signing pattern is proven (~6 weeks).

---

## The 6 PRs

| PR | What it does | Effort | Blocks |
|---|---|---|---|
| 1 | Governance + supply-chain hardening (CODEOWNERS, SECURITY.md, dependabot, zizmor, CodeQL advisory, SHA-pin all hooks/actions, `persist-credentials: false` on every checkout) | 2.5 days | 2, 3 |
| 2 | uv.lock as single source: replace `mirrors-mypy` and `psf/black` with local `language: system` hooks (NOT a deprecation — fixes isolated-env import resolution per [Jared Khan](https://jaredkhan.com/blog/mypy-pre-commit)); delete `[project.optional-dependencies].dev`; re-enable `pydantic.mypy` plugin | 4-6 days | 3 |
| 3 | CI authoritative + composite actions (Decision-4: composite, NOT reusable workflows); **14** frozen bare-name check jobs (D17 amended by D30 — Round 10 added Smoke Tests, Security Audit, Quickstart); creative-agent service bootstrap (D32); 3-phase merge (overlap → rendered-name capture → atomic flip → cleanup); coverage hard-gate from day 1 (D11 revised) | 4.5-5.5 days | 4 |
| 4 | Hook relocation: 5 grep hooks → AST guards; **10** to pre-push (D27 revised — includes mypy per D3); 4 to CI-only; 6 deleted; real math **36 effective − 13 − 10 − 1 = 12** commit-stage (exactly at ceiling, zero headroom); CLAUDE.md guards table audit DEFERS to post-v2.0-rebase (target ~81 rows, D18 revised) | 2 days | 5 |
| 5 | Version consolidation: Python, Postgres, uv anchors single-sourced; `test_architecture_uv_version_anchor` guard. **Black/ruff target-version DEFERRED per D28 (ADR-008 — separate post-#1234 PR)** | 2 days | none |
| 6 | Image supply chain: `harden-runner` (audit→block, **v2.16.0+** for CVE-2025-32955 + GHSA-46g3-37rh-v698), `cosign` keyless signing + SBOM + provenance, dependency-review, `scorecard.yml` self-host (Week 6 follow-up; resolves D-pending-4 → D25) | 1.5-2 days | none |

---

## The 3 critical blockers — STATUS: FIXED in specs

These were the load-bearing defects that would have failed at runtime. All three are now corrected in the per-PR specs (2026-04-25).

| # | Defect | Status |
|---|---|---|
| 1 | PR 3 spec used `name: 'CI / Quality Gate'` job names with workflow `name: CI` — GitHub renders this as `CI / CI / Quality Gate` (auto-prefix). Phase B PATCH would 422. | ✅ Fixed: 11 sites in `pr3-ci-authoritative.md`; D26 added; pre-flip rendered-name capture step at Phase B Step 1b |
| 2 | PR 4 hook math: 37 commit-stage today − 15 deletions − 5 moves = 17 (vs ≤12 acceptance) | ✅ Fixed: real baseline is **36 effective commit-stage** (40 active minus 4 manual). 2 of 15 deletions are already manual. Math: **36 − 13 − 10 − 1 = 12** (exactly at ≤12 ceiling, zero headroom); D27 revised in 2026-04-25 Round 8 sweep — adds mypy as the 10th move per D3. |
| 3 | `tests/unit/_architecture_helpers.py` collision — both PR 2 commit 8 and PR 4 commit 1 said "create" | ✅ Fixed: PR 2 creates baseline (~30 lines); PR 4 EXTENDS to ~221 lines (reconciled draft at `drafts/_architecture_helpers.py`) |

---

## The 48 locked decisions (D1-D48)

D1: Solo maintainer (@chrishuie sole CODEOWNERS) ·
D2: Branch protection + @chrishuie bypass (ADR-002) ·
D3: Mypy in CI, not pre-commit ·
D4: Signed commits deferred ·
D5: NEVER auto-merge Dependabot (sustainability tripwire: 5 backlog → recruit) ·
D6: No merge queue ·
D7: Pre-commit (not prek) for CI; prek optional-local ·
D8: No pre-commit.ci ·
D9: `.claude/` out of scope; CLAUDE.md guards table updated in PR 4 ·
D10: CodeQL Path C — advisory 2 weeks, gating Week 5 ·
D11: Coverage hard-gate from PR 3 day 1 at 53.5% (revised 2026-04-25 P0 sweep) ·
D12: pre-commit autoupdate --freeze ·
D13: Fix pydantic.mypy errors in PR 2 (tripwire >200) ·
D14: Migrate ui-tests extras → dependency-groups (tests/ui/ stays local-only via `tox -e ui`) ·
D15: Delete Gemini key fallback (unconditional mock) ·
D16: Dependabot ignores adcp until #1217 merges ·
D17: 11 frozen CI check names — **AMENDED by D30 to 14 names** ·
D18: **27 baseline + 1 + 4 + 1 + 8 + 27 + 4 + 9 = ~81** final guards (post-v2.0-rebase canonical; revised in 2026-04-25 Round 8 — was ~73, drift-corrected to 81 after v2.0 architecture/ count was re-verified at 27, not 31) ·
D19: Per-PR specs, not master doc ·
D20: Path 1 sequencing (#1234 first, v2.0 rebases) ·
D21: `docs/development/contributing.md` (594 lines) is canonical; root `CONTRIBUTING.md` is thin pointer (revised in P0 sweep) ·
D22: zizmor placement — CI-only (was D-pending-1) ·
D23: check-parameter-alignment — delete (was D-pending-2) ·
D24: UV_VERSION anchor in `_setup-env` (was D-pending-3) ·
D25: harden-runner adoption → PR 6 (was D-pending-4) ·
D26: Workflow naming — drop `CI /` prefix from job names (resolves Blocker #1) ·
D27: Pre-commit hook reallocation — **10** to pre-push (9 named + mypy per D3); revised math **36−13−10−1=12** (resolves Blocker #2; revised Round 8) ·
D28: Defer black/ruff target-version bump out of PR 5 (P0 sweep; ADR-008 follow-up after #1234) ·
D29: Structural-guard marker name `arch_guard` (was `architecture` — collision with entity-marker) ·
D30: **Frozen CI check names: 14** (was 11). Adds Smoke Tests, Security Audit, Quickstart (Round 10) ·
D31: **`default_install_hook_types: [pre-commit, pre-push]` mandatory** in `.pre-commit-config.yaml` (Round 10 — load-bearing one-liner) ·
D32: Creative-agent containerized service bootstrap fully spec'd in PR 3 commit 9 (43 lines, 10 env vars, pinned commit `ca70dd1e2a6c`) ·
D33: xdist test config — `pytest-xdist≥3.6` + `pytest-randomly` in dev group (PR 2 commit 4.5); `--dist=loadscope` in CI ·
D34: Container hardening — `@sha256:` digest pin + `USER` non-root (PR 5); `SOURCE_DATE_EPOCH` + Trivy OS-layer scan (PR 6) ·
D35: gitleaks adopted — pre-commit hook + workflow with SARIF upload (PR 1) ·
D36: ADR file location — ADR-001/002/003 inline in PR 1 spec, lifted to docs/decisions/ at commit time; ADR-008 in drafts/, copied to docs/decisions/ in PR 5 ·
D37: `workflow_dispatch` trigger preserved in `ci.yml` (matches `test.yml:8`) ·
D38: `Schema Contract` job runs under `tox -e integration` env (DATABASE_URL set), not unit (which unsets it) ·
D39: Creative-agent integration uses docker-run script-step pattern, NOT GHA `services:` (Round 11 fix for R11A-03 — services blocks can't cross-resolve hostnames) ·
D40: Postgres `max_connections` tuned app-side via `DB_POOL_SIZE=4` + `DB_MAX_OVERFLOW=8` env (Round 11 fix for R11E-02 — GHA services has no `command:` field) ·
D41: pytest-json-report path stays at `{toxworkdir}/<env>.json`; composite globs both `test-results/` and `.tox/<env>.json` (Round 11 fix for R11E-03) ·
D42: integration_db Alembic divergence accepted with tripwire (Round 11 R11B-2 — full unification deferred) ·
D43: DATABASE_URL canonical credentials (CI: adcp_user/test_password/adcp_test; compose: dev-realistic; tests must NOT hardcode) (Round 11 R11B-1) ·
D44: `minimum_pre_commit_version: 3.2.0` in `.pre-commit-config.yaml` (Round 11 R11C-06 — D31's `default_install_hook_types` requires pre-commit ≥3.2) ·
D45: Phase B branch-protection flip FORBIDDEN on Fri/Sat/Sun + holiday eve (Round 11 R11C-02 — solo-maintainer weekend lockout mitigation) ·
D46: Pre-flight P9 grep-guard for stale-string drift (propagation discipline; Round 12 — addresses recurring "11 frozen" / "D1-D28" propagation lag across non-spec surfaces) ·
D47: `release-please publish-docker` MUST gate on CI green via `gh api` step (Round 12 post-issue-review — closes #1228 A4 P0 that 12 rounds missed; without it red main ships signed-but-broken images per R44) ·
D48: Production deploy coupling (Fly.io pulls from ghcr.io tag scheme; cosign verify NOT enforced on Fly side; D47 gates publish on CI green; rollback via fly deploy --image; documented in §Production Deploy Coupling)

---

## The 14 frozen rendered CI check names (D17 amended by D30 + D26)

Workflow `name: CI`, job `name: 'Quality Gate'` etc. — GitHub renders the concatenation. **Round 10 expansion (D30) added Smoke Tests, Security Audit, Quickstart** — currently-running CI jobs that the original D17 silently dropped:

```
CI / Quality Gate         CI / Smoke Tests           CI / Migration Roundtrip
CI / Type Check           CI / Unit Tests            CI / Coverage
CI / Schema Contract      CI / Integration Tests     CI / Summary
CI / Security Audit       CI / E2E Tests
CI / Quickstart           CI / Admin UI Tests
                          CI / BDD Tests
```

Branch protection requires exact-string match. Reusable workflow nesting can produce 3-segment names — verify with `scripts/capture-rendered-names.sh` BEFORE Phase B flip. PR 6's `Security / Dependency Review` is OUTSIDE the 14 (lives in `security.yml` namespace; PR 6 commit 4 must update `test_architecture_required_ci_checks_frozen` guard's expected list per R36).

---

## Per-PR cold-start executability (post-fix)

| PR | Spec | Briefing | Checklist | Verify script | Grade |
|----|------|----------|-----------|---------------|-------|
| 1  | ✅ | `briefings/pr1-briefing.md` | `checklists/pr1-checklist.md` | `scripts/verify-pr1.sh` | A |
| 2  | ✅ | `briefings/pr2-briefing.md` | `checklists/pr2-checklist.md` | `scripts/verify-pr2.sh` | A− |
| 3  | ✅ | `briefings/pr3-phase-a-briefing.md` + `briefings/point4-phase-b-flip.md` | `checklists/pr3-phase-{a,b,c}-checklist.md` | `scripts/verify-pr3.sh` + `scripts/capture-rendered-names.sh` + `scripts/flip-branch-protection.sh` (admin) | A− |
| 4  | ✅ | `briefings/pr4-briefing.md` | `checklists/pr4-checklist.md` | `scripts/verify-pr4.sh` | A− |
| 5  | ✅ | `briefings/pr5-briefing.md` | `checklists/pr5-checklist.md` | `scripts/verify-pr5.sh` | A |
| 6  | ✅ | `briefings/pr6-briefing.md` | `checklists/pr6-checklist.md` | `scripts/verify-pr6.sh` | B+ |

---

## Calendar (6 weeks part-time)

| Week | Activity | Deliverable |
|------|----------|-------------|
| 1 | Pre-flight (A1-A14 incl. allow_auto_merge audit, dependabot drain) + PR 1 (CodeQL advisory; persist-credentials everywhere; pinact + actionlint) | PR 1 merged; Scorecard ≥6.5 |
| 2 | PR 2 + Dependabot intake | PR 2 merged mid-week |
| 3 | PR 3 Phase A + 48h soak + rendered-name capture (composite `_pytest`, not reusable) | Phase A merged |
| 4 | PR 3 Phase B (admin flip; `--paginate` + `--app_id`) + Phase C (cleanup); 48h soak each | PR 3 fully landed; coverage hard-gated from day 1 (D11 revised) |
| 5 | PR 4 + PR 5 (without target-version bump per D28) + flip CodeQL to gating | Close #1234 |
| 6 | PR 6 (harden-runner v2.16+ audit→block + cosign + SBOM + scorecard.yml + ghcr immutability) | Scorecard ≥7.5 verified |

---

## Cost of Operations (Round 13 addition)

**One-time engineer cost:**
- ~19.75-23.75 engineer-days for the 6-PR rollout (~$X/day fully-loaded; user fills in)
- Solo execution: 6 calendar weeks part-time. Parallel-agent execution (PR 1+2+6 in parallel; PR 3-4-5 sequential): ~3.5-4 weeks.

**GHA usage delta (estimate):**
- Phase A overlap (48h, week 3-4): ~2× normal workflow load (both `test.yml` and `ci.yml` running on every PR)
- Steady-state post-rollout: ~3-4× current per-PR minutes due to 14 frozen checks vs ~6 today, harden-runner overhead, Trivy scan per release, gitleaks full-history per PR, dep-review per PR
- Estimated incremental: ~$120/month new ongoing (assumes ~5000 min/month baseline × 3× × $0.008/min for ubuntu-latest)
- Mitigation: composite actions reduce per-job overhead; conditional jobs (per `dorny/paths-filter` follow-up) could halve steady-state

**Registry storage delta:**
- SBOM (~50KB) + provenance (~5KB) + cosign bundle (~2KB) per release × 3 semver variants × 2 registries = ~350KB extra per release
- Negligible at typical release cadence

**SaaS subscription delta:**
- StepSecurity (harden-runner): free for OSS
- Sigstore (cosign): free
- OpenSSF Scorecard: free
- **No new paid subscriptions**

**Ongoing maintainer time:**
- Dependabot triage: ~2-3 hr/week (4 ecosystems × weekly cadence × 5 min/PR review)
- D5 sustainability tripwire: at 5 PRs backlog → recruit second maintainer (the cost converts to a hiring decision, not a time sink)
- Daily branch-protection-snapshot cron: ~5 min/week to glance at issues opened (if any)

**Total annual cost (estimate, year 1):**
- One-time: 24 days × $X/day = ~$X
- GHA: ~$1,440/year incremental
- Maintainer time: ~120 hr/year on Dependabot triage
- Total recurring: $1,440 + (120 hr × $X/hr) annually

---

## Alerting Strategy (Round 13 addition)

**Current (solo-maintainer-appropriate):** All gates open GitHub Issues with CRITICAL labels. Daily branch-protection-snapshot cron pings CODEOWNERS = @chrishuie.

**For a 40-person team:** GitHub Issues alone are insufficient. **A26 pre-flight (Round 13)** configures notification routing to a team channel. Recommended:
- **Slack/Teams**: configure GitHub repo notifications → forward CRITICAL labels to `#salesagent-ci-alerts` (or equivalent)
- **PagerDuty/Opsgenie (optional)**: route P0 alerts (Phase B failure, branch-protection PATCH 422, signed-but-broken-image) to on-call
- **Dashboard (optional)**: existing observability stack to display: GHA usage spike, Dependabot backlog count, Scorecard score trend, Phase A overlap status during weeks 3-4

**Decision boundary:** notification routing is OUT-OF-SCOPE of this rollout (org infrastructure decision). A26 is the pre-flight checkpoint that ensures it's set up before launch.

---

## What you must NEVER do (any PR)

- Push to origin (user owns this — `feedback_user_owns_git_push.md`)
- Run `gh pr create` (user owns this)
- Mutate branch protection via `gh api -X PATCH branches/main/...` (admin only — use `scripts/flip-branch-protection.sh` only by user invocation)
- Use `--no-verify`, `--ignore`, `--deselect`, `pytest.mark.skip` to bypass failures
- Bundle CSRF middleware into PR 1 (Path C; deferred to v2.0's `src/admin/csrf.py`)
- Auto-merge Dependabot PRs (D5)
- **Click "Enable auto-merge" button on any PR** (R30; A11 pre-flight gates this at the repo-toggle level)
- **Skip the daily branch-protection snapshot drift check** (R23/R20/R30 mitigation chain)
- Touch files outside your PR's spec scope
- Use `harden-runner`'s `disable-sudo: true` (CVE-2025-32955; use `disable-sudo-and-containers: true`)
- Use `harden-runner` v2.12.x or older (DoH/DNS-over-TCP egress-bypass advisories; pin v2.16.0+)
- Frame the mirrors-mypy migration as "deprecation" (it's not — it's an isolated-env import-resolution fix)
- Run `ruff --fix --select UP` or any other unsafe autofix during a migration (per `feedback_no_unsafe_autofix.md`; D28 deferred this entirely)

---

## When to STOP (escalation triggers)

- Branch-protection action requested → admin only; ask user
- Mypy delta >200 in PR 2 → D13 tripwire; comment out plugin, file follow-up
- Phase A check fails on main → don't flip; investigate
- Rendered names diverge from D17 expected list → don't flip; either fix names or update PATCH body
- harden-runner block-mode locks out CI → revert to audit; capture more telemetry
- Dependabot backlog ≥5 → pause forward work, clear backlog (D5)
- Test fails you can't diagnose in 15 min → escalate to user

---

## How to escalate

Write `escalations/pr<N>-<topic>.md` with:
- What you were trying to do
- What blocked you (with command output)
- What you tried
- What you think should happen

Then STOP. The user reads, decides, you resume.

---

## After you finish a PR

1. All commits on local branch (NOT pushed)
2. Run full verification: `bash scripts/verify-pr<N>.sh`
3. Run `./run_all_tests.sh`
4. Generate PR description from `templates/pr-description.md`
5. Update `00-MASTER-INDEX.md` status row to `merged YYYY-MM-DD`
6. Report to user; user owns push and PR creation

---

## Inventory of supporting artifacts (28 active + audit-trail)

**Core (always read):**
- `RESUME-HERE.md` (orientation)
- `EXECUTIVE-SUMMARY.md` (this file)
- `00-MASTER-INDEX.md` (status table)
- `01-pre-flight-checklist.md` (A1-A23 admin + P1-P9 agent prep)
- `02-risk-register.md` (R1-R10 + R16/R19/R20/R23 promoted + R26-R47 added; R11-R15, R17-R18, R21-R22, R24-R25 remain LOW info in `research/edge-case-stress-test.md`)
- `03-decision-log.md` (D1-D48)
- `architecture.md` (current vs target — partially stale; specs are authoritative)
- `landing-schedule.md` (6-week calendar)

**Per-PR (load only the one for your PR):**
- `pr1-supply-chain-hardening.md` … `pr6-image-supply-chain.md`
- `briefings/pr<N>-briefing.md` (where applicable)
- `checklists/pr<N>-checklist.md`
- `scripts/verify-pr<N>.sh`

**Templates (always read for executor handoff):**
- `templates/executor-prompt.md` (with embedded continuity hygiene rules)
- `templates/pr-description.md`

**Operational (Phase B / cross-cutting):**
- `scripts/capture-rendered-names.sh` (pre-flip name probe)
- `scripts/flip-branch-protection.sh` (admin-only)
- `preflight-ttl-guard.md` (per-PR freshness checks)

**Multi-team execution scaffolding (Round 13):**
- `COORDINATION.md` — multi-agent PR-claiming registry (consult on session start)
- `REBASE-PROTOCOL.md` — mandatory rebase order for shared files (.pre-commit-config.yaml, pyproject.toml, release-please.yml)
- `ONBOARDING-CHEAT-SHEET.md` — 10-minute orientation for fresh agents
- `FAILURE-BROADCAST-PROTOCOL.md` — escalation comms protocol

**Audit trail (read-only; do not edit):**
- `research/` (6 files + README) — measurements, audits, tool YAML
- `drafts/` (4 ADRs + helpers + 8 guard skeletons + table + hook config + README) — staging content

**Operational runbooks (in-flight playbooks):**
- `runbooks/A4-pydantic-mypy-explosion.md` (D13 tripwire response)
- `runbooks/B3-branch-protection-flip-422.md` (Phase B recovery)
- `runbooks/G1-pr1-revert-cleanup.md`, `G2-pr3-phase-b-revert.md` (revert procedures)
- `runbooks/D1-harden-runner-blocks-egress.md` (PR 6 specific)
- (Other runbook files exist as audit trail; the 5 above are the most-likely-needed during execution.)
