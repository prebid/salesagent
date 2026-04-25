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

## D14 — `[project.optional-dependencies].ui-tests`: migrate, don't delete

**Decided 2026-04-25:** PR 2 migrates the `ui-tests` extras block to `[dependency-groups].ui-tests` (PEP 735) alongside the dev migration.

- **Rationale:** `tests/ui/test_inventory_tree_smoke.py` (91 lines, playwright-based) and `tox.ini:75-81` `[testenv:ui]` env actively use it. Delete is wrong; migrate keeps it consistent with the dev group.
- **Touch points:** `pyproject.toml:79-84` (move block), `tox.ini:77` (`extras = ui-tests` → `dependency_groups = ui-tests`), `scripts/setup/setup_conductor_workspace.sh:212` (`--extra ui-tests` → `--group ui-tests`).
- **Tripwire:** none.

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

**Decided 2026-04-25:** The 11 frozen check names — these are the *rendered* check-run names that GitHub publishes (workflow `name:` + ` / ` + job `name:`):

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

## D18 — Structural guard count baseline: 27 + 1 + 4 + 1 + 8 + 31 + 9 = ~73

**Decided 2026-04-25, REVISED 2026-04-25 post-integrity-audit, REVISED 2026-04-25 (P0 sweep + v2.0 disk-truth audit):** Existing on-disk guards count = 27 (23 `test_architecture_*.py` + 3 transport-boundary guards `test_no_toolerror_in_impl.py`, `test_transport_agnostic_impl.py`, `test_impl_resolved_identity.py` + `check_code_duplication.py` script). Math:

| Source | Guards added |
|---|---|
| Baseline (on disk today) | 27 |
| PR 2 (`_architecture_helpers.py` baseline guard) | 1 |
| PR 4 (4 new + 1 extended) | 4 |
| PR 5 (`test_architecture_uv_version_anchor`) | 1 |
| PR 1/3/6 governance (scorecard, actions-sha-pinned, workflow-permissions, persist-credentials-false, required-checks-frozen, workflow-concurrency, workflow-timeout-minutes, dependabot-groups-complete) | 8 |
| v2.0 architecture tests under `tests/unit/architecture/` + 4 top-level | 31 |
| v2.0 baseline JSONs under `.guard-baselines/` | 9 |
| **Final post-v2.0-rebase** | **~73** |

- **Rationale:** v2.0's PR #1221 contributes far more than the original "9 baseline JSONs" claim — disk-truth + v2-overlap audits in 2026-04-25 P0 sweep verified 31 net-new architecture tests (27 under `tests/unit/architecture/` + 4 top-level: `test_architecture_no_scoped_session.py`, `_no_module_level_get_engine.py`, `_no_runtime_psycopg2.py`, `_get_db_connection_callers_allowlist.py`) plus 9 baseline JSONs.
- **CLAUDE.md table audit step:** **DEFERRED to a post-v2.0-rebase commit, NOT executed in PR 4 commit 9.** Rationale: v2.0 will land 3 of the 5 "missing rows" (`test_architecture_bdd_no_direct_call_impl.py`, `test_architecture_bdd_obligation_sync.py`, `test_architecture_test_marker_coverage.py`); only **residual 2** (`test_architecture_no_silent_except.py`, `test_architecture_production_session_add.py`) are PR 4's responsibility. Current CLAUDE.md table is **22 rows with 0 phantoms + 5 missing** (corrected from earlier "24 rows with 3 phantoms + 5 missing"). Final corrected table source: `drafts/claudemd-guards-table-final.md` (still claims 52 rows; needs ~73-row revision post-v2.0).
- **Tripwire:** when v2.0 phase PRs land, append the 31+9 new guard rows post-rebase.

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

## D27 — Pre-commit hook reallocation: 9 hooks moved to pre-push

**Decided 2026-04-25, REVISED 2026-04-25 (P0 sweep — disk-truth audit):** To meet PR 4's `commit-stage hooks ≤ 12` acceptance, PR 4 commit 5 moves 9 hooks to pre-push. The 4 additional hooks beyond the original 5: `mcp-schema-alignment`, `check-tenant-context-order`, `ast-grep-bdd-guards`, `check-migration-completeness`.

- **Real baseline: 33 effective commit-stage hooks** (36 total `- id:` minus 3 already `stages: [manual]`). The 4 hooks that are already manual: `smoke-tests`, `test-migrations`, `pytest-unit`, `mcp-endpoint-tests` — but disk-truth audit found only 3 are at `stages: [manual]` today; the fourth is at a different stage. (Earlier "37 commit-stage" framing double-counted manual hooks.)
- **Math:** 33 effective commit-stage − 13 commit-stage deletions (2 of plan's 15 are already manual: `pytest-unit`, `mcp-endpoint-tests` — they reduce dead-manual count, not commit-stage count) − 9 moves to pre-push − 1 consolidation = **10 commit-stage** (under ≤12 ceiling).
- **Note:** v2.0 phase PR also deletes `test-migrations` hook (already manual). PR 4's hook-deletion list double-counts if v2.0 lands first; verify post-rebase.
- **Tripwire:** if `time pre-commit run --all-files` warm exceeds 2s after PR 4 lands, identify additional move candidates.

## D28 — Defer black/ruff target-version bump out of PR 5

**Decided 2026-04-25 (Round 5+6 P0 sweep):** The black/ruff py311 → py312 target-version bump is **DEFERRED** out of PR 5 to a separate hand-reviewed PR after #1234 closes.

- **Rationale:** the original PR 5 step `ruff check --target-version py312 --fix --select UP` replicates the exact pattern that triggered the 2026-04-14 unsafe-autofix incident (UP040 production-schema breakage). Per `feedback_no_unsafe_autofix.md`, "If a lint rule would rewrite 3+ files in source, STOP and ask." Target-version bump unlocks no value chain in #1234.
- **PR 5 retains:** cross-file uv version consolidation, Python version consolidation, Postgres version consolidation, structural guard `test_architecture_uv_version_anchor`. The load-bearing piece (anchor consolidation) stays.
- **PR 5 drops:** `[tool.black].target-version` py311 → py312, `[tool.ruff].target-version` py311 → py312, `ruff check --target-version py312 --fix --select UP` mass-fix, `--no-verify` carve-out (was needed because UP040 fix-cycle could mismatch hooks).
- **Follow-up:** filed as 'Post-#1234: bump black/ruff py311 → py312 with hand-applied UP040 fixes.' See ADR-008 (`drafts/adr-008-target-version-bump.md`).
- **Tripwire:** revisit after PR 5 ships AND `_pytest.yml` → composite migration is verified stable.

## Decisions still open (will be resolved in flight)

(None as of 2026-04-25 — D-pending-1..4 promoted to D22-D25 above. The earlier reference to "D-pending-5" in `pr4-hook-relocation.md:499` is a one-off mention of the issue body's bar tightening from <5s to <2s; not a decision-log-status item — handled inline at that PR 4 acceptance criterion.)

## Change log

- 2026-04 — D1, D3-D9 captured from issue #1234
- 2026-04-25 — D2, D10-D21 added; D7 revised after OSS validation surfaced prek adopters
- 2026-04-25 (post-integrity-audit) — D-pending-1..4 promoted to D22-D25; D26 (workflow naming) and D27 (hook reallocation) added; D17 and D18 revised; D-pending-5 resolved as inline acceptance criterion (not a separate decision)
- 2026-04-25 (Round 5+6 P0 sweep) — D11 reframed (drop "advisory" — hard gate from day 1); D18 rewritten (~73 final post-v2.0-rebase, was 42); D27 rewritten (real baseline 33 effective, math 33−13−9−1=10); D28 added (defer target-version bump per ADR-008)
