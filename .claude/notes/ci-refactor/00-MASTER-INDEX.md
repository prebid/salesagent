# CI & Pre-commit Refactor — Master Index

Tracks the rollout of GitHub issue [#1234](https://github.com/prebid/salesagent/issues/1234): a 5-PR sequence that brings the salesagent repo to top-tier OSS supply-chain posture.

## Status

| PR | Title | Status | Spec | Hidden scope |
|---|---|---|---|---|
| PR 1 | Supply-chain hardening | not started | [pr1-supply-chain-hardening.md](pr1-supply-chain-hardening.md) | 2.5 days (Path C CodeQL) |
| PR 2 | uv.lock single-source for pre-commit deps | not started | [pr2-uvlock-single-source.md](pr2-uvlock-single-source.md) | 4-6 days (pydantic.mypy delta) |
| PR 3 | CI authoritative + reusable workflows | not started | [pr3-ci-authoritative.md](pr3-ci-authoritative.md) | 3-4 days (3-phase merge) |
| PR 4 | Hook relocation + structural guards | not started | [pr4-hook-relocation.md](pr4-hook-relocation.md) | 2 days |
| PR 5 | Cross-surface version consolidation | not started | [pr5-version-consolidation.md](pr5-version-consolidation.md) | 2 days |

**Total realistic effort:** ~14-18 engineer-days, ~5 calendar weeks part-time.

## Read in this order

1. [03-decision-log.md](03-decision-log.md) — every locked-in decision, why it was made, when to revisit
2. [01-pre-flight-checklist.md](01-pre-flight-checklist.md) — admin actions that must happen before PR 1 is authored
3. [02-risk-register.md](02-risk-register.md) — top-10 risks, mitigations, rollback procedures
4. The 5 per-PR specs in order — each is self-contained for the executor agent
5. [templates/executor-prompt.md](templates/executor-prompt.md) — agent prompt template
6. [templates/pr-description.md](templates/pr-description.md) — PR description template

## Sequencing

PR 1 → PR 2 → PR 3 (3-phase) → PR 4 → PR 5. Strict ordering for these reasons:

- **PR 2 depends on PR 1**: SHA-pinning convention established in PR 1 governs the local-hook migration in PR 2.
- **PR 3 depends on PR 1**: branch-protection bypass for `@chrishuie` (ADR-002) and `permissions: {}` audit live in PR 1 → PR 3 needs that baseline.
- **PR 4 depends on PR 3**: PR 4 deletes pre-commit hooks whose work has been absorbed into the `CI / Quality Gate` job introduced in PR 3. If PR 4 lands first, main loses enforcement.
- **PR 5 depends on PR 3**: PR 5 anchors versions through the `_pytest.yml` reusable workflow. Without PR 3, anchoring is impossible.

## Concurrent work coordination

- **PR #1217** (adcp 3.10 → 3.12 migration, open, conflicting): assume merges before our PR 2; PR 2 designed to tolerate either ordering. Re-verify before authoring PR 2.
- **PR #1221** (Flask-to-FastAPI v2.0, open, your branch): planning artifact; will be carved into smaller PRs. Path 1 sequencing chosen — issue #1234 lands first, v2.0 phase PRs rebase onto the new layered model. CSRF concern in PR 1 deferred to v2.0's own CSRF middleware (`src/admin/csrf.py`, +331 lines on the v2.0 branch).
  - **`[project.optional-dependencies].dev` is already deleted on v2.0** — coordinate so PR 2 doesn't accidentally re-introduce the block during rebase.
  - **9 new structural guards (`.guard-baselines/*.json`)** ship with v2.0 — projected post-rollout guard count is 27 (existing) + 1 (PR 2) + 4 (PR 4) + 9 (v2.0) = **41**. PR 4's CLAUDE.md guards table update will need a final pass once v2.0 phase landings settle.

## Success criteria (5 weeks from start)

- All 5 PRs merged on main
- Issue #1234 closed
- OpenSSF Scorecard ≥7.5/10
- `time pre-commit run --all-files` warm < 2s
- `time make quality` < 2 minutes
- 4 weeks of Dependabot PRs cleared with no >5-PR backlog
- ≥1 contributor PR has gone through CODEOWNERS auto-request flow
- Zero post-merge reverts of any of the 5 PRs

## Calendar (5 weeks part-time)

| Week | Activity | Deliverable |
|---|---|---|
| Week 1 | Pre-flight checklist + PR 1 (Path C CodeQL advisory) | PR 1 merged by EOW |
| Week 2 | OpenSSF Scorecard re-run; PR 2; first Dependabot PRs land | PR 2 merged mid-week |
| Week 3 | PR 3 Phase A (overlap, both old + new workflows running) + 48h soak | Phase A merged; new workflows running advisory |
| Week 4 | PR 3 Phase B (admin flips required-checks list) + Phase C (cleanup) + PR 4 | PR 3 fully landed; PR 4 in review |
| Week 5 | PR 4 merged; PR 5; final OpenSSF Scorecard verification; close #1234; flip CodeQL to gating | All 5 PRs merged; Scorecard ≥7.5 |

Built-in slack: 1-2 days per week absorbs Dependabot review load.

**Dependabot backlog tripwire:** if open Dependabot PRs reach 5, pause forward work and clear them. The "no auto-merge" decision is only safe if review keeps pace.

## Update protocol

Update this file after each PR merges:
- Status column → `merged`
- Append a "PR #N merged YYYY-MM-DD" line under the heading
- Cross-reference any drift catalog items (PD1-PD25) closed by the PR

Issue #1234 also gets a comment per merge listing closed PD items.
