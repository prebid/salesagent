# Fresh-Executor Audit: CI Refactor Plan

## BLOCKER LIST (would halt execution and require user input)

### Cross-cutting blockers
1. **`.claude/notes/ci-refactor/scripts/` directory does not exist.** Every PR spec references `bash .claude/notes/ci-refactor/scripts/verify-pr<N>.sh` and PR 3 references `flip-branch-protection.sh`. None of these scripts exist. Executor would either skip verification (silent failure) or have to compose them from inline bash (no source of truth).
2. **`docs/decisions/` directory does not exist.** PR 1 commit 7 says `docs/decisions/.placeholder removed if it exists (mkdir creates the directory implicitly)`. Mkdir is not implicit when the parent dir doesn't exist either — PR 1 creates 3 ADRs in a non-existent directory. Plan glosses over this.
3. **Pre-flight checklist may not be complete at execution time.** Spec says "Depends on: pre-flight checklist complete" but executor cannot verify A1-A10 (admin actions). Executor would need to assume completion or stop.

### PR 1 specific blockers
4. **Commit 1 `[project.urls]` content not specified.** Spec says "add `[project.urls]` block" with verification `grep -qE '\[project\.urls\]' pyproject.toml` — but provides ZERO example values. Executor must invent URLs (Repository, Issues, Documentation, etc.) without guidance. Likely to ask user.
5. **Commit 2 CONTRIBUTING.md is a 10-bullet outline, not 120 lines of prose.** §"Embedded CONTRIBUTING.md outline" line 846 says "The agent should fill in prose from these bullets. Target ~120 lines." Executor writes prose fresh. Quality is unverifiable; the verification only checks `wc -l ≥ 80` and a few greps.
6. **Commit 8 `pre-commit autoupdate --freeze` procedure ambiguity.** Lines 159-167 say "run on a scratch branch first to review the diff before committing." But spec doesn't say "STOP and present diff to user" or "agent reviews and decides." Executor has no decision criterion — what makes a bumped hook acceptable vs. unacceptable?
7. **Commit 9 SHA-pinning loop is not idempotent or rate-limit-safe.** The `gh api repos/$tool/git/refs/tags/$tag` loop iterates over every action ref unauthenticated-rate-limited (60/hr). With ~30 unique refs across files, hitting cache or auth-required action repos may rate-limit. No fallback procedure. Output is meant to be applied "via `sed` or manual edits" — no concrete script.
8. **Commit 10 Gemini key — D15 says "verified — no e2e test invokes a live Gemini client."** Executor can rely on this as a pre-flight finding (D15 is locked). OK.
9. **Commit 11 zizmor allowlist — `.github/zizmor.yml` configuration syntax not specified.** Spec says "configures audit rules" but no example block. Executor would have to consult zizmor docs.
10. **`Co-Authored-By` confusion.** Template line 64 says `NO trailing "Co-Authored-By" line unless the user has explicitly enabled it`. CLAUDE.md says always include Claude as co-author. Direct contradiction.

### PR 2 specific blockers
11. **Commit 1 ADR-001 is "no-op if PR 1 commit 7 already added it."** This produces a dangling commit (a commit with no diff). Conventional commits and conventional CI tools will fail. Executor has no rule for handling no-op commits — skip the commit? Make a docs touchup? Squash with the next?
12. **Commit 2→3 ordering tension.** Commit 2 title says "replace mirrors-mypy" but verification says "assumes pydantic plugin error fixes in commit 3." Commit 2's `pre-commit run --all-files` will fail (the verification line `! grep -q 'mirrors-mypy'` runs but `pre-commit run` is not in commit 2 verification). Then commit 3 is "fix wherever the new errors land" — executor has no upper bound. If errors > 200, "STOP and escalate" but escalation procedure ("Comment out `pydantic.mypy` from `mypy.ini:3` temporarily") is buried in commit 2's prose, not in escalation triggers section.
13. **Commit 3 "wherever the new errors land" — unbounded scope.** No file allowlist, no time budget, no upper bound on edits. If error count is 50, executor must touch ~50 files across `src/core/`. PR scope is unbounded.
14. **Commit 5's "no-op if v2.0 already deleted [project.optional-dependencies].dev"** — same as Blocker #11. Commit with no diff is non-idiomatic.
15. **Commit 8's `_architecture_helpers.py` — duplicates PR 4 commit 1.** PR 2 commit 8 says "tests/unit/_architecture_helpers.py (new, ~30 lines, shared AST/YAML helpers)" but PR 4 commit 1 also says "new, ~50 lines". Conflicting authorship. Whichever PR runs first creates the file; the second has to detect-and-extend.
16. **Commit 9 CLAUDE.md table — "5 missing rows" specified by name, "3 phantom rows" not specified by name.** Executor has to discover phantoms by diff — no list provided. Also: D18 says "PR 4 corrects to 32" but PR 2 commit 9 says "post-PR-2: 28; PR 4 adds 4 more for final 32." Two specs disagree on the table state at PR 2 merge time.

### PR 3 specific blockers
17. **3-phase merge: Executor agent's session covers Phase A only.** Phase B is admin-only. Executor stops after Phase A merge. Phase C is "small follow-up PR" — does the same executor pick it up 48h later? Different fresh agent? Unspecified.
18. **Commit 7 "removing `|| true` may surface real failures."** Spec says "passes" in verification but no procedure if removing exposes a real bug. Implicit assumption: ruff passes clean today. Not pre-verified.
19. **Commit 10 "schema-alignment fail-hard"** — locates test file via `grep -rn 'pytest.skip.*network'`. Executor doesn't know which file in advance.
20. **`peter-evans/create-pull-request@<SHA>`** placeholder in PR 1 fallback never resolved. PR 1 commit 5 SHA-pinning loop iterates `.github/workflows/` — fallback file may not be committed yet.
21. **Phase A vs Phase C blurred.** PR 3 spec describes both. PR 4 spec line 5 says "Depends on: PR 3 Phase C merged." Single PR encompasses Phase A only; "PR 3" being merged is genuinely ambiguous.

### PR 4 specific blockers
22. **Commit 1 `_architecture_helpers.py` conflict with PR 2 commit 8.** Already noted. PR 4 says "new" — assumes PR 2 didn't create it. If PR 2 already created a 30-line version, PR 4 commit 1 must extend, not create.
23. **Commit 3 — "5 new test files" — order unspecified.** The 5 files don't have inter-dependencies but `_architecture_helpers.py` import order is significant. Does each test file get its own commit? Spec is silent.
24. **Commit 5 pre-push hook installation.** "If pre-push install is required for testing, where does the executor install it?" — not stated. Executor would need to run `pre-commit install --hook-type pre-push` to test commit 5's verification. No mention.
25. **Commit 9 "post-rollout table content" not provided as canonical text.** Plan says update to 32 rows. The 4 PR 4 additions are listed but the existing 28 rows aren't. Executor must construct the full table — likely diverges from D18's stated post-rollout state.

### PR 5 specific blockers
26. **Commit 1 `docker-compose*.yml` build-arg propagation unspecified.** Spec says "No compose change needed unless Python version differs from the ARG default" — but compose files do not currently consume `--build-arg PYTHON_VERSION`. Executor must verify each compose file accepts the arg or modify them. Spec waves this off.
27. **Commit 8 PG17 regression "judgment call."** Line 286: "Don't block PR 5 merge unless the failure is a tenant-isolation or schema correctness issue." This is a JUDGMENT, but executor has no rubric — what counts as "tenant-isolation"? Likely escalates.

---

## IMPLICIT ASSUMPTION LIST

1. Pre-flight A1-A10 are complete (executor cannot verify).
2. Pre-flight P2 captured `.mypy-baseline.txt` (PR 2 commit 2 needs this; checked file does NOT exist).
3. Pre-flight P3 captured `.zizmor-preflight.txt` (does NOT exist — PR 1 commit 11 references it).
4. `docs/decisions/` directory exists OR `mkdir -p` is implicit (it's not — does not exist).
5. `verify-pr<N>.sh` scripts exist (they don't — `scripts/` dir absent).
6. Coverage was last measured 2026-04 at 55.56% — pre-flight A7 says re-measure if drifted >1pp. Executor cannot verify.
7. PR #1217 fate decided (D-pre-flight A6) — executor cannot verify.
8. The 4 hooks bumped by `autoupdate --freeze` (lines 262, 275, 281, 289) match what's actually in `.pre-commit-config.yaml` today.
9. zizmor reports findings consistent with pre-flight P3 estimate (~35 findings, 0-3 template-injection).
10. `gh api` is configured with admin-scope token by user.
11. Test infrastructure works on first try (`./run_all_tests.sh`).
12. `make quality` is green BEFORE PR work begins (no PR specifies "verify clean baseline first").
13. v2.0 branch hasn't introduced conflicting hook deletions or guard-baseline files mid-rollout.
14. PR 2 commit 5 v2.0 sync — executor knows whether the deletion already landed.

---

## MISSING ARTIFACT LIST

| Artifact | Referenced in | Status |
|---|---|---|
| `.claude/notes/ci-refactor/scripts/verify-pr1.sh` | PR 1, 2, 3, 4, 5 | Does not exist |
| `.claude/notes/ci-refactor/scripts/flip-branch-protection.sh` | PR 3 | Does not exist |
| `docs/decisions/` directory | PR 1 commit 7, PR 2 commit 1 | Does not exist |
| `.mypy-baseline.txt` | PR 2 commit 2 | Pre-flight P2 not run |
| `.zizmor-preflight.txt` | PR 1 commit 11 | Pre-flight P3 not run |
| `branch-protection-snapshot.json` | PR 3 Phase B | Pre-flight A1 not run |
| `branch-protection-snapshot-required-checks.json` | PR 3 rollback | Pre-flight A1 not run |
| Full ~120-line CONTRIBUTING.md prose | PR 1 commit 2 | Outline only; agent writes |
| `[project.urls]` example values | PR 1 commit 1 | Not provided |
| `.github/zizmor.yml` content | PR 1 commit 11 | "configures audit rules" — no syntax |
| Phantom CLAUDE.md guard table rows (3 to remove) | PR 2 commit 9 | Not enumerated |
| 28 existing CLAUDE.md guard rows | PR 4 commit 9 | Not provided as canonical text |
| `tests/unit/_architecture_helpers.py` ownership | PR 2 commit 8 vs PR 4 commit 1 | Both claim to create it |

---

## SCOPE AMBIGUITY LIST

1. **PR 2 commit 3 unbounded scope** — "wherever errors land" with no allowlist.
2. **PR 1 commit 8 autoupdate-freeze decision** — "review diff before committing" without rubric.
3. **PR 3 multi-phase boundary** — when does "PR 3" end for executor sessions?
4. **PR 4 commit 9 CLAUDE.md table** — D18 says "32 final" but no canonical text.
5. **PR 5 commit 8 PG17 judgment call** — no rubric for blocking.
6. **No-op commits in PR 2** (commits 1 and 5) — handling unspecified.
7. **`Co-Authored-By` in template vs CLAUDE.md** — direct contradiction.
8. **`.github/dependabot.yml` `package-ecosystem: "pre-commit"`** — fallback YAML path mentioned but never invoked. Executor unsure whether to commit primary, fallback, or both.

---

## PER-PR EXECUTION-FLOW SIMULATION

### PR 1 — first 30 minutes

1. **Read spec + decision log** (~5 min).
2. **Run `git checkout -b feat/ci-refactor-pr1-supply-chain-hardening`**. Expected output: branch created.
3. **Commit 1 — start drafting SECURITY.md.** Lift the embedded markdown from spec lines 393-482. OK.
4. **Modify `pyproject.toml`** — needs `[project.urls]` block. **FIRST STUCK POINT** at ~10 min: spec says "add" but provides no example values. Executor either invents (homepage = github.com/prebid/salesagent, issues, docs, etc.) or asks user.
5. **Run verification** — `! grep -qE 'description = "Add your description here"' pyproject.toml` — executor checks current description. If it's NOT the placeholder, the verification is a no-op (the modification was unneeded). Spec says modify L4 description but doesn't say the current value.
6. **Commit 1.** Try `git commit` — pre-commit hook runs. **SECOND STUCK POINT** at ~20 min: pre-commit currently has 23s warm latency per pre-flight A8. Executor waits. If any unrelated hook fails (the codebase wasn't pre-verified clean), executor must fix or stop.

### PR 2 — first 30 minutes

1. **Read spec + decision log + check D13** (~5 min).
2. **Verify `.mypy-baseline.txt` exists** — does NOT exist. **FIRST STUCK POINT** at minute 6. Spec says "Mandatory before PR 2 is authored." Executor either runs P2 themselves (out of pre-flight scope) or stops.
3. **If executor runs P2 anyway**: `uv run mypy src/ --config-file=mypy.ini > .mypy-baseline.txt 2>&1` — current mypy run takes ~45s per CLAUDE.md typical times. If error count > 0 today (without pydantic.mypy plugin), baseline is non-zero — executor cannot tell which errors are pre-existing vs. plugin-introduced.
4. **Commit 1 ADR-001 status check** — does PR 1 commit 7 file exist? Executor needs to know whether to write it or skip. **SECOND STUCK POINT** at minute 15: no-op commit handling unspecified.
5. **Commit 2 mypy hook swap** — modify `.pre-commit-config.yaml` lines 289-305. Executor cannot verify those line numbers without reading the file (P1 says verify line numbers but executor cannot run pre-flight).

---

## ESCALATION PATHS — 5 SCENARIOS

| Scenario | What executor should do |
|---|---|
| Pre-flight not complete (P2 missing) | STOP. Write `.claude/notes/ci-refactor/escalations/pr<N>-preflight-incomplete.md` listing missing items. Do NOT attempt to run admin-only pre-flight steps. |
| Commit verification fails after fix attempt | After 2 fix attempts, STOP. Do NOT `--no-verify`. Write escalation note with: failing command, output, attempted fixes, suspected root cause. |
| Test passing in pre-flight now failing | STOP. Do not skip. Suspect: rebase introduced regression. Diff main vs. branch. If genuine regression, escalate; do NOT proceed. |
| User-only action required (admin gh api) | STOP. Note in escalation file. The template already says "do NOT run gh api -X PATCH branches/main" — this should be consistent across all 5 PRs but only PR 3 mentions it explicitly. |
| Mid-PR merge conflict (#1217 lands) | STOP and escalate. Per template line 119: "The executor cannot resolve concurrent merges." Do not attempt rebase autonomously. |

---

## TEMPLATE COMPLETENESS — `executor-prompt.md`

**Sufficient for fresh agent? NO.** Gaps:

1. Missing pre-flight verification step. Template should require executor to read pre-flight A1-P6 status FIRST and stop if incomplete.
2. Missing `--no-verify` policy clarification — implicit but not absolute. Should also forbid `pytest --ignore`, `-k "not"`, `pytest.mark.skip`.
3. Missing rule for **no-op commits** (PR 2 commits 1, 5).
4. Missing rule for **unbounded-scope commits** (PR 2 commit 3): hard time/file budget needed.
5. Co-author line conflict — should be resolved one way: "follow CLAUDE.md unless template overrides."
6. Missing **rebase policy**: if main moves during execution, rebase or stop?
7. Missing **commit boundary rule**: spec says "do NOT amend" but no guidance on when an amend would be the right answer (e.g., to fix a typo discovered before the next commit).
8. The template's Read-in-order list is missing `01-pre-flight-checklist.md` — executor doesn't read it.

### Recommended additions to template (key additions)

```
## Pre-flight verification (BLOCKING)

Before starting:
- [ ] Verify `.claude/notes/ci-refactor/branch-protection-snapshot.json` exists (A1)
- [ ] Verify `.mypy-baseline.txt` exists (P2; only required for PR 2)
- [ ] Verify `.zizmor-preflight.txt` exists (P3; only required for PR 1)
- [ ] Verify `make quality` passes on main (green baseline)
If any checkbox fails, STOP and escalate.

## Hard scope budgets

- PR 2 commit 3 cap: max 30 files modified, max 100 lines of `# type: ignore`. If hit, escalate.
- Any commit exceeding 500 lines of net diff: pause and confirm scope before committing.

## No-op commit policy

If a commit's intended diff is empty (e.g., another PR already landed the change):
- Do NOT make an empty commit.
- SKIP and add a note in PR description: "Commit N skipped — no-op (already landed in <ref>)."

## Co-author resolution

Use Co-Authored-By trailer per CLAUDE.md. Template was wrong on 2026-04; corrected.

## Rebase policy

If `main` advances mid-PR, run `git fetch origin && git rebase origin/main` between commits. If rebase produces conflicts, STOP and escalate.
```

---

## NET ASSESSMENT PER PR

| PR | Status | Required to make READY |
|---|---|---|
| PR 1 | **NEEDS WORK** | (1) Provide `[project.urls]` example values; (2) draft full 120-line CONTRIBUTING.md prose; (3) provide `.github/zizmor.yml` example syntax; (4) create `verify-pr1.sh` script; (5) verify pre-flight A1-A10/P1-P6 completion procedure. |
| PR 2 | **NEEDS WORK** | (1) Resolve `_architecture_helpers.py` ownership (PR 2 vs PR 4); (2) Cap commit 3 scope; (3) Specify how to handle no-op commits 1 and 5; (4) Enumerate the 3 phantom CLAUDE.md rows; (5) Reconcile D18 row counts (28 vs 32); (6) Run pre-flight P2 first. |
| PR 3 | **NEAR-READY** | (1) Disambiguate "Phase A merged" vs "PR 3 done" boundary; (2) Add a phase-B-failure procedure beyond inverse `gh api`; (3) Locate the schema-alignment test file referenced in commit 10; (4) Validate `peter-evans/create-pull-request` SHA pin in fallback. |
| PR 4 | **NEEDS WORK** | (1) Resolve `_architecture_helpers.py` conflict with PR 2; (2) Provide canonical full 32-row CLAUDE.md guards table text; (3) Document pre-push install procedure for commit 5 verification; (4) Specify intra-commit ordering for the 5 new guard files. |
| PR 5 | **NEAR-READY** | (1) Verify `docker-compose*.yml` accepts `--build-arg PYTHON_VERSION`; (2) Provide rubric for "tenant-isolation/schema correctness" PG17 judgment; (3) Verify the file-line numbers (`pyproject.toml:117`, `:138`) are still accurate. |

**Cross-cutting fix required before any PR is READY:** create `.claude/notes/ci-refactor/scripts/` with `verify-pr<N>.sh` files (lifted from inline bash blocks), and pre-create `docs/decisions/` directory with a `.gitkeep`. Run pre-flight A1-A10 before authorizing any agent to start PR 1.
