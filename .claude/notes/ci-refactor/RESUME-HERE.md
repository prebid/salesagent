# Resume Here — CI/Pre-commit Refactor

**Date snapshot:** 2026-04-25 (last research-round update).

**Rollout status:** 4 rounds of opus-subagent research complete (~19 agents across 4 rounds). Plan files on disk. Refactor application has **NOT** yet started — the 11-step sequence to apply changes is documented in [REFACTOR-RUNBOOK.md](REFACTOR-RUNBOOK.md).

If you are a fresh agent picking this up cold, read this file first. It tells you where everything is and what to do next.

---

## What this rollout is

GitHub issue [#1234](https://github.com/prebid/salesagent/issues/1234) — refactor `.pre-commit-config.yaml` and `.github/workflows/` into a layered, supply-chain-hardened CI system. The work is broken into 5 sequential PRs (PR 1-5) plus a follow-up PR 6 for image-signing/SBOM/Sigstore work. Estimated **~14-18 engineer-days, ~5-6 calendar weeks part-time.**

A concurrent v2.0 (Flask-to-FastAPI) effort is in flight under [PR #1221](https://github.com/prebid/salesagent/pull/1221). Sequencing decision **D20** chose Path 1 — issue #1234 lands first, v2.0 phase PRs rebase onto the new layered model.

---

## Read order for cold-start (~14-20k tokens total)

1. **This file** (`RESUME-HERE.md`) — orientation
2. [`EXECUTIVE-SUMMARY-DRAFT.md`](EXECUTIVE-SUMMARY-DRAFT.md) — one-screen orientation; if you read no other research file, read this
3. [`00-MASTER-INDEX.md`](00-MASTER-INDEX.md) — status table, calendar, sequencing
4. [`03-decision-log.md`](03-decision-log.md) — every locked decision (D1-D21) + 4 D-pending entries
5. [`02-risk-register.md`](02-risk-register.md) — top-10 risks (R1-R10); see also `research/edge-case-stress-test.md` for R11-R25 unincorporated
6. [`01-pre-flight-checklist.md`](01-pre-flight-checklist.md) — admin actions A1-A10 + agent steps P1-P6
7. **The per-PR spec for the PR you're working on** (one of `pr1-supply-chain-hardening.md` … `pr6-image-supply-chain.md`)
8. [`templates/executor-prompt.md`](templates/executor-prompt.md) — agent operating contract
9. `CLAUDE.md` at repo root (`/Users/quantum/Documents/ComputedChaos/salesagent/CLAUDE.md`) — codebase patterns; non-negotiable

---

## Critical blockers the next session MUST fix during refactor application

The final integrity audit found three load-bearing defects. They MUST be fixed during refactor application or the rollout will fail in production. Full details in [`research/integrity-audit.md`](research/integrity-audit.md).

1. **Workflow naming bug (BLOCKER #1).** PR 3 spec has `name: CI` workflow + `name: 'CI / Quality Gate'` job. GitHub renders this as `CI / CI / Quality Gate` (auto-prefix). Phase B's atomic `gh api -X PATCH` references `CI / Quality Gate`, so the flip will silently fail with 422 — main becomes unmergeable. **Fix:** drop `CI / ` prefix from job names in `pr3-ci-authoritative.md` (the workflow's `name:` already prefixes). Verify against `03-decision-log.md` D17.

2. **PR 4 hook count fails its own ≤12 acceptance (BLOCKER #2).** Current trajectory: 36 commit-stage hooks − 15 deletions − 5 moves to pre-push − 1 consolidation = 16 commit-stage hooks. Acceptance target is ≤12. **Fix:** identify 4 more hooks to either delete or move to pre-push in PR 4 spec; candidates per the integrity audit are `mcp-schema-alignment`, `check-tenant-context-order`, `ast-grep-bdd-guards`, `check-migration-completeness`.

3. **`tests/unit/_architecture_helpers.py` ownership collision (BLOCKER #3).** PR 2 commit 8 says "create"; PR 4 commit 1 also says "create". Second one wins, overwriting the first. **Fix:** PR 2 creates baseline (~30 lines); PR 4 commit 1 changes from "create" to "extend" the existing module. The reconciled final shape is at [`drafts/_architecture_helpers.py`](drafts/_architecture_helpers.py) (~221 lines).

---

## Seven dirty dimensions to clean up

Less critical but should be fixed during refactor application:

4. **Final guard count: 41 → 42** in master index + D18 (PR 5's `test_architecture_uv_version_anchor` not counted)
5. **CLAUDE.md guards table: D18 says "3 phantom + 5 missing" but actually "0 phantom + 5 missing"** — correct D18 wording. Fixed final table in [`drafts/claudemd-guards-table-final.md`](drafts/claudemd-guards-table-final.md).
6. **D-pending-5 referenced in `pr4-hook-relocation.md:499` but not defined** — define or rewrite
7. **PD15 dual-claim** by PR 1 + PR 3 — disambiguate as PD15a (SHA-pin) and PD15b (workflow permissions)
8. **Effort total drift**: master index says "14-18 engineer-days" but per-PR sums give 13.5-17. Pick one; recommend update to "13.5-17 engineer-days" with note about PR 6 being separate
9. **Calendar Week 4 packs Phase B + C + PR 4** — too tight for two ≥48h soak windows. Move PR 4 to Week 5; extend rollout to 6 weeks (PR 6 fits in Week 6 slack)
10. **PR 6 spec previously missing — now present at [`pr6-image-supply-chain.md`](pr6-image-supply-chain.md)** — was extracted from round-4 plan-content drafts

---

## What's on disk (full inventory)

### Plan files at root

- `00-MASTER-INDEX.md` — status, calendar, sequencing
- `01-pre-flight-checklist.md` — admin + agent pre-flight steps
- `02-risk-register.md` — R1-R10 (R11-R25 in `research/edge-case-stress-test.md`)
- `03-decision-log.md` — D1-D21 + D-pending-1..4
- `pr1-supply-chain-hardening.md` through `pr5-version-consolidation.md` — per-PR specs
- `pr6-image-supply-chain.md` — PR 6 spec (post-rollout supply-chain hardening)
- `architecture.md` — current vs target architecture (current = 4 workflows, 36 commit-stage hooks, 0 SHA-pinned actions, no ADRs/CODEOWNERS/dependabot; target = layered hooks + 11 frozen check names + 52 structural guards + cosign signing + ≥7.5 Scorecard)
- `landing-schedule.md` — 6-week calendar with dependency graph
- `preflight-ttl-guard.md` — bash block to paste at start of every PR's checklist (per-artifact freshness checks)
- `EXECUTIVE-SUMMARY-DRAFT.md` — "if you read one file" doc draft (~102 lines; promote to `EXECUTIVE-SUMMARY.md` during refactor)
- `minimal-context-bundle.md` — file order for cold-start
- `continuity-hygiene.md` — 15 conventions to survive context wipe
- `self-sufficiency-scores.md` — per-PR cold-start executability ratings (PR 1: A; PR 2-6: B)

### Subdirectories

- `runbooks/` — **28 operational runbooks** for during-rollout incidents (sections A through G: plan-execution / CI failures / concurrent collisions / tool malfunctions / decay / security / rollback)
- `checklists/` — **8 per-PR implementation checklists** (PR 1, 2, 3 phases A/B/C, 4, 5, 6) with executable bash verification per commit
- `briefings/` — **13 cold-start briefings**: 5 simulated context-wipe points + 8 per-PR situational briefings
- `drafts/` — pre-execution content the executor agent will lift to canonical paths during PR execution:
  - `adr-004` through `adr-007` — ADR drafts ready for `docs/decisions/`
  - `_architecture_helpers.py` — final reconciled helper module (~221 lines, mtime-keyed AST cache, anchor consistency, allowlist helpers, failure-message format)
  - `guards/` — **8 guard skeletons** (test_architecture_*.py files; ready to drop into `tests/unit/`)
  - `claudemd-guards-table-final.md` — corrected 52-row table (sectioned by Schema/Transport/DB/BDD/Test integrity/Governance/Cross-file)
  - `precommit-prepush-hook.md` — `architecture-guards` pre-push hook config block
- `research/` — round-3/4 audit and measurement outputs:
  - `empirical-baseline.md` — measured numbers (40 hooks, 36 commit-stage, 26 existing guards, 55.56% coverage, 99 CSRF gap, 0/24 SHA-pinned actions, 0/8 persist-credentials)
  - `external-tool-yaml.md` — production-ready YAML for zizmor, pinact, OSSF Scorecard, harden-runner, attest-build-provenance, dependency-review
  - `violation-strategy.md` — Strategy A/B/C per new guard + 7 backfill commits B1-B7 for PR 1
  - `handoff-readiness-audit.md` — 14 blockers, 4 implicit assumptions, 13 missing artifacts (most resolved by drafts/)
  - `edge-case-stress-test.md` — 30 failure modes, R11-R25 risks, Bayesian risk math
  - `integrity-audit.md` — 3 critical blockers, 7 dirty dimensions, refactor application order
- `templates/` — `executor-prompt.md` and `pr-description.md` (existing)

### NOT yet on disk (will be created during refactor application)

- `EXECUTIVE-SUMMARY.md` — final version (drafted in `EXECUTIVE-SUMMARY-DRAFT.md`)
- `REFACTOR-RUNBOOK.md` — the 11-step sequence (NEXT FILE TO READ)

---

## What you must NEVER do (any session)

- **Push to origin or open PRs** — user owns these (per `feedback_user_owns_git_push.md` memory)
- **Mutate branch protection** via `gh api -X PATCH branches/main` — admin-only; only the user runs these
- **Use `--no-verify`, `--ignore`, `-k "not …"`, `pytest.mark.skip`** to bypass failing tests — CLAUDE.md test-integrity policy is zero-tolerance
- **Bundle CSRF middleware into PR 1** — D10 chose Path C (advisory CodeQL for 2 weeks); v2.0's `src/admin/csrf.py` is expected to address the 99 missing-CSRF findings
- **Auto-merge Dependabot PRs** — D5 forbids absolutely
- **Touch files outside your PR's spec scope** — strict per-PR boundaries

---

## When to STOP and escalate

- A test fails you can't diagnose in 15 minutes → write `escalations/pr<N>-<topic>.md` and STOP
- Branch-protection action requested → admin only; ask user
- Mypy delta >200 in PR 2 → D13 tripwire; comment out `pydantic.mypy` plugin, file follow-up
- Phase A check fails on main → don't flip; investigate
- harden-runner block-mode locks out CI → revert to audit; capture more telemetry
- Dependabot backlog ≥5 open PRs → pause forward work, clear backlog (D5 sustainability tripwire)

---

## Next step

Read [`REFACTOR-RUNBOOK.md`](REFACTOR-RUNBOOK.md) for the 11-step sequence to apply changes to the 11 base plan files, fix the 3 critical blockers, and clean up the 7 dirty dimensions. Then begin applying.

The actual code-modifying PRs come AFTER the plan refactor is complete. The user explicitly stated: "for right now though only explore, investigate, think, and prepare to plan a strategy to implement." The refactor itself is still planning work — not source-code editing.

When ready to execute the actual rollout, launch the executor agent with `templates/executor-prompt.md` filled in for PR 1.
