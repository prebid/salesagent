# Refactor Application Plan

The 11-step sequence to apply round-3/4 research findings to the existing 11 base plan files. Estimated 6-8 hours of careful editing in a fresh session with full context budget.

**Read [`RESUME-HERE.md`](RESUME-HERE.md) first.** It has the inventory and the user-facing constraints. This file is the operational sequence.

**Hard rule:** every step is markdown-only editing of existing plan files. No source-code changes. No `git push`. No `gh pr create`. No `gh api -X PATCH branches/main` mutations.

---

## Preamble — what we're doing

Round 4 produced ~4,000 lines of new content now persisted to disk under:
- `runbooks/` (28 files)
- `checklists/` (8 files)
- `briefings/` (13 files)
- `drafts/` (4 ADRs + helpers + 8 guard skeletons + table + hook config)
- `research/` (6 audit/measurement outputs)
- `architecture.md`, `pr6-image-supply-chain.md`, `landing-schedule.md`, `preflight-ttl-guard.md`, `EXECUTIVE-SUMMARY-DRAFT.md`, `continuity-hygiene.md`, `self-sufficiency-scores.md`, `minimal-context-bundle.md`

Refactor application means **integrating** these into the 11 base plan files (00-MASTER-INDEX.md, 01-pre-flight-checklist.md, 02-risk-register.md, 03-decision-log.md, pr1-…md through pr5-…md, templates/) AND creating one final navigation file (`EXECUTIVE-SUMMARY.md`).

---

## Critical-blockers-first ordering

Per the integrity audit (`research/integrity-audit.md`), three defects MUST be fixed before anything else. They are load-bearing — if the rollout starts without them, Phase B will fail (Blocker #1), PR 4 cannot meet its acceptance criterion (Blocker #2), and PR 4 will silently overwrite PR 2's work (Blocker #3).

After the 3 blockers, fix the 7 dirty dimensions, then add round-4 content, then create EXECUTIVE-SUMMARY.md.

---

## Step 1 — Fix Blocker #1 (workflow naming bug) in `pr3-ci-authoritative.md`

**Symptom:** spec has `name: CI` workflow + `name: 'CI / Quality Gate'` job → GitHub renders as `CI / CI / Quality Gate`. Branch protection contract (D17) references `CI / Quality Gate`. Phase B `gh api -X PATCH` will fail with 422.

**Fix:** drop `CI / ` prefix from job `name:` fields throughout PR 3 spec. Workflow header stays `name: CI` so GitHub auto-prefixes correctly. Affected lines per `research/integrity-audit.md`: `pr3-ci-authoritative.md:184` (workflow `name:`), 199, 213, 222, 230, 237, 246, 254, 262, 270, 294, 323 (each `name: 'CI / X'` → `name: 'X'`).

**Concretely:**
```yaml
# Before:
name: CI
on: [pull_request, push]
jobs:
  quality-gate:
    name: 'CI / Quality Gate'   # ← renders as 'CI / CI / Quality Gate'

# After:
name: CI
on: [pull_request, push]
jobs:
  quality-gate:
    name: 'Quality Gate'   # ← renders as 'CI / Quality Gate' via auto-prefix
```

The 11 frozen check names in D17 stay verbatim (`CI / Quality Gate`, etc.) — those are the *rendered* names, which are what branch protection sees.

**Verify:** after edit, search for `name: 'CI / ` in `pr3-ci-authoritative.md` — should return zero matches. Search for `name: '` in jobs blocks — each should be the bare title (no `CI / ` prefix).

**Also update:** `templates/executor-prompt.md` and any per-PR briefing/checklist that copy-pasted the `CI / Quality Gate` job-name pattern. Cross-reference search via grep.

---

## Step 2 — Fix Blocker #2 (PR 4 hook count math) in `pr4-hook-relocation.md`

**Symptom:** PR 4 spec claims commit-stage hook count drops to ≤12 post-rollout, with acceptance criterion `HOOKS_COMMIT ≤ 12`. Math:
```
36 commit-stage today − 15 deletions − 5 moves to pre-push − 1 consolidation = 15 commit-stage
```
Result is 15, not ≤12. Off by 3. PR 4 fails its own acceptance.

**Fix:** identify 4 additional hooks to either delete or move to pre-push. Per `research/integrity-audit.md`, candidate hooks for additional moves:
- `mcp-schema-alignment` — move to pre-push (medium-cost YAML schema validation)
- `check-tenant-context-order` — move to pre-push (Python script invocation; not formatter-fast)
- `ast-grep-bdd-guards` — move to pre-push (only relevant pre-push since BDD tests run there anyway)
- `check-migration-completeness` — move to pre-push (only matters if alembic/ files changed)

Update PR 4 commit 5 ("Move to pre-push stage") to add these 4 hooks. Update PR 4 §"Hooks MOVED to pre-push" tally from 5 to 9. Update PR 4 acceptance criterion math to reflect 36 − 15 − 9 − 1 = **11 commit-stage hooks**, comfortably under the 12 target.

**Verify:** the math in `pr4-hook-relocation.md` body should reach ≤12 with explicit accounting.

---

## Step 3 — Fix Blocker #3 (`_architecture_helpers.py` collision) in PR 2 + PR 4 specs

**Symptom:** `pr2-uvlock-single-source.md` commit 8 says it CREATES `tests/unit/_architecture_helpers.py` (~30 lines). `pr4-hook-relocation.md` commit 1 ALSO says it CREATES the same file (~50 lines). Whichever runs second overwrites the first.

**Fix:**
- PR 2 commit 8: keep "create" — establishes baseline (~30 lines: `repo_root`, `parse_module` mtime-keyed cache, `iter_function_defs`, `iter_call_expressions`, `src_python_files`)
- PR 4 commit 1: change wording from "create" to "extend" — reads the PR-2 baseline and adds the additional helpers (`iter_workflow_files`, `iter_compose_files`, `iter_action_uses`, `iter_python_version_anchors`, `iter_postgres_image_refs`, `assert_violations_match_allowlist`, `assert_anchor_consistency`, `format_failure`)

**Final reconciled module is at [`drafts/_architecture_helpers.py`](drafts/_architecture_helpers.py)** (221 lines). PR 4 commit 1's spec should embed this draft.

**Concretely:**
- Edit `pr4-hook-relocation.md` commit 1 wording: "create new ~50 lines" → "extend the existing module from PR 2 commit 8; final shape is at `.claude/notes/ci-refactor/drafts/_architecture_helpers.py`"
- Update PR 4 commit 1's verification: instead of `test ! -f tests/unit/_architecture_helpers.py` (file shouldn't exist before), use `test -f tests/unit/_architecture_helpers.py && grep -q 'parse_module' tests/unit/_architecture_helpers.py` (file exists from PR 2; we extend it)

---

## Step 4 — Fix the 7 dirty dimensions

Apply each of these to the indicated file:

### 4a — Final guard count (master index + D18)
- Edit `00-MASTER-INDEX.md`: change "guard count" math to **52 final** (26 existing + 1 PR 2 + 4 PR 4 + 4 PR 4 (additional from drafts: explicit_nested_serialization, no_advisory_ci, hook_count_permanent, claudemd_table_complete) + 1 PR 5 + 9 v2.0 = ~45-52 depending on which round-4 additions are accepted)
- Edit `03-decision-log.md` D18: change wording. Per `research/integrity-audit.md`, current claim of "27 + 1 + 4 + 9 = 41" is off — add PR 5's `test_architecture_uv_version_anchor` for **42** at minimum. With round-4 additions: ~52.
- Cross-reference: `drafts/claudemd-guards-table-final.md` is the 52-row source of truth.

### 4b — D18 phantom rows wording
- Edit `03-decision-log.md` D18: change "3 phantom rows + 5 missing" to "**0 phantom rows + 5 missing**" per `research/integrity-audit.md` finding. Update PR 4 commit 9's task description accordingly.

### 4c — D-pending-5 (referenced but undefined)
- Edit `pr4-hook-relocation.md:499` (the reference). Either define D-pending-5 in `03-decision-log.md` or rewrite the reference as inline ("the issue's bar may tighten to <2s"). **Recommend rewrite** — it's a one-off bar tightening that doesn't need decision-log status.

### 4d — PD15 dual claim
- Both PR 1 and PR 3 claim "PD15". Disambiguate:
  - `pr1-supply-chain-hardening.md`: PD15 → **PD15a** (SHA-pin scope)
  - `pr3-ci-authoritative.md`: PD15 → **PD15b** (workflow permissions remainder)
- Update master index drift-catalog mention if any.

### 4e — Effort total drift
- `00-MASTER-INDEX.md` says "14-18 engineer-days". Sum of per-PR estimates is 13.5-17 (PR 1: 2.5d / PR 2: 4-6d / PR 3: 3-4d / PR 4: 2d / PR 5: 2d).
- **Update master index to "13.5-17 engineer-days for the 5-PR rollout; +1.5-2d for PR 6 follow-up = 15-19d total"**.

### 4f — Calendar Week 4 packing
- `00-MASTER-INDEX.md` calendar packs Phase B + C + PR 4 in Week 4. Each phase needs ≥48h soak (Phase A→B and Phase B→C). Plus PR 4 review.
- **Move PR 4 to Week 5**. Extend rollout to 6 weeks. PR 6 fits in Week 6 slack.
- Update calendar table accordingly. Cross-check `landing-schedule.md` — that file already documents the corrected 6-week calendar; lift its content into master index.

### 4g — PR 6 reference
- `pr6-image-supply-chain.md` now exists at the repo root (just persisted from round-3 plan-content draft).
- Update `00-MASTER-INDEX.md` to reference PR 6 in the per-PR list (was 5 PRs; now 6 with PR 6 as optional follow-up).
- Confirm `D-pending-4` (harden-runner adoption) is now resolved by PR 6 — update D-pending-4 status in `03-decision-log.md` to "Resolved by PR 6 (filed as `pr6-image-supply-chain.md`)".

---

## Step 5 — Integrate research findings into base files

The 6 research files are persisted at `research/` but their content is not yet referenced from base files. Add references:

### 5a — Risk register additions
- Edit `02-risk-register.md`. Currently has R1-R10. Append R11-R25 from `research/edge-case-stress-test.md`:
  - R11: `_architecture_helpers.py` cache bug breaks all guards
  - R12: false-positive normalization (FP budget tracking)
  - R13: two-tier feedback gap (pre-push hook mitigation)
  - R14: guard deprecation pathway ambiguity
  - R15: advisory-window mechanical flip enforcement
  - R16-R25: 10 edge-case findings (concurrent PR cascades, tool failures, branch-protection edge cases, security incidents)
- Update top-of-register table from "Top 10" to "Top 25 risks". Cross-link each PR spec's Risks section to relevant new R-numbers.

### 5b — Decision additions D22-D27
- Edit `03-decision-log.md`. Append:
  - D22: harden-runner adoption (resolves D-pending-4) — audit mode 2 weeks then block
  - D23: workflow naming convention — drop `CI /` prefix from job names (per Step 1 fix)
  - D24: pre-existing violation Strategy A/B/C audited per-guard in pre-flight (link `research/violation-strategy.md`)
  - D25: PR 6 timing — Week 6 slack (post-PR-5)
  - D26: SBOM provenance via `provenance: mode=max` per ADR-007
  - D27: guard naming normalization — rename 3 transport guards to `test_architecture_*` prefix

### 5c — Pre-flight checklist additions
- Edit `01-pre-flight-checklist.md`. Add:
  - A11: capture full repo settings via `gh api repos/prebid/salesagent --jq '.security_and_analysis,.web_commit_signoff_required,.allow_auto_merge'`
  - A12: capture existing tag-signing / release-please config snapshot
  - P7: verify `tests/unit/_architecture_helpers.py` does NOT exist yet
  - P8: verify `yamllint` available in `[dependency-groups].dev`
  - P9: probe `black --check --target-version py312 src/` line-delta count
  - P10: verify `cosign` and `gh attestation` CLI availability for PR 6
- Update sign-off block to 22 boxes (was 16).

### 5d — Empirical baseline measurements
- Edit `00-MASTER-INDEX.md` "Drift catalog evidence" section. Replace estimated counts with measured numbers from `research/empirical-baseline.md`:
  - Hooks: 40 total (36 commit-stage, 0 pre-push, 4 manual)
  - Existing guards: 26 (23 architecture + 3 transport)
  - SHA-pinned actions: 0/24 today
  - persist-credentials: false: 0/8 today
  - Workflows missing top-level permissions: 2/4 (test.yml + pr-title-check.yml)
  - Coverage: 55.56%

---

## Step 6 — Add new ADRs to PR 4 spec

The 4 ADRs are persisted at `drafts/adr-{004,005,006,007}-*.md`. Each is committed to `docs/decisions/` during PR 4 (or PR 6) execution.

- Edit `pr4-hook-relocation.md`: add Commits 11-13 (or wherever they fit chronologically) that copy the 4 ADR drafts to `docs/decisions/`. Specifically:
  - PR 4 adds ADR-004, ADR-005, ADR-006 (deprecation, fitness functions, allowlist)
  - PR 6 adds ADR-007 (build provenance)
- Update PR 4 acceptance criteria to require all 3 ADR files present + each has `## Status` heading.
- Reference the drafts in PR 4 spec: "Lift verbatim from `.claude/notes/ci-refactor/drafts/adr-004-guard-deprecation-criteria.md`".

---

## Step 7 — Add 8 new guard skeletons to PR 1 / PR 3 / PR 4 specs

The 8 guard skeletons at `drafts/guards/*.py` need to be referenced from the per-PR specs (which guard belongs to which PR):

- **PR 1 owns:** `test_architecture_workflow_concurrency.py`, `test_architecture_persist_credentials_false.py`, `test_architecture_workflow_timeout_minutes.py` (3 Fortune-50 guards)
- **PR 3 owns:** `test_architecture_no_advisory_ci.py`, `test_architecture_required_ci_checks_frozen.py`
- **PR 4 owns:** `test_architecture_explicit_nested_serialization.py`, `test_architecture_pre_commit_hook_count.py`, `test_architecture_helpers.py` (meta-guard)

For each owning PR's spec:
- Add a commit step ("test: add `test_architecture_<name>.py`")
- Reference the source draft (`drafts/guards/<filename>.py`)
- Add to the spec's Verification block: `pytest tests/unit/<filename> -v`
- Add a row to the CLAUDE.md guards table delta in PR 4 commit 9

Per the violation-strategy analysis (`research/violation-strategy.md`), these guards have pre-existing violations that need backfill commits B1-B7 in PR 1 BEFORE the guards land. Add backfill commits to PR 1 spec (workflow_permissions, actions_sha_pinned, precommit_sha_frozen, workflow_concurrency, persist_credentials_false, workflow_timeout_minutes, no_advisory_ci partial). PR 1 grows from ~9 commits to ~16 commits.

---

## Step 8 — Update CLAUDE.md guards table content in PR 4

The 52-row final table is at `drafts/claudemd-guards-table-final.md`. PR 4 commit 9 should:
- Lift this content verbatim into `CLAUDE.md` (replacing the existing 24-row table)
- Update the count text in CLAUDE.md from "24" to actual final count (~52)

Edit `pr4-hook-relocation.md` commit 9 to embed the draft path:
> "Replace existing guards table with content from `.claude/notes/ci-refactor/drafts/claudemd-guards-table-final.md`. Verify all 52 rows reference test files that exist on disk (use the verify script in the spec)."

---

## Step 9 — Add pre-push hook + TTL guard to PR 4 + checklists

Pre-push hook config is at `drafts/precommit-prepush-hook.md`. Pre-flight TTL guard is at `preflight-ttl-guard.md`.

- Edit `pr4-hook-relocation.md`: add a commit ("ci: add architecture-guards pre-push hook for R13 mitigation") that lifts the YAML from `drafts/precommit-prepush-hook.md` into `.pre-commit-config.yaml`. Plus the CONTRIBUTING.md "Hook installation" snippet.
- Edit each per-PR checklist (`checklists/pr{1,2,3-phase-a,3-phase-c,4,5,6}-checklist.md`) to start with the TTL guard bash block from `preflight-ttl-guard.md`. Already drafted in those checklist files; verify by grep.

---

## Step 10 — Update templates

Edit `templates/executor-prompt.md`:
- Add slug list footer: pr1-supply-chain-hardening, pr2-uvlock-single-source, pr3-ci-authoritative, pr4-hook-relocation, pr5-version-consolidation, pr6-image-supply-chain
- Standardize ADMIN/USER ACTION tagging
- Add escalation-path subsection (lift from `briefings/point*.md` "Escalation triggers" patterns)
- Add 15 continuity-hygiene rules (lift verbatim from `continuity-hygiene.md`)
- Add cold-start verification block (TTL guard + `git status` + read-spec instruction)

Edit `templates/pr-description.md`:
- Standardize Risks section to cite R-numbers explicitly
- Add "Decisions cited" subsection
- Add coordination-notes pattern (Admin actions / User actions / Agent actions)

---

## Step 11 — Create `EXECUTIVE-SUMMARY.md` (final navigation)

`EXECUTIVE-SUMMARY-DRAFT.md` has the full sketch. Promote it to `EXECUTIVE-SUMMARY.md`:
- Read `EXECUTIVE-SUMMARY-DRAFT.md`
- Inline the sketched content (it's currently inside a markdown code fence within the draft)
- Verify all D-numbers + R-numbers + check names are consistent with the post-refactor state
- Update guard count from "41 final" to actual post-refactor number (probably 45-52)
- Add cross-link from `RESUME-HERE.md` and `00-MASTER-INDEX.md`
- Final length: ~150-180 lines

After this file lands, the cold-start cost drops from ~22-28k tokens → ~14-20k for any future fresh agent.

---

## Verification after all 11 steps

Run a final consistency audit:

```bash
cd /Users/quantum/Documents/ComputedChaos/salesagent/.claude/notes/ci-refactor

# 1. All ADR/D/R/PD references resolve
grep -rohE 'D[0-9]+\b' *.md | sort -u   # should be D1 through D27 + D-pending-1..4
grep -rohE 'R[0-9]+\b' *.md | sort -u   # should be R1 through R25
grep -rohE 'PD[0-9]+\b' *.md | sort -u  # should be PD1 through PD24, plus PD15a, PD15b
grep -rohE 'ADR-[0-9]+' *.md | sort -u  # should be ADR-001 through ADR-007

# 2. Workflow naming bug fixed
grep -n "name: 'CI /" pr3-ci-authoritative.md   # should return 0 hits
grep -n 'name: ' pr3-ci-authoritative.md | grep -v 'name: CI$' | head   # job names should be bare

# 3. Hook count math
grep -A1 '≤ 12' pr4-hook-relocation.md   # acceptance still expressed
grep -E 'Hooks MOVED to pre-push.*[0-9]' pr4-hook-relocation.md   # should be 9 not 5

# 4. _architecture_helpers.py ownership
grep -A2 '_architecture_helpers.py' pr2-uvlock-single-source.md | grep -i create
grep -A2 '_architecture_helpers.py' pr4-hook-relocation.md | grep -i extend

# 5. New files exist
test -f EXECUTIVE-SUMMARY.md
test -f architecture.md
test -f pr6-image-supply-chain.md
test -f drafts/_architecture_helpers.py

# 6. Calendar shows 6 weeks
grep -E 'Week [56]' 00-MASTER-INDEX.md
```

If all 6 checks pass, the refactor is complete. Update `00-MASTER-INDEX.md` status block:
```
## Status: READY FOR EXECUTOR HANDOFF

- Plan version: 2026-MM-DD post-refactor
- Total per-PR specs: 6 (PR 1-5 + PR 6 follow-up)
- Total decisions: 27 (D1-D27) + 4 D-pending resolved
- Total risks: 25 (R1-R25)
- Total guards added: 16 (post-rollout count: ~52 with v2.0)
- Pre-flight checklist: 22 items (A1-A12, P1-P10)
- Architecture documented (current + target side-by-side)
- 28 operational runbooks ready
- 13 cold-start briefings ready
- 8 guard skeletons drafted
- 4 ADR drafts ready
```

Then update the memory note `ci_refactor_rollout_state.md` to reflect "READY FOR EXECUTOR HANDOFF" and the user can launch the executor agent on PR 1 by following `templates/executor-prompt.md` filled in for PR 1.

---

## Time estimate

| Step | Hours |
|------|-------|
| 1. Workflow naming bug | 0.25 |
| 2. PR 4 hook count math | 0.5 |
| 3. Helpers collision | 0.25 |
| 4. 7 dirty dimensions | 1.5 |
| 5. Research integrations (R11-R25, D22-D27, A11-A12, P7-P10, baselines) | 2.0 |
| 6. ADR drafts → PR 4/6 specs | 0.5 |
| 7. Guard skeletons → per-PR specs + backfill commits | 1.5 |
| 8. CLAUDE.md table content | 0.25 |
| 9. Pre-push hook + TTL guard | 0.25 |
| 10. Template updates | 0.5 |
| 11. EXECUTIVE-SUMMARY.md | 0.5 |
| **Final consistency audit** | 0.25 |
| **Total** | **~8 hours** |

This is the next session's work. Do it from a fresh context with full budget.

---

## What NOT to do during refactor application

- **Do not** start any of the 6 PRs (PR 1-PR 6) themselves — those are AFTER refactor application is complete. The user explicitly said: "for right now though only explore, investigate, think, and prepare to plan a strategy to implement."
- **Do not** modify any source code. This is markdown-only editing of `.claude/notes/ci-refactor/` files.
- **Do not** push or open PRs. The user owns those operations.
- **Do not** mutate branch protection. The user owns that.
- **Do not** delete the `drafts/` or `research/` subdirectories after lifting their content — they're the audit trail.

---

## Final handoff state

After all 11 steps complete and the verification audit passes:

1. `RESUME-HERE.md` → updated with "REFACTOR COMPLETE" status
2. `EXECUTIVE-SUMMARY.md` → exists, ~180 lines
3. All 11 base plan files updated with round-3/4 findings
4. `pr6-image-supply-chain.md` properly integrated as the 6th PR
5. Memory note `ci_refactor_rollout_state.md` → updated with executor-handoff readiness

The user can then launch the executor agent on PR 1 by:
1. Reading `RESUME-HERE.md` + `EXECUTIVE-SUMMARY.md`
2. Filling in `templates/executor-prompt.md` with PR 1 specifics from `pr1-supply-chain-hardening.md` + `briefings/pr1-briefing.md` + `checklists/pr1-checklist.md`
3. Confirming pre-flight A1-A12 + P1-P10 are complete (some are admin-only — user runs)
4. Launching the executor

Best practice: launch one PR at a time. Do not run multiple PR executors in parallel — too much risk of file conflicts and merge ordering hazards.
