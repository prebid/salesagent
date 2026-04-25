# CI/Pre-commit Refactor — Final Integrity Audit

**Verdict (TL;DR): NEEDS-WORK** — 6 dimensions clean, 9 dimensions dirty. Major numerical inconsistencies, missing PR 6 spec, stale CLAUDE.md table, and one critical blocker (D17's check name renders as `CI / CI / Quality Gate` — branch protection flip will FAIL silently).

---

## 1. Numerical claims — **DIRTY**

### Existing structural-guard count: 21 vs 22 vs 23 vs 26 vs 27 — UNRESOLVED

**Disk truth (verified via `ls`):**
- `tests/unit/test_architecture_*.py` files = **23** (count above is exactly 23)
- Transport-boundary guards (`test_no_toolerror_in_impl.py`, `test_transport_agnostic_impl.py`, `test_impl_resolved_identity.py`) = **3**
- `check_code_duplication.py` script (counted as a guard in CLAUDE.md row 105) = **1 (script, not test)**
- **Total: 23 + 3 = 26 test files** + 1 script = **26 enforced guards**

**Plan claims:**
- `00-MASTER-INDEX.md:40`: "27 (existing) + 1 (PR 2) + 4 (PR 4) + 9 (v2.0) = **41**"
- `03-decision-log.md:152`: "23 `test_architecture_*.py` + 3 transport-boundary guards … + `check_code_duplication.py` script. **27**"
- `01-pre-flight-checklist.md:167`: "Should show 26-27 entries"
- `pr4-hook-relocation.md:11`: "Migrates 5 grep-based hooks to AST-based structural guards" + line 596: "backfilled to all 27 existing guards"
- `pr4-hook-relocation.md:527`: "32 (28 from PR 2 corrections + 4 PR 4 additions)" — math: 28+4=**32**, but baseline of 28 contradicts 27
- `CLAUDE.md` table: **22 rows** (counted: includes header + 22 data rows). Actual disk = 26.

**Resolution required:** The arithmetic must be one of:
- baseline = 26 (23 architecture tests + 3 transport guards), excluding the duplication script
- baseline = 27 (the same + 1 script)

D18 says 27. Master index says 27. Pre-flight says 26-27. PR 4 says "all 27 existing guards" but its CLAUDE.md update target is "32 (28 from PR 2 + 4 PR 4)"… 28 = 27 + 1 (PR 2 adds one). Math is internally consistent IF baseline is 27. **Final guard count = 41 is correct only if PR 2 adds 1 + PR 4 adds 4 + PR 5 adds 1 (uv-version-anchor) + v2.0 adds 9: 27+1+4+1+9=42.**

**The 41 vs 42 discrepancy:** PR 5 commit 4 adds `tests/unit/test_architecture_uv_version_anchor.py` (`pr5-version-consolidation.md:172-196`). That guard is not counted in any "final guard count" math. **Authoritative truth = 42**, not 41. Update master index L40 and D18 L152.

### Hook count: 36 vs 26 vs ≤12 — DIRTY

**Disk truth:**
- Total `- id:` entries in `.pre-commit-config.yaml` = **40**
- `stages: [manual]` hooks = **4** (smoke-tests, test-migrations, pytest-unit, mcp-endpoint-tests)
- Effective at commit stage today = **40 − 4 = 36 commit-stage hooks**

**Plan claims:**
- `pr4-hook-relocation.md:11`: "Drops warm pre-commit latency from ~23s to ~1.7s (10× improvement)" — implied baseline of "many hooks"
- `pr4-hook-relocation.md:486`: "commit hook count $HOOKS_COMMIT > 12" — target ≤ 12
- Master index: no explicit "current hook count" claim
- 01-pre-flight-checklist.md A8: "Issue claims 18-30s warm" — references warm latency, not hook count

**No file claims today's count is 36.** This is OK — but the PR 4 deletion math is incomplete:

PR 4 commit 7 deletes 15 hooks (counted: `no-tenant-config`, `enforce-jsontype`, `check-rootmodel-access`, `enforce-sqlalchemy-2-0`, `check-import-usage`, `check-gam-auth-support`, `check-response-attribute-access`, `check-roundtrip-tests`, `check-code-duplication`, `check-parameter-alignment`, `pytest-unit`, `mcp-endpoint-tests`, `suggest-test-factories`, `no-skip-integration-v2`, `check-migration-heads`). That's **15**.

PR 4 commit 5 moves 5 hooks to pre-push: `check-docs-links`, `check-route-conflicts`, `type-ignore-no-regression`, `adcp-contract-tests`, `mcp-contract-validation`.

PR 4 commit 6 consolidates 2 hooks (`no-skip-tests`, `no-fn-calls`) into 1 (`repo-invariants`) — net −1.

**Math:** 36 (current) − 15 (deleted) − 5 (moved) − 1 (consolidation) + 1 (PR 2 adds nothing new on commit stage but PR 4 adds `repo-invariants`) = **16 commit-stage hooks**. ≤ 12 target violated by 4.

**This is a HARD failure of PR 4's own acceptance criterion** unless the count is reconciled. Reviewer must:
- (a) Delete more hooks, or
- (b) Move more to pre-push, or
- (c) Loosen the ≤12 target.

### Effort total: 13.5 vs 14-18 vs 5 days — DIRTY

- Master index L15: "**~14-18 engineer-days, ~5 calendar weeks part-time**"
- Per-PR sums:
  - PR 1: "2.5 days" (L4)
  - PR 2: "4-6 days" (L4)
  - PR 3: "3-4 days" (L4)
  - PR 4: "2 days" (L3)
  - PR 5: "2 days" (L4)
  - **Sum: 13.5 to 17 days** (low: 2.5+4+3+2+2; high: 2.5+6+4+2+2)

Master index says 14-18; per-PR sum says 13.5-17. **Off by 0.5 days on the low end and 1 day on the high end.** Pick one and fix everywhere. Recommendation: update master index L15 to "13.5-17 engineer-days".

### Total commits per PR — DIRTY

- PR 1: 11 commits (commits 1-11, verified in spec)
- PR 2: 9 commits
- PR 3 Phase A: 10 commits + Phase C: 2 commits = 12 commits, but Phase A is one PR and Phase C is another
- PR 4: 10 commits
- PR 5: 8 commits

No file claims a "total commits" number. This dimension is implicitly clean BUT the executor template `templates/executor-prompt.md` line 96 says "Commits: <count>" — operator must fill in correctly per PR. Add an explicit count to each spec's header line for safety.

### 11 frozen check names — CLEAN

D17 lists exactly 11 names; PR 3 references all 11; CONTRIBUTING.md outline references all 11. Names are consistent across:
- `03-decision-log.md:134-144`
- `pr3-ci-authoritative.md:351, 521-531, 545-555`
- `pr1-supply-chain-hardening.md:870` (CONTRIBUTING.md outline)

All 11 in identical wording.

---

## 2. Decision numbers (D1-D27 + D-pending) — **DIRTY**

**Defined in 03-decision-log.md:** D1-D21 (21 decisions), D-pending-1 through D-pending-4

**Master index references D-pending-5** is **NOT defined.** Search results show:
- `pr4-hook-relocation.md:499`: "warm < 5s (issue's bar; D-pending-5 may tighten to < 2s)"
- `03-decision-log.md`: only D-pending-1 through D-pending-4

**Resolution:** Either define D-pending-5 in the decision log OR rewrite PR 4 line 499 as "the issue's bar may tighten to < 2s".

**D-numbers go up to D21**, not D27 as the audit prompt assumed. Round-2/round-3 added items are folded into D11-D21 already (dated 2026-04-25).

**D-pending status:**
- D-pending-1 (zizmor placement): default decided (CI-only); not formally resolved
- D-pending-2 (check-parameter-alignment salvage): default decided (delete); referenced in `pr4-hook-relocation.md:455` "per D-pending-2"
- D-pending-3 (UV_VERSION orphan): default decided (anchor); referenced in `pr5-version-consolidation.md:7, 161`
- D-pending-4 (harden-runner): default decided (PR 6 candidate); referenced in `pr1-supply-chain-hardening.md:23`

All D-pending defaults are referenced in spec; should be promoted to formal D-numbers (D22-D25). This would also resolve the missing D-pending-5 ambiguity.

---

## 3. Risk numbers (R1-R25) — **CLEAN with one ambiguity**

`02-risk-register.md` defines exactly R1-R10. No R11+ exists. Audit prompt's mention of "R11-R15 from round-2" and "R16-R25 from round-3" is from the prompt's anticipated state — the actual file has only 10. Each R# is referenced by at least one PR spec:
- R1, R8, R6 → PR 3
- R2, R5, R6 → PR 2
- R3, R4, R9 → PR 1
- R7 → PR 4

**No orphans.** R0801 in the grep is pylint code, not a risk register entry — false positive.

---

## 4. ADR numbers — **DIRTY**

**The audit prompt suggests:**
- ADR-001: solo-bypass (PR 1) ← renamed FROM "single-source-deps"
- ADR-002: pull_request_target trust ← renamed FROM "solo-bypass"
- ADR-003: single-source-deps (PR 2) ← renamed FROM "pull_request_target"

**Actual plan state (per PR 1 commits 7, 11):**
- ADR-001: `single-source-pre-commit-deps.md` (PR 1 commit 7)
- ADR-002: `solo-maintainer-bypass.md` (PR 1 commit 7)
- ADR-003: `pull-request-target-trust.md` (PR 1 commit 11)

The plan **DID NOT** apply the round-2 renumbering. `pr1-supply-chain-hardening.md` lines 137, 138, 248, 332-333, 675, 765 all use the original numbering. `pr2-uvlock-single-source.md:30` references "ADR-001 (single-source pre-commit deps)" consistent with original.

**ADR-004 through ADR-007 (round-2 additions) — NOT FOUND IN ANY PLAN FILE.** No PR creates them. Either:
- (a) The audit prompt is wrong (these were not in scope), OR
- (b) The plan needs new commits to PR 4 (ADR-004 guard deprecation, ADR-005 fitness functions, ADR-006 allowlist) and PR 6 (ADR-007 build provenance, but PR 6 doesn't exist)

**Recommendation:** Drop the ADR-004 through ADR-007 references from the audit-mental-model. The plan as written has 3 ADRs (ADR-001, ADR-002, ADR-003), all created in PR 1. This is consistent across all spec files.

---

## 5. Drift catalog references (PD1-PD25) — **DIRTY**

**Disk truth (from PR specs):** PD1 through PD24 are referenced. **PD25 does NOT appear** in any plan file.

**Coverage map:**
- PR 1: PD3, PD4, PD5, PD6, PD7, PD13, PD14, PD15, PD23, PD24
- PR 2: PD1, PD2, PD8 (partial)
- PR 3: PD10 (partial), PD11, PD15, PD16 (groundwork)
- PR 4: PD16, PD17, PD18, PD19, PD20, PD21, PD22
- PR 5: PD9, PD10, PD11, PD12

**Conflicts (same PD claimed by 2+ PRs):**
- **PD15** appears in PR 1 AND PR 3 (both claim closure). PR 1 calls it "permissions/SHA-pin"; PR 3 says "Closes #1233 D12, PD15." (line 481) for the unconditional creative agent. Overlap is genuine — they touch different aspects. Spec should mark PR 1's as "(supply-chain SHA-pin)" and PR 3's as "(workflow permissions)" to disambiguate.
- **PD16** in PR 3 ("groundwork") AND PR 4. PR 3 sets up; PR 4 closes. Marked correctly.
- **PD11** in PR 3 (UV_VERSION groundwork) AND PR 5 (full closure). Marked correctly.
- **PD10** in PR 3 (partial) AND PR 5 (full). Marked correctly.

**Missing from any closure:** PD25. Either there is no PD25 in the issue (audit-prompt assumption wrong) or the plan is missing closure. **High likelihood the issue's drift catalog only goes to PD24.**

---

## 6. File path references — **DIRTY**

Files referenced but verified as not existing on main:

| Path | Referenced by | Created by | Status |
|---|---|---|---|
| `tests/unit/_architecture_helpers.py` | PR 2 commit 8, PR 4 commit 1 | **BOTH PRs** | DUPLICATE CREATION — collision |
| `.github/CODEOWNERS` | PR 1 commit 3 | PR 1 | OK |
| `docs/decisions/adr-001-*.md` | PR 1 commit 7, PR 2 commit 1 | **BOTH PRs (no-op fallback)** | OK with caveat |
| `docs/decisions/adr-002-*.md` | PR 1 commit 7 | PR 1 | OK |
| `docs/decisions/adr-003-*.md` | PR 1 commit 11 | PR 1 | OK |
| `pr6-image-supply-chain.md` | Master index? | **NOT WRITTEN** | MISSING |
| `tests/unit/test_architecture_pre_commit_no_additional_deps.py` | PR 2 commit 8 | PR 2 | OK |
| `tests/unit/test_architecture_uv_version_anchor.py` | PR 5 commit 4 | PR 5 | OK |
| `.github/scripts/migration_roundtrip.sh` | PR 3 commit 4 | PR 3 | OK |
| `.github/codeql/codeql-config.yml` | PR 1 commit 6 | PR 1 (optional but recommended) | OK |
| `.coverage-baseline` | PR 3 commit 6 | PR 3 | OK |
| `.pre-commit-coverage-map.yml` | PR 4 commit 4 | PR 4 | OK |
| `.pre-commit-hooks/check_repo_invariants.py` | PR 4 commit 6 | PR 4 | OK |

**Critical findings:**
- **`tests/unit/_architecture_helpers.py` collision** — PR 2 commit 8 (`pr2-uvlock-single-source.md:181-183`) creates this file, AND PR 4 commit 1 (`pr4-hook-relocation.md:30, 42-92`) creates a different version. PR 2's version is short (~30 lines, basic helpers). PR 4's version is 50 lines with `parse_module`, `iter_function_defs`, `iter_call_expressions`, `src_python_files`, `repo_root`. **PR 4 must be re-spec'd to "extend the existing helper module from PR 2"**, not re-create it. Verify in PR 4: `pr4-hook-relocation.md:30` says "new" — change to "extend".
- **PR 6 (image supply chain) — NOT WRITTEN.** Audit prompt suggests `pr6-image-supply-chain.md` exists. It does not. Master index L43 lists "PR 5" as "final PR of rollout" implying no PR 6. The plan currently is 5 PRs. PR 6 is mentioned as a future placeholder in:
  - `pr1-supply-chain-hardening.md:23`: "harden-runner adoption (Fortune-50 pattern, but file as PR 6 follow-up per D-pending-4)"
  - `pr3-ci-authoritative.md:24`: "harden-runner) → PR 6 follow-up"
  - **No spec exists.** If PR 6 is intended for this rollout, write it. If not, remove the references.

---

## 7. Command consistency — **DIRTY (minor)**

`gh api` syntax inconsistencies:
- `pr3-ci-authoritative.md:514, 651`: `gh api -X PATCH /repos/...` (POST/PATCH style with leading slash)
- `01-pre-flight-checklist.md:12, 24`: `gh api repos/prebid/salesagent/...` (no leading slash, GET style)
- `02-risk-register.md:27`: `gh api -X PATCH /repos/...` (consistent with PR 3)

Both are valid syntactically. Pick a convention. Recommendation: leading slash for write ops, no slash for reads — that's already what's there. No fix needed.

`uv run` patterns: consistent everywhere except `pr1-supply-chain-hardening.md:933, 935`: uses `uvx` explicitly for `zizmor` and `pip-audit` instead of `uv run`. This is correct per uvx semantics (one-shot tool invocation), and matches `pr1-supply-chain-hardening.md:142` (`uvx zizmor`).

---

## 8. Hook count math (PR 4) — **DIRTY**

Already shown in §1. Final commit-stage count math:

```
Today:        40 hooks − 4 manual    = 36 commit-stage
PR 4 deletes:                        −15 (15 hooks listed in commit 7)
PR 4 moves to pre-push:              −5 (commit 5)
PR 4 consolidates:                   −1 (no-skip-tests + no-fn-calls → repo-invariants, net -1)
PR 2 effects on commit stage:        ±0 (mypy and black move from external repo to local hook,
                                          they remain commit-stage hooks)
                                     ────
Resulting commit-stage:              =15

But the spec verification at line 486 expects HOOKS_COMMIT ≤ 12.
```

**The math fails the acceptance criterion by 3.** Either:
- (a) Delete `mcp-schema-alignment`, `ast-grep-bdd-guards`, and `check-tenant-context-order` (move some to pre-push), or
- (b) Loosen target to ≤16

**This is a HARD ROADBLOCK.** The plan as written cannot meet acceptance.

---

## 9. Guard count math — **DIRTY**

Already shown in §1. Authoritative count = **42** (not 41). Update:
- `00-MASTER-INDEX.md:40`: change "= 41" to "= 42"
- `03-decision-log.md:152`: change "Final post-rollout: **41**" to "**42**"
- `pr4-hook-relocation.md:527`: extend the math note — "PR 5 contributes 1 more (test_architecture_uv_version_anchor.py)" before the v2.0 note

---

## 10. CLAUDE.md table accuracy — **DIRTY**

**Disk: 26 architecture/transport tests + 1 duplication script = 27.** **Table: 22 rows.**

Missing from table (5 rows):
- `test_architecture_no_silent_except.py` (exists)
- `test_architecture_bdd_no_direct_call_impl.py` (exists)
- `test_architecture_bdd_obligation_sync.py` (exists)
- `test_architecture_production_session_add.py` (exists)
- `test_architecture_test_marker_coverage.py` (exists)

Table also references no phantom rows — every row's test file does exist on disk.

PR 4 commit 9 says "Add 4 new rows … (28 from PR 2 corrections + 4 PR 4 additions)" → 32. But the math is wrong: 22 (current) + 5 (missing) + 1 (PR 2: pre_commit_no_additional_deps) + 4 (PR 4) + 1 (PR 5: uv_version_anchor) = **33**, not 32. This drift compounds with §9.

D18 says PR 4 should "verify the test file exists; for each test file under tests/unit/test_architecture_*.py, verify a row exists. Three rows must be removed (they reference files that don't exist on disk). Five rows must be added (files exist but were omitted from the table)."

But there are **0 phantom rows** (every table file exists). So "Three rows must be removed" is wrong. **Update D18 L155** to "Five rows must be added (no phantom rows)".

---

## 11. Calendar coherence — **CLEAN with one drift**

Calendar (master index L55-61):
- Week 1: Pre-flight + PR 1
- Week 2: PR 2 + Dependabot PRs
- Week 3: PR 3 Phase A
- Week 4: PR 3 Phase B + PR 3 Phase C + PR 4
- Week 5: PR 4 + PR 5 + close issue

Per-PR `Depends on` claims:
- PR 2 depends on PR 1 — Week 2 OK
- PR 3 depends on PR 1, PR 2 — Week 3 OK
- PR 4 depends on PR 3 Phase C — Week 4-5 OK
- PR 5 depends on PR 3 Phase C — Week 5 OK (independent of PR 4)

**One drift:** Week 4 packs Phase B + Phase C + opening PR 4. Phase B requires ≥48h Phase A soak; Phase C requires ≥48h Phase B stable. That's ≥96h between Phase A merge and Phase C merge = 4 days. Adding PR 4 review on top in the same week leaves <3 days. **Realistic re-allocation:** Week 4 = Phase B + Phase C; Week 5 = PR 4 + PR 5. Update master index L60 to remove "+ PR 4" from Week 4.

---

## 12. Workflow naming bug — **CRITICAL DIRTY (BLOCKER)**

`pr3-ci-authoritative.md:184`: workflow has `name: CI`
`pr3-ci-authoritative.md:199`: job has `name: 'CI / Quality Gate'`

When GitHub renders this for branch protection, the check name becomes **`CI / CI / Quality Gate`**, not `CI / Quality Gate`. Branch protection at `pr3-ci-authoritative.md:521-531` references `CI / Quality Gate`. **The atomic flip in Phase B will fail with "no matching check found" and main will be unmergeable.**

Resolutions (pick one):
- (a) Drop `name: CI` from workflow header; let job names stand alone
- (b) Strip the `CI / ` prefix from job names: `name: 'Quality Gate'`, `name: 'Type Check'`, etc.

Option (b) is cleaner. Update:
- `pr3-ci-authoritative.md:184` (keep `name: CI`)
- `pr3-ci-authoritative.md:199, 213, 222, 230, 237, 246, 254, 262, 270, 294, 323` (strip `CI / ` prefix)
- `pr3-ci-authoritative.md:351, 521-531, 545-555` (rendered names: `CI / Quality Gate`, etc., remain as-is — GitHub auto-prefixes)
- `03-decision-log.md:134-144` (D17 verbatim list — keep with `CI / ` prefix; that's the rendered form)
- `pr1-supply-chain-hardening.md:870` (CONTRIBUTING.md outline)

**Audit risk:** R1 (branch-protection lockout) escalates from MEDIUM probability to HIGH if this is not fixed. **Fix this before applying any commit.**

---

## 13. Pre-flight artifacts — **CLEAN**

A1-A10 + P1-P6 = 16 items. Each has a verification command. Each is referenced consistently:
- A1 (branch-protection snapshot) → R1, PR 3 Phase B
- A7 (coverage baseline) → D11, PR 3 commit 6
- A9 (Scorecard baseline) → R10, success criteria
- P2 (mypy baseline) → D13, PR 2 commits 2-3
- P3 (zizmor preflight) → R4, PR 1 commit 11
- P5 (guards-on-disk) → D18

Audit prompt mentioned A12 + P10 — not in the plan. The plan has A1-A10 + P1-P6 = 16. Smaller scope, clean.

---

## 14. Verify-script vs structural guard split — **CLEAN**

Each PR has a `verify-pr<N>.sh` invoked from spec. None duplicate work that's already in a structural guard. The `verify-pr1.sh` (lines 286-340 of PR 1) checks file presence and content; structural guards check code patterns. No overlap.

---

## 15. Synthesis worksheet "ready for handoff" — **N/A**

Audit prompt mentions "20-row checklist" — not in any plan file. Either was stripped before commit or is in an earlier draft. The pre-flight checklist (16 items) plus per-PR acceptance criteria fulfill this role. No discrepancy to report.

---

## Summary

### Top 10 inconsistencies (fix-first order)

| # | Issue | Files | Impact |
|---|---|---|---|
| 1 | **Workflow name renders `CI / CI / Quality Gate`** | `pr3-ci-authoritative.md:184, 199-323` | **BLOCKER** — Phase B atomic flip will fail |
| 2 | **PR 4 hook count fails ≤12 acceptance** | `pr4-hook-relocation.md:486` | **BLOCKER** — PR 4 cannot merge as written |
| 3 | **`_architecture_helpers.py` created twice** (PR 2 + PR 4) | `pr2-uvlock-single-source.md:181`, `pr4-hook-relocation.md:30` | Second commit will fail with "file exists" |
| 4 | **Final guard count 41 vs 42** | `00-MASTER-INDEX.md:40`, `03-decision-log.md:152` | Off-by-one in PR 5 contribution |
| 5 | **CLAUDE.md table = 22 rows but disk = 26 tests** | `CLAUDE.md:85-108`, D18 | "3 phantom rows" claim is false; 0 phantom + 5 missing |
| 6 | **D-pending-5 referenced but not defined** | `pr4-hook-relocation.md:499`, `03-decision-log.md` | Undefined decision |
| 7 | **PD15 claimed by both PR 1 and PR 3** | `pr1...md:3`, `pr3...md:3, 481` | Reviewer confusion; mark explicitly |
| 8 | **Effort total 14-18 vs 13.5-17** | `00-MASTER-INDEX.md:15` | 0.5d-1d off-by |
| 9 | **Calendar packs Phase B + C + PR 4 in Week 4** | `00-MASTER-INDEX.md:60` | Insufficient buffer for 96h soak windows |
| 10 | **PR 6 referenced but spec missing** | `pr1...md:23`, `pr3...md:24`, D-pending-4 | Plan is 5 PRs; remove PR 6 references or write the spec |

### Top 5 hidden risks

1. **Phase B branch-protection flip will silently fail** if check names don't match (Issue #1). Inverse rollback at `pr3-ci-authoritative.md:651` would not help because the snapshot is taken from the working state. **Risk: main becomes unmergeable for the rollback duration.**

2. **PR 2 redefines `_architecture_helpers.py` first**, then PR 4 tries to recreate the same path. The PR 4 spec line 30 says "new"; if executed as written, the file write will succeed but lose PR 2's content (helpers added by PR 2's guard). **Risk: silent guard regression — `test_architecture_pre_commit_no_additional_deps.py` from PR 2 may break when PR 4 lands.**

3. **PR 4 commit 7's hook count math is unstated.** Spec lists 15 deletions but doesn't enumerate the resulting count. Reviewer following the spec verbatim will produce 16 commit-stage hooks (failing the ≤12 verifier). **Risk: PR 4 fails CI on its own verification.**

4. **D17's 11 frozen names** are referenced 3+ times across files but **branch protection currently requires the names** `Security Audit`, `Smoke Tests…`, etc. (per `test.yml` job names). The flip in Phase B replaces THE ENTIRE LIST atomically. If the new workflow's check names differ from D17 (Issue #1's risk), no transition is possible. **Risk: hardcoded blocker.**

5. **PR 1's `[project.urls]` add** + **PR 1's adcp ignore in dependabot.yml** + **D16's "remove temporary adcp ignore" follow-up** = 3 separate touch points to `pyproject.toml` / `dependabot.yml`. If PR #1217 merges between PR 1 author and review, D16's "TODO" comment will be stale. **Risk: stale TODO surviving into shipped code; minor but trackability gap.**

### Refactor application order

When applying the plan refactor, fix in this order to minimize cascade-rework:

1. **First**: Fix Issue #1 (workflow naming) — touches 1 file (`pr3-ci-authoritative.md`), but everything downstream (D17, CONTRIBUTING.md outline, branch protection scripts) flows from this decision.
2. **Then**: Fix Issue #2 (hook count) — re-allocate 4+ hooks (e.g., move `mcp-schema-alignment`, `check-tenant-context-order`, `ast-grep-bdd-guards`, `check-migration-completeness` to pre-push). Update `pr4-hook-relocation.md` commits 5 and 7.
3. **Then**: Fix Issue #3 (helpers collision) — change PR 4 commit 1 from "create" to "extend".
4. **Then**: Fix Issues #4, #5 (guard counts) — re-do the math, update master index + D18 + PR 4 commit 9.
5. **Then**: Fix Issue #6 (D-pending-5) — define it OR rewrite the reference.
6. **Then**: Fix Issue #7 (PD15 dual claim) — split into "PD15a (SHA-pin)" and "PD15b (permissions)" or amend wording.
7. **Then**: Fix Issues #8, #9 (effort + calendar) — pure documentation drift.
8. **Last**: Decide on Issue #10 (PR 6 — write or remove).

### Net assessment: **NEEDS-WORK**

- Clean: 6 dimensions (3, 7, 11 partial, 13, 14, 15)
- Dirty: 9 dimensions (1, 2, 4, 5, 6, 8, 9, 10, 12)

**Issue #1 (workflow naming bug) is a critical blocker** — it makes Phase B impossible. **Issue #2 (PR 4 hook count failure) is a critical blocker** — it makes PR 4 fail its own verification. **Issue #3 (helpers collision) is a workflow blocker** — second PR will fail to apply.

These three blockers MUST be fixed before any executor agent runs the spec. The remaining 6 dirty dimensions are documentation-only drift and can be cleaned up during the rollout without halting forward progress.
