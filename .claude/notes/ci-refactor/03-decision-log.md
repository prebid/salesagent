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

## D11 — Coverage baseline: advisory for 4 weeks at `current - 2%`

**Decided 2026-04-25:** PR 3 introduces `.coverage-baseline` set to `53.5` (current measured 55.56% minus 2pp safety margin). Advisory for 4 weeks. Flip to gating in Week 7-8.

- **Rationale:** ratcheting-from-current can deadlock on legit bugfix PRs that delete over-fitted tests; advisory window absorbs measurement noise from `pytest-xdist` shuffling.
- **Tripwire:** if any single PR within the advisory window lowers measured coverage by >3pp, investigate before flipping to gating.

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

## D18 — Structural guard count baseline: 27 + 1 (PR2) + 4 (PR4) + 1 (PR5) + 9 (v2.0) = 42

**Decided 2026-04-25, REVISED 2026-04-25 post-integrity-audit:** Existing on-disk guards count = 27 (23 `test_architecture_*.py` + 3 transport-boundary guards `test_no_toolerror_in_impl.py`, `test_transport_agnostic_impl.py`, `test_impl_resolved_identity.py` + `check_code_duplication.py` script). PR 2 adds 1. PR 4 adds 4 + extends 1. PR 5 adds 1 (`test_architecture_uv_version_anchor`). v2.0 contributes 9 via `.guard-baselines/`. **Final post-rollout: 42** (canonical count for cross-doc reconciliation).

- **Rationale:** doc-drafting agent re-counted disk; the "24" in CLAUDE.md was wrong. PR 4 corrects the table to the post-rollout count and reserves the v2.0 9 as future entries.
- **CLAUDE.md table audit step (mandatory in PR 4):** for each row, verify the test file exists; for each test file under `tests/unit/test_architecture_*.py`, verify a row exists. **0 phantom rows** (all listed files exist on disk; the integrity audit corrected the earlier "3 phantom" claim — there were never any phantoms). **5 missing rows** must be added (files exist but were omitted from the table): `test_architecture_no_silent_except.py`, `test_architecture_bdd_no_direct_call_impl.py`, `test_architecture_bdd_obligation_sync.py`, `test_architecture_production_session_add.py`, `test_architecture_test_marker_coverage.py`. Final corrected 52-row table source: `drafts/claudemd-guards-table-final.md` (sectioned by Schema/Transport/DB/BDD/Test integrity/Governance/Cross-file).
- **Tripwire:** when v2.0 phase PRs land, append the 9 new guard rows.

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

## D21 — Document collisions: keep root `CONTRIBUTING.md`

**Decided 2026-04-25:** Root `CONTRIBUTING.md` (currently 20 lines) is the canonical contributor guide. PR 1 rewrites it to ~120 lines.

- **Rationale:** root location is GitHub-recognized (auto-displayed in PR creation flow). `docs/development/contributing.md` (existing duplicate) is demoted to a thin pointer or deleted in PR 1.
- **`docs/development/ci-pipeline.md`** already exists (~70 lines). PR 4 rewrites/expands it; not a new file.
- **Tripwire:** none.

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
- **Critical:** harden-runner pinned version MUST be v2.12.0 or later, with `disable-sudo-and-containers: true` (NOT `disable-sudo: true`) per CVE-2025-32955.

## D26 — Workflow naming convention: drop `CI /` prefix from job names

**Decided 2026-04-25** (resolves Blocker #1 from `research/integrity-audit.md`): GitHub renders status checks as `<workflow.name> / <job.name>`. The workflow has `name: CI`; jobs use bare `name: 'Quality Gate'`, NOT `name: 'CI / Quality Gate'`. Including the prefix produces `CI / CI / Quality Gate` (silent bug — Phase B branch-protection PATCH would 422 against unprefixed names).

- **Source:** [community/discussions/46752](https://github.com/orgs/community/discussions/46752)
- **Affected files:** `.github/workflows/ci.yml`, plus PR 3 Phase B PATCH body and verification scripts.
- **Tripwire:** when adding a new required check, follow this convention. Verify rendered name via `gh api repos/.../check-runs --jq '.check_runs[].name'` before adding to branch protection.

## D27 — Pre-commit hook reallocation: 9 hooks moved to pre-push (not 5)

**Decided 2026-04-25** (resolves Blocker #2 from `research/integrity-audit.md`): To meet PR 4's `commit-stage hooks ≤ 12` acceptance with the actual 37-hook baseline (drifted +1 from the 36 measured), PR 4 commit 5 moves 9 hooks to pre-push, not 5. The 4 additional hooks: `mcp-schema-alignment`, `check-tenant-context-order`, `ast-grep-bdd-guards`, `check-migration-completeness`.

- **Math:** 37 commit-stage today − 15 deletions − 9 moves to pre-push − 1 consolidation = **12 commit-stage** (exactly at the ceiling).
- **Tripwire:** if `time pre-commit run --all-files` warm exceeds 2s after PR 4 lands, identify additional move candidates.

## Decisions still open (will be resolved in flight)

(None as of 2026-04-25 — D-pending-1..4 promoted to D22-D25 above. The earlier reference to "D-pending-5" in `pr4-hook-relocation.md:499` is a one-off mention of the issue body's bar tightening from <5s to <2s; not a decision-log-status item — handled inline at that PR 4 acceptance criterion.)

## Change log

- 2026-04 — D1, D3-D9 captured from issue #1234
- 2026-04-25 — D2, D10-D21 added; D7 revised after OSS validation surfaced prek adopters
- 2026-04-25 (post-integrity-audit) — D-pending-1..4 promoted to D22-D25; D26 (workflow naming) and D27 (hook reallocation) added; D17 and D18 revised; D-pending-5 resolved as inline acceptance criterion (not a separate decision)
