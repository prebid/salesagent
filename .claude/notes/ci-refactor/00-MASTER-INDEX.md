# CI & Pre-commit Refactor — Master Index

Tracks the rollout of GitHub issue [#1234](https://github.com/prebid/salesagent/issues/1234): a **6-PR rollout** (PR 1-5 core + PR 6 image supply-chain follow-up) that brings the salesagent repo to top-tier OSS supply-chain posture.

## Status

| PR | Title | Status | Spec | Hidden scope |
|---|---|---|---|---|
| PR 1 | Supply-chain hardening | not started | [pr1-supply-chain-hardening.md](pr1-supply-chain-hardening.md) | 2.5 days (Path C CodeQL); closes PD15a + PD15b |
| PR 2 | uv.lock single-source for pre-commit deps | not started | [pr2-uvlock-single-source.md](pr2-uvlock-single-source.md) | 4-6 days (pydantic.mypy delta) |
| PR 3 | CI authoritative + reusable workflows | not started | [pr3-ci-authoritative.md](pr3-ci-authoritative.md) | 3-4 days (3-phase merge) |
| PR 4 | Hook relocation + structural guards | not started | [pr4-hook-relocation.md](pr4-hook-relocation.md) | 2 days |
| PR 5 | Cross-surface version consolidation | not started | [pr5-version-consolidation.md](pr5-version-consolidation.md) | 2 days |
| PR 6 | Image supply chain (cosign + harden-runner + SBOM + scorecard.yml) | not started | [pr6-image-supply-chain.md](pr6-image-supply-chain.md) | 1.5-2 days (Week 6 follow-up; resolves D25) |

**Total realistic effort:** ~13.5-17 engineer-days for the 5-PR core rollout (PRs 1-5); +1.5-2 days for PR 6 follow-up = **15-19 engineer-days total, ~6 calendar weeks part-time**.

## Read in this order

1. [03-decision-log.md](03-decision-log.md) — every locked-in decision, why it was made, when to revisit
2. [01-pre-flight-checklist.md](01-pre-flight-checklist.md) — admin actions that must happen before PR 1 is authored
3. [02-risk-register.md](02-risk-register.md) — top-10 risks, mitigations, rollback procedures
4. The 6 per-PR specs in order — each is self-contained for the executor agent
5. [templates/executor-prompt.md](templates/executor-prompt.md) — agent prompt template
6. [templates/pr-description.md](templates/pr-description.md) — PR description template

## Sequencing

PR 1 → PR 2 → PR 3 (3-phase) → PR 4 → PR 5. Strict ordering for these reasons:

- **PR 2 depends on PR 1**: SHA-pinning convention established in PR 1 governs the local-hook migration in PR 2.
- **PR 3 depends on PR 1**: branch-protection bypass for `@chrishuie` (ADR-002) and `permissions: {}` audit live in PR 1 → PR 3 needs that baseline.
- **PR 4 depends on PR 3**: PR 4 deletes pre-commit hooks whose work has been absorbed into the `CI / Quality Gate` job introduced in PR 3. If PR 4 lands first, main loses enforcement.
- **PR 5 depends on PR 3**: PR 5 anchors versions through the `_pytest/action.yml` composite action (Decision-4 P0 sweep — was reusable workflow `_pytest.yml`). Without PR 3, anchoring is impossible.

## Concurrent work coordination

- **PR #1217** (adcp 3.10 → 3.12 migration, open, conflicting): assume merges before our PR 2; PR 2 designed to tolerate either ordering. Re-verify before authoring PR 2.
- **PR #1221** (Flask-to-FastAPI v2.0, open, branch `feat/v2.0.0-flask-to-fastapi`): 341 files changed, 31 new architecture tests (27 under `tests/unit/architecture/` + 4 top-level: `test_architecture_no_scoped_session.py`, `_no_module_level_get_engine.py`, `_no_runtime_psycopg2.py`, `_get_db_connection_callers_allowlist.py`) + 9 `.guard-baselines/*.json`. Will be carved into smaller PRs (none yet). Path 1 sequencing chosen — issue #1234 lands first, v2.0 phase PRs rebase onto the new layered model. PR #1221 was 5 days old as of P0 sweep (D20 tripwire fires at ~2026-05-04). CSRF concern in PR 1 deferred to v2.0's own CSRF middleware (`src/admin/csrf.py`, +331 lines on the v2.0 branch).
  - **`[project.optional-dependencies].dev` is already deleted on v2.0** — PR 2's pyproject.toml change becomes "verify the block is absent (deleted on v2.0); no-op if v2.0 already merged."
  - **`.pre-commit-config.yaml` three-way collision warning** — PR 1 SHA-pins, PR 2 deletes the entire `mirrors-mypy` block + replaces with `language: system` local hook, AND v2.0 bumps `mirrors-mypy rev: v1.18.2 → v1.19.1` + edits `additional_dependencies`. If v2.0 phase PR landed mid-PR-2-review, re-run autoupdate-freeze on the resulting block before authoring PR 2's deletion commit.
  - **`test-migrations` already deleted on v2.0** — PR 4's hook-deletion list double-counts if v2.0 lands first; verify post-rebase.
  - **31 + 9 = 40 v2.0 guard contributions** + 8 PR 1/3/6 governance guards — projected post-rollout guard count is **~73** (D18 revised in 2026-04-25 P0 sweep). PR 4's CLAUDE.md guards table audit defers to a post-v2.0-rebase commit.

## Success criteria (6 weeks from start)

- All 6 PRs merged on main (PR 1-5 + PR 6 follow-up; see `pr6-image-supply-chain.md`)
- Issue #1234 closed
- **OpenSSF Scorecard target phased:**
  - ≥6.5/10 after PR 1 (governance + SHA-pinned + permissions; `Signed-Releases` still capped without PR 6)
  - ≥7.5/10 after PR 6 (image signing via cosign satisfies `Signed-Releases`; CodeQL gating satisfies `SAST`)
- `time pre-commit run --all-files` warm < 2s
- `time make quality` < 2 minutes
- 4 weeks of Dependabot PRs cleared with no >5-PR backlog
- ≥1 contributor PR has gone through CODEOWNERS auto-request flow
- Zero post-merge reverts of any of the 6 PRs
- **All R26-R30 + R19/R20/R23 mitigations in place** (A11-A14 pre-flight passed; daily branch-protection snapshot cron landed; harden-runner v2.16+ pinned; cosign signing split into build+sign jobs)

## Calendar (6 weeks part-time)

| Week | Activity | Deliverable |
|---|---|---|
| Week 1 | Pre-flight A1-A14 (incl. allow_auto_merge audit, dependabot drain, mypy plugin snapshot, admin tier confirmation) + PR 1 (Path C CodeQL advisory; pinact + actionlint) | PR 1 merged by EOW; Scorecard ≥6.5 verified |
| Week 2 | OpenSSF Scorecard re-run; PR 2; first Dependabot PRs land | PR 2 merged mid-week |
| Week 3 | PR 3 Phase A (overlap, both old + new workflows running) + 48h soak; composite `_pytest` (not reusable) | Phase A merged; new workflows running advisory; rendered-name capture for Phase B |
| Week 4 | PR 3 Phase B (admin flips required-checks list with --paginate + --app_id) + Phase C (cleanup); coverage hard-gated from PR 3 day 1 (D11 revised, no Week 7-8 flip) | PR 3 fully landed; ≥48h soak A→B and B→C |
| Week 5 | PR 4 (real hook math 33−13−9−1=10) + PR 5 (without target-version bump per D28) + flip CodeQL to gating | PR 4 + PR 5 merged; close #1234 |
| Week 6 | PR 6 (harden-runner v2.16+ audit→block + cosign + SBOM + scorecard.yml + ghcr immutability) | PR 6 merged; Scorecard ≥7.5 verified |

Built-in slack: 1-2 days per week absorbs Dependabot review load. Week 4 was previously packed with Phase B + Phase C + PR 4 — too tight for two ≥48h soak windows; PR 4 moved to Week 5 to relieve.

**Dependabot backlog tripwire:** if open Dependabot PRs reach 5, pause forward work and clear them. The "no auto-merge" decision is only safe if review keeps pace.

## Update protocol

Update this file after each PR merges:
- Status column → `merged`
- Append a "PR #N merged YYYY-MM-DD" line under the heading
- Cross-reference any drift catalog items (PD1-PD25) closed by the PR

Issue #1234 also gets a comment per merge listing closed PD items.
