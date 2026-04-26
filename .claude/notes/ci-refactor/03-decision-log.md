# Decision Log

Captures every locked-in decision for the CI/pre-commit refactor (issue #1234). Decisions here override conflicting guidance in the issue body or any agent recommendation.

Format: each decision has a date, a one-line statement, the rationale, and a tripwire for revisiting.

## D1 — Solo-maintainer model

**Decided 2026-04 (issue #1234 §Decisions locked in #1):** `@chrishuie` is the sole CODEOWNERS entry. No multi-tier team structure.

- **Rationale:** matches operational reality. Adding additional maintainers is a separate, future decision.
- **Tripwire:** revisit when a second maintainer joins. ADR-002 covers the bypass mechanics.

## D2 — Branch protection + bypass

**Decided 2026-04-25:** Branch protection on `main` with `required_approving_review_count: 1`, `require_code_owner_reviews: true`, AND `@chrishuie` on the bypass list.

- **Rationale:** GitHub forbids self-approval. Without bypass, solo maintainer cannot merge. Bypass partially defeats CODEOWNERS for the maintainer's own PRs but preserves all other invariants (CI, no force-push, public PR trail). Captured in ADR-002 (drafted in PR 1).
- **Alternative rejected:** "0 reviews + CODEOWNERS required" is impossible (settings conflict). "1 review + second-reviewer requirement" blocks all work.
- **Tripwire:** when ADR-002's tripwire fires (second maintainer joins), remove `@chrishuie` from bypass.

## D3 — Mypy placement: CI-only

**Decided 2026-04 (issue #1234 §Decisions locked in #2):** mypy lives in CI, not in pre-commit (commit stage). Local feedback via `make quality`.

- **Rationale:** matches CPython, ruff, pydantic, FastAPI, Django, SQLAlchemy. Mypy in pre-commit is the Pandas pattern — documented drift pain.
- **Note:** PR 2 still creates a `local` mypy hook for invocation parity, but the hook moves to `stages: [pre-push]` in PR 4. CI's `CI / Type Check` job is authoritative.
- **Tripwire:** none — this is a steady-state architectural choice.

## D4 — Signed commits: deferred

**Decided 2026-04 (issue #1234 §Decisions locked in #3):** Commit signing is NOT required, NOT mandated in CONTRIBUTING.md, NOT enforced by branch protection.

- **Rationale:** future community decision. Adopting now is friction without commensurate threat-model justification at solo-maintainer scale.
- **Note:** **release-tag signing** is separate (sigstore/cosign for tags). That decision is also deferred but ought to be revisited when PR 5 lands and version anchors stabilize.
- **Tripwire:** revisit if a contributor base materializes or if release tampering becomes a stated threat.

## D5 — Dependabot auto-merge: NEVER

**Decided 2026-04 (issue #1234 §Decisions locked in #4):** Every dependency-update PR (pip, pre-commit, github-actions, docker) requires CODEOWNERS review. No `auto-merge: true` workflow. Period.

- **Rationale:** direct response to recent supply-chain attacks (tj-actions/changed-files, Axios, event-stream) where auto-merge of dependabot bumps delivered payloads. The point of pinning + reviewing is the review.
- **Sustainability tripwire:** if maintainer review backlog reaches 5+ open Dependabot PRs, pause forward work on issue #1234 and clear backlog. If backlog persists across multiple weeks, escalate to recruiting a second maintainer (D1 tripwire) — never to enabling auto-merge.

## D6 — Merge queue: not adopted

**Decided 2026-04 (issue #1234 §Decisions locked in #5):** No `merge_group:` triggers on workflows. No coordinated branch-protection-for-merge-queue work.

- **Rationale:** PR volume doesn't justify it; merge queue adds operational complexity disproportionate to repo throughput.
- **Tripwire:** revisit if monthly PR merge count consistently exceeds ~30.

## D7 — prek: not adopted for CI

**Decided 2026-04, REVISED 2026-04-25:** CI uses `pre-commit`, not `prek`. Contributors may use prek locally if they prefer; the `.pre-commit-config.yaml` is config-compatible.

- **Original rationale (issue #1234 §Decisions locked in #6):** Amdahl-bounded 10% speedup, single maintainer, production-maturity concerns, "zero top-OSS adoption."
- **Revision rationale (2026-04-25):** Round-2 OSS validation surfaced that **prek IS adopted by CPython, Apache Airflow, FastAPI, Ruff, and Home Assistant** as of early 2026. The "zero top-OSS adoption" framing was wrong. The remaining valid argument is the Amdahl-law performance ceiling on this workload (~10% of wall-clock improvement after PR 4's hook trimming, since ~85-95% of warm time is hook-internal work).
- **Net:** decision stands (CI uses pre-commit), but rationale is updated. CONTRIBUTING.md mentions prek as optional-local with a one-line "the maintainers do not test against it" note.
- **Tripwire:** revisit if prek adopts a CI-grade execution mode that materially exceeds pre-commit's correctness guarantees.

## D8 — pre-commit.ci: not adopted

**Decided 2026-04 (issue #1234 §Decisions locked in #7):** No `.pre-commit-ci.yaml`, no hosted-runner pattern.

- **Rationale:** zero adoption in the OSS reference set. Conflicts with PR 2's `language: system` choice (pre-commit.ci runs in an ephemeral container without the project venv).
- **Tripwire:** none — architecturally incompatible with our chosen pattern (ADR-001).

## D9 — `.claude/` and `CLAUDE.md`: out of scope (issue's wording)

**Decided 2026-04 (issue #1234 §Decisions locked in #8):** ROOT `CLAUDE.md` is updated for the structural-guards table (PR 4) but `.claude/` directory contents are out of scope.

- **Note (revised):** PR 4 *does* update CLAUDE.md guards table because the table is wrong on disk today (lists 24 rows with 3 names that don't exist; omits 5 that do). Skipping the table fix would propagate stale state. This is the only `CLAUDE.md` edit in scope.
- **Tripwire:** none.

## D10 — CSRF gating path: Path C (advisory CodeQL for 2 weeks)

**Decided 2026-04-25:** PR 1 lands with CodeQL **advisory**, not gating. Flip to gating at the start of Week 5 (after v2.0 phase PRs introducing CSRF middleware have had time to land).

- **Rationale:** static analysis projects 99 missing-CSRF findings on the current Flask admin routes. Gating from day 1 means PR 1 cannot land until 99 findings are triaged. Path A (suppress with codeql-config.yml justification) creates security debt; Path B (ship Flask-WTF CSRFProtect in PR 1) bundles two large concerns. Path C uses the 2-week window to let v2.0's structural CSRF fix (`src/admin/csrf.py` on the FastAPI branch) reduce the finding count organically.
- **Original directive:** the user's first answer was "blocking to start, rollback to advisory if needed." Revised after CSRF scope was discovered.
- **Tripwire:** end of Week 4. If CodeQL findings ≤ 5, flip to gating. If > 5 but ≤ 20, extend advisory by 1 week and triage. If > 20, file a follow-up issue and accept indefinite advisory until v2.0 lands.

## D11 — Coverage baseline: hard gate from PR 3 day 1 at `current - 2%`

**Decided 2026-04-25, REVISED 2026-04-25 (P0 sweep):** PR 3 introduces `.coverage-baseline` set to `53.5` (current measured 55.56% minus 2pp safety margin) as a **hard gate from PR 3 landing**. Coverage job runs `coverage report --fail-under=$(cat .coverage-baseline)`. Ratchet upward only when measured-stable across 4+ consecutive PRs.

- **Rationale:** the 2pp safety margin absorbs measurement noise from `pytest-xdist` shuffling; a true regression triggers immediate failure rather than accumulating advisory debt. The earlier "advisory for 4 weeks" framing was contradicted by the actual implementation (`--fail-under` is a hard gate); aligning the framing with the implementation eliminates ambiguity. Week 7-8 "flip to gating" step removed (already gating).
- **Tripwire:** if any single PR lowers measured coverage by >3pp, investigate before allowing further ratcheting.

## D12 — `pre-commit autoupdate --freeze` scope: bump-to-latest

**Decided 2026-04-25:** PR 1 runs `pre-commit autoupdate --freeze` which bumps every external hook to its latest tag AND rewrites `rev:` to a 40-char SHA with `# frozen: v<tag>` trailing comment.

- **Rationale:** Fortune-50 pattern (CPython, uv, FastAPI). Staying on stale pinned versions is itself a supply-chain risk.
- **Mitigation:** runs in a scratch branch first; review the diff (4 hooks: pre-commit-hooks, black, ruff, mirrors-mypy will become local in PR 2 anyway) before committing the freeze.
- **Tripwire:** if any bumped hook breaks `pre-commit run --all-files` on main, hold the SHA at the previous version (pin individually with `pre-commit autoupdate --freeze --repo <url>` for the others).

## D13 — Pydantic.mypy plugin: fix errors in PR 2

**Decided 2026-04-25:** When PR 2 makes the dead `pydantic.mypy` plugin live, fix the resulting errors in the same PR.

- **Estimated delta:** 40-150 new mypy errors, mostly `[arg-type]` and `[call-overload]` in `src/core/schemas*.py`.
- **Rationale:** the plugin has been silently disabled since project inception. Fixing the errors restores the type contract the project claimed to enforce. Inline `# type: ignore[code]` is acceptable for genuinely-Pydantic-internal cases.
- **Pre-flight measurement step (mandatory before PR 2):** `uv run mypy src/ --config-file=mypy.ini > .mypy-baseline 2>&1; wc -l .mypy-baseline` captures the baseline; PR 2 must drive the count down or hold it flat.
- **Tripwire:** if delta exceeds 200, defer plugin re-enablement to a separate follow-up PR; PR 2 ships only the local-hook migration.

## D14 — `[project.optional-dependencies].ui-tests`: migrate, don't delete (clarified 2026-04-26: tests/ui/ stays local-only)

**Decided 2026-04-25, CLARIFIED 2026-04-26 (Round 10 sweep):** PR 2 migrates the `ui-tests` extras block to `[dependency-groups].ui-tests` (PEP 735) alongside the dev migration. **`tests/ui/` is intentionally local-only** — it ships no CI job in PR 3 (no `UI Tests` frozen check name in D30); contributors run via `tox -e ui` on demand, with Playwright browser installation as a one-time local setup step.

- **Rationale:** `tests/ui/test_inventory_tree_smoke.py` (91 lines, playwright-based) and `tox.ini:75-81` `[testenv:ui]` env actively use it. Delete is wrong; migrate keeps it consistent with the dev group.
- **Why local-only and not in CI:** Playwright suite requires a browser installation (~200MB), runs against a stood-up Docker stack, and exercises a different surface (visual regression, click-through flows) than the unit/integration/admin/e2e suites already in CI. Adding a 15th `UI Tests` frozen check would (a) extend Phase B PATCH risk surface, (b) add ~5 minutes to CI wall-clock for limited incremental signal (admin tests already cover server-side correctness; UI tests verify rendering), (c) require fork-PR-safe Playwright cache management. Round 10 audit's "tests/ui/ is CI-orphaned" framing was correct in description but the intentional-local-only status was undocumented; clarified here.
- **Round 10 status (re-verified 2026-04-26):** `tests/ui/` exists with 3 files (`__init__.py, conftest.py, test_inventory_tree_smoke.py`); `tests/admin/` is a separate dir with 4 different files. The `abbcfa9b` rename moved a SUBSET of UI tests to `tests/admin/`; both directories are intentionally distinct (admin = server-side template rendering; ui = browser-side Playwright). NOT a partial-rename remnant.
- **Touch points:** `pyproject.toml:79-84` (move block), `tox.ini:77` (`extras = ui-tests` → `dependency_groups = ui-tests`), `scripts/setup/setup_conductor_workspace.sh:212` (`--extra ui-tests` → `--group ui-tests`).
- **Tripwire:** if `tests/ui/` grows substantially (>10 test files, or coverage of a critical user-facing path the admin tests don't touch), revisit and decide whether to add `UI Tests` as a 15th frozen check name. Never delete `tests/ui/` without an explicit replacement plan.

## D15 — Gemini key fallback: delete unconditionally

**Decided 2026-04-25:** PR 1 replaces `${{ secrets.GEMINI_API_KEY || 'test_key_for_mocking' }}` at `test.yml:342` with unconditional `GEMINI_API_KEY: test_key_for_mocking`.

- **Rationale:** verified — no e2e test invokes a live Gemini client. `src/core/config.py:141` documents the key as optional. Production code at `src/core/tools/creatives/_processing.py:177-182` handles the no-key case with a clear error.
- **No GitHub agentic-workflow alternative needed.**
- **Tripwire:** if a future test legitimately requires real Gemini, gate it behind a maintainer label (the `if: contains(github.event.pull_request.labels.*.name, 'use-real-gemini')` pattern).

## D16 — Dependabot ignores `adcp` until #1217 merges

**Decided 2026-04-25:** `.github/dependabot.yml` has `ignore: dependency-name: "adcp"` with a TODO comment referencing #1234. Removed in a cleanup commit immediately after #1217 merges.

- **Rationale:** PR #1217 is the manual adcp 3.10 → 3.12 migration, currently CONFLICTING. Letting Dependabot file a competing PR creates resolution work for nothing.
- **Tripwire:** when #1217 merges, file a follow-up issue ("dependabot: remove temporary adcp ignore") referencing this decision.

## D17 — Required CI check names: issue's verbatim list

**Decided 2026-04-25, AMENDED 2026-04-26 (Round 10 sweep — see D30):** The original 11 names are listed below. **D30 expands the canonical list to 14** by adding `Smoke Tests`, `Security Audit`, and `Quickstart` (currently-running CI jobs the original D17 silently dropped). Read D30 for the full 14-name list and the rationale for the addition. The 11 names below remain valid as a subset of the 14.

The 11 frozen check names — these are the *rendered* check-run names that GitHub publishes (workflow `name:` + ` / ` + job `name:`):

1. `CI / Quality Gate`
2. `CI / Type Check`
3. `CI / Schema Contract`
4. `CI / Unit Tests`
5. `CI / Integration Tests`
6. `CI / E2E Tests`
7. `CI / Admin UI Tests`
8. `CI / BDD Tests`
9. `CI / Migration Roundtrip`
10. `CI / Coverage`
11. `CI / Summary`

- **Rationale:** issue #1234 §Target architecture lists these. Three different agent reports proposed alternative casings/groupings; the issue is the source of truth.
- **Implementation note (D26 corollary):** the workflow `.github/workflows/ci.yml` has `name: CI` and each job has `name: 'Quality Gate'` (NOT `name: 'CI / Quality Gate'`). GitHub auto-prefixes the workflow name. Including `CI / ` in the job name produces `CI / CI / Quality Gate` (the Blocker #1 bug). See [community/discussions/46752](https://github.com/orgs/community/discussions/46752).
- **Verification before Phase B (mandatory):** capture actual rendered names via `gh api repos/.../commits/<sha>/check-runs` and confirm exact-string match against this list. Reusable workflow nesting can produce 3-segment names (e.g., `CI / Unit Tests / pytest`). See PR 3 Phase B Step 1b.
- **Note:** branch-protection still references current names (`Security Audit`, `Smoke Tests…`, etc.). PR 3's 3-phase merge handles the rename atomically. See PR 3 spec.
- **Frozen:** rename requires coordinated branch-protection update; treat as a contract.

## D18 — Structural guard count baseline: 27 + 1 + 4 + 1 + 8 + 27 + 4 + 9 = ~81

**Decided 2026-04-25, REVISED 2026-04-25 post-integrity-audit, REVISED 2026-04-25 (P0 sweep + v2.0 disk-truth audit), REVISED 2026-04-25 (Round 8 — drift re-verified v2.0 architecture/ count):** Existing on-disk guards count = 27 (23 `test_architecture_*.py` + 3 transport-boundary guards `test_no_toolerror_in_impl.py`, `test_transport_agnostic_impl.py`, `test_impl_resolved_identity.py` + `check_code_duplication.py` script). Math:

| Source | Guards added |
|---|---|
| Baseline (on disk today) | 27 |
| PR 2 (`_architecture_helpers.py` baseline guard) | 1 |
| PR 4 (4 new + 1 extended) | 4 |
| PR 5 (`test_architecture_uv_version_anchor`) | 1 |
| PR 1/3/6 governance (scorecard, actions-sha-pinned, workflow-permissions, persist-credentials-false, required-checks-frozen, workflow-concurrency, workflow-timeout-minutes, dependabot-groups-complete) | 8 |
| v2.0 architecture tests under `tests/unit/architecture/` (Round 8 drift-verified: actual count is **27**, was claimed 31) | 27 |
| v2.0 top-level architecture tests (`no_scoped_session`, `no_module_level_get_engine`, `no_runtime_psycopg2`, `get_db_connection_callers_allowlist`) | 4 |
| v2.0 baseline JSONs under `.guard-baselines/` | 9 |
| **Final post-v2.0-rebase** | **~81** |

- **Rationale:** v2.0's PR #1221 contributes 27 architecture tests in `tests/unit/architecture/` + 4 top-level — Round 8 drift audit corrected the earlier "31 architecture tests" framing (was conflating the architecture/ subdirectory + top-level into one number). Plus 9 baseline JSONs.
- **CLAUDE.md table audit step:** **DEFERRED to a post-v2.0-rebase commit, NOT executed in PR 4 commit 9.** Rationale: v2.0 lands 3 of the 5 "missing rows" (`test_architecture_bdd_no_direct_call_impl.py`, `test_architecture_bdd_obligation_sync.py`, `test_architecture_test_marker_coverage.py`); v2.0 also **deletes `test_architecture_no_silent_except.py`** entirely (Round 8 drift-confirmed). PR 4 commit 9 adds only **1 residual row** (`test_architecture_production_session_add.py`), NOT 2 as earlier framing claimed. Current CLAUDE.md table is **22 rows with 0 phantoms + 5 missing**. Final corrected table source: `drafts/claudemd-guards-table-final.md` (still claims 52 rows; needs ~81-row revision post-v2.0).
- **Tripwire:** when v2.0 phase PRs land, append the 27+4+9 new guard rows post-rebase, and remove `no_silent_except` from any planned addition list.

## D19 — Master plan format: per-PR specs, not master doc

**Decided 2026-04-25:** Use `.claude/notes/ci-refactor/pr<N>-<slug>.md` per-PR specs, not a single 600-line master doc.

- **Rationale:** fits the agent-driven execution model. Each executor agent loads only their own spec. Independent revertability is easier to reason about.
- **Note:** the issue itself plays the master-doc role (TL;DR + 8 decisions + drift catalog). Duplicating it in `docs/decisions/refactor-ci-precommit-plan.md` would create a drift surface.
- **Tripwire:** none.

## D20 — Sequencing relative to PR #1221 (Flask-to-FastAPI v2.0)

**Decided 2026-04-25:** Path 1. Issue #1234 lands first; v2.0 phase PRs rebase onto the new layered model.

- **Rationale:** v2.0 phase PRs benefit from SHA-pinned hooks, single-source uv.lock, layered CI, etc. The supply-chain hardening is independent of the Flask/FastAPI runtime choice.
- **Critical coordination:** **`[project.optional-dependencies].dev` is already deleted on the v2.0 branch.** PR 2's pyproject.toml change must NOT re-introduce the block when v2.0 rebases. Verify pyproject.toml diff during PR 2 final review.
- **Tripwire:** if v2.0 phase PRs are blocked for >2 weeks, revisit sequencing — issue #1234 PRs 3-5 may need to wait.

## D21 — Document collisions: docs/development/contributing.md is canonical (revised 2026-04-25 P0 sweep)

**Decided 2026-04-25, REVISED 2026-04-25 (Round 5+6 P0 sweep):** `docs/development/contributing.md` (594 lines on disk — verified by 2026-04-25 disk-truth audit) is the canonical contributor guide. Root `CONTRIBUTING.md` (currently 20 lines) becomes a thin pointer (~30 lines: 6 conventional-commit prefixes inline + "See docs/development/contributing.md for full contributor workflow." + `pre-commit install --hook-type pre-commit --hook-type pre-push` instruction).

- **Rationale (revised):** the original D21 framing assumed `docs/development/contributing.md` was a thin duplicate of the 20-line root. Round 6 disk-truth audit found it's actually a 594-line substantive contributor doc — 30× larger than root. Demoting it to a pointer would lose content. Better: keep docs version as canonical, make root the thin pointer (root is GitHub-recognized and auto-displayed in PR creation flow; the pointer redirect is sufficient).
- **`docs/development/ci-pipeline.md`** already exists (~70 lines). PR 4 rewrites/expands it; not a new file.
- **Tripwire:** none.
- **Implication for PR 1 commit 2:** scope changes from "rewrite root → 120 lines, demote docs version" to "rewrite root → ~30-line thin pointer, KEEP docs/development/contributing.md as-is." `verify-pr1.sh` line-count gates: root ≤ 50 lines, docs/development/contributing.md ≥ 500 lines.

## D22 — zizmor placement: CI-only

**Decided 2026-04-25** (promoted from D-pending-1): zizmor runs in CI (`.github/workflows/security.yml`), not as a pre-commit hook. Matches issue #1234 framing and avoids the layering problem of having pre-commit run a workflow-security linter that targets files pre-commit doesn't typically inspect.

- **Tripwire:** if zizmor's CI signal is consistently slow (>30s wall-clock per PR), revisit local pre-commit invocation.

## D23 — check-parameter-alignment: delete

**Decided 2026-04-25** (promoted from D-pending-2): `check-parameter-alignment` pre-commit hook is deleted in PR 4 commit 7. Coverage is preserved by `test_architecture_boundary_completeness.py` which already enforces MCP/A2A wrapper completeness via AST inspection.

- **Tripwire:** if a regression bypasses `boundary_completeness`, file a follow-up to extend that guard, NOT to resurrect the grep hook.

## D24 — UV_VERSION: anchor in `_setup-env` composite action

**Decided 2026-04-25** (promoted from D-pending-3): PR 3's `.github/actions/setup-env/action.yml` declares `UV_VERSION: <pinned>` as a single source of truth. PR 5 enforces cross-file consistency via `test_architecture_uv_version_anchor.py`.

- **Tripwire:** when uv ships a major version (e.g., 0.5 → 1.0), bump the anchor in one place; the guard catches drift across `.python-version`, mypy.ini, Dockerfile, tox.ini.

## D25 — harden-runner adoption: PR 6 (follow-up)

**Decided 2026-04-25** (promoted from D-pending-4, **resolved by PR 6**): harden-runner is adopted as a Week 6 follow-up PR. Audit-mode for 2 weeks, then flip to block-mode with allowlist captured from telemetry. See `pr6-image-supply-chain.md` Commits 1 + 3.

- **Tripwire:** if audit-mode reveals unexpected egress endpoints (e.g., suspicious analytics/telemetry from any pinned action), do NOT add to allowlist without supply-chain investigation.
- **Critical (revised 2026-04-25 P0 sweep):** harden-runner pinned version MUST be **v2.16.0 or later**, with `disable-sudo-and-containers: true` (NOT `disable-sudo: true`). v2.12.0 is the floor for CVE-2025-32955; **v2.13+** patches additional medium DoH/DNS-over-TCP egress-bypass advisories ([GHSA-46g3-37rh-v698](https://github.com/step-security/harden-runner/security/advisories)). v2.16.0+ captures all post-CVE advisories.

## D26 — Workflow naming convention: drop `CI /` prefix from job names

**Decided 2026-04-25** (resolves Blocker #1 from `research/integrity-audit.md`): GitHub renders status checks as `<workflow.name> / <job.name>`. The workflow has `name: CI`; jobs use bare `name: 'Quality Gate'`, NOT `name: 'CI / Quality Gate'`. Including the prefix produces `CI / CI / Quality Gate` (silent bug — Phase B branch-protection PATCH would 422 against unprefixed names).

- **Source:** [community/discussions/46752](https://github.com/orgs/community/discussions/46752)
- **Affected files:** `.github/workflows/ci.yml`, plus PR 3 Phase B PATCH body and verification scripts.
- **Tripwire:** when adding a new required check, follow this convention. Verify rendered name via `gh api repos/.../check-runs --jq '.check_runs[].name'` before adding to branch protection.

## D27 — Pre-commit hook reallocation: 10 hooks moved to pre-push

**Decided 2026-04-25, REVISED 2026-04-25 (P0 sweep), REVISED 2026-04-25 (Round 8 sweep — disk re-verified):** To meet PR 4's `commit-stage hooks ≤ 12` acceptance, PR 4 commit 5 moves **10 hooks to pre-push**:
- 9 from the original P0-sweep list: `check-docs-links`, `check-route-conflicts`, `type-ignore-no-regression`, `adcp-contract-tests`, `mcp-contract-validation`, `mcp-schema-alignment`, `check-tenant-context-order`, `ast-grep-bdd-guards`, `check-migration-completeness`.
- **+1 from D3:** `mypy` (PR 2 creates the local mypy hook at commit-stage for invocation parity during the migration window; PR 4 moves it to pre-push per D3 — this move was always implied but was missing from D27's enumerated list).

- **Real baseline (Round 8 disk-verified):** **36 effective commit-stage hooks** (40 total `- id:` minus 4 at `stages: [manual]`: `smoke-tests`, `test-migrations`, `pytest-unit`, `mcp-endpoint-tests`). Earlier "33 effective" framing in P0 sweep was off by 3 due to a counting error (missed 1 active hook + miscounted manual hooks). Actual disk-truth: 40 active − 4 manual = 36.
- **Math:** 36 effective commit-stage − 13 commit-stage deletions (15 plan deletions − 2 already-manual: `pytest-unit`, `mcp-endpoint-tests`) − **10 moves to pre-push** − 1 consolidation = **12 commit-stage** (exactly at ≤12 ceiling, zero headroom).
- **Math expansion:** `36 − 13 − 10 − 1 = 12` is shorthand for `36 effective commit-stage − 13 deletions − 10 pre-push moves − 2 grep one-liner consolidations + 1 new `repo-invariants` consolidation hook = 12`. The `−1` term collapses the consolidation. Round 9 verification confirmed the math against actual `.pre-commit-config.yaml` count.
- **Note 1:** v2.0 phase PR also deletes `test-migrations` hook (already manual). Net effect on commit-stage count: zero. PR 4's hook-deletion list double-counts if v2.0 lands first; verify post-rebase.
- **Note 2:** v2.0 also deletes `test_architecture_no_silent_except.py` (drift-confirmed Round 8). PR 4 commit 9 must NOT add this row to CLAUDE.md table.
- **Tripwire:** if `time pre-commit run --all-files` warm exceeds 2s after PR 4 lands, identify additional move candidates from the 12 remaining commit-stage hooks (no-hardcoded-urls, repo-invariants, the 8 pre-commit-hooks built-ins, black, ruff). Most are already <50ms each — additional moves unlikely needed.

## D28 — Defer black/ruff target-version bump out of PR 5

**Decided 2026-04-25 (Round 5+6 P0 sweep):** The black/ruff py311 → py312 target-version bump is **DEFERRED** out of PR 5 to a separate hand-reviewed PR after #1234 closes.

- **Rationale:** the original PR 5 step `ruff check --target-version py312 --fix --select UP` replicates the exact pattern that triggered the 2026-04-14 unsafe-autofix incident (UP040 production-schema breakage). Per `feedback_no_unsafe_autofix.md`, "If a lint rule would rewrite 3+ files in source, STOP and ask." Target-version bump unlocks no value chain in #1234.
- **PR 5 retains:** cross-file uv version consolidation, Python version consolidation, Postgres version consolidation, structural guard `test_architecture_uv_version_anchor`. The load-bearing piece (anchor consolidation) stays.
- **PR 5 drops:** `[tool.black].target-version` py311 → py312, `[tool.ruff].target-version` py311 → py312, `ruff check --target-version py312 --fix --select UP` mass-fix, `--no-verify` carve-out (was needed because UP040 fix-cycle could mismatch hooks).
- **Follow-up:** filed as 'Post-#1234: bump black/ruff py311 → py312 with hand-applied UP040 fixes.' See ADR-008 (`drafts/adr-008-target-version-bump.md`).
- **Tripwire:** revisit after PR 5 ships AND `_pytest.yml` → composite migration is verified stable.

## D29 — Structural-guard marker name: `arch_guard` (was `architecture`)

**Status:** Locked 2026-04-25 (Round 9 sweep)

**Decision:** The pytest marker registered for structural guards (PR 4 commit 1) is named `arch_guard`, NOT `architecture`. Registration target is `pytest.ini` `[pytest]` section under `markers = ` continuation lines (NOT `pyproject.toml [tool.pytest.ini_options]`).

**Rationale:** `tests/conftest.py:25-45,146-153,786-800` registers `architecture` as an ENTITY-marker auto-applied by filename pattern (`test_architecture_*.py`, `no_toolerror_in_impl`, `transport_agnostic_impl`, etc.). PR 4 originally planned a SECOND `architecture` marker for structural guards; same name, different semantics → silent conflation under `pytest -m architecture`. Round 9 verification surfaced the collision. Rename disambiguates: structural guards use `arch_guard`; entity-marker stays `architecture`.

**Empirical correction:** the project uses `pytest.ini` (with `--strict-markers`), not `pyproject.toml`, for pytest config. Verified against repo state.

**Tripwire:** if a future PR re-introduces `architecture` as a marker name in code, the structural guard `test_architecture_marker_naming` flags it.

**Affected:** PR 2 commit 8 (registration target + name), PR 4 commits 1-2 (marker rename), all references in architecture.md, briefings/pr2-pr4-pr5-briefing.md, checklists/pr4-checklist.md, drafts/guards/.

## D30 — Frozen CI check names: **14** (was 11). Adds Smoke Tests, Security Audit, Quickstart.

**Decided 2026-04-26 (Round 10 sweep):** D17's 11 frozen names was incomplete. Three currently-running CI jobs in `.github/workflows/test.yml` were silently dropped from the new structure:

1. `CI / Smoke Tests` — `test.yml:34-75` runs `pytest tests/smoke/ -v` + `@pytest.mark.skip` grep gate (~30s, no Docker; fast-fail layer for import / migration / startup errors)
2. `CI / Security Audit` — `test.yml:15-32` runs `uvx uv-secure --ignore-vulns GHSA-7gcm-g887-7qv7,GHSA-5239-wwwm-4pmq`. **Two active CVE allowlists** would be lost without preservation.
3. `CI / Quickstart` — `test.yml:239-285` runs `docker compose up -d --wait` + migration assertion + table-existence assertion (10-min timeout). Catches docker-compose drift that other tests don't see.

The 14 frozen rendered names (D26 convention — workflow `name: CI` + bare job name; GitHub auto-prefixes):

```
CI / Quality Gate         CI / Smoke Tests           CI / Migration Roundtrip
CI / Type Check           CI / Unit Tests            CI / Coverage
CI / Schema Contract      CI / Integration Tests     CI / Summary
CI / Security Audit       CI / E2E Tests
CI / Quickstart           CI / Admin UI Tests
                          CI / BDD Tests
```

- **Rationale:** smoke gap caught manually 2026-04-26; subsequent Round 10 audits surfaced security-audit and quickstart with the same root cause (planning was reasoning about the new layered model abstractly without auditing existing CI). Preserving these three as frozen names is the only way to keep equivalent regression coverage post-Phase C delete of `test.yml`.
- **Cascade:** `EXECUTIVE-SUMMARY.md`, `00-MASTER-INDEX.md`, `pr3-ci-authoritative.md` (job declarations + Phase B PATCH body), `summary.needs:` list, `drafts/guards/test_architecture_required_ci_checks_frozen.py` expected list, `landing-schedule.md`. PR 6's `Security / Dependency Review` is OUTSIDE the 14 (lives in `security.yml` namespace, not `CI /`).
- **Tripwire:** when the 15th name is proposed (e.g., `UI Tests` for tests/ui Playwright suite), require explicit decision-log entry. Casual additions inflate Phase B PATCH risk.
- **Affected:** D17 amended (count 11 → 14; full list re-published here); PR 3 spec gains 3 job declarations; structural guard expected-list updated.

## D31 — `default_install_hook_types: [pre-commit, pre-push]` mandatory in `.pre-commit-config.yaml`

**Decided 2026-04-26 (Round 10 sweep):** PR 4 commit 1 adds `default_install_hook_types: [pre-commit, pre-push]` at the top of `.pre-commit-config.yaml`. Without it, contributors who run the documented `pre-commit install` only get pre-commit-stage hooks; the **entire 10-hook pre-push tier (D27 — including mypy per D3) silently does not execute**.

- **Rationale:** D27 moves 10 hooks to `stages: [pre-push]` and the math `36 − 13 − 10 − 1 = 12` requires they actually run somewhere. `pre-commit install --hook-type pre-push` is the explicit workaround documented in CONTRIBUTING.md, but it relies on contributor compliance. `default_install_hook_types` is the canonical pre-commit directive that auto-installs both hook types from a single `pre-commit install` invocation. Top-OSS adoption confirmed: pydantic, FastAPI, ruff, every project using pre-push stages.
- **Empirical impact:** silent pre-push bypass is the load-bearing failure mode for D27. Hook math is mathematically correct but operationally ineffective without this directive.
- **Tripwire:** if a future PR adds a `stages: [pre-commit-msg]` or other hook-type, append it to the `default_install_hook_types` list (CI structural guard verifies completeness).
- **Affected:** PR 4 commit 1 (or commit 0 — stage adjustment), `scripts/check-hook-install.sh` warning message simplified, `docs/development/contributing.md` `pre-commit install` instruction simplified (no longer needs `--hook-type pre-push` qualifier).

## D32 — Creative-agent containerized service bootstrap: full inventory in PR 3 commit 9

**Decided 2026-04-26 (Round 10 sweep):** PR 3 commit 9's previously-handwavy "unconditional creative agent in integration" expands to enumerate the full bootstrap from `.github/workflows/test.yml:180-223` (43 lines). Required elements:

1. `docker network create creative-net`
2. `postgres:16-alpine` second container (named `creative-postgres`) on `creative-net`
3. Build creative-agent image from pinned commit `ca70dd1e2a6c` (re-verify SHA at PR 3 author time)
4. Run creative-agent container with **10 environment variables**:
   - `NODE_ENV=test`
   - `PORT=3000`
   - `DATABASE_URL=postgres://creative:creative@creative-postgres:5432/creative`
   - `RUN_MIGRATIONS=true`
   - `ALLOW_INSECURE_COOKIES=true`
   - `DEV_USER_EMAIL=test@example.com`
   - `DEV_USER_ID=test-user`
   - `AGENT_TOKEN_ENCRYPTION_SECRET` (test value, not real secret)
   - `WORKOS_API_KEY` (test value, mock-only)
   - `WORKOS_CLIENT_ID` (test value, mock-only)
5. 60-iteration health-check poll on `http://creative-agent:3000/health`
6. Export `CREATIVE_AGENT_URL=http://creative-agent:3000` for tests in `_pytest/action.yml`

- **Rationale:** integration tests silently break without this. PR 3 commit 9 spec body referenced "unconditional creative agent" but provided zero bootstrap content — only a permissions verification block. Round 10 audit caught this; before Round 10 the executor would have shipped a broken integration job.
- **Test mode justification:** `WORKOS_*` and `AGENT_TOKEN_ENCRYPTION_SECRET` are test-only values; the creative-agent has a `NODE_ENV=test` path that uses mock identity. No real WorkOS account needed in CI.
- **Tripwire:** if the creative-agent commit `ca70dd1e2a6c` becomes stale (>3 months old), bump and re-verify health check passes; document in PR 3 spec.
- **Affected:** PR 3 commit 9 spec rewrite (~50 lines of YAML); `_pytest/action.yml` env block adds `CREATIVE_AGENT_URL` passthrough.

## D33 — xdist test config: `pytest-xdist≥3.6` + `pytest-randomly` in dev group; `--dist=loadscope` in CI

**Decided 2026-04-26 (Round 10 sweep):** PR 2 gains a new commit (4.5, between current 4 and 5) adding both packages to `[dependency-groups].dev`:

```toml
[dependency-groups]
dev = [
  ...
  "pytest-xdist>=3.6",          # required by PR 3 commit 4b template-clone
  "pytest-randomly",             # order-independence enforcement
  ...
]
```

PR 3's `_pytest/action.yml` composite passes `--dist=loadscope` to the pytest invocation under integration / e2e / admin / bdd envs. Default `--dist=load` (random) splits session-scoped DB fixtures across workers — known incompat with the project's UUID-per-test DB pattern.

- **Rationale:** PR 3 spec line 9 declared `pytest-xdist≥3.6` as a precondition ("MUST be added... before this PR's xdist commits land. Best location: PR 2 commit 4 or 5") but neither PR 2 nor PR 3 actually added it. Round 10 audit caught the gap. `pytest-randomly` is corroborated by audits 7 + 9 as the order-independence enforcement standard adopted by Django, attrs, structlog. (`filelock>=3.20.3` already exists in `pyproject.toml:48` as a main dependency — no action needed there.)
- **Note on `--dist=loadgroup`:** if any tests use `@pytest.mark.xdist_group`, `loadgroup` is needed instead. Audit found no current usages; default `loadscope` is correct. If groups are introduced post-rollout, change the flag in `_pytest/action.yml` only.
- **Tripwire:** if `pytest -n auto` shows fixture-state errors (e.g., `IntegrityError: duplicate key`) post-PR-3, switch to `--dist=loadgroup` and tag affected fixtures.
- **Affected:** PR 2 new commit 4.5; PR 3 `_pytest/action.yml` invocation.

## D34 — Container hardening: `@sha256:` digest pin + `USER` non-root in PR 5; `SOURCE_DATE_EPOCH` + Trivy OS-layer scanning in PR 6

**Decided 2026-04-26 (Round 10 sweep):** Round 10 audits (7 + 8 corroborating) surfaced incoherent supply-chain posture: the plan SHA-pins all GitHub Actions and signs container images with cosign, but leaves the most-trusted layer (Python base image) tag-only and unscanned, and runs as root.

PR 5 adds:
1. **Base image SHA-pinning** — `Dockerfile:4,43` `FROM python:3.12-slim` → `FROM python:3.12-slim@sha256:<64hex>`. Dependabot `package-ecosystem: docker` (already in PR 1's config) picks up `@sha256:` updates.
2. **`USER` non-root** — `RUN groupadd -r app && useradd -r -g app app` then `USER app:app` before ENTRYPOINT. Image manifest declares non-root user (Kubernetes `runAsNonRoot: true` admission compatible).
3. **Structural guard** `test_architecture_dockerfile_digest_pinned.py` — fails if `FROM` lacks `@sha256:` or no `USER` directive present.

PR 6 adds:
4. **`SOURCE_DATE_EPOCH=$(git log -1 --format=%ct)`** — passed via `--build-arg` in `release-please.yml publish-docker` job, plus BuildKit `--source-date-epoch` flag with `rewrite-timestamp`. Two builds of the same source produce identical image digests.
5. **Trivy OS-layer scan** — new commit using `aquasecurity/trivy-action@<SHA>` post-build, fail on `severity: CRITICAL,HIGH` + `vuln-type: os,library` + `ignore-unfixed: true`. Pin to known-good SHA (post-2026-03 trivy-action supply-chain incident — verify SHA at PR 6 author time).

- **Rationale:** pip-audit covers Python deps only. Base image inherits 30-50 OS CVEs/month (glibc, openssl, zlib, etc.) via `python:3.12-slim`. Without Trivy, those CVEs are unscored. Without `@sha256:`, the docker-hub IPv6/index races make tag-only pulls non-deterministic. Without `SOURCE_DATE_EPOCH`, two CI runs produce different digests — defeats the point of cosign signing for reproducibility verification. Top-OSS pattern (CNCF graduated containers, Kubernetes release builds, sigstore project itself).
- **Distroless / Chainguard runtime image:** P2 follow-up. Project's runtime stage installs `nginx + supercronic + curl`; decompose into sidecars before going distroless.
- **Tripwire:** if Trivy reports >5 ignorable CRITICAL findings (false-positive flood), tighten `--severity` filter rather than ignoring categories wholesale.
- **Affected:** PR 5 Dockerfile rewrite (~10 line additions); PR 5 new structural guard; PR 6 new commit (~30 lines for Trivy + SOURCE_DATE_EPOCH).

## D35 — `gitleaks` adopted for secret detection: PR 1 (pre-commit hook + workflow)

**Decided 2026-04-26 (Round 10 sweep):** PR 1 adds `gitleaks` as both a pre-commit hook (commit-stage, fast scan of changed files) and a workflow job (full-history scan with SARIF upload to GitHub Security tab).

```yaml
# .pre-commit-config.yaml additions (PR 1)
- repo: https://github.com/gitleaks/gitleaks
  rev: <SHA>  # frozen: v8.x.x
  hooks:
    - id: gitleaks
```

```yaml
# .github/workflows/security.yml additions (PR 1)
gitleaks:
  runs-on: ubuntu-latest
  permissions:
    contents: read
    security-events: write
  steps:
    - uses: actions/checkout@<SHA>
      with:
        fetch-depth: 0
        persist-credentials: false
    - uses: gitleaks/gitleaks-action@<SHA>  # frozen: v2.x.x
      with:
        upload-sarif: true
```

- **Rationale:** GitHub's native secret scanning + push-protection (enabled in PR 6 admin step) covers known-pattern secrets but misses entropy-based detection and full-history scanning that gitleaks provides. Top-OSS norm: 24,400+ stars; broadly adopted by CNCF projects, sigstore, Apache Foundation.
- **Tripwire:** if gitleaks generates >50 findings against current main, baseline via `gitleaks.toml` and ratchet down. Do NOT suppress with broad-category ignores.
- **Affected:** PR 1 `.pre-commit-config.yaml` add; PR 1 `security.yml` new job; pre-flight P-new captures `.gitleaks-baseline.txt` count.

## D36 — ADR file lifecycle: `docs/decisions/` is the production location; drafts/ is for in-flight content

**Decided 2026-04-26 (Round 10 sweep):**

- **ADR-001 (single-source pre-commit deps), ADR-002 (solo-maintainer bypass), ADR-003 (pull_request_target defenses):** PR 1 commit 7 + 11 write these **directly to `docs/decisions/`** (creating the directory if absent). The canonical text lives inline in the PR 1 spec body (§Embedded ADR-001 / §Embedded ADR-002 / §Embedded ADR-003); the executor lifts verbatim at commit time. Skipping a drafts/ stage avoids the doubled-work of "write to drafts/, then move in PR 5."
- **ADR-008 (target-version deferral):** already exists in `drafts/adr-008-target-version-bump.md`. PR 5 commit copies (NOT moves) to `docs/decisions/adr-008-target-version-bump.md`. The drafts/ original stays as audit trail.
- **Future ADRs** (ADR-004/005/006/007/009): exist in drafts/ as planning artifacts. They land in `docs/decisions/` only when their corresponding PR or follow-up issue ships.

**Rationale:** Round 10 audit caught `docs/decisions/` did not exist on disk and the ADRs were referenced inline in spec body but never staged as standalone reviewable files. Two corrections were possible: (a) double-stage via drafts/ → docs/decisions/, (b) write directly to docs/decisions/ since the spec body has the canonical text. Path (b) chosen because PR 1 commit 7's existing structure already writes to `docs/decisions/`; reverting to drafts/ would create a second write target without reviewer benefit. ADR-008 stays in drafts/ as audit trail because it evolved across multiple planning rounds; production version is the snapshot.

- **Tripwire:** if a new ADR is referenced in 3+ files without either (a) inline canonical text in the relevant PR spec or (b) an entry in `drafts/`, file as a Round-N+1 sweep gap.
- **Affected:** no spec change — PR 1 commit 7 + 11 already write to `docs/decisions/`. PR 5 gains a one-commit ADR-008 copy from drafts/. Round 10 audit's CRIT-8 framing ("docs/decisions/ doesn't exist") is resolved automatically when PR 1 commit 7 lands (creates the directory + first three files).

## D37 — `workflow_dispatch` trigger preserved in `ci.yml`

**Decided 2026-04-26 (Round 10 sweep):** PR 3's `ci.yml` declares `on: { pull_request: …, push: …, workflow_dispatch: }` (matches current `test.yml:8` capability). Manual-run preservation lets maintainers force a full-suite run for in-flight PRs after Phase B flip (R32 mitigation pre-requisite per `pr3-ci-authoritative.md:541`).

- **Rationale:** Phase B Step 1b requires a `workflow_dispatch` to capture rendered names. Phase B Step 2.5's in-flight PR drain procedure also benefits from operator-triggered re-runs without forcing a `git push --force-with-lease` no-op. PR 3 spec body in Round 9 elided the trigger; round 10 audit caught the omission.
- **R28 interaction:** `concurrency: cancel-in-progress: ${{ github.event_name == 'pull_request' }}` ensures `workflow_dispatch` runs are NOT cancelled by subsequent push events (already mitigated; this is the existing fix, just locked).
- **Tripwire:** if `workflow_dispatch` becomes a vector for accidental main-branch CI burn (fork PRs, bot dispatch), gate behind `if: github.actor == 'chrishuie'` per-job.
- **Affected:** PR 3 ci.yml `on:` block.

## D38 — `Schema Contract` job runs under integration env (not unit)

**Decided 2026-04-26 (Round 10 sweep):** PR 3's `Schema Contract` job runs `pytest tests/unit/test_adcp_contract.py tests/integration/test_mcp_contract_validation.py` under `tox -e integration` (with DATABASE_URL set), NOT `tox -e unit` (which unsets DATABASE_URL at `tox.ini:38`).

- **Rationale:** `test_mcp_contract_validation.py` is in `tests/integration/` and depends on DB for tenant fixture loading. Running it under unit would silently skip the DB-dependent assertions or fail with import errors. Round 10 audit caught the cross-env drift.
- **Alternative considered:** move `test_mcp_contract_validation.py` to `tests/unit/` and refactor to mock the DB. Rejected: the test legitimately exercises the DB-loaded MCP tool registry; mocking would lose coverage.
- **Tripwire:** if a future "schema contract" check is purely static (no DB), use a third tox env (`tox -e schema-contract`) rather than overloading integration. Integration env is heavy.
- **Affected:** PR 3 `ci.yml` Schema Contract job invocation; `landing-schedule.md` Week 4 step.

## D39 — Creative-agent integration uses docker-run script-step pattern, NOT GHA `services:` blocks

**Decided 2026-04-26 (Round 11 sweep — corrects Round 10 D32; port direction corrected 2026-04-26 Round 12 R12A-02):** PR 3 integration-tests job runs creative-agent + adcp-postgres as `docker run` script steps on a custom `creative-net` Docker network. Round 10's `services:` block design is technically broken in GitHub Actions — service containers each get their own bridge network; they cannot resolve each other by hostname.

- **Rationale:** R11A-03 caught this. GHA service containers communicate only with the runner host via port mapping. They are NOT on a shared docker network with each other. Two services CANNOT reach each other by name. The existing `test.yml:180-223` pattern uses `docker network create + docker run` precisely because of this constraint. Round 10's spec dropped element #1 from D32 ("docker network create creative-net") when translating to `services:` syntax — silently breaking cross-service networking.
- **Health probe location:** runs on the runner host (where curl is preinstalled), NOT inside the creative-agent container (where curl is likely missing from a Node-based image). R11A-04 fix.
- **Env values matched to disk truth (`test.yml:201-211` verbatim):** `NODE_ENV=production` (not `test`), port `9999:8080` (host:container — i.e., `docker run -p 9999:8080`; host=9999, container=8080; **R12A-02 fix: earlier prose had the direction reversed**), DB user `adcp` / password `localdev` / db `adcp_registry`, AGENT_TOKEN_ENCRYPTION_SECRET = literal 32-char string `local-ci-encryption-key-32chars!!`, WORKOS keys = `sk_test_dummy` / `client_dummy`, DEV_USER_EMAIL = `ci@test.com`, DEV_USER_ID = `ci-user`. Round 10's spec used wrong values.
- **CREATIVE_AGENT_URL:** `http://localhost:9999/api/creative-agent` (path includes `/api/creative-agent` because creative-agent is one route in the adcp monolith — Round 10 had no path).
- **Tripwire:** if upstream creative-agent ever publishes a standalone Docker image with proper service-isolation, revisit; until then, the script-step pattern is required.
- **Affected:** PR 3 commit 3 integration-tests job rewritten; PR 3 commit 9 spec body updated to match disk truth; D32 superseded for the YAML structure (env value list still authoritative).

## D40 — Postgres `max_connections` tuning: app-side, not service-side (with explicit wiring contract per Round 12 R12A-01)

**Decided 2026-04-26 (Round 11 sweep), AMENDED 2026-04-26 (Round 12 R12A-01):** GHA `services:` blocks do not support a `command:` or `cmd:` field; the only writable fields are `image`, `env`, `ports`, `options`, `credentials`, `volumes`. To prevent "too many clients" flake under xdist, **reduce the app's connection pool in CI** rather than tuning Postgres.

`_pytest/action.yml` env block sets:
```yaml
DB_POOL_SIZE: '4'
DB_MAX_OVERFLOW: '8'
```

Math: 4 xdist workers × (4 pool + 8 overflow) = 48 conn at peak, comfortably under Postgres default `max_connections=100`.

**Wiring contract (Round 12 R12A-01 fix — load-bearing):** `src/core/database/database_session.py:108-109` (PgBouncer branch) and `:124-125` (direct PG branch) hardcode `pool_size` and `max_overflow` as Python literals. Without app-side wiring, the env vars are ignored and D40's mitigation silently no-ops. **PR 2 gains a new commit** (between current 4.5 and 5) that wires the env vars:

```python
# src/core/database/database_session.py — replace the literal 10 / 20 with:
pool_size=int(os.getenv("DB_POOL_SIZE", "10")),
max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "20")),
```

Both branches (PgBouncer at lines 108-109 and direct PG at 124-125) get the override. Defaults preserve existing production behavior; CI overrides via env. `verify-pr3.sh` greps for `os.getenv("DB_POOL_SIZE"` to enforce the wiring lands.

- **Rationale:** R11E-02 surfaced the connection-saturation risk; R31 documented it; R12A-01 caught that the original D40 mitigation was non-operational. App-side override is cleanest because the change lives in one composite env block + ~2 lines of app code; Postgres-side tuning would require either (a) a custom postgres image (adds Dockerfile to repo) or (b) a docker-run step in every DB-using job (loses GHA's auto-wait-on-healthy semantics).
- **Pre-flight verification (P-NEW from Round 12):** new commit in PR 2 lands the wiring BEFORE PR 3's `_pytest/action.yml` sets the env vars. Without this commit, `_pytest/action.yml` env vars are inert.
- **Tripwire:** if `tox -e integration -- -n auto` post-Phase-A-soak shows "too many clients" failures, escalate to (a) custom postgres image or (b) docker-run pattern.

## D41 — pytest-json-report path: keep `{toxworkdir}/<env>.json`; composite globs both candidates

**Decided 2026-04-26 (Round 11 sweep — fixes R11E-03):** tox.ini emits pytest-json-report to `{toxworkdir}/<env>.json` (e.g., `.tox/unit.json`). The `_pytest/action.yml` composite's upload-artifact step globs BOTH `test-results/` and `.tox/<env>.json` so the JSON report is captured regardless of which path future tox.ini changes use.

- **Rationale:** R11E-03 caught that the earlier `path: test-results/` was a silent-empty-artifact: tox writes elsewhere, so the artifact was always empty, and developers debugging a failure had only the GHA stdout log. The dual-path glob is forward-compatible: if a future PR moves tox.ini's path to `test-results/`, the existing glob still matches.
- **Tripwire:** if a future structural guard requires structured failure data and the artifact is empty, revisit. As long as the glob includes both paths, this is safe.
- **Affected:** PR 3 commit 2 `_pytest/action.yml` upload step.

## D42 — `integration_db` schema source: accept divergence with Alembic; tripwire on production drift

**Decided 2026-04-26 (Round 11 sweep — R11B-2):** `tests/conftest_db.py:323-528` `integration_db` fixture uses `Base.metadata.create_all` to materialize the schema; `migration-roundtrip` job tests Alembic migrations end-to-end. The two paths can drift if a model is added without a corresponding migration.

- **Rationale:** Round 11 R11B audit caught this as a P0 divergence. The cleanest fix would be to route `integration_db` through `scripts/ops/migrate.py` (Alembic) for full parity. That's a multi-day refactor with non-trivial test fixture rewrites — **defer to a follow-up PR after #1234 closes**.
- **Risk acceptance:** the existing structural guards in CLAUDE.md (e.g., `test_architecture_migration_completeness.py`) catch the most likely drift class — model added without migration. The migration-roundtrip job catches Alembic-side breakage. The remaining gap is "model field changed (column type, nullable, default) without migration"; this is caught by the `alembic check` future-add per Round 10 SF-26 (also a follow-up).
- **Tripwire:** if a production deploy fails due to schema-vs-Alembic drift, prioritize the integration_db→Alembic refactor.
- **Affected:** no PR change; documented as accepted divergence.

## D43 — DATABASE_URL credentials: CI canonical (`adcp_user/test_password/adcp_test`); compose uses dev realism; tests must NOT hardcode

**Decided 2026-04-26 (Round 11 sweep — R11B-1 partial fix):** CI uses `postgresql://adcp_user:test_password@localhost:5432/adcp_test`. Compose uses production-like `secure_password_change_me/adcp` for dev realism. Tests MUST read `DATABASE_URL` from env; hardcoded credentials in `tests/ui/conftest.py:35` and `tests/e2e/conftest.py:406` are bugs to fix in a follow-up PR.

- **Rationale:** unifying on one credential triple across 3 environments is desirable but invasive. Compose's "looks-like-production" credentials catch a class of bugs (env-var-not-passed-through) that all-test-creds miss. CI's "obviously-test" creds make leak detection trivial.
- **Hard rule:** no test file hardcodes credentials. Existing offenders are tracked as follow-ups; PR 3 does not unify them but D43 sets the policy.
- **Tripwire:** if a CI test fails with `password authentication failed`, root-cause to the test file (likely hardcodes compose creds); fix forward.
- **Follow-up filed:** "Post-#1234: parameterize hardcoded DB credentials in tests/ui and tests/e2e conftests."

## D44 — `.pre-commit-config.yaml` declares `minimum_pre_commit_version: 3.2.0`

**Decided 2026-04-26 (Round 11 sweep — R11C-06):** PR 4 commit 1 (in addition to `default_install_hook_types: [pre-commit, pre-push]` per D31) adds `minimum_pre_commit_version: 3.2.0` to the top of `.pre-commit-config.yaml`.

- **Rationale:** `default_install_hook_types` is a pre-commit ≥3.2 feature. Older versions (Debian stable, system-installed) silently ignore unknown directives, leaving the entire pre-push tier disabled. This is exactly the failure mode D31 was supposed to prevent. The `minimum_pre_commit_version` directive causes `pre-commit install` to fail noisily on older versions, surfacing the issue at install time rather than silently at commit time.
- **Tripwire:** if a contributor reports that `pre-commit install` fails with a version-too-old error, point them at `uv tool install pre-commit` or `pip install --upgrade pre-commit`.
- **Affected:** PR 4 commit 1; docs/development/contributing.md mentions the 3.2 minimum.

## D45 — Phase B branch-protection flip: forbidden on Fri/Sat/Sun + holiday eve

**Decided 2026-04-26 (Round 11 sweep — R11C-02 mitigation):** Phase B atomic flip MUST NOT be executed on a Friday, Saturday, Sunday, or the day before a US/EU holiday. Pre-flight A22 enforces a day-of-week + holiday-calendar check.

- **Rationale:** if Phase B fails (e.g., 422 from rendered-name divergence), main becomes unmergeable until inverse PATCH is applied. With solo maintainer, recovery requires admin presence. A weekend-eve flip can leave main unmergeable for 36-72h, blocking all merges including security fixes.
- **Tripwire:** if a future Phase B-class operation is unavoidable on a weekend, require a second admin temporarily added to the bypass list with a known-revoke time. Document in an explicit ADR before executing.
- **Affected:** `01-pre-flight-checklist.md` adds A22; `landing-schedule.md` Week 4 (Phase B) explicitly marks Mon-Thu only.

## D46 — Pre-flight P9 stale-string grep guard (propagation discipline)

**Decided 2026-04-26 (Round 12 sweep — addresses recurring propagation drift):** Each sweep round adds new content to per-PR specs. Historically the propagation across non-spec surfaces (verify scripts, briefings, executor template, admin scripts, architecture.md) trails by 1-2 rounds, leading to stale strings like "11 frozen", "D1-D28", "R1-R10" misleading executors and reviewers. Round 11 R11D-02 caught this once; Round 12 R12-A/B/C caught the same class across more surfaces.

**Mitigation:** `01-pre-flight-checklist.md` gains item P9 — a shell script that fails non-zero if any of these stale-string patterns appear outside explicitly allowlisted history-marker contexts:

```bash
# scripts/check-stale-strings.sh (NEW)
set -euo pipefail
EXIT=0
declare -a PATTERNS=(
  '11 frozen check names'   # superseded by 14 (D17 amended by D30)
  'D1-D28'                  # superseded by D1-D45 (Round 11)
  'D1-D38'                  # superseded by D1-D45 (Round 11; intermediate state)
  'R1-R10\\b'               # superseded by R1-R42 (Round 11/12)
  '18 rules'                # superseded by 19 rules (Round 9 added Rule 19)
  'promoted to standalone drafts'  # ADR-001/002/003 are inline per drafts/README.md
)
ALLOWLIST_FILES=(
  '.claude/notes/ci-refactor/RESUME-HERE.md'              # has audit-trail sections
  '.claude/notes/ci-refactor/architecture.md'             # explicitly marked stale; banner forwards
  '.claude/notes/ci-refactor/REFACTOR-RUNBOOK.md'         # superseded; kept as audit trail
  '.claude/notes/ci-refactor/research/'                   # read-only audit trail
  '.claude/notes/ci-refactor/03-decision-log.md'          # decision history may cite older counts
)
# … grep + filter logic that excludes ALLOWLIST_FILES …
```

Pre-flight P9 runs the script; exit 0 means the corpus is clean of propagation drift; exit 1 means the next sweep round must clean up before being declared complete.

- **Rationale:** without an automated check, each sweep round leaves residual stale strings that the next round (and reviewers) trip over. R11D-02 fixed this for some surfaces in Round 11; R12-C caught the same class across executor template + briefings + admin scripts. The structural fix is to make "did we propagate the new naming?" a checked invariant.
- **Tripwire:** if a future sweep introduces a NEW stale-string pattern (e.g., decisions are renumbered, frozen-name count changes), update the PATTERNS array. The ALLOWLIST is for files explicitly marked as audit-trail (banner declared); production-facing files like verify scripts and briefings are NOT allowlisted.
- **Affected:** new file `scripts/check-stale-strings.sh`; `01-pre-flight-checklist.md` adds P9 step + sign-off box.

## D47 — `release-please publish-docker` MUST gate on CI green (Round 12 post-issue-review)

**Decided 2026-04-26 (post-issue-review of #1228):** PR 6 commit 2's `publish-docker` job in `release-please.yml` adds a step that requires the `ci.yml` workflow to have concluded `success` on the same release commit BEFORE building, signing, or pushing the image. Without this gate, red main can ship signed-and-attested-but-broken images — and after #1234 PR 6 (cosign + Trivy + SBOM + provenance), the image is **authoritatively** broken (the supply-chain trail makes the bad build look verified).

`needs:` doesn't cross workflows, so the gate is implemented as a `gh api` lookup in a step:

```yaml
- name: Require CI green on release commit
  env:
    GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
    RELEASE_SHA: ${{ needs.release-please.outputs.sha }}
  run: |
    STATUS=$(gh api -X GET \
      "repos/${{ github.repository }}/actions/workflows/ci.yml/runs" \
      -f head_sha="$RELEASE_SHA" -f per_page=1 \
      --jq '.workflow_runs[0].conclusion // "missing"')
    [[ "$STATUS" == "success" ]] || { echo "CI conclusion: $STATUS (must be success)" >&2; exit 1; }
```

- **Rationale:** #1228 Cluster A4 caught this — `publish-docker` had only `needs: release-please` (the release-please job itself), no test gate. Round 10/11/12 audits all missed it; surfaced during the issue-rewrite work for #1228.
- **Why not branch protection:** branch protection's required-status-checks would block the COMMIT but not the WORKFLOW. release-please can fire on a `release_created` output even on a commit that hasn't yet completed CI (race window between push and CI completion). The in-workflow gate closes the race.
- **Tripwire:** if a future workflow restructure changes ci.yml's name or splits CI into multiple top-level workflows, update the `gh api` query path.
- **Affected:** PR 6 commit 2 (added "Require CI green on release commit" step before docker setup); R44 (risk register) added.

## Decisions still open (will be resolved in flight)

(None as of 2026-04-26 — D-pending-1..4 promoted to D22-D25 above. The earlier reference to "D-pending-5" in `pr4-hook-relocation.md:499` is a one-off mention of the issue body's bar tightening from <5s to <2s; not a decision-log-status item — handled inline at that PR 4 acceptance criterion.)

## Change log

- 2026-04 — D1, D3-D9 captured from issue #1234
- 2026-04-25 — D2, D10-D21 added; D7 revised after OSS validation surfaced prek adopters
- 2026-04-25 (post-integrity-audit) — D-pending-1..4 promoted to D22-D25; D26 (workflow naming) and D27 (hook reallocation) added; D17 and D18 revised; D-pending-5 resolved as inline acceptance criterion (not a separate decision)
- 2026-04-25 (Round 5+6 P0 sweep) — D11 reframed (drop "advisory" — hard gate from day 1); D18 rewritten (~73 final post-v2.0-rebase, was 42); D27 rewritten (real baseline 33 effective, math 33−13−9−1=10); D28 added (defer target-version bump per ADR-008)
- 2026-04-25 (Round 9 verification sweep) — D29 added (marker rename `architecture` → `arch_guard`, registration target `pytest.ini`); D27 amended with math-expansion clarifying note
- 2026-04-26 (Round 10 completeness audit sweep) — D30 (frozen names 11→14: adds Smoke Tests, Security Audit, Quickstart); D31 (`default_install_hook_types` mandatory); D32 (creative-agent bootstrap full inventory); D33 (xdist+randomly added; `--dist=loadscope`); D34 (container hardening: `@sha256:` + `USER` non-root + `SOURCE_DATE_EPOCH` + Trivy); D35 (gitleaks adopted in PR 1); D36 (ADR file location: drafts/ → docs/decisions/); D37 (`workflow_dispatch` preserved in ci.yml); D38 (Schema Contract job under integration env, not unit). D17 amended (count 11→14; full list re-published in D30). D27 unchanged.
- 2026-04-26 (Round 11 verification + extension sweep) — D39 added (creative-agent script-step pattern; corrects Round 10 D32's GHA-broken `services:` design); D40 (Postgres connection pool tuning app-side via DB_POOL_SIZE/DB_MAX_OVERFLOW env); D41 (pytest-json-report path glob both `test-results/` and `.tox/<env>.json`); D42 (integration_db Alembic divergence accepted with tripwire); D43 (DATABASE_URL credentials: CI canonical + compose dev-realism + no test-side hardcoding); D44 (`minimum_pre_commit_version: 3.2.0`); D45 (Phase B forbidden on Fri/weekend/holiday eve). D32 amended for env values matching disk truth (NODE_ENV=production not test; port 8080→9999 not 3000; path `/api/creative-agent` not `/health`; 10 env values match `test.yml:201-211` verbatim). D36 unchanged. R38-R42 added.
- 2026-04-26 (Round 12 verification + sweep) — D46 added (pre-flight P9 grep-guard for stale-string drift propagation discipline). D40 amended with explicit wiring contract (R12A-01 caught that the original D40 mitigation was non-operational because `src/core/database/database_session.py` hardcoded pool sizes as Python literals; new PR 2 commit wires `os.getenv("DB_POOL_SIZE", ...)`). D39 prose corrected: port direction was reversed in the decision text (`8080:9999 (host:container)` → `9999:8080`); YAML in PR 3 spec was always correct. R43 added (verify-script drift behind spec amendments). Mechanical sweep: 11 → 14 names propagated through verify-pr3.sh, verify-pr4.sh, flip-branch-protection.sh, capture-rendered-names.sh, add-required-check.sh, executor-prompt.md; D1-D28 → D1-D45; "18 rules" → "19 rules"; verify-pr5.sh extended with USER + SOURCE_DATE_EPOCH + @sha256 + ADR-008 checks; verify-pr6.sh extended with Trivy + dep-review-config + frozen-checks-guard checks; verify-pr3.sh extended with creative-agent script-step pattern + filelock + DB_POOL_SIZE app-wiring + pytest-xdist/randomly checks; verify-pr4.sh extended with default_install_hook_types + minimum_pre_commit_version + frozen-checks-guard-lift checks. RESUME-HERE.md:34 struck the unstruck "promoted to standalone drafts" claim. EXECUTIVE-SUMMARY.md last-refresh, effort, R/D ranges updated.
- 2026-04-26 (Round 12 post-issue-review) — surfaced while rewriting #1228, #1233, #1189, #1234 issue bodies. Three genuine new findings landed in spec: D47 added (release-please publish-docker MUST gate on CI green via `gh api` step — closes #1228 Cluster A4, the P0 that 12 audit rounds all missed); R44 added (red main can ship signed-but-broken images if D47 missing); PR 3 spec gains `timeout-minutes` on the 5 jobs Round 11 R11E-04 flagged (Quality Gate 10, Type Check 10, Migration Roundtrip 10, Coverage 10, Summary 5); `_setup-env` composite `cache-dependency-glob` extended to include `pyproject.toml` (closes #1228 Cluster C5 — stale-cache class). Acknowledged-but-out-of-scope: #1189 (creative-agent caching) tracked separately as small follow-up after PR 3; #1228 Tier 3 architectural items (F1/F2/G2: ruff ignores, mypy lenient flags, 301-entry obligation allowlist) deferred to post-#1234 architectural-debate issues; #1228 E1/E2 (`google_ad_manager_original` phantom refs + `.mypy_baseline` orphan) low-priority cleanup not folded into this rollout.
