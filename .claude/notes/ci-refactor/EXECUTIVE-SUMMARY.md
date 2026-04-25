# CI/Pre-commit Refactor — Executive Summary

**If you have time to read ONE file before being parachuted in, this is it.**
Read order for cold-start: this file → `RESUME-HERE.md` → `pr<N>-<slug>.md` for your PR. ~14-20k tokens total.

Last refresh: 2026-04-25 (post-integrity-audit + Round 5+6 P0 sweep applied, blockers fixed, D22-D28, R19/R20/R23 promoted, R26-R30 added).

---

## What is this?

A 6-PR rollout (issue [#1234](https://github.com/prebid/salesagent/issues/1234)) that brings salesagent to top-tier OSS supply-chain posture. **~15-19 engineer-days, ~6 calendar weeks part-time.** PRs land sequentially; PR 6 is a Week-6 follow-up.

A concurrent v2.0 (Flask-to-FastAPI) effort runs under [PR #1221](https://github.com/prebid/salesagent/pull/1221). Per **D20** (Path 1 sequencing), issue #1234 lands first; v2.0 phase PRs rebase onto the new layered model.

---

## The 6 PRs

| PR | What it does | Effort | Blocks |
|---|---|---|---|
| 1 | Governance + supply-chain hardening (CODEOWNERS, SECURITY.md, dependabot, zizmor, CodeQL advisory, SHA-pin all hooks/actions, `persist-credentials: false` on every checkout) | 2.5 days | 2, 3 |
| 2 | uv.lock as single source: replace `mirrors-mypy` and `psf/black` with local `language: system` hooks (NOT a deprecation — fixes isolated-env import resolution per [Jared Khan](https://jaredkhan.com/blog/mypy-pre-commit)); delete `[project.optional-dependencies].dev`; re-enable `pydantic.mypy` plugin | 4-6 days | 3 |
| 3 | CI authoritative + composite actions (Decision-4: composite, NOT reusable workflows); 11 frozen bare-name check jobs (D17 + D26); 3-phase merge (overlap → rendered-name capture → atomic flip → cleanup); coverage hard-gate from day 1 (D11 revised) | 3-4 days | 4 |
| 4 | Hook relocation: 5 grep hooks → AST guards; **9** to pre-push (D27); 4 to CI-only; 6 deleted; real math 33 effective − 13 − 9 − 1 = 10 commit-stage; CLAUDE.md guards table audit DEFERS to post-v2.0-rebase (target ~73 rows, D18 revised) | 2 days | 5 |
| 5 | Version consolidation: Python, Postgres, uv anchors single-sourced; `test_architecture_uv_version_anchor` guard. **Black/ruff target-version DEFERRED per D28 (ADR-008 — separate post-#1234 PR)** | 2 days | none |
| 6 | Image supply chain: `harden-runner` (audit→block, **v2.16.0+** for CVE-2025-32955 + GHSA-46g3-37rh-v698), `cosign` keyless signing + SBOM + provenance, dependency-review, `scorecard.yml` self-host (Week 6 follow-up; resolves D-pending-4 → D25) | 1.5-2 days | none |

---

## The 3 critical blockers — STATUS: FIXED in specs

These were the load-bearing defects that would have failed at runtime. All three are now corrected in the per-PR specs (2026-04-25).

| # | Defect | Status |
|---|---|---|
| 1 | PR 3 spec used `name: 'CI / Quality Gate'` job names with workflow `name: CI` — GitHub renders this as `CI / CI / Quality Gate` (auto-prefix). Phase B PATCH would 422. | ✅ Fixed: 11 sites in `pr3-ci-authoritative.md`; D26 added; pre-flip rendered-name capture step at Phase B Step 1b |
| 2 | PR 4 hook math: 37 commit-stage today − 15 deletions − 5 moves = 17 (vs ≤12 acceptance) | ✅ Fixed: real baseline is 33 effective commit-stage (36 minus 3 manual). 2 of 15 deletions are already manual. Math: 33 − 13 − 9 − 1 = 10 (under ≤12 ceiling); D27 revised in 2026-04-25 P0 sweep |
| 3 | `tests/unit/_architecture_helpers.py` collision — both PR 2 commit 8 and PR 4 commit 1 said "create" | ✅ Fixed: PR 2 creates baseline (~30 lines); PR 4 EXTENDS to ~221 lines (reconciled draft at `drafts/_architecture_helpers.py`) |

---

## The 28 locked decisions (D1-D28)

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
D14: Migrate ui-tests extras → dependency-groups ·
D15: Delete Gemini key fallback (unconditional mock) ·
D16: Dependabot ignores adcp until #1217 merges ·
D17: 11 frozen CI check names (the *rendered* names; see D26) ·
D18: **27 baseline + 1 + 4 + 1 + 8 + 31 + 9 = ~73** final guards (post-v2.0-rebase canonical; revised in 2026-04-25 P0 sweep) ·
D19: Per-PR specs, not master doc ·
D20: Path 1 sequencing (#1234 first, v2.0 rebases) ·
D21: `docs/development/contributing.md` (594 lines) is canonical; root `CONTRIBUTING.md` is thin pointer (revised in P0 sweep) ·
D22: zizmor placement — CI-only (was D-pending-1) ·
D23: check-parameter-alignment — delete (was D-pending-2) ·
D24: UV_VERSION anchor in `_setup-env` (was D-pending-3) ·
D25: harden-runner adoption → PR 6 (was D-pending-4) ·
D26: Workflow naming — drop `CI /` prefix from job names (resolves Blocker #1) ·
D27: Pre-commit hook reallocation — 9 to pre-push; revised math 33−13−9−1=10 (resolves Blocker #2) ·
D28: Defer black/ruff target-version bump out of PR 5 (P0 sweep; ADR-008 follow-up after #1234)

---

## The 11 frozen rendered CI check names (D17 + D26)

Workflow `name: CI`, job `name: 'Quality Gate'` etc. — GitHub renders the concatenation:

```
CI / Quality Gate
CI / Type Check
CI / Schema Contract
CI / Unit Tests
CI / Integration Tests
CI / E2E Tests
CI / Admin UI Tests
CI / BDD Tests
CI / Migration Roundtrip
CI / Coverage
CI / Summary
```

Branch protection requires exact-string match. Reusable workflow nesting can produce 3-segment names — verify with `scripts/capture-rendered-names.sh` BEFORE Phase B flip.

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
- `01-pre-flight-checklist.md` (A1-A14 admin + P1-P6 agent prep)
- `02-risk-register.md` (R1-R10 + R19/R20/R23 promoted + R26-R30 added; R11-R18, R24-R25 remain LOW info in `research/edge-case-stress-test.md`)
- `03-decision-log.md` (D1-D28)
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

**Audit trail (read-only; do not edit):**
- `research/` (6 files + README) — measurements, audits, tool YAML
- `drafts/` (4 ADRs + helpers + 8 guard skeletons + table + hook config + README) — staging content

**Operational runbooks (in-flight playbooks):**
- `runbooks/A4-pydantic-mypy-explosion.md` (D13 tripwire response)
- `runbooks/B3-branch-protection-flip-422.md` (Phase B recovery)
- `runbooks/G1-pr1-revert-cleanup.md`, `G2-pr3-phase-b-revert.md` (revert procedures)
- `runbooks/D1-harden-runner-blocks-egress.md` (PR 6 specific)
- (Other runbook files exist as audit trail; the 5 above are the most-likely-needed during execution.)
